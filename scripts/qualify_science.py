#!/usr/bin/env python3
"""Check or execute a reviewed calibration/integration acceptance manifest.

Default mode validates prerequisites only. --execute runs the exact argv under
owned process cleanup; results live outside the checkout. No LLM is used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import psutil
from apexa_slurm import atomic_json, digest_file
from apexa_subprocess import run


def checked_file(item):
    path = Path(item["path"])
    if not path.is_absolute() or not path.is_file() or digest_file(path) != item["sha256"]:
        raise ValueError("A required file is missing or differs from its reviewed SHA-256")
    return path


def validate(manifest):
    if manifest.get("schema_version") != 1 or manifest.get("workflow") != "calibration_integration":
        raise ValueError("Expected a version-1 calibration_integration manifest")
    reference = manifest.get("reference_provenance", {})
    if reference.get("independent") is not True or not reference.get("reviewed_by") or not reference.get("source"):
        raise ValueError("An independently reviewed reference and its provenance are required")
    if not manifest.get("inputs") or not manifest.get("command") or not isinstance(manifest["command"], list):
        raise ValueError("Frozen inputs and an explicit argv command are required")
    for item in manifest["inputs"]:
        checked_file(item)
    engine = checked_file(manifest["engine"])
    software = manifest.get("software", [])
    for item in software:
        checked_file(item)
    if manifest["command"][0] != str(engine):
        raise ValueError("Command executable does not match the pinned engine")
    if not all(isinstance(arg, str) and "\x00" not in arg for arg in manifest["command"]):
        raise ValueError("Command must be a string argv array")
    frozen_paths = {item["path"] for item in [manifest["engine"], *manifest["inputs"], *software]}
    for arg in manifest["command"][1:]:
        # External wrapper/configuration files are part of the experiment, too.
        # Inline -c code is already frozen verbatim in the manifest.
        if "{output_dir}" not in arg and Path(arg).is_absolute() and Path(arg).is_file() and arg not in frozen_paths:
            raise ValueError("Pin command scripts/configuration files in the software manifest")
    if not any("{output_dir}" in arg for arg in manifest["command"]):
        raise ValueError("Command must place results under {output_dir}")
    checks = manifest.get("checks", [])
    if not {"json_scalar", "curve"} <= {c.get("kind") for c in checks}:
        raise ValueError("Calibration geometry AND integrated-curve checks are required")
    for check in checks:
        if check.get("kind") not in {"json_scalar", "curve"}:
            raise ValueError("Unknown acceptance check")
        checked_file(check["reference"])
        p = Path(check["artifact"])
        if p.is_absolute() or ".." in p.parts:
            raise ValueError("Artifacts must be relative paths within the run output")
        for name in ("atol", "rtol", "grid_atol"):
            value = check.get(name, 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Acceptance tolerances must be finite and nonnegative")
    timeout = manifest.get("timeout_seconds", 3600)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 86400:
        raise ValueError("Set an execution timeout of at most one day")
    return {"prerequisites": "verified", "execution": "not_run", "scientific_verdict": "unassessed",
            "reference_review": reference, "checks": len(checks)}


def evaluate(manifest, output):
    checks = []
    for spec in manifest["checks"]:
        try:
            actual = (output / spec["artifact"]).resolve()
            if not actual.is_relative_to(output.resolve()):
                raise ValueError("Artifact escaped its run directory")
            expected = checked_file(spec["reference"])
            if spec["kind"] == "json_scalar":
                a, b = json.loads(actual.read_text()), json.loads(expected.read_text())
                for key in spec["field"]:
                    a, b = a[key], b[key]
                a, b = float(a), float(b)
                passed = math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, abs_tol=spec.get("atol", 0), rel_tol=spec.get("rtol", 0))
                detail = {"actual": a if math.isfinite(a) else None, "reference": b if math.isfinite(b) else None}
            elif spec["kind"] == "curve":
                a, b = np.loadtxt(actual, ndmin=2), np.loadtxt(expected, ndmin=2)
                shape_ok = a.shape == b.shape and a.shape[0] >= 2 and a.shape[1] >= 2
                finite = np.isfinite(a).all() and np.isfinite(b).all()
                passed = bool(shape_ok and finite and np.allclose(a[:, 0], b[:, 0], atol=spec.get("grid_atol", 0), rtol=0)
                              and np.allclose(a[:, 1], b[:, 1], atol=spec.get("atol", 0), rtol=spec.get("rtol", 0)))
                detail = {"shape_matches": shape_ok, "finite": bool(finite),
                          "rmse": float(np.sqrt(np.mean((a[:, 1] - b[:, 1]) ** 2))) if shape_ok and finite else None}
            else:
                raise ValueError("Unknown acceptance check")
            checks.append({"artifact": spec["artifact"], "kind": spec["kind"], "passed": passed,
                           "sha256": digest_file(actual), **detail})
        except (ValueError, KeyError, IndexError, TypeError, OSError):
            checks.append({"artifact": spec["artifact"], "kind": spec["kind"], "passed": False,
                           "error": "Missing, invalid, changed or incompatible reference/artifact"})
    return {"scientific_verdict": "accepted" if checks and all(c["passed"] for c in checks) else "rejected", "checks": checks}


async def execute(manifest, output):
    validate(manifest)
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if output.is_relative_to(root):
        raise ValueError("Keep qualification results outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)  # never reuse a possibly executed run
    identity = {"manifest": manifest, "execution": "running", "scientific_verdict": "unassessed",
                "host": platform.node(), "platform": platform.platform(), "started": time.time(),
                "pid": os.getpid(), "python": sys.version.split()[0]}
    atomic_json(output / "run.json", identity)
    argv = [arg.replace("{output_dir}", str(output)) for arg in manifest["command"]]
    task = asyncio.create_task(run(argv, cwd=output, timeout=manifest.get("timeout_seconds", 3600)))
    process = psutil.Process()
    peak_rss, cpu_samples, samples = 0, {}, 0
    start = time.monotonic()
    try:
        while not task.done():
            rss = 0
            for child in process.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                    times = child.cpu_times()
                    cpu_samples[(child.pid, child.create_time())] = times.user + times.system
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            peak_rss = max(peak_rss, rss)
            samples += 1
            await asyncio.wait([task], timeout=0.1)
        result = await task
        (output / "stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (output / "stderr.log").write_text(result.stderr or "", encoding="utf-8")
        identity.update(execution="completed" if result.returncode == 0 else "failed", returncode=result.returncode)
        validate(manifest)  # input/reference/engine drift invalidates acceptance
        if result.returncode == 0:
            identity.update(evaluate(manifest, output))
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        identity.update(execution="uncertain", scientific_verdict="unassessed")
        atomic_json(output / "run.json", identity)
        raise
    finally:
        identity["performance"] = {"wall_seconds": time.monotonic() - start,
            "sampled_peak_worker_rss_bytes": peak_rss, "sampled_worker_cpu_seconds": sum(cpu_samples.values()),
            "samples": samples, "sample_interval_seconds": 0.1, "gpu_utilization": "not_measured",
            "requested_device": manifest.get("device", "unspecified")}
        atomic_json(output / "run.json", identity)
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if args.execute:
        output = args.output_dir or Path(os.environ.get("APEXA_HOME", str(Path.home() / ".apexa"))) / "qualification" / uuid.uuid4().hex
        result = asyncio.run(execute(manifest, output))
        print(json.dumps({"record": str(output / "run.json"), "execution": result["execution"],
                          "scientific_verdict": result["scientific_verdict"], "performance": result["performance"]}, indent=2))
        return 0 if result["scientific_verdict"] == "accepted" else 1
    print(json.dumps(validate(manifest), indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"execution": "not_confirmed", "scientific_verdict": "unassessed", "error": str(exc)}))
        sys.exit(2)
