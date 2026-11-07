#!/usr/bin/env python3
"""Export the prespecified acquisition-level main analysis without rerunning trials.

Read a frozen study plus its retained baseline. Write a new report directory outside
both studies. Incomplete cohorts get accounting only, never comparative estimates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_pilot import Evidence, adoption, key


def indexed(entries):
    result = {key(e): e for e in entries}
    if len(result) != len(entries):
        raise ValueError("Duplicate trial record")
    return result


def paired_estimates(acquisitions, arms, *, samples=10000, seed=20261107):
    """Resample acquisitions within calibrant, preserving every arm and repeat."""
    values = np.array([[a[arm] for arm in arms] for a in acquisitions], dtype=float)
    rng = np.random.default_rng(seed)
    strata = sorted({a["calibrant"] for a in acquisitions})
    blocks = []
    for calibrant in strata:
        indices = [i for i, a in enumerate(acquisitions) if a["calibrant"] == calibrant]
        blocks.append(rng.choice(indices, size=(samples, len(indices)), replace=True))
    draws = values[np.concatenate(blocks, axis=1)].mean(axis=1)
    a1 = arms.index("A1")
    return {arm: {
        "mean_difference_A1_minus_arm": float((values[:, a1] - values[:, i]).mean()),
        "percentile_95_interval": np.quantile(draws[:, a1] - draws[:, i], [.025, .975]).tolist(),
    } for i, arm in enumerate(arms) if arm != "A1"}


def select_trace_pairs(rows, external_arms):
    by_key = indexed(rows)
    chosen = {}
    for arm in external_arms:
        pairs = {name: None for name in ("both_complete", "A1_only", "external_only", "neither_complete")}
        for a in sorted((r for r in rows if r["arm"] == "A1"), key=lambda r: r["schedule_index"]):
            b = by_key[(arm, a["scan"], a["trial"])]
            name = {(True, True): "both_complete", (True, False): "A1_only",
                    (False, True): "external_only", (False, False): "neither_complete"}[
                        (a["endpoint"], b["endpoint"])]
            if pairs[name] is None:
                pairs[name] = {"scan": a["scan"], "trial": a["trial"],
                               "A1_schedule_index": a["schedule_index"],
                               "external_schedule_index": b["schedule_index"]}
        chosen[arm] = pairs
    return chosen


def accounting(rows):
    terminal = [r for r in rows if r["record_status"] == "terminal"]
    successful = [r for r in terminal if r["endpoint"]]
    evaluable = [r for r in terminal if r["technically_evaluable"]]
    def costs(group):
        result = {}
        for field in ("elapsed_s", "model_requests"):
            values = [r[field] for r in group if r.get(field) is not None]
            result[field] = {"observed": len(values), "missing": len(group)-len(values),
                             "median": statistics.median(values) if values else None,
                             "total": sum(values) if values else None}
        return result
    return {"scheduled": len(rows), "terminal": len(terminal),
            "started_without_terminal": sum(r["record_status"] == "started_without_terminal" for r in rows),
            "unrun": sum(r["record_status"] == "unrun" for r in rows),
            "completed_endpoint": len(successful),
            "rate_all_scheduled": len(successful)/len(rows) if len(terminal) == len(rows) else None,
            "technically_evaluable": len(evaluable),
            "endpoint_in_evaluable": sum(r["endpoint"] for r in evaluable),
            "outcomes": dict(Counter(r["outcome"] for r in terminal)),
            "trials_with_transport_errors": sum(r["transport_error"] for r in terminal),
            "budget_exhausted": sum(r["budget_exhausted"] for r in terminal),
            "timeouts": sum(r["outcome"] == "timeout" for r in terminal),
            "all_terminal_cost": costs(terminal), "successful_only_cost": costs(successful)}


def analyze(root, baseline_root):
    current, prior = Evidence(root), Evidence(baseline_root)
    root, baseline_root = current.root, prior.root
    frozen = current.json(root / "setup/frozen.json")
    old = prior.json(baseline_root / "setup/frozen.json")
    spec = frozen["spec"]
    if frozen["inputs"] != old["inputs"] or frozen["solver"] != old["solver"]:
        raise ValueError("Retained baseline inputs/solver differ")
    for name in ("scans", "wall_seconds", "cpu_threads", "tilt_tolerance_deg"):
        if spec[name] != old["spec"][name]:
            raise ValueError(f"Retained baseline differs in {name}")
    worker = "source/benchmark/harness_comparison/baseline_worker.py"
    if current.read(root/worker) != prior.read(baseline_root/worker):
        raise ValueError("Retained numerical baseline worker differs")
    plan = prior.json(baseline_root / "setup/baseline/plan.json")
    calibrants = {int(Path(x["path"]).name.split("_")[-1].split(".")[0]): x["calibrant"]
                 for x in plan["cases"]}
    schedule = current.json(root / "setup/main_schedule.json")
    entries = schedule["entries"]
    expected = {(a, s, t) for a in spec["arms"] for s in spec["scans"] for t in range(spec["repeats"])}
    if set(indexed(entries)) != expected or len({e["schedule_index"] for e in entries}) != len(entries):
        raise ValueError("Main schedule does not match declared arms/acquisitions/repeats")
    main = root / "results/main"
    if (main/"schedule.json").exists() and current.json(main/"schedule.json") != schedule:
        raise ValueError("Saved main schedule differs from frozen schedule")
    baseline = baseline_root / "results/baseline-main"
    b_schedule = prior.json(baseline / "schedule.json")
    if b_schedule["scans"] != spec["scans"]:
        raise ValueError("Retained baseline schedule differs")
    baseline_entries = [dict(arm="B0", scan=s, trial=0, schedule_index=i)
                        for i, s in enumerate(spec["scans"], 1)]
    rows = []
    for saved, ev, cohort in ((main, current, entries), (baseline, prior, baseline_entries)):
        records = indexed(ev.jsonl(saved / "runs.jsonl") if (saved / "runs.jsonl").exists() else [])
        if not set(records).issubset(indexed(cohort)):
            raise ValueError("Unscheduled terminal trial")
        for e in cohort:
            is_baseline = e["arm"] == "B0"
            tag = f"{e['arm']}_{e['scan']}_{e['trial']}"
            row = {k: e[k] for k in ("schedule_index", "arm", "scan", "trial")}
            row.update(calibrant=calibrants[e["scan"]], endpoint=None)
            r = records.get(key(e))
            if r is None:
                started = saved/tag if is_baseline else saved/"started"/f"{e['schedule_index']:04d}.json"
                row["record_status"] = "started_without_terminal" if started.exists() else "unrun"
                rows.append(row)
                continue
            if r["schedule_index"] != e["schedule_index"]:
                raise ValueError("Terminal schedule index mismatch")
            audit = r.get("model_audit") or {}
            workspace = saved/tag/"workspace" if is_baseline else saved/"workspaces"/tag
            verified = adoption(workspace, ev, r.get("predicted_tz"), spec["tilt_tolerance_deg"])
            interrupted = r["outcome"] == "interrupted"
            record_matches = (verified.get("artifact_sha256") == r.get("artifact_sha256")
                              and verified["decision"] == r.get("scientific_status"))
            row.update(verified, record_status="terminal", outcome=r["outcome"],
                       artifact_matches_terminal_record=record_matches,
                       return_code=r.get("return_code"),
                       elapsed_s=None if interrupted else r.get("elapsed_s"),
                       model_requests=0 if is_baseline else audit.get("requests"),
                       technically_evaluable=(is_baseline and not interrupted) or audit.get("scientific_evaluable") is True,
                       transport_error=bool(audit.get("transport_errors")),
                       budget_exhausted=bool(audit.get("budget_exhausted")),
                       automatic_procedure_hashes_delivered=audit.get("automatic_procedure_hashes_delivered", []))
            row["endpoint"] = (r["outcome"] == "correct" and r.get("return_code") == 0
                               and verified["accepted_relative_consistency"] is True
                               and record_matches
                               and row["technically_evaluable"] and not row["budget_exhausted"])
            rows.append(row)
    arms = [*spec["arms"], "B0"]
    complete = all(r["record_status"] == "terminal" for r in rows)
    table = []
    for scan in spec["scans"]:
        item = {"scan": scan, "calibrant": calibrants[scan]}
        for arm in arms:
            group = [r for r in rows if r["scan"] == scan and r["arm"] == arm]
            item[arm] = statistics.mean(r["endpoint"] for r in group) if all(
                r["record_status"] == "terminal" for r in group) else None
        table.append(item)
    return {"schema_version": 1, "complete": complete, "study_id": spec["study_id"],
            "endpoint": "Within-budget completion with accepted hash-verified artifact and relative tilt agreement",
            "limits": ["Automated endpoint; independent scientific execution/raw-image review remains required.",
                       "All scheduled trials are retained, including infrastructure failures and interruptions.",
                       "Three repeats are averaged within acquisition; B0 contributes once per acquisition.",
                       "Intervals describe observed development acquisitions, not unseen-data generalization.",
                       "Timeout costs are censored; interrupted elapsed time is unknown, not zero.",
                       "Provider model metadata does not establish gateway routing or identical inference settings.",
                       "Started-without-terminal records do not prove a live worker; inspect current processes."],
            "bootstrap": {"samples": 10000, "seed": 20261107, "stratified_by": "calibrant",
                          "unit": "acquisition", "interval": "percentile 95%"},
            "arms": {a: accounting([r for r in rows if r["arm"] == a]) for a in arms},
            "acquisitions": table, "trials": rows,
            "paired_comparisons": paired_estimates(table, arms) if complete else None,
            "illustrative_pairs": select_trace_pairs(rows, [a for a in ("A3", "A4", "A5") if a in arms]) if complete else None,
            "source_sha256": {"current": current.hashes, "retained_baseline": prior.hashes}}


def markdown(report):
    lines = ["# Main comparison accounting", "",
             "Cohort complete; scientific review remains required." if report["complete"] else
             "Cohort incomplete. Counts are progress only; comparative rates and intervals are withheld.", "",
             "| Arm | Scheduled | Terminal | Endpoint | Technically evaluable | Started, no terminal | Unrun |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm, a in report["arms"].items():
        lines.append(f"| {arm} | {a['scheduled']} | {a['terminal']} | {a['completed_endpoint']} | {a['technically_evaluable']} | {a['started_without_terminal']} | {a['unrun']} |")
    arms = list(report["arms"])
    lines += ["", "Acquisition-level endpoint proportions (B0: one trial; agents: three repeats):", "",
              "| Scan | Calibrant | " + " | ".join(arms) + " |",
              "| --- | --- | " + " | ".join(["---:"]*len(arms)) + " |"]
    for row in report["acquisitions"]:
        values = ["pending" if row[a] is None else f"{row[a]:.3f}" for a in arms]
        lines.append(f"| {row['scan']} | {row['calibrant']} | " + " | ".join(values) + " |")
    if report["complete"]:
        lines += ["", "Paired differences, A1 minus comparator; descriptive stratified 95% bootstrap intervals:", ""]
        for arm, d in report["paired_comparisons"].items():
            lo, hi = d["percentile_95_interval"]
            lines.append(f"- {arm}: {d['mean_difference_A1_minus_arm']:.3f} [{lo:.3f}, {hi:.3f}]")
    lines += ["", *[f"- {x}" for x in report["limits"]], ""]
    return "\n".join(lines)


def plot_completed(report, output):
    """Render the declared estimates, with all scheduled outcomes visible."""
    if not report["complete"]:
        raise ValueError("Comparative figures require a completed cohort")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    arms = list(report["arms"])
    labels = {"A1": "APEXA", "A3": "Claude Code", "A4": "OpenCode", "A5": "Pi",
              "A7": "APEXA: auto off", "B0": "Fixed MIDAS"}
    categories = {"endpoint": ("Accepted + consistent", "#287d5d"),
                  "accepted_wrong": ("Accepted inconsistent", "#ad3940"),
                  "nonacceptance": ("Explicit nonacceptance", "#bd9c59"),
                  "timeout": ("Timeout", "#b16a43"),
                  "budget_exhausted": ("Request/wall budget", "#8562a1"),
                  "infra_error": ("Transport/identity", "#74899d"),
                  "missing_manifest": ("No final manifest", "#b6bbc2"),
                  "interrupted": ("Interrupted", "#3e4a58"),
                  "error": ("Execution/artifact error", "#dfcbaf")}
    def category(row):
        if row["endpoint"]:
            return "endpoint"
        outcome = row["outcome"]
        if outcome in categories:
            return outcome
        if outcome in ("honest_failure", "false_alarm") and row.get("decision") in ("provisional", "rejected", "abstained"):
            return "nonacceptance"
        return "error"
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.3), constrained_layout=True)
    ax = axes[0, 0]
    left = np.zeros(len(arms))
    for kind, (label, color) in categories.items():
        counts = [sum(category(r) == kind for r in report["trials"] if r["arm"] == a) for a in arms]
        if any(counts):
            ax.barh(range(len(arms)), counts, left=left, color=color, label=label)
            left += counts
    ax.set(yticks=range(len(arms)), yticklabels=[labels.get(a, a) for a in arms],
           xlabel="Scheduled trials (B0 once per acquisition)", title="(a) Full outcome accounting")
    ax.invert_yaxis()
    ax.legend(fontsize=6.8, loc="upper left", bbox_to_anchor=(0, -.23), ncol=2, frameon=False)
    ax = axes[0, 1]
    pairs = report["paired_comparisons"]
    for i, (arm, value) in enumerate(pairs.items()):
        mean = value["mean_difference_A1_minus_arm"]
        lo, hi = value["percentile_95_interval"]
        ax.plot([lo, hi], [i, i], color="#37668a", linewidth=2)
        ax.plot(mean, i, "o", color="#37668a")
    ax.axvline(0, color=".55", linestyle="--", linewidth=.8)
    ax.set(yticks=range(len(pairs)), yticklabels=[labels.get(a, a) for a in pairs],
           xlim=(-1, 1), xlabel="A1 − comparator: mean completion difference",
           title="(b) Paired acquisitions; stratified 95% intervals")
    ax.invert_yaxis()
    for ax, field, scale, title, xlabel in [
        (axes[1, 0], "elapsed_s", 60, "(c) All terminal wall times", "Minutes; × denotes a timeout"),
        (axes[1, 1], "model_requests", 1, "(d) All terminal model requests", "Requests; missing values omitted"),
    ]:
        for i, arm in enumerate(arms):
            group = [r for r in report["trials"] if r["arm"] == arm and r.get(field) is not None]
            for j, row in enumerate(group):
                offset = .17*((j % 7)-3)/3
                ax.scatter(row[field]/scale, i+offset, s=13,
                           marker="x" if row["outcome"] == "timeout" else "o",
                           color="#287d5d" if row["endpoint"] else "#74899d", alpha=.7)
            if group:
                median = statistics.median(r[field]/scale for r in group)
                ax.plot([median, median], [i-.25, i+.25], color="black", linewidth=1.5)
        ax.set(yticks=range(len(arms)), yticklabels=[labels.get(a, a) for a in arms],
               xlabel=xlabel, title=title)
        ax.invert_yaxis()
    fig.suptitle("Automated endpoints; independent scientific review required", fontsize=11)
    for ext in ("pdf", "png"):
        fig.savefig(output/f"comparison.{ext}", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if any(args.output.resolve().is_relative_to(p.resolve()) for p in (args.root, args.baseline_root)):
        parser.error("Output must be outside both original studies")
    report = analyze(args.root, args.baseline_root)
    report["analysis_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/"analysis.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    (args.output/"analysis.md").write_text(markdown(report))
    for name in ("acquisitions", "trials"):
        fields = list(dict.fromkeys(k for row in report[name] for k in row))
        with (args.output/f"{name}.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(report[name])
    if report["complete"]:
        plot_completed(report, args.output)
    print(json.dumps({"complete": report["complete"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
