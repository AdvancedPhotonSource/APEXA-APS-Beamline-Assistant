"""Run six isolated real-gold FF attempts after fresh execution/transport gates."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import fcntl
import json
import math
import multiprocessing
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ff_harness_pilot.baseline import INPUTS, checked_inputs, sha, write
from ff_harness_pilot.evaluate import evaluate, grain_structure, parameter_facts
from ff_harness_pilot.qualify import environment
import rotation_benchmark as rb
from model_audit import ModelAudit, require_provider_available
import native_context

def wall_budget(reference_seconds):
    if not math.isfinite(reference_seconds) or reference_seconds <= 0:
        raise ValueError("A completed positive reference duration is required")
    return max(3600, 300 * math.ceil((2 * reference_seconds + 900) / 300))


def source_hashes(root):
    files = list((root / "source/benchmark/ff_harness_pilot").glob("*.py"))
    files += [root / "source/benchmark/ff_harness_pilot" / name for name in ("task.txt", "PROTOCOL.md")]
    files += [root / "source/benchmark/harness_comparison" / name for name in (
        "rotation_benchmark.py", "model_audit.py", "run_comparison.py", "native_context.py",
        "_apexa_driver.py", "procedure_audit.py", "qualify_guided_adapters.py")]
    return {str(path.relative_to(root / "source")): sha(path) for path in sorted(files)}


def setup_hashes(root):
    return {name: sha(root / "setup" / name) for name in (
        "native-environment.json", "native-cli-versions.json", "midas-freeze-with-plots.txt",
        "native-context.json", "sandbox-solver-check.json", "qualification.json")}


def freeze(root):
    status = json.loads((root / "setup/reference-status.json").read_text())
    if status["status"] != "execution_completed_pending_scientific_audit":
        raise ValueError("Fresh reference execution has not completed")
    reference = Path(status["output"])
    receipt = json.loads((reference / "reference.json").read_text())
    for name in ("calibration", "pipeline"):
        if json.loads((reference / name / "exit.json").read_text())["return_code"] != 0:
            raise ValueError("Reference stage failed")
    grain_path = Path(receipt["grain_artifact"])
    parameter_path = reference / "calibration/Parameters.txt"
    if sha(grain_path) != receipt["grain_sha256"] or sha(parameter_path) != receipt["parameter_sha256"]:
        raise ValueError("Reference artifact identity changed")
    structure, facts = grain_structure(grain_path), parameter_facts(parameter_path)
    if not (structure["nonempty"] and structure["rotations_valid"] and structure["unique_integer_ids"]
            and facts["finite_geometry"] and facts["operator_facts_match"]):
        raise ValueError("Reference structural/parameter check failed; inspect before agent trials")
    qualification = json.loads((root / "setup/qualification.json").read_text())
    if not qualification["passed"]:
        raise ValueError("Native interface qualification has not passed")
    if qualification["source_sha256"] != sha(HERE / "qualify.py"):
        raise ValueError("Qualification environment code changed")
    if not json.loads((root / "setup/sandbox-solver-check.json").read_text())["passed"]:
        raise ValueError("Solver access inside the sandbox is not qualified")
    context = root / "setup/native-context.json"
    native_context.load(context, native_repo=root / "source")
    inputs = checked_inputs(root)
    slots = [list(range(start, start + 8)) for start in (32, 40, 48)]
    physical = [tuple((Path(f"/sys/devices/system/cpu/cpu{cpu}/topology") / key).read_text().strip()
                     for key in ("physical_package_id", "core_id")) for slot in slots for cpu in slot]
    if len(set(physical)) != 24:
        raise ValueError("Pilot CPU slots share physical cores")
    budget = wall_budget(receipt["total_numerical_elapsed_s"])
    prompt = (HERE / "task.txt").read_text().format(wall_seconds=budget)
    frozen = {"schema_version": 1, "task": "real_gold_ff", "frozen_unix": time.time(),
        "model": "claudeopus5", "upstream": "https://apps.inside.anl.gov/argoapi/v1",
        "arms_by_wave": [["A1", "A3", "A5"], ["A5", "A1", "A3"]], "cpu_slots": slots,
        "max_requests": 120, "max_output_tokens": 16000, "wall_seconds": budget,
        "budget_basis": "max(3600, ceil((2 * fresh reference seconds + 900) / 300) * 300)",
        "reference_seconds": receipt["total_numerical_elapsed_s"],
        "reference_sha256": sha(reference / "reference.json"), "reference_output": str(reference),
        "reference_structural_check": structure, "reference_parameter_check": facts,
        "qualification_sha256": sha(root / "setup/qualification.json"),
        "context_sha256": sha(context), "inputs": inputs, "source_hashes": source_hashes(root),
        "setup_hashes": setup_hashes(root),
        "installed_solver_packages": json.loads(subprocess.check_output([
            str(root / "envs/midas/bin/python"), "-c",
            "import importlib.metadata as m,json;print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))"], text=True)),
        "solver_freeze_sha256": sha(root / "setup/midas-freeze-with-plots.txt"),
        "prompt": prompt, "physical_validity": "unestablished",
        "scope": "Single-acquisition workflow pilot; two attempts per system, no system ranking",
        "retries": "No replacement or budget extension for an admitted scientific attempt"}
    with (root / "setup/pilot-frozen.json").open("x") as stream:
        json.dump(frozen, stream, indent=2)
        stream.write("\n")
    return frozen


def run_trial(root, frozen, wave, slot, arm):
    output = root / "results/agents"
    trial_id = f"{arm}_1181_{wave}"
    status_path = output / "trial_status" / (trial_id + ".json")
    if status_path.exists():
        raise RuntimeError("Attempt already admitted; never silently repeat it")
    state = {"arm": arm, "trial": wave, "slot": slot, "started_unix": time.time(),
             "pid": os.getpid(), "status": "admitted", "cpu_affinity": frozen["cpu_slots"][slot],
             "host": platform.node(), "load_at_admission": list(os.getloadavg())}
    write(status_path, state)
    try:
        os.sched_setaffinity(0, state["cpu_affinity"])
        os.environ.update(environment(root))
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[name] = "8"
        if source_hashes(root) != frozen["source_hashes"]:
            raise RuntimeError("Frozen experiment code changed")
        if setup_hashes(root) != frozen["setup_hashes"]:
            raise RuntimeError("Frozen setup receipts changed")
        gold, calibration = [root / "data" / entry[1] for entry in INPUTS[:2]]
        candidates = output / "procedure-candidates" / (trial_id + ".jsonl")
        # The child wall timer governs scientific runtime. The relay also bounds
        # staging/preflight overhead; input copies must not consume the task limit.
        with ModelAudit(frozen["upstream"], os.environ["ANL_USERNAME"], frozen["model"],
            output / "model_calls" / (trial_id + ".jsonl"), max_requests=frozen["max_requests"],
            max_output_tokens=frozen["max_output_tokens"], timeout=frozen["wall_seconds"] + 1800,
            procedure_candidates=candidates) as audit:
            result = rb.run_one(rb.ARMS[arm], gold, 1181, wave, {}, None,
                audit.base_url, audit.token, frozen["model"], .1, frozen["wall_seconds"], output, False,
                ground_truth=root / "setup/withheld-reference.json",
                extra_deny=[root / "setup", root / "results", root / "data", root / "source/benchmark"],
                native_context_manifest=root / "setup/native-context.json", structured_logs=True,
                scratch_root=root / "work", procedure_candidates=candidates, pi_image_input=True,
                prompt_override=frozen["prompt"], additional_inputs=[calibration], result_kind="execution_only")
        summary = audit.summary()
        workspace = output / "workspaces" / trial_id
        assessment = evaluate(workspace, state["started_unix"])
        state.update(status="finished", finished_unix=time.time(), execution=asdict(result),
            model_audit=summary, artifact_assessment=assessment,
            procedure_delivery_qualified=arm != "A1" or bool(summary["automatic_procedure_hashes_delivered"]))
    except BaseException as exc:
        state.update(status="infrastructure_error", finished_unix=time.time(),
                     error=f"{type(exc).__name__}: {exc}")
    state["load_at_finish"] = list(os.getloadavg())
    write(status_path, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--wait-for-gates", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    os.environ["APEXA_PROVIDER_HOLD_FILE"] = str(root / "setup/provider-hold.json")
    with (root / "setup/pilot.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / "setup/pilot-frozen.json").exists():
            raise RuntimeError("A pilot was already frozen; inspect its attempts before any recovery")
        state = {"pid": os.getpid(), "started_unix": time.time(), "status": "waiting_for_gates",
                 "admitted_trials": 0, "finished_trials": 0}
        status_path = root / "setup/pilot-status.json"
        try:
            deadline = time.time() + 18000
            while True:
                ref = json.loads((root / "setup/reference-status.json").read_text())
                qual = json.loads((root / "setup/qualification-status.json").read_text())
                state.update(reference_status=ref["status"], qualification_status=qual["status"],
                             updated_unix=time.time())
                write(status_path, state)
                if "failed" in (ref["status"], qual["status"]):
                    raise RuntimeError("Setup gate failed; no scientific attempt admitted")
                if ref["status"] == "execution_completed_pending_scientific_audit" and qual["status"] == "qualified":
                    break
                if not args.wait_for_gates or time.time() > deadline:
                    raise RuntimeError("Setup gates have not completed")
                time.sleep(15)
            state.update(status="freezing_protocol")
            write(status_path, state)
            frozen = freeze(root)
            rows = []
            for wave, arms in enumerate(frozen["arms_by_wave"]):
                require_provider_available(root)
                state.update(status="running", wave=wave, wall_seconds=frozen["wall_seconds"])
                write(status_path, state)
                with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
                    futures = [pool.submit(run_trial, root, frozen, wave, slot, arm)
                               for slot, arm in enumerate(arms)]
                    state["admitted_trials"] += len(futures)
                    write(status_path, state)
                    for future in as_completed(futures):
                        rows.append(future.result())
                        state["finished_trials"] = len(rows)
                        write(status_path, state)
                        write(root / "results/agents/summary.json", {"attempts": rows,
                              "physical_validity": "unestablished", "trace_audit": "required"})
            state.update(status="execution_finished_pending_trace_audit", finished_unix=time.time())
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}", finished_unix=time.time())
            write(status_path, state)
            raise
        write(status_path, state)


if __name__ == "__main__":
    main()
