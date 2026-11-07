#!/usr/bin/env python3
"""Prepare, qualify and explicitly launch the Copland harness study.

All generated records are under --root. prepare is read-only with respect to
raw data and does not call models. qualify tests synthetic file I/O only.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import rotation_benchmark as rb
from model_audit import ModelAudit

SPEC = HERE / "studies/cvpr-2026-11/study.json"
MANIFEST = HERE / "studies/cvpr-2026-11/knowledge.json"
MANUALS = REPO / "knowledge_base/study_manuals/rotation"
SMOKE_PROMPT = """This is a calibration-workflow file-I/O qualification task, not a scientific analysis.
Read the exact bytes of {name} in the current directory and write an identical
copy to smoke_copy.txt. Do not run a scientific solver or create a result manifest.
After creating the copy, reply READY. Use the available tools to do the work."""


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def source_hashes():
    paths = [HERE / n for n in ("study.py", "rotation_benchmark.py", "run_comparison.py",
                                "_apexa_driver.py", "model_audit.py", "procedure_audit.py")]
    paths += [REPO / n for n in ("apexa_agents.py", "midas_comprehensive_server.py",
                                "_calibrate_runner.py", "skill_registry.py", "apexa_knowledge_policy.py")]
    paths += list(REPO.glob("*.py"))
    paths += [REPO / n for n in ("servers.config", "pyproject.toml", "uv.lock") if (REPO / n).exists()]
    paths += [SPEC, MANIFEST]
    policy = rb.load_manifest(MANIFEST)
    paths += [REPO / p for p in policy["files"]]
    return {str(p.relative_to(REPO)): sha(p) for p in paths}


def solver_inventory(python):
    code = '''import importlib,importlib.metadata as m,pathlib,hashlib,json
out={}
for name in ["midas-calibrate-v2","midas-params","midas-integrate","numpy","torch","h5py"]:
 mod=importlib.import_module(name.replace("-","_"));root=pathlib.Path(mod.__file__).parent
 digest=hashlib.sha256()
 if name.startswith("midas-"):
  for p in sorted(root.rglob("*.py")):
   digest.update(str(p.relative_to(root)).encode());digest.update(p.read_bytes())
 out[name]={"version":m.version(name),"source_sha256":digest.hexdigest() if name.startswith("midas-") else None}
print(json.dumps(out))'''
    p = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(p.stdout)


def configured_environment(root, spec):
    env = dict(os.environ)
    bins = [root / "runtime/harnesses/node_modules/.bin", root / "runtime/node/bin",
            Path(spec["solver_python"]).parent]
    env["PATH"] = os.pathsep.join([str(p) for p in bins] + [env.get("PATH", "")])
    env["APEXA_MIDAS_BIN"] = str(Path(spec["solver_python"]).parent)
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = str(spec["cpu_threads"])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["APEXA_MAX_ITERATIONS"] = str(spec["max_model_requests"])
    if spec.get("filesystem_scope") == "software_and_trial_only":
        env["RB_SOFTWARE_READ_ROOTS"] = json.dumps([
            str(root / "source"), str(root / "runtime/harnesses"), str(root / "runtime/node"),
            str(Path(spec["solver_python"]).parent.parent), sys.prefix, sys.base_prefix,
            *spec.get("additional_software_roots", [])])
    else:
        env.pop("RB_SOFTWARE_READ_ROOTS", None)
    return env


def harness_versions(root):
    versions = {}
    inspection = root / "runtime/inspection"
    inspection.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                 "CLAUDE_CONFIG_DIR", "PI_CODING_AGENT_DIR"):
        env[name] = str(inspection / name.lower())
    for name in ("claude", "opencode", "pi"):
        binary = shutil.which(name)
        if binary is None:
            raise RuntimeError(f"{name} is not installed on the configured study PATH")
        p = subprocess.run(rb.sandboxed([binary, "--version"], [], writable=[inspection]),
                           env=env, cwd=inspection, capture_output=True, text=True, timeout=30, check=True)
        versions[name] = {"path": str(Path(binary).resolve()), "version": p.stdout.strip(),
                          "launcher_sha256": sha(Path(binary).resolve())}
    for name, path in (("npm_lock", root / "runtime/harnesses/package-lock.json"),
                       ("node", root / "runtime/node/bin/node")):
        versions[name] = {"path": str(path.resolve()), "sha256": sha(path)}
    return versions


def inspect_inputs(python, files):
    code = '''import sys,json,hashlib,pathlib,h5py
out=[]
for name in json.loads(sys.argv[1]):
 p=pathlib.Path(name);h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(4*1024*1024),b""):h.update(b)
 with h5py.File(p,"r") as f:
  d=f["exchange/data"];shape=list(d.shape);dtype=str(d.dtype)
 if shape != [21,2880,2880]:raise ValueError(f"Unexpected raw exposure shape: {p.name}: {shape}")
 out.append({"path":str(p),"bytes":p.stat().st_size,"sha256":h.hexdigest(),"shape":shape,"dtype":dtype})
print(json.dumps(out))'''
    p = subprocess.run([str(python), "-c", code, json.dumps([str(f) for f in files])],
                       capture_output=True, text=True, timeout=900, check=True)
    return json.loads(p.stdout)


def prepare(root, spec):
    target = root / "setup/frozen.json"
    if target.exists():
        raise RuntimeError("A frozen setup already exists. Verify it; do not overwrite a study identity.")
    reference = root / "setup/reference.csv"
    truth = rb.load_truth(reference, value_col="tz_deg")
    scans = sorted(set(spec["scans"] + spec["pilot_scans"]))
    files = rb.select_exposures(Path(spec["raw_root"]), "*.vrx.h5", truth, scans)
    print(f"Hashing and inspecting {len(files)} raw files; no copying or fitting.", flush=True)
    inputs = inspect_inputs(spec["solver_python"], files.values())
    inventory = solver_inventory(spec["solver_python"])
    if inventory["midas-calibrate-v2"]["version"] != spec["solver_version"]:
        raise RuntimeError("Installed solver differs from the protocol")
    record = {"schema_version": 1, "created_unix": time.time(), "spec": spec,
              "source_hashes": source_hashes(), "reference_sha256": sha(reference),
              "inputs": inputs, "solver": inventory, "harnesses": harness_versions(root),
              "model_calls": 0, "scientific_runs": 0}
    for cohort, cases, repeats in (("main", spec["scans"], spec["repeats"]),
                                    ("pilot", spec["pilot_scans"], 1)):
        write_new(root / f"setup/{cohort}_schedule.json",
                  rb.make_schedule(spec["arms"], cases, repeats, spec["order_seed"]))
    write_new(target, record)
    print(f"Prepared {target}; main cohort {len(spec['scans']) * len(spec['arms']) * spec['repeats']} trials.")


def verify(root, spec, *, check_inputs=True):
    frozen = json.loads((root / "setup/frozen.json").read_text())
    if frozen["spec"] != spec or frozen["source_hashes"] != source_hashes():
        raise RuntimeError("Protocol/source changed after freezing; create a new documented study revision")
    if sha(root / "setup/reference.csv") != frozen["reference_sha256"]:
        raise RuntimeError("Evaluator reference changed")
    if solver_inventory(spec["solver_python"]) != frozen["solver"]:
        raise RuntimeError("Numerical environment changed after freezing")
    if harness_versions(root) != frozen["harnesses"]:
        raise RuntimeError("Harness installation changed after freezing")
    if check_inputs:
        for item in frozen["inputs"]:
            if sha(item["path"]) != item["sha256"]:
                raise RuntimeError("Raw input changed after freezing")
    for cohort, scans, repeats in (("main", spec["scans"], spec["repeats"]),
                                  ("pilot", spec["pilot_scans"], 1)):
        schedule = json.loads((root / f"setup/{cohort}_schedule.json").read_text())
        if schedule != rb.make_schedule(spec["arms"], scans, repeats, spec["order_seed"]):
            raise RuntimeError("Frozen schedule changed")
    return frozen


def denied_locations(root, spec):
    return [root / "setup", root / "results", root / "source/manuscripts",
            Path(spec["raw_root"]).parents[3] / "analysis",
            Path(spec["production_repo"]), Path.home() / ".codex", Path.home() / ".pi"]


def sandbox_check(root):
    probe = root / "setup/sandbox-probe"
    probe.mkdir()
    secret = probe / "reference.txt"
    secret.write_text(secrets.token_hex(32))
    work = probe / "work"
    work.mkdir()
    code = "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())"
    read = subprocess.run(rb.sandboxed([sys.executable, "-c", code, str(secret)], [str(secret)]),
                          capture_output=True, text=True, timeout=20)
    # A blocked file read may either fail or expose the empty /dev/null mount.
    leaked = secret.read_text() in read.stdout + read.stderr
    write = subprocess.run(rb.sandboxed([sys.executable, "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ok')", str(work / "ok")],
        [str(secret)], writable=[work]), capture_output=True, text=True, timeout=20)
    forbidden = probe / "outside-workspace.txt"
    outside = subprocess.run(rb.sandboxed([sys.executable, "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('bad')", str(forbidden)],
        [str(secret)], writable=[work]), capture_output=True, text=True, timeout=20)
    scope_code = """import json,sys
from pathlib import Path
roots=json.loads(sys.argv[1])
visible=[str(p) for root in roots if Path(root).exists() for p in Path(root).iterdir()]
print(json.dumps(visible))"""
    scope = subprocess.run(rb.sandboxed([sys.executable, "-c", scope_code,
        json.dumps(["/gdata", "/gpfs", "/net", "/APSshare"])], [str(secret)], writable=[work]),
        capture_output=True, text=True, timeout=20)
    result = {"backend": rb.SANDBOX, "reference_not_visible": not leaked,
              "read_return_code": read.returncode, "workspace_writable": write.returncode == 0 and (work / "ok").read_text() == "ok"}
    # An unusable sandbox cannot pass merely by failing to launch its interpreter.
    result["outside_write_blocked"] = outside.returncode != 0 and not forbidden.exists()
    if os.environ.get("RB_SOFTWARE_READ_ROOTS"):
        result["unrelated_data_roots_hidden"] = scope.returncode == 0 and json.loads(scope.stdout) == []
    result["passed"] = result["reference_not_visible"] and result["workspace_writable"] and result["outside_write_blocked"]
    result["passed"] = result["passed"] and result.get("unrelated_data_roots_hidden", True)
    if not result["passed"]:
        result["diagnostic"] = (write.stderr or read.stderr)[-2000:]
    write_new(root / "setup/sandbox.json", result)
    return result


def run_trial(root, spec, output, entry, exposure, truth, *, smoke=False):
    arm, scan, trial = entry["arm"], entry["scan"], entry["trial"]
    api_key = os.environ.get("ANL_USERNAME", "")
    if not api_key:
        raise RuntimeError("ANL_USERNAME is required; credentials are never stored in study files")
    log = output / "model_calls" / f"{arm}_{scan}_{trial}.jsonl"
    candidates = output / "procedure_candidates" / f"{arm}_{scan}_{trial}.jsonl"
    with ModelAudit(spec["model_base_url"], api_key, spec["model"], log,
                    max_requests=8 if smoke else spec["max_model_requests"],
                    max_output_tokens=spec["max_output_tokens"],
                    timeout=240 if smoke else spec["wall_seconds"],
                    procedure_candidates=candidates) as audit:
        result = rb.run_one(rb.ARMS[arm], exposure, scan, trial, truth, MANUALS,
            audit.base_url, audit.token, spec["model"], spec["tilt_tolerance_deg"],
            240 if smoke else spec["wall_seconds"], output, False,
            ground_truth=root / "setup/reference.csv", extra_deny=denied_locations(root, spec),
            knowledge_manifest=MANIFEST, structured_logs=True,
            scratch_root=root / "work",
            prompt_override=SMOKE_PROMPT.format(name=exposure.name) if smoke else None,
            require_manifest=not smoke, procedure_candidates=candidates)
    result.model_audit = audit.summary()
    apply_model_audit(result, smoke=smoke)
    for key in ("schedule_index", "schedule_block", "schedule_position"):
        setattr(result, key, entry.get(key))
    result.comparison_kind = "native_systems_and_automatic_procedure_ablation"
    return result


def apply_model_audit(result, *, smoke=False):
    audit = result.model_audit
    audit["outcome_before_model_audit"] = result.outcome
    if not audit["qualified" if smoke else "scientific_evaluable"]:
        # Timeouts remain timeouts; transport and identity are also visible in
        # separate fields. Never discard budget failures from the denominator.
        if result.outcome != "timeout":
            result.outcome = "infra_error"
        result.detail += "; model transport/identity/usage qualification incomplete (see audit)"
    if not smoke and audit["budget_exhausted"] and result.outcome != "timeout":
        result.outcome = "budget_exhausted"
        result.detail += "; declared model-request or wall-time budget exhausted"


def qualify(root, spec):
    verify(root, spec, check_inputs=False)
    if (root / "setup/qualification.json").exists():
        raise RuntimeError("Qualification record exists; preserve it and use a new setup revision for repairs")
    sandbox = sandbox_check(root)
    if not sandbox["passed"]:
        raise RuntimeError("Sandbox qualification failed; see setup/sandbox.json")
    output = root / "results/qualification"
    output.mkdir(parents=True)
    fixture = root / "setup/qualification_000000.txt"
    fixture.write_text("Synthetic file-copy fixture; no diffraction data.\n" + secrets.token_hex(32) + "\n")
    records = []
    for arm in spec["arms"]:
        print(f"Qualifying {arm}: model transport and synthetic file I/O only.", flush=True)
        result = run_trial(root, spec, output, {"arm": arm, "scan": 0, "trial": 0}, fixture, {}, smoke=True)
        copied = output / f"workspaces/{arm}_0_0/smoke_copy.txt"
        success = (result.return_code == 0 and copied.is_file() and not copied.is_symlink()
                   and copied.read_bytes() == fixture.read_bytes()
                   and result.model_audit["qualified"])
        record = {"arm": arm, "passed": success, "model_audit": result.model_audit,
                  "return_code": result.return_code, "detail": result.detail}
        records.append(record)
        with (output / "checks.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(json.dumps(record), flush=True)
        verify(root, spec, check_inputs=False)
    by_arm = {r["arm"]: r for r in records}
    procedure_check = (bool(by_arm["A1"]["model_audit"]["automatic_procedure_hashes_delivered"])
                       and not by_arm["A7"]["model_audit"]["automatic_procedure_hashes_delivered"])
    passed = all(r["passed"] for r in records) and procedure_check
    write_new(root / "setup/qualification.json", {"passed": passed,
              "source_hashes": source_hashes(), "checks": records, "scientific_runs": 0,
              "automatic_procedure_delivery_passed": procedure_check})
    if not passed:
        raise RuntimeError("Qualification failed; see setup/qualification.json. No scientific trials were run.")


def atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def append_record(path, data):
    with path.open("a") as f:
        f.write(json.dumps(data) + "\n")
        f.flush()
        os.fsync(f.fileno())


def check_admission(root):
    path = root / "setup/main_admission.json"
    if not path.is_file():
        raise RuntimeError("Main cohort held until the deterministic baseline and pilot review are recorded")
    admission = json.loads(path.read_text())
    if admission.get("frozen_sha256") != sha(root / "setup/frozen.json"):
        raise RuntimeError("Main admission belongs to a different frozen setup")
    if admission.get("qualification_sha256") != sha(root / "setup/qualification.json"):
        raise RuntimeError("Main admission qualification changed")
    for record in admission["reviewed_evidence"]:
        if sha(record["path"]) != record["sha256"]:
            raise RuntimeError("Reviewed pilot/baseline evidence changed")
    return admission


def admit(root, spec, review):
    verify(root, spec)
    q = json.loads((root / "setup/qualification.json").read_text())
    if not q["passed"] or not q.get("automatic_procedure_delivery_passed"):
        raise RuntimeError("New revision did not pass startup/procedure qualification")
    record = json.loads(review.read_text())
    for field in ("pilot_review_complete", "baseline_qualified", "accounting_regressions_passed",
                  "numerical_semantics_unchanged", "retain_all_pilot_outcomes"):
        if record.get(field) is not True:
            raise RuntimeError(f"Missing main admission review: {field}")
    evidence = record.get("reviewed_evidence", [])
    if len(evidence) < 3:
        raise RuntimeError("Provide pilot, baseline and regression evidence")
    for item in evidence:
        if sha(item["path"]) != item["sha256"]:
            raise RuntimeError("Review evidence hash mismatch")
    write_new(root / "setup/main_admission.json", {
        **record, "created_unix": time.time(), "frozen_sha256": sha(root / "setup/frozen.json"),
        "qualification_sha256": sha(root / "setup/qualification.json")})


def prior_results(output, schedule):
    entries = {e["schedule_index"]: e for e in schedule["entries"]}
    records = []
    seen = set()
    path = output / "runs.jsonl"
    for line in path.read_text().splitlines() if path.exists() else []:
        row = json.loads(line)
        index = row.get("schedule_index")
        entry = entries.get(index)
        if index in seen or entry is None or any(row[k] != entry[k] for k in ("arm", "scan", "trial")):
            raise RuntimeError("Terminal records duplicate or disagree with the frozen schedule")
        seen.add(index)
        records.append(rb.Run(**row))
    return records


def run(root, spec, cohort):
    if cohort == "main":
        check_admission(root)
    lock_path = Path(spec.get("scientific_lock", root / "setup/scientific-run.lock"))
    with lock_path.open("a") as lock:
        print("Waiting for the common numerical-study lock.", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return run_locked(root, spec, cohort)


def run_locked(root, spec, cohort):
    frozen = verify(root, spec)
    q = json.loads((root / "setup/qualification.json").read_text())
    if not q["passed"] or q["source_hashes"] != source_hashes():
        raise RuntimeError("The current setup has not passed qualification")
    schedule = json.loads((root / f"setup/{cohort}_schedule.json").read_text())
    output = root / "results" / cohort
    output.mkdir(parents=True, exist_ok=True)
    if (output / "schedule.json").exists():
        if json.loads((output / "schedule.json").read_text()) != schedule:
            raise RuntimeError("Saved run schedule differs from the frozen cohort")
    else:
        write_new(output / "schedule.json", schedule)
    (output / "started").mkdir(exist_ok=True)
    truth = rb.load_truth(root / "setup/reference.csv", value_col="tz_deg")
    inputs = {int(p["path"].split("_")[-1].split(".")[0]): p for p in frozen["inputs"]}
    results = prior_results(output, schedule)
    completed = {r.schedule_index for r in results}
    for entry in schedule["entries"]:
        index = entry["schedule_index"]
        if index in completed:
            continue
        started = output / "started" / f"{index:04d}.json"
        if started.exists():
            result = rb.Run(entry["arm"], rb.ARMS[entry["arm"]].harness, entry["scan"],
                            entry["trial"], spec["model"], True, rb.ARMS[entry["arm"]].enforcement,
                            outcome="interrupted", schedule_index=index,
                            schedule_block=entry["schedule_block"], schedule_position=entry["schedule_position"],
                            detail="Started before supervisor interruption; retained without rerunning")
            results.append(result)
            append_record(output / "runs.jsonl", asdict(result))
            continue
        source = inputs[entry["scan"]]
        if sha(source["path"]) != source["sha256"]:
            raise RuntimeError("Raw input changed; remaining trials stay not run")
        host = rb.wait_for_host(spec["model_base_url"])
        verify(root, spec, check_inputs=False)
        write_new(started, {**entry, "started_unix": time.time(), "supervisor_pid": os.getpid()})
        atomic_json(output / "progress.json", {"completed": len(results), "scheduled": schedule["run_count"],
                    "active": entry, "updated_unix": time.time()})
        result = run_trial(root, spec, output, entry, Path(source["path"]), truth)
        result.host = host
        results.append(result)
        append_record(output / "runs.jsonl", asdict(result))
        atomic_json(output / "summary.json", rb.summarise(results))
        atomic_json(output / "progress.json", {"completed": len(results), "scheduled": schedule["run_count"],
                    "active": None, "updated_unix": time.time()})
        print(f"{entry['schedule_index']}/{schedule['run_count']}: {entry['arm']} {entry['scan']} {result.outcome}", flush=True)
    atomic_json(output / "summary.json", rb.summarise(results))
    atomic_json(output / "progress.json", {"completed": len(results), "scheduled": schedule["run_count"],
                "active": None, "status": "completed", "updated_unix": time.time()})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("prepare", "verify", "qualify", "admit", "run"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--cohort", choices=("pilot", "main"), default="pilot")
    p.add_argument("--review", type=Path)
    args = p.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        p.error("Create and review the dedicated study directory first")
    spec = json.loads(SPEC.read_text())
    os.environ.update(configured_environment(root, spec))
    if args.action == "run":
        run(root, spec, args.cohort)
    elif args.action == "admit":
        if args.review is None:
            p.error("admit requires --review")
        admit(root, spec, args.review)
    else:
        result = globals()[args.action](root, spec)
        if args.action == "verify":
            print("Frozen source, reference, solver and harness identities match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
