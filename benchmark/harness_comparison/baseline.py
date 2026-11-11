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


def run_case(root, spec, plan, source, scan, truth, cpus=None, cohort="main"):
    if cpus is not None:
        os.sched_setaffinity(0, cpus)
    case = root / f"results/baseline-{cohort}" / f"B0_{scan}_0"
    workspace = case / "workspace"
    env = study.configured_environment(root, spec)
    cache = case / "cache"
    cache.mkdir()
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "TMPDIR", "NUMBA_CACHE_DIR"):
        env[key] = str(cache)
    for key in ("ANL_USERNAME", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    command = rb.sandboxed([spec["solver_python"], str(WORKER), "--image", source["path"],
                           "--output", str(workspace)], study.denied_locations(root, spec),
                           read_only=[WORKER, Path(source["path"])], writable=[case])
    if sys.platform.startswith("linux"):
        split = command.index("--")
        command[split:split] = ["--bind", str(case), str(case)]
    started = time.monotonic()
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
    result.policy_bundle["cpu_affinity"] = cpus
    return result


def run(root, spec, cohort):
    if cohort == "main":
        study.check_admission(root)
    print("Waiting for the shared scientific-run lock.", flush=True)
    with Path(spec.get("scientific_lock", root / "setup/scientific-run.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = verify_plan(root, spec)
        output = root / f"results/baseline-{cohort}"
        output.mkdir(parents=True, exist_ok=True)
        scans = plan[f"{cohort}_scans"]
        schedule = {"baseline": plan["baseline"], "scans": scans, "run_count": len(scans)}
        if (output / "schedule.json").exists():
            if json.loads((output / "schedule.json").read_text()) != schedule:
                raise RuntimeError("Saved baseline schedule changed")
        else:
            study.write_new(output / "schedule.json", schedule)
        truth = study.load_reference(root, spec)
        inputs = {int(Path(p["path"]).name.split("_")[-1].split(".")[0]): p for p in plan["cases"]}
        rows = [json.loads(line) for line in (output / "runs.jsonl").read_text().splitlines()] if (output / "runs.jsonl").exists() else []
        if len({r["scan"] for r in rows}) != len(rows) or any(r["scan"] not in scans for r in rows):
            raise RuntimeError("Baseline terminal records duplicate or disagree with schedule")
        results = [rb.Run(**r) for r in rows]
        completed = {r.scan for r in results}
        from expanded_study import cpu_sets, deadline_reached
        sets = cpu_sets(spec) if spec.get("execution", {}).get("workers", 1) > 1 else [None]
        from concurrent.futures import ProcessPoolExecutor
        import multiprocessing

        def retain(result, index):
            result.schedule_index = index
            results.append(result)
            completed.add(result.scan)
            study.append_record(output / "runs.jsonl", asdict(result))
            study.atomic_json(output / "progress.json", {"completed": len(results), "scheduled": len(scans),
                "not_launched": sum(r.outcome == "not_launched_cutoff" for r in results),
                "updated_unix": time.time()})
            print(f"B0 {result.scan}: {result.outcome}", flush=True)

        with ProcessPoolExecutor(max_workers=len(sets), mp_context=multiprocessing.get_context("spawn")) as pool:
            for start in range(0, len(scans), len(sets)):
                stopped = deadline_reached(spec)
                if not stopped:
                    verify_plan(root, spec)
                    stopped = deadline_reached(spec)
                active = []
                for slot, scan in enumerate(scans[start:start + len(sets)]):
                    index = start + slot + 1
                    if scan in completed:
                        continue
                    case = output / f"B0_{scan}_0"
                    workspace = case / "workspace"
                    if case.exists() or stopped:
                        result = grade_workspace(workspace, scan, truth, 0, -1, spec)
                        result.outcome = "interrupted" if case.exists() else "not_launched_cutoff"
                        result.detail = "Existing attempt retained without rerunning" if case.exists() else "Prespecified cutoff reached; no solver launched"
                        retain(result, index)
                        continue
                    source = inputs[scan]
                    if study.sha(source["path"]) != source["sha256"]:
                        raise RuntimeError("Raw input changed")
                    workspace.mkdir(parents=True)
                    study.write_new(case / "started.json", {"scan": scan, "cpu_affinity": sets[slot], "started_unix": time.time()})
                    active.append((index, scan, pool.submit(run_case, root, spec, plan, source, scan, truth, sets[slot], cohort)))
                for index, scan, future in active:
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = grade_workspace(output / f"B0_{scan}_0/workspace", scan, truth, 0, -1, spec)
                        result.outcome, result.detail = "infra_error", f"Worker exception: {type(exc).__name__}: {exc}"
                    retain(result, index)
        study.atomic_json(output / "summary.json", rb.summarise(results))
        study.atomic_json(output / "progress.json", {"completed": len(results), "scheduled": len(scans),
            "not_launched": sum(r.outcome == "not_launched_cutoff" for r in results),
            "status": "stopped_at_cutoff" if any(r.outcome == "not_launched_cutoff" for r in results) else "completed",
            "updated_unix": time.time()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--spec", type=Path, default=study.SPEC)
    parser.add_argument("--cohort", choices=("pilot", "main"), default="pilot")
    args = parser.parse_args()
    root = args.root.resolve()
    study.SPEC = args.spec.resolve()
    if not study.SPEC.is_relative_to(study.REPO):
        parser.error("Specification must be inside the source snapshot")
    spec = json.loads(study.SPEC.read_text())
    os.environ.update(study.configured_environment(root, spec))
    if args.action == "prepare":
        prepare(root, spec)
    else:
        run(root, spec, args.cohort)


if __name__ == "__main__":
    main()
