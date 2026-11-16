#!/usr/bin/env python3
"""Generate four real calibration handoffs once; never launch scientific agents.

Run under the qualified MIDAS Python. Outputs stay in the study's personal
scratch directory. Original acquisitions and prior studies are read-only inputs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, indent=2, allow_nan=False) + "\n"
    if exclusive:
        with path.open("x") as stream:
            stream.write(body)
    else:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(body)
        temporary.replace(path)


def source_image(root, entry):
    files = list((root.parent / "verification/data/rot_series2").glob(
        f"*{entry['scan']:06d}.vrx.h5"))
    if len(files) != 1:
        raise RuntimeError(f"Expected one staged input for scan {entry['scan']}")
    return files[0]


def coefficients(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        fields = line.split("#", 1)[0].split()
        if fields and (fields[0] == "Lsd" or fields[0] in {f"p{i}" for i in range(15)}):
            value = float(fields[1])
            if not math.isfinite(value) or fields[0] in values:
                raise ValueError("Nonfinite or duplicated parameter")
            values[fields[0]] = value
    if "Lsd" not in values:
        raise ValueError("No exported distance")
    return values


def worker(root, spec, entry):
    import numpy as np
    from midas_calibrate.params import CalibrationParams
    from midas_calibrate_v2.pipelines import four_stage as fs
    from midas_calibrate_v2.forward.distortion import P_COEF_NAMES

    out = root / "fixtures_private" / entry["id"]
    source = root / "source/_calibrate_runner.py"
    sys.path.insert(0, str(source.parent))
    px = entry["pixel_pitch_um"]
    params = CalibrationParams(
        NrPixelsY=2880, NrPixelsZ=2880, pxY=px, pxZ=px, Lsd=900000,
        BC_y=1440, BC_z=1440, RhoD=math.sqrt(2) * 1440 * px,
        Wavelength=12.398419843320026 / spec["energy_kev"], SpaceGroup=225,
        LatticeConstant=(entry["lattice_angstrom"],) * 3 + (90, 90, 90),
        EtaBinSize=10, RBinSize=0.25, Device="cpu", tolTilts=10)
    template = out / "template.txt"
    template.write_text(params.to_text())
    original = fs.autocalibrate_pv
    calls = 0

    def scalars(value):
        a = np.asarray(value).reshape(-1)
        return [float(x) if np.isfinite(x) else None for x in a]

    def record_pv(*args, **kwargs):
        nonlocal calls
        calls += 1
        index = calls
        current = kwargs["spec"]
        record = {
            "call": index, "started_unix": time.time(),
            "before_refine_flags": {name: bool(current.parameters[name].refined)
                                    for name in P_COEF_NAMES if name in current.parameters},
        }
        path = out / f"stage_{index}.json"
        write_json(path, record)
        result = original(*args, **kwargs)
        record.update(finished_unix=time.time(),
                      unpacked={name: scalars(value) for name, value in result.unpacked.items()})
        write_json(path, record)
        return result

    fs.autocalibrate_pv = record_pv
    text = source.read_text()
    thaw = "_fs._thaw_distortion = _thaw_respecting_request"
    verifier = "distortion_verification = verify_frozen_distortion("
    note = 'notes.append(f"distortion frozen through Stage 2: {_frozen_distortion}")'
    for marker in (thaw, verifier, note):
        if text.count(marker) != 1:
            raise RuntimeError(f"Runner source changed at fixture instrumentation: {marker}")
    if entry["legacy_thaw"]:
        text = text.replace(thaw, "_fs._thaw_distortion = _orig_thaw")
    # This common generation-only change saves an invalid candidate for review.
    # It evaluates and retains the real verifier; production repair tools are not changed.
    text = text.replace(verifier, "distortion_verification = fixture_verify(")
    text = text.replace(note, 'notes.append("Post-fit verification retained in fixture evidence")')
    namespace = {"__name__": "__main__", "__file__": str(source)}

    def fixture_verify(unpacked, frozen):
        try:
            result = namespace["verify_frozen_distortion"](unpacked, frozen)
        except ValueError as exc:
            result = {"status": "failed", "error": str(exc), "required_coefficients": frozen}
        write_json(out / "post_fit_verification.json", result)
        return result

    namespace["fixture_verify"] = fixture_verify
    write_json(out / "generation_identity.json", {
        "fixture": entry, "generator_sha256": sha(__file__),
        "runner_source_sha256": sha(source),
        "executed_runner_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "template_sha256": sha(template), "legacy_thaw": entry["legacy_thaw"],
        "generation_only_candidate_export": True, "model_calls": 0,
    }, exclusive=True)
    sys.argv = [str(source), "--image", str(source_image(root, entry)),
                "--template", str(template), "--output-dir", str(out / "fit"),
                "--pipeline", entry["pipeline"], "--calibrant", entry["calibrant"],
                "--wavelength", str(params.Wavelength), "--px-um", str(px),
                "--im-trans", "2", "--data-loc", "exchange/data", "--skip-frame", "1",
                "--frame-count", "20", "--frame-reduce", "mean", "--device", "cpu"]
    if entry["pipeline"] == "four-stage":
        sys.argv += ["--refine-distortion", entry["model"], "--tol-tilts-deg", "10"]
    exec(compile(text, str(source), "exec"), namespace)


def qualify(root, spec, entry, receipt, inputs):
    out = root / "fixtures_private" / entry["id"]
    payload = json.loads((out / "runner.stdout.json").read_text())
    values = coefficients(out / "fit/paramstest_v2.txt")
    reference_mm = inputs[str(entry["scan"])]["distance_readback_mm"]
    discrepancy = abs(values["Lsd"] / 1000 - reference_mm) / abs(reference_mm)
    checks = {
        "worker_exit_zero": receipt["return_code"] == 0,
        "image_gate_passed": payload["gate"]["passed"] is True,
        "input_unchanged": sha(source_image(root, entry)) == inputs[str(entry["scan"])]["sha256"],
    }
    details = {"fitted_distance_um": values["Lsd"], "readback_mm": reference_mm,
               "relative_distance_discrepancy": discrepancy}
    if entry["family"] == "scale":
        mismatch = entry["pixel_pitch_um"] != spec["documented_pixel_pitch_um"]
        checks["expected_distance_behavior"] = (
            discrepancy > spec["distance_discrepancy_fraction"] if mismatch else
            discrepancy <= spec["distance_discrepancy_fraction"])
        effective = payload["phase_provenance"]["effective_px_um"]
        checks["effective_pixel_pitch"] = effective == entry["pixel_pitch_um"]
    else:
        stage = json.loads((out / "stage_2.json").read_text())
        flags = stage["before_refine_flags"]
        moved = {k: v for k, v in values.items() if k.startswith("p") and abs(v) > 1e-12}
        checks["complete_flags_and_coefficients"] = len(flags) == 15 and len(values) == 16
        checks["expected_flags"] = sum(flags.values()) == (15 if entry["legacy_thaw"] else 0)
        checks["expected_coefficients"] = bool(moved) == entry["legacy_thaw"]
        verdict = json.loads((out / "post_fit_verification.json").read_text())["status"]
        checks["original_verifier_agrees"] = verdict == ("failed" if entry["legacy_thaw"] else "verified")
        details.update(stage2_free_parameters=sum(flags.values()), moved_exported_coefficients=moved)
    files = {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob("*"))
             if p.is_file() and p.name != "qualification.json"}
    result = {"fixture": entry["id"], "passed": all(checks.values()),
              "checks": checks, "details": details, "files_sha256": files}
    write_json(out / "qualification.json", result)
    return result


def physical_cpu_sets(count, per_slot):
    allowed = os.sched_getaffinity(0)
    text = subprocess.check_output(["lscpu", "-p=CPU,CORE,SOCKET"], text=True)
    unique = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        cpu, core, socket = map(int, line.split(","))
        if cpu in allowed:
            unique.setdefault((socket, core), cpu)
    chosen = sorted(unique.values())[-count * per_slot:]
    if len(chosen) != count * per_slot:
        raise RuntimeError("Insufficient distinct physical cores")
    return [chosen[i * per_slot:(i + 1) * per_slot] for i in range(count)]


def prepare(root):
    import h5py

    spec = json.loads((root / "setup/study.json").read_text())
    if importlib.metadata.version("midas-calibrate-v2") != spec["solver_version"]:
        raise RuntimeError("Numerical solver release does not match plan")
    setup = root / "setup"
    with (root.parent / "scientific-run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_json(setup / "preparation-started.json", {
            "pid": os.getpid(), "started_unix": time.time(), "model_calls": 0,
            "plan_sha256": sha(setup / "PLAN.md"), "spec_sha256": sha(setup / "study.json"),
            "generator_sha256": sha(__file__),
        }, exclusive=True)
        inputs = {}
        for entry in spec["fixtures"]:
            if str(entry["scan"]) in inputs:
                continue
            image = source_image(root, entry)
            with h5py.File(image, "r") as f:
                if list(f["exchange/data"].shape) != [21, 2880, 2880]:
                    raise RuntimeError("Unexpected detector frame shape")
                readback = f[spec["readback_path"]]
                units = readback.attrs.get("units", "")
                if isinstance(units, bytes):
                    units = units.decode()
                if units != "mm" or readback.size != 1:
                    raise RuntimeError("Distance readback has unexpected shape/units")
                distance = float(readback[()].reshape(-1)[0])
                if not math.isfinite(distance) or distance <= 0:
                    raise RuntimeError("Invalid nominal distance readback")
            inputs[str(entry["scan"])] = {"path": str(image), "sha256": sha(image),
                "bytes": image.stat().st_size, "readback_path": spec["readback_path"],
                "distance_readback_mm": distance, "units": units}
        write_json(setup / "input_identity.json", inputs, exclusive=True)
        cpus = physical_cpu_sets(spec["parallel_slots"], spec["cpu_threads"])
        write_json(setup / "compute.json", {"cpu_sets": cpus, "solver_python": sys.executable,
            "solver_version": spec["solver_version"], "cuda_visible_devices": "",
            "source_commit": spec["apexa_source_commit"]}, exclusive=True)
        completed, active = [], []

        def state(status):
            write_json(setup / "preparation-status.json", {"status": status, "pid": os.getpid(),
                "updated_unix": time.time(), "completed": len(completed), "scheduled": 4,
                "active": active, "model_calls": 0, "results": completed})

        def launch(entry, cpu_set):
            out = root / "fixtures_private" / entry["id"]
            out.mkdir(parents=True, exist_ok=False)
            env = dict(os.environ)
            for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
                env[key] = str(spec["cpu_threads"])
            env.update(CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
            started = time.time()
            command = ["taskset", "-c", ",".join(map(str, cpu_set)), sys.executable,
                       str(Path(__file__).resolve()), "--root", str(root), "--fixture", entry["id"]]
            with (out / "runner.stdout.json").open("x") as stdout, (out / "runner.stderr.log").open("x") as stderr:
                process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr,
                                           cwd=root / "source", start_new_session=True)
                try:
                    return_code = process.wait(timeout=5400)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    return_code = 124
            receipt = {"pid": process.pid, "return_code": return_code, "started_unix": started,
                       "finished_unix": time.time(), "cpu_affinity": cpu_set, "command": command}
            write_json(out / "exit.json", receipt)
            return qualify(root, spec, entry, receipt, inputs)

        state("preparing_fixtures")
        # Each wave uses disjoint CPU sets. No queued task can accidentally reuse
        # a slot while an earlier task on that slot is still running.
        for offset in range(0, len(spec["fixtures"]), len(cpus)):
            entries = spec["fixtures"][offset:offset + len(cpus)]
            active[:] = [e["id"] for e in entries]
            state("preparing_fixtures")
            with ThreadPoolExecutor(max_workers=len(cpus)) as pool:
                jobs = {pool.submit(launch, e, cpus[i]): e for i, e in enumerate(entries)}
                for future in as_completed(jobs):
                    entry = jobs[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {"fixture": entry["id"], "passed": False,
                                  "error": f"{type(exc).__name__}: {exc}"}
                    completed.append(result)
                    active.remove(entry["id"])
                    state("preparing_fixtures")
            if not all(r["passed"] for r in completed):
                state("fixture_qualification_failed")
                return 1
        write_json(setup / "fixture_qualification.json", {
            "passed": all(r["passed"] for r in completed), "results": completed,
            "model_calls": 0, "scientific_agent_admission": False,
            "remaining_gates": ["public handoff construction", "checker/scorer validation",
                                "native-agent and sandbox qualification", "immutable trial freeze"],
        }, exclusive=True)
        state("fixtures_qualified_agent_setup_required")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--fixture")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.fixture:
        specification = json.loads((root / "setup/study.json").read_text())
        row = next(e for e in specification["fixtures"] if e["id"] == args.fixture)
        worker(root, specification, row)
    else:
        raise SystemExit(prepare(root))
