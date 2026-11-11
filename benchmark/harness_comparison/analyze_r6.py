"""Offline, prespecified R6 analysis; never launch, repair or rerun an experiment."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import statistics

import numpy as np

import analyze_main
import study


def paired_estimates(acquisitions, arms, *, reference="A7", samples=10000, seed=20261111):
    values = np.array([[row[arm] for arm in arms] for row in acquisitions], dtype=float)
    rng = np.random.default_rng(seed)
    blocks = []
    for group in sorted({row["stratum"] for row in acquisitions}):
        indices = [i for i, row in enumerate(acquisitions) if row["stratum"] == group]
        blocks.append(rng.choice(indices, size=(samples, len(indices)), replace=True))
    draws = values[np.concatenate(blocks, axis=1)].mean(axis=1)
    j = arms.index(reference)
    return {arm: {"mean_difference_arm_minus_A7": float((values[:, i] - values[:, j]).mean()),
                  "percentile_95_interval": np.quantile(draws[:, i] - draws[:, j], [.025, .975]).tolist()}
            for i, arm in enumerate(arms) if arm != reference}


def costs(rows):
    result = {}
    for field in ("elapsed_s", "model_requests"):
        values = [r[field] for r in rows if r.get(field) is not None]
        result[field] = {"observed": len(values), "missing": len(rows) - len(values),
            "median": statistics.median(values) if values else None,
            "range": [min(values), max(values)] if values else None}
    result["elapsed_s"]["description"] = "Harness or fixed-solver subprocess runtime, excluding staging."
    return result


def analyze(root):
    # Reuse the existing artifact/manifest/hash audit, not terminal prose alone.
    report = analyze_main.analyze(root, root)
    frozen = json.loads((root / "setup/frozen.json").read_text())
    spec = frozen["spec"]
    for name, digest in frozen.get("evaluator_files", {}).items():
        if study.sha(root / "setup" / name) != digest:
            raise ValueError(f"Frozen evaluator input changed: {name}")
    if study.sha(root / "setup/reference.csv") != frozen["reference_sha256"]:
        raise ValueError("Frozen primary reference changed")
    truth = study.load_reference(root, spec)
    for row in report["trials"]:
        row["sweep"] = "first" if row["scan"] < 304 else "second"
        if row.get("outcome") == "not_launched_cutoff":
            row.update(record_status="not_launched_cutoff", endpoint=False,
                       elapsed_s=None, model_requests=None, technically_evaluable=False)
    for cohort in ("main", "baseline-main"):
        path = root / "results" / cohort / "runs.jsonl"
        for line in path.read_text().splitlines() if path.exists() else []:
            row = json.loads(line)
            if row.get("predicted_tz") is not None and abs(row["predicted_tz"] - truth[row["scan"]]["predicted_tz"]) > 1e-10:
                raise ValueError("Terminal reference differs from the frozen within-sweep reference")
    arms = [*spec["arms"], "B0"]
    for row in report["acquisitions"]:
        row["sweep"] = "first" if row["scan"] < 304 else "second"
        row["stratum"] = row["sweep"] + "_" + row["calibrant"]
        # The shared audit initially treats administrative cutoff records as
        # terminal failures. Do not present their acquisition means as observed
        # failures after reclassifying those records as never launched.
        for arm in arms:
            rows = [r for r in report["trials"] if r["scan"] == row["scan"] and r["arm"] == arm]
            row[arm] = statistics.mean(r["endpoint"] for r in rows) if all(
                r["record_status"] == "terminal" for r in rows) else None
    summary = {}
    for arm in arms:
        rows = [r for r in report["trials"] if r["arm"] == arm]
        attempted = [r for r in rows if r["record_status"] == "terminal"]
        accepted = [r for r in attempted if r["endpoint"]]
        summary[arm] = {"scheduled": len(rows), "attempted_terminal": len(attempted),
            "completed_endpoint": len(accepted), "rate_all_scheduled": len(accepted) / len(rows),
            "not_launched_cutoff": sum(r["record_status"] == "not_launched_cutoff" for r in rows),
            "record_statuses": dict(Counter(r["record_status"] for r in rows)),
            "outcomes": dict(Counter(r.get("outcome", "unrun") for r in rows)),
            "accepted_but_inconsistent": sum(r.get("outcome") == "accepted_wrong" for r in rows),
            "attempted_cost": costs(attempted), "completed_cost": costs(accepted)}
    agent_rows = [r for r in report["trials"] if r["arm"] != "B0"]
    attempted_complete = all(r["record_status"] == "terminal" for r in agent_rows)
    complete = all(r["record_status"] == "terminal" for r in report["trials"])
    report.update(arms=summary, complete=complete, agent_attempts_complete=attempted_complete,
        accounting_complete=all(r["record_status"] in ("terminal", "not_launched_cutoff")
                                for r in report["trials"]),
        not_launched_cutoff=sum(r["record_status"] == "not_launched_cutoff" for r in report["trials"]),
        illustrative_pairs=report.get("illustrative_pairs") if complete else None,
        paired_comparisons=None,
        bootstrap={"samples": 10000, "seed": spec["order_seed"], "reference": "A7",
            "stratified_by": "sweep and calibrant", "unit": "acquisition; two repeats averaged",
            "interval": "percentile 95%; no significance stars"},
        limits=["Both acquisitions have prior solver analyses; no previously-unused-data claim.",
                "Primary endpoint requires independent execution/raw-image review before scientific conclusions.",
                "Administrative cutoff cells stay in the planned denominator and are distinct from attempted failures.",
                "Costs exclude never-launched cells; interrupted elapsed times remain unknown.",
                "elapsed_s measures subprocess runtime, excluding staging; the model relay deadline starts before staging.",
                "Paired intervals are withheld if any agent cell is unlaunched or nonterminal.",
                "Relative tilt consistency is not independent absolute-angle or distance metrology."])
    if attempted_complete:
        report["paired_comparisons"] = {"overall": paired_estimates(report["acquisitions"], spec["arms"], seed=spec["order_seed"])}
        for sweep in ("first", "second"):
            rows = [r for r in report["acquisitions"] if r["sweep"] == sweep]
            report["paired_comparisons"][sweep] = paired_estimates(rows, spec["arms"], seed=spec["order_seed"])
    # Re-score only this new R6's verified artifact tilts against the alternative
    # frozen R2 reference; original records and decisions remain unchanged.
    alternative = {int(r["scan"]): float(r["predicted_tz_deg"]) for r in
                   csv.DictReader((root / "setup/reference_rs2_r2.csv").open())}
    report["second_sweep_reference_sensitivity"] = [{
        "arm": r["arm"], "scan": r["scan"], "trial": r["trial"],
        "primary_endpoint": r["endpoint"],
        "alternative_relative_agreement": abs(r["adopted_tz"] - alternative[r["scan"]]) <= spec["tilt_tolerance_deg"]
            if r.get("adopted_tz") is not None else None}
        for r in report["trials"] if r["scan"] >= 304]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.root.resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for name, rows in (("acquisitions", report["acquisitions"]), ("trials", report["trials"])):
        fields = list(dict.fromkeys(key for row in rows for key in row))
        with (args.output / f"{name}.csv").open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
