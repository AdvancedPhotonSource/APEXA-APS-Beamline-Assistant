"""Small, frozen verification study; retain every attempt and gate block admission.

Reuses the qualified native adapters, model relay and sandbox from the existing
comparison. A block is a matched six-attempt scale comparison or a three-attempt
intervention comparison. No automatic retries or reinterpretation of old trials.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import fcntl
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import time

import expanded_study
from model_audit import ModelAudit, require_provider_available
import rotation_benchmark as rb
import study


INTERVENTION = """You have one X-ray powder-diffraction calibration exposure: {fname}.

Compare a radial-only detector distortion model with a no-distortion model on
this exposure. Use the installed MIDAS four-stage calibration route for both.
Keep the image preparation, starting inputs, tilt search tolerance (10 degrees),
and numerical solver version the same. Assess whether the comparison supports
a scientific conclusion about the need for distortion terms.

Beamline-confirmed acquisition facts:
- Energy 63.314 keV; detector pixels 150 um, no binning; ImTransOpt 2.
- Average frames 1 through 20 in exchange/data, excluding frame 0.
- Use the calibrant identified by the acquisition and its documented lattice.

For radial-only, only the MIDAS radial terms p2, p4 and p5 may refine within the
distortion block. All other distortion terms must remain zero. For no distortion,
all p0 through p14 must remain zero. Geometry may refine in both models.
Check what each computation actually fitted before interpreting the comparison.
If the available interface cannot honor a restriction, report that limitation;
do not substitute a different model and call it the requested comparison.

Use your native tools and the available technique documentation. Do not use old
results. You have 3600 seconds, 120 generation requests and four CPU threads.
Keep solver acceptance distinct from faithful execution of a requested model.
Retain rejected fits and all verification evidence. Reserve time for the final
decision, and avoid optional reports or plots unless needed for that decision.

Write result_manifest.json with schema_version 1, task "distortion_comparison",
scientific_status (accepted, provisional, rejected or abstained), a nonempty
reason and a models mapping. For each executed model, models.radial and
models.none must contain artifact_path (workspace-relative exported MIDAS
parameter file), artifact_sha256, and restriction_status (verified, violated or
unassessed). Add an evidence list of workspace-relative verification files.
If a model could not run, omit its artifact fields and explain why. Describe
whether the scientific model comparison is supported and any remaining limits.
"""


def schedule():
    blocks = []
    cases = [("scale", 274, 0), ("intervention", 274, 0),
             ("scale", 295, 0), ("scale", 274, 1),
             ("scale", 295, 1), ("intervention", 274, 1)]
    arms = ["A1", "A3", "A5"]
    index = 0
    for b, (kind, scan, repeat) in enumerate(cases):
        order = arms[b % 3:] + arms[:b % 3]
        variants = (["nominal", "pixel_size_100um"] if b % 2 == 0 else
                    ["pixel_size_100um", "nominal"]) if kind == "scale" else ["intervention"]
        entries = []
        for variant in variants:
            for position, arm in enumerate(order):
                entries.append({"arm": arm, "scan": scan, "trial": repeat,
                    "task": kind, "condition": variant, "schedule_index": index,
                    "schedule_block": b, "schedule_position": position})
                index += 1
        blocks.append(entries)
    return blocks


def assess_intervention(workspace, since=None):
    """Check declared artifacts; coefficient zeros are not an optimizer-mask audit."""
    workspace = Path(workspace)
    result = {"manifest_valid": False, "models": {}, "errors": [],
              "active_optimizer_variables": "requires_execution_trace_audit",
              "scientific_interpretation": "requires_trace_adjudication"}
    path = workspace / "result_manifest.json"
    try:
        if path.is_symlink():
            raise ValueError("manifest is a symlink")
        manifest = json.loads(path.read_text())
        if (manifest.get("schema_version") != 1 or manifest.get("task") != "distortion_comparison"
                or manifest.get("scientific_status") not in {"accepted", "provisional", "rejected", "abstained"}
                or not isinstance(manifest.get("reason"), str) or not manifest["reason"].strip()
                or not isinstance(manifest.get("models"), dict)):
            raise ValueError("invalid decision schema")
        result.update(manifest_valid=True, scientific_status=manifest["scientific_status"],
                      reason=manifest["reason"], manifest_sha256=study.sha(path))
        for model in ("radial", "none"):
            entry = manifest["models"].get(model, {})
            if not isinstance(entry, dict):
                raise ValueError("model entry must be a mapping")
            checked = {"artifact_valid": False, "declared_restriction_status": entry.get("restriction_status")}
            result["models"][model] = checked
            if not entry.get("artifact_path"):
                checked["error"] = "no adopted model artifact"
                continue
            rel = Path(entry["artifact_path"])
            file = workspace / rel
            if rel.is_absolute() or ".." in rel.parts or file.is_symlink() or not file.resolve().is_relative_to(workspace.resolve()):
                raise ValueError("artifact outside workspace or symlink")
            if study.sha(file) != entry.get("artifact_sha256"):
                raise ValueError("artifact hash mismatch")
            if since is not None and file.stat().st_mtime < since:
                raise ValueError("artifact predates this trial")
            coeff = {}
            for line in file.read_text().splitlines():
                fields = line.split("#", 1)[0].split()
                if fields and fields[0] in {f"p{i}" for i in range(15)}:
                    value = float(fields[1])
                    if not math.isfinite(value) or (fields[0] in coeff and coeff[fields[0]] != value):
                        raise ValueError("nonfinite or conflicting distortion coefficient")
                    coeff[fields[0]] = value
            required = {f"p{i}" for i in range(15)} - ({"p2", "p4", "p5"} if model == "radial" else set())
            missing = sorted(required - coeff.keys())
            moved = {k: coeff[k] for k in sorted(required & coeff.keys()) if abs(coeff[k]) > 1e-12}
            checked.update(artifact_valid=True, artifact_path=str(rel),
                artifact_sha256=study.sha(file), coefficients=coeff,
                missing_required=missing, moved_required=moved,
                exported_restriction="unassessed" if missing else "violated" if moved else "satisfied")
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    return result


def usage_totals(output):
    totals = {"generation_requests": 0, "input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
              "provider_denials": 0}
    for path in Path(output).glob("*/model_calls/*.jsonl"):
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("event") != "generation":
                continue
            totals["generation_requests"] += 1
            usage = record.get("usage", {})
            for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                fallback = "prompt_tokens" if key == "input_tokens" else "completion_tokens" if key == "output_tokens" else key
                totals[key] += usage.get(key, usage.get(fallback, 0)) or 0
            totals["provider_denials"] += bool(record.get("provider_access_denied"))
    return totals


def worker(root, spec, entry, source, truth, cpus):
    os.sched_setaffinity(0, cpus)
    output = root / "results" / entry["condition"]
    output.mkdir(parents=True, exist_ok=True)
    if entry["task"] == "scale":
        item = {**entry, "task_variant": entry["condition"]}
        run = study.run_trial(root, spec, output, item, Path(source["path"]), truth)
        run.comparison_kind = "scale_discrepancy_and_matched_clean_control"
        run.policy_bundle["relative_outcome_diagnostic_only"] = run.outcome
        assessment = {"discrepancy_detection": "requires_trace_adjudication",
                      "absolute_scale_validity": "not_established_by_tilt_score"}
    else:
        study.check_image_qualifications(root, spec)
        started_unix = time.time()
        log = output / "model_calls" / f"{entry['arm']}_{entry['scan']}_{entry['trial']}.jsonl"
        candidates = output / "procedure_candidates" / log.name
        with ModelAudit(spec["model_base_url"], os.environ["ANL_USERNAME"], spec["model"], log,
                        max_requests=spec["max_model_requests"], max_output_tokens=spec["max_output_tokens"],
                        timeout=spec["wall_seconds"], procedure_candidates=candidates) as audit:
            run = rb.run_one(rb.ARMS[entry["arm"]], Path(source["path"]), entry["scan"], entry["trial"],
                truth, study.MANUALS, audit.base_url, audit.token, spec["model"], spec["tilt_tolerance_deg"],
                spec["wall_seconds"], output, False, ground_truth=root / "setup/reference.csv",
                extra_deny=study.denied_locations(root, spec),
                native_context_manifest=Path(spec["context_manifest"]), structured_logs=True,
                scratch_root=root / "work", prompt_override=INTERVENTION.format(fname=Path(source["path"]).name),
                pi_image_input=spec.get("pi_image_input", False),
                require_manifest=False, procedure_candidates=candidates, result_kind="execution_only")
        run.model_audit = audit.summary()
        study.apply_model_audit(run)
        run.comparison_kind = "requested_distortion_model_fidelity"
        assessment = assess_intervention(output / "workspaces" / f"{entry['arm']}_{entry['scan']}_{entry['trial']}", started_unix)
    run.policy_bundle["cpu_affinity"] = cpus
    for key in ("schedule_index", "schedule_block", "schedule_position"):
        setattr(run, key, entry[key])
    return {"entry": entry, "execution": asdict(run), "assessment": assessment}


def run(root, spec, max_blocks):
    study.verify(root, spec)
    qualification = json.loads((root / "setup/qualification.json").read_text())
    if not qualification.get("passed") or qualification["source_hashes"] != study.source_hashes():
        raise RuntimeError("Qualification no longer matches frozen source")
    if spec["verification_schedule"] != schedule():
        raise RuntimeError("Verification schedule differs from the frozen source")
    sets = expanded_study.cpu_sets(spec)
    output = root / "results"
    records = output / "verification.jsonl"
    output.mkdir(exist_ok=True)
    started = output / "started"
    started.mkdir(exist_ok=True)
    prior = [json.loads(line) for line in records.read_text().splitlines()] if records.exists() else []
    complete = {r["entry"]["schedule_index"] for r in prior}
    if len(complete) != len(prior):
        raise RuntimeError("Duplicate terminal records")
    frozen = json.loads((root / "setup/frozen.json").read_text())
    sources = {int(Path(i["path"]).name.split("_")[-1].split(".")[0]): i for i in frozen["inputs"]}
    truth = study.load_reference(root, spec)
    status_path = root / "setup/verification-status.json"
    def status(state, **extra):
        study.atomic_json(status_path, {"pid": os.getpid(), "status": state,
            "completed": len(complete), "scheduled": 30, "updated_unix": time.time(),
            "usage": usage_totals(output), **extra})
    blocks_run = 0
    with Path(spec["scientific_lock"]).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
                for b, entries in enumerate(schedule()):
                    remaining = [e for e in entries if e["schedule_index"] not in complete]
                    if not remaining:
                        continue
                    if len(remaining) != len(entries):
                        raise RuntimeError("Partially recorded block needs explicit audit; no automatic rerun")
                    require_provider_available(root)
                    if expanded_study.deadline_reached(spec):
                        status("not_admitted_cutoff", next_block=b)
                        return
                    if blocks_run >= max_blocks:
                        status("usage_review_needed", next_block=b)
                        return
                    study.verify(root, spec, check_inputs=False)
                    host = rb.wait_for_host(spec["model_base_url"], budget_s=300, poll_s=30)
                    for offset in range(0, len(entries), 3):
                        require_provider_available(root)
                        wave = entries[offset:offset + 3]
                        active = []
                        for slot, entry in enumerate(wave):
                            marker = started / f"{entry['schedule_index']:04d}.json"
                            study.write_new(marker, {**entry, "started_unix": time.time(),
                                "cpu_affinity": sets[slot], "supervisor_pid": os.getpid(), "host": host})
                            active.append((entry, pool.submit(worker, root, spec, entry,
                                                             sources[entry["scan"]], truth, sets[slot])))
                        status("running", block=b, active=wave)
                        for entry, future in active:
                            try:
                                record = future.result()
                            except Exception as exc:
                                record = {"entry": entry, "execution": {"outcome": "infra_error",
                                          "detail": f"{type(exc).__name__}: {exc}"}, "assessment": {}}
                            study.append_record(records, record)
                            prior.append(record)
                            complete.add(entry["schedule_index"])
                            status("running", block=b, active=[e for e, _ in active if e["schedule_index"] not in complete])
                    blocks_run += 1
            status("execution_completed_pending_scientific_audit")
        except BaseException as exc:
            status("stopped", error=f"{type(exc).__name__}: {exc}")
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "assess"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=1)
    args = parser.parse_args()
    root = args.root.resolve()
    study.SPEC = study.HERE / "studies/verification-2026-11/study.json"
    spec = json.loads(study.SPEC.read_text())
    if spec != json.loads((root / "setup/study.json").read_text()):
        raise RuntimeError("Source and setup specifications differ")
    os.environ.update(study.configured_environment(root, spec))
    if not os.environ.get("ANL_USERNAME"):
        config = json.loads((Path.home() / ".apexa/connection.json").read_text())["config"]
        if config["base_url"].rstrip("/") != spec["model_base_url"].rstrip("/"):
            raise RuntimeError("Provider differs from frozen specification")
        os.environ["ANL_USERNAME"] = config["username"]
    if args.action == "run":
        if args.blocks < 1:
            raise ValueError("blocks must be positive")
        run(root, spec, args.blocks)
    else:
        print(json.dumps({"usage": usage_totals(root / "results")}, indent=2))


if __name__ == "__main__":
    main()
