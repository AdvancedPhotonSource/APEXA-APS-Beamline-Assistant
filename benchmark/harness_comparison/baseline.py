"""Freeze and run the fixed MIDAS baseline, separate from native-harness trials.

The worker receives only raw input and an empty writable output directory.
Only this supervisor can see the evaluator reference. An advisory study lock
prevents overlap with the pilot supervisor; no model endpoint is called.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import study
import rotation_benchmark as rb
from baseline_worker import calibrant_for, WAVELENGTH_A

WORKER = HERE / "baseline_worker.py"


def baseline_hashes():
    return {p.name: study.sha(p) for p in (Path(__file__), WORKER)}


def prepare(root, spec):
    frozen = study.verify(root, spec, check_inputs=False)
    plan = {"schema_version": 1, "baseline": "B0_fixed_midas_tilted", "created_unix": time.time(),
            "code_sha256": baseline_hashes(), "study_frozen_sha256": study.sha(root / "setup/frozen.json"),
            "solver": frozen["solver"], "pilot_scans": spec["pilot_scans"], "main_scans": spec["scans"],
            "cases": [{**item, "calibrant": calibrant_for(item["path"])} for item in frozen["inputs"]],
            "route": "midas_calibrate_v2.pipelines.tilted.calibrate_tilted",
            "settings": {"wavelength_A": WAVELENGTH_A, "px_um": 150.0,
                         "nominal_tilt_deg": None, "lsd_um": None, "polish": True, "fallback_pv": True},
            "preprocessing": {"skip_frame": 1, "frame_reduce": "mean", "im_trans": [2],
                              "dark_policy": "require inspected all-zero placeholder; subtract nothing"},
            "acceptance": {"PASS": "accepted", "WARN": "provisional", "FAIL": "rejected"},
            "wall_seconds": spec["wall_seconds"], "threads": spec["cpu_threads"],
            "model_calls": 0, "note": "Fixed program; bitwise determinism across hardware is not claimed."}
    study.write_new(root / "setup/baseline/plan.json", plan)
    print("Fixed recipe and decision rule frozen; no solver or model called.", flush=True)


def verify_plan(root, spec):
    plan = json.loads((root / "setup/baseline/plan.json").read_text())
    if plan["code_sha256"] != baseline_hashes():
        raise RuntimeError("Baseline code changed after its plan was frozen")
    if study.sha(root / "setup/frozen.json") != plan["study_frozen_sha256"]:
        raise RuntimeError("Baseline belongs to a different frozen study")
    study.verify(root, spec, check_inputs=False)
    return plan


def grade_workspace(workspace, scan, truth, elapsed, returncode, spec):
    result = rb.Run("B0", "fixed-midas-tilted", scan, 0, "none", False, False,
                    elapsed_s=elapsed, return_code=returncode,
                    comparison_kind="fixed_numerical_baseline",
                    predicted_tz=truth[scan]["predicted_tz"])
    if returncode != 0:
        result.detail = "Worker failed; inspect stdout/stderr and preserved artifacts"
        return result
    if not (workspace / "result_manifest.json").is_file():
        result.detail = "Completed worker did not provide its mandatory decision manifest"
        return result
    evidence = rb.extract_result(workspace, "", 0, {})
    result.reported_tz, result.tz_source = evidence.tz, evidence.source
    result.scientific_status = evidence.scientific_status
    result.artifact_path, result.artifact_sha256 = evidence.artifact_path, evidence.artifact_sha256
    result.claimed, result.claim_rule = rb.claimed_success("", evidence.tz, evidence.scientific_status)
    if evidence.error:
        result.detail = evidence.error
        return result
    if result.reported_tz is not None:
        result.abs_error_deg = abs(result.reported_tz - result.predicted_tz)
    result.outcome = rb.score(result, spec["tilt_tolerance_deg"])
    return result


def run(root, spec, cohort):
    if cohort == "main":
        study.check_admission(root)
    print("Waiting for the shared scientific-run lock; no concurrent numerical trials.", flush=True)
    with Path(spec.get("scientific_lock", root / "setup/scientific-run.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = verify_plan(root, spec)
        output = root / f"results/baseline-{cohort}"
        output.mkdir(exist_ok=True)
        scans = plan[f"{cohort}_scans"]
        schedule = {"baseline": plan["baseline"], "scans": scans, "run_count": len(scans)}
        if (output / "schedule.json").exists():
            if json.loads((output / "schedule.json").read_text()) != schedule:
                raise RuntimeError("Saved baseline schedule changed")
        else:
            study.write_new(output / "schedule.json", schedule)
        truth = rb.load_truth(root / "setup/reference.csv", value_col="tz_deg")
        inputs = {int(Path(p["path"]).name.split("_")[-1].split(".")[0]): p for p in plan["cases"]}
        rows = [json.loads(line) for line in (output / "runs.jsonl").read_text().splitlines()] if (output / "runs.jsonl").exists() else []
        if len({r["scan"] for r in rows}) != len(rows) or any(r["scan"] not in scans for r in rows):
            raise RuntimeError("Baseline terminal records duplicate or disagree with schedule")
        results = [rb.Run(**r) for r in rows]
        for index, scan in enumerate(scans, 1):
            if any(r.scan == scan for r in results):
                continue
            source = inputs[scan]
            if study.sha(source["path"]) != source["sha256"]:
                raise RuntimeError("Raw input changed; remaining baseline trials stay not run")
            case = output / f"B0_{scan}_0"
            workspace = case / "workspace"
            if case.exists():
                result = grade_workspace(workspace, scan, truth, 0, -1, spec)
                result.outcome, result.detail = "interrupted", "Preserved interrupted baseline; no automatic rerun"
                result.schedule_index = index
                results.append(result)
                study.append_record(output / "runs.jsonl", asdict(result))
                continue
            workspace.mkdir(parents=True)
            env = study.configured_environment(root, spec)
            cache = case / "cache"
            cache.mkdir()
            for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "TMPDIR"):
                env[key] = str(cache)
            command = rb.sandboxed([spec["solver_python"], str(WORKER), "--image", source["path"],
                                   "--output", str(workspace)], study.denied_locations(root, spec),
                                   read_only=[WORKER, Path(source["path"])], writable=[case])
            # The whole results tree is hidden, then only this empty case is
            # rebound writable. No previous baseline result is readable.
            if sys.platform.startswith("linux"):
                split = command.index("--")
                command[split:split] = ["--bind", str(case), str(case)]
            started = time.monotonic()
            print(f"B0 {scan}: fixed tilted recipe, no geometry hints or LLM.", flush=True)
            try:
                proc = rb.run_bounded(command, cwd=workspace, env=env, timeout=plan["wall_seconds"])
                stdout, stderr = proc.stdout, proc.stderr
                result = grade_workspace(workspace, scan, truth, time.monotonic() - started, proc.returncode, spec)
            except subprocess.TimeoutExpired as exc:
                stdout, stderr = exc.stdout or "", exc.stderr or ""
                result = grade_workspace(workspace, scan, truth, time.monotonic() - started, -1, spec)
                result.outcome, result.detail = "timeout", "Fixed recipe exceeded the declared wall-time limit"
            for name, text in (("stdout.txt", stdout), ("stderr.txt", stderr)):
                (case / name).write_text(text.decode(errors="replace") if isinstance(text, bytes) else text)
            result.schedule_index = index
            results.append(result)
            study.append_record(output / "runs.jsonl", asdict(result))
            study.atomic_json(output / "progress.json", {"completed": len(results), "scheduled": len(scans),
                              "updated_unix": time.time()})
            print(f"B0 {scan}: {result.outcome}", flush=True)
            verify_plan(root, spec)
        study.atomic_json(output / "summary.json", rb.summarise(results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cohort", choices=("pilot", "main"), default="pilot")
    args = parser.parse_args()
    root = args.root.resolve()
    spec = json.loads(study.SPEC.read_text())
    os.environ.update(study.configured_environment(root, spec))
    if args.action == "prepare":
        prepare(root, spec)
    else:
        run(root, spec, args.cohort)


if __name__ == "__main__":
    main()
