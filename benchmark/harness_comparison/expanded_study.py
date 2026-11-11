"""Fixed, CPU-isolated batches for a separately frozen supported comparison.

The caller holds the common scientific lock for the entire cohort. Batches
follow the frozen schedule, and the next batch waits for both terminal records.
There is no completion-dependent reordering or retry of a started exposure.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import json
import multiprocessing
import os
from pathlib import Path
import time

import study
import rotation_benchmark as rb


def cpu_sets(spec):
    execution = spec["execution"]
    sets = execution["cpu_affinity"]
    if (execution.get("policy") != "fixed_batches_v1" or execution["workers"] != len(sets)
            or not sets or any(len(cpus) != spec["cpu_threads"] for cpus in sets)
            or any(type(cpu) is not int or cpu < 0 for cpus in sets for cpu in cpus)
            or len({cpu for cpus in sets for cpu in cpus}) != sum(map(len, sets))):
        raise ValueError("Invalid fixed worker/CPU policy")
    available = os.sched_getaffinity(0)
    if not all(set(cpus) <= available for cpus in sets):
        raise ValueError("Frozen worker CPUs are unavailable")
    # Logical CPU numbers must not hide sharing a physical core.
    topology = []
    for cpus in sets:
        for cpu in cpus:
            base = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
            topology.append(tuple((base / name).read_text().strip()
                                  for name in ("physical_package_id", "core_id")))
    if len(set(topology)) != len(topology):
        raise ValueError("Workers would share a physical CPU core")
    return sets


def execute(root, spec, output, entry, source, truth, cpus):
    os.sched_setaffinity(0, cpus)
    result = study.run_trial(root, spec, output, entry, Path(source["path"]), truth)
    result.policy_bundle["cpu_affinity"] = cpus
    if entry.get("task_variant") == "pixel_size_100um":
        result.comparison_kind = "supplied_pixel_size_contradiction"
        # Relative tilt cannot validate an absolute scale. Retain its numerical
        # diagnostic, but never count it as challenge success.
        result.policy_bundle["nominal_outcome_diagnostic_only"] = result.outcome
        if result.outcome not in ("timeout", "infra_error", "budget_exhausted", "error", "missing_manifest"):
            result.outcome = "challenge_" + (result.scientific_status or "unresolved")
        result.detail += "; discrepancy detection and response require saved-evidence adjudication"
    return result


def interrupted(entry, spec):
    return rb.Run(entry["arm"], rb.ARMS[entry["arm"]].harness, entry["scan"],
                  entry["trial"], spec["model"], True, rb.ARMS[entry["arm"]].enforcement,
                  outcome="interrupted", schedule_index=entry["schedule_index"],
                  schedule_block=entry["schedule_block"], schedule_position=entry["schedule_position"],
                  detail="Started before supervisor interruption; retained without rerunning")


def run_locked(root, spec, cohort):
    sets = cpu_sets(spec)
    frozen = study.verify(root, spec)
    qualification = json.loads((root / "setup/qualification.json").read_text())
    if not qualification["passed"] or qualification["source_hashes"] != study.source_hashes():
        raise RuntimeError("Frozen setup has not passed qualification")
    schedule = json.loads((root / f"setup/{cohort}_schedule.json").read_text())
    output = root / "results" / cohort
    output.mkdir(parents=True, exist_ok=True)
    if (output / "schedule.json").exists():
        if json.loads((output / "schedule.json").read_text()) != schedule:
            raise RuntimeError("Saved schedule changed")
    else:
        study.write_new(output / "schedule.json", schedule)
    (output / "started").mkdir(exist_ok=True)
    results = study.prior_results(output, schedule)
    completed = {row.schedule_index for row in results}
    truth = study.load_reference(root, spec)
    sources = {int(p["path"].split("_")[-1].split(".")[0]): p for p in frozen["inputs"]}

    def progress(active, status="running"):
        study.atomic_json(output / "progress.json", {
            "completed": len(results), "scheduled": schedule["run_count"],
            "active": active, "status": status, "updated_unix": time.time(),
            "execution": spec["execution"]})

    def retain(result):
        study.append_record(output / "runs.jsonl", asdict(result))
        results.append(result)
        completed.add(result.schedule_index)
        study.atomic_json(output / "summary.json", rb.summarise(results))
        print(f"{len(results)}/{schedule['run_count']}: {result.arm} {result.scan} {result.outcome}", flush=True)

    with ProcessPoolExecutor(max_workers=len(sets), mp_context=multiprocessing.get_context("spawn")) as pool:
        for start in range(0, len(schedule["entries"]), len(sets)):
            entries = schedule["entries"][start:start + len(sets)]
            unfinished = [e for e in entries if e["schedule_index"] not in completed]
            if not unfinished:
                continue
            host = rb.wait_for_host(spec["model_base_url"])
            study.verify(root, spec, check_inputs=False)
            active = []
            for slot, original in enumerate(entries):
                entry = dict(original)
                index = entry["schedule_index"]
                if index in completed:
                    continue
                marker = output / "started" / f"{index:04d}.json"
                if marker.exists():
                    retain(interrupted(entry, spec))
                    continue
                source = sources[entry["scan"]]
                if study.sha(source["path"]) != source["sha256"]:
                    raise RuntimeError("Raw input changed")
                if cohort == "challenge":
                    entry["task_variant"] = spec["challenge_variant"]
                study.write_new(marker, {**entry, "started_unix": time.time(),
                    "supervisor_pid": os.getpid(), "cpu_affinity": sets[slot]})
                active.append((entry, pool.submit(execute, root, spec, output, entry, source, truth, sets[slot])))
            progress([e for e, _ in active])
            # Retain every attempt even if one infrastructure worker fails.
            for entry, future in active:
                try:
                    result = future.result()
                except Exception as exc:
                    result = interrupted(entry, spec)
                    result.outcome = "infra_error"
                    result.detail = f"Worker exception: {type(exc).__name__}: {exc}"
                result.host = host
                retain(result)
                progress([e for e, _ in active if e["schedule_index"] not in completed])
    progress([], "completed")
