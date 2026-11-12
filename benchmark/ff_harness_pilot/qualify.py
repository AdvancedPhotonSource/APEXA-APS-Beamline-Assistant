"""Qualify installed native FF-pilot interfaces on synthetic fixtures only."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import getpass
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "harness_comparison"))
import rotation_benchmark as rb
from model_audit import ModelAudit


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def environment(root):
    env = dict(os.environ, **json.loads((root / "setup/native-environment.json").read_text()))
    env["PATH"] = os.pathsep.join(map(str, [root / "envs/midas/bin", root / "runtime/node/bin",
        root / "runtime/harnesses/node_modules/.bin"])) + os.pathsep + env.get("PATH", "")
    env["ANL_USERNAME"] = env.get("ANL_USERNAME") or getpass.getuser()
    env["APEXA_MAX_ITERATIONS"] = "120"
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = "4"
    code = ("import sys,json,pathlib;print(json.dumps([sys.prefix,sys.base_prefix,"
            "str(pathlib.Path(sys._base_executable).parent.parent)]))")
    solver = json.loads(subprocess.check_output([str(root / "envs/midas/bin/python"), "-c", code], text=True))
    roots = [
        str(root / "source"), str(root / "runtime/harnesses"), str(root / "runtime/node"),
        str(root / "context/embedding-assets"), str(root / "envs/apexa"),
        sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent), *solver]
    # A reused virtualenv can be addressed through the revision's symlink while
    # console-script shebangs and APEXA_MIDAS_BIN name its original location.
    # Bind both exact prefixes; neither exposes the surrounding data directories.
    roots += [str(Path(path).resolve()) for path in roots]
    env["RB_SOFTWARE_READ_ROOTS"] = json.dumps(list(dict.fromkeys(roots)))
    return env


def copy_check(root, arm, slot, attempt=1):
    os.environ.update(environment(root))
    os.sched_setaffinity(0, list(range(4 * slot, 4 * slot + 4)))
    output = root / "results/qualification"
    if attempt > 1:
        output = output / f"attempt-{attempt}"
    fixture = root / "setup/ff-copy-fixture.txt"
    candidates = output / "procedure-candidates" / f"{arm}.jsonl"
    with ModelAudit("https://apps.inside.anl.gov/argoapi/v1", os.environ["ANL_USERNAME"],
        "claudeopus5", output / "model_calls" / f"{arm}.jsonl", max_requests=8,
        max_output_tokens=16000, timeout=300, procedure_candidates=candidates) as audit:
        result = rb.run_one(rb.ARMS[arm], fixture, 0, 0, {}, None,
            audit.base_url, audit.token, "claudeopus5", .1, 300, output, False,
            ground_truth=root / "setup/withheld-reference.json",
            extra_deny=[root / "setup", root / "results", root / "source/benchmark"],
            native_context_manifest=root / "setup/native-context.json", structured_logs=True,
            scratch_root=root / "work", procedure_candidates=candidates, pi_image_input=True,
            prompt_override="This is a synthetic FF-workflow file-I/O qualification, not a scientific analysis. "
                "Read ff-copy-fixture.txt and use your tools to write an exact byte-for-byte copy to "
                "smoke_copy.txt. Do not run a solver. Reply READY after the copy is written.",
            result_kind="execution_only")
    summary = audit.summary()
    copied = output / f"workspaces/{arm}_0_0/smoke_copy.txt"
    passed = (result.return_code == 0 and copied.is_file() and not copied.is_symlink()
              and copied.read_bytes() == fixture.read_bytes() and summary["qualified"])
    if arm == "A1":
        passed = passed and bool(summary["automatic_procedure_hashes_delivered"])
    row = {"arm": arm, "passed": passed, "execution": asdict(result), "model_audit": summary}
    write(output / f"{arm}-qualification.json", row)
    return row


def token_count_check(root, env):
    """Probe optional transport explicitly; native CLI need not issue this call."""
    import httpx
    with ModelAudit("https://apps.inside.anl.gov/argoapi/v1", env["ANL_USERNAME"],
        "claudeopus5", root / "setup/token-count-model-audit.jsonl",
        max_requests=0, timeout=60) as audit:
        response = httpx.post(audit.base_url + "/messages/count_tokens",
            headers={"x-api-key": audit.token, "anthropic-version": "2023-06-01"},
            json={"model": "claude-opus-5", "messages": [
                {"role": "user", "content": "Synthetic transport qualification."}]}, timeout=45)
        response.raise_for_status()
        value = response.json().get("input_tokens")
    summary = audit.summary()
    record = {"passed": type(value) is int and value > 0
              and summary["successful_token_counts"] == 1 and not summary["violations"],
              "model_audit": summary, "source": "explicit relay endpoint probe",
              "native_cli_issued_request": False, "scientific_trials": 0}
    write(root / "setup/token-count-qualification.json", record)
    if not record["passed"]:
        raise RuntimeError("Explicit token-count transport did not pass")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--wait-for-context", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Resume after inspecting a setup failure; retain passed gates")
    parser.add_argument("--copy-attempt", type=int, default=1)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.copy_attempt < 1:
        parser.error("copy-attempt must be positive")
    state_path = root / "setup/qualification-status.json"
    if state_path.exists():
        previous = json.loads(state_path.read_text())
        if not args.resume or previous["status"] != "failed":
            raise RuntimeError("Only an explicitly inspected failed qualification can resume")
        with (root / "setup/qualification-history.jsonl").open("a") as stream:
            stream.write(json.dumps(previous) + "\n")
    state = {"pid": os.getpid(), "started_unix": time.time(), "status": "waiting_for_context",
             "scientific_trials": 0}

    def update(**changes):
        state.update(changes, updated_unix=time.time())
        write(state_path, state)

    try:
        update()
        deadline = time.time() + 3600
        while not (root / "setup/native-context.json").is_file():
            context = root / "setup/context-status.json"
            if context.exists() and json.loads(context.read_text()).get("status") == "failed":
                raise RuntimeError("Native context preparation failed")
            if not args.wait_for_context or time.time() > deadline:
                raise RuntimeError("Native context preparation has not completed")
            time.sleep(15)
            update()
        health = json.loads((root / "setup/retrieval-health.json").read_text())
        if not health["passed"]:
            raise RuntimeError("Native retrieval health did not pass")
        env = environment(root)
        os.environ.update(env)
        # Native Bash/token count and image transport are separate gates.
        script = root / "source/benchmark/harness_comparison/qualify_guided_adapters.py"
        commands = [
            ("claude-bash", ["--arm", "A3", "--test", "bash", "--context-lines", "200", "--settle-seconds", "20"]),
            ("claude-image", ["--arm", "A3", "--test", "image", "--image-input"]),
            ("pi-image", ["--arm", "A5", "--test", "image", "--image-input"]),
        ]
        for name, arguments in commands:
            update(status="running", phase=name)
            output = root / "setup" / name
            if not output.exists():
                with (root / "logs" / f"{name}.log").open("x") as log:
                    subprocess.run([sys.executable, str(script), *arguments, "--output", str(output)],
                                   env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=420)
            receipt = json.loads((output / "qualification.json").read_text())
            for filename, expected in receipt["source_hashes"].items():
                actual = hashlib.sha256((script.parent / filename).read_bytes()).hexdigest()
                if actual != expected:
                    raise RuntimeError(f"Source changed since {name}: {filename}")
            if not receipt["passed"]:
                raise RuntimeError(f"{name} did not qualify")
        update(status="running", phase="explicit_token_count_transport")
        token_receipt = root / "setup/token-count-qualification.json"
        if not token_receipt.exists():
            token_count_check(root, env)
        elif not json.loads(token_receipt.read_text())["passed"]:
            raise RuntimeError("Retained token-count check failed")
        fixture = root / "setup/ff-copy-fixture.txt"
        if not fixture.exists():
            fixture.write_text("Synthetic FF file I/O qualification.\n" + secrets.token_hex(32) + "\n")
        update(status="running", phase="concurrent_native_file_io", copy_attempt=args.copy_attempt)
        with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = [pool.submit(copy_check, root, arm, slot, args.copy_attempt)
                       for slot, arm in enumerate(["A1", "A3", "A5"])]
            results = [future.result() for future in futures]
        passed = all(row["passed"] for row in results)
        receipt = {"passed": passed, "checks": results, "copy_attempt": args.copy_attempt,
                   "scientific_trials": 0, "finished_unix": time.time(),
                   "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        output = root / "results/qualification"
        if args.copy_attempt > 1:
            output = output / f"attempt-{args.copy_attempt}"
        write(output / "summary.json", receipt)
        write(root / "setup/qualification.json", receipt)
        if not passed:
            raise RuntimeError("Concurrent native file I/O did not pass for every arm")
        update(status="qualified", phase="completed", finished_unix=time.time())
    except BaseException as exc:
        update(status="failed", error=f"{type(exc).__name__}: {exc}", finished_unix=time.time())
        raise


if __name__ == "__main__":
    main()
