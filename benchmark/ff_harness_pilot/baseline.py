"""Fresh upstream-MIDAS FF reference and timing; no model or APEXA tool calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time


INPUTS = [
    ("gold", "Au_FF_box_1p1x0p025_att6/Au_FF_box_1p1x0p025_att6_001181.vrx.h5",
     23931825300, "15e2ab11946dae8e6c97a905e034289e24121a36692751c9355896d4e3855605"),
    ("aero_180", "cali/Ceria_0723_63keV_900mm_100x100_2p0s_aero_180_001187.vrx.h5",
     514542588, "6a4e66eaaa3c040e33a1d6cd8e9ff6fb231289049261e6c8cdbd8772cd9bec5e"),
    ("aero_0", "cali/Ceria_0723_63keV_900mm_100x100_2p0s_aero_0_001188.vrx.h5",
     514542588, "ad353bea12b8cbce05178d8ebbb7f99fe276f95d81a0e478167a86dad990e50e"),
]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def checked_inputs(root):
    import h5py
    import numpy as np
    records = []
    for role, relative, size, digest in INPUTS:
        path = root / "data" / relative
        if path.is_symlink() or path.stat().st_size != size or sha(path) != digest:
            raise ValueError(f"Input identity mismatch: {role}")
        with h5py.File(path) as handle:
            image, dark = handle["exchange/data"], handle["exchange/dark"][()]
            expected = (1442, 2880, 2880) if role == "gold" else (21, 2880, 2880)
            if image.shape != expected or image.dtype != np.dtype("uint16"):
                raise ValueError(f"Unexpected image layout: {role}")
            if role == "gold" and np.any(dark):
                raise ValueError("Gold embedded dark is not zero")
            if role != "gold" and float(dark.mean()) < 100:
                raise ValueError("Calibrant measured dark is missing")
            records.append({"role": role, "path": str(path), "bytes": size,
                            "sha256": digest, "shape": list(image.shape),
                            "dark_shape": list(dark.shape), "dark_mean": float(dark.mean())})
    return records


def execute(command, folder, env, timeout):
    folder.mkdir(parents=True, exist_ok=False)
    record = {"argv": command, "started_unix": time.time(), "timeout_seconds": timeout}
    write(folder / "command.json", record)
    with (folder / "execution.log").open("x") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            result = 124
    record.update(return_code=result, finished_unix=time.time())
    record["elapsed_s"] = record["finished_unix"] - record["started_unix"]
    write(folder / "exit.json", record)
    if result:
        raise RuntimeError(f"{folder.name} failed with status {result}; retain execution.log")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cpus", default="32,33,34,35,36,37,38,39")
    parser.add_argument("--wait-for-inputs", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    out = root / "results/reference-ff"
    if out.exists():
        raise RuntimeError("Reference directory exists; preserve it rather than rerunning")
    state_path = root / "setup/reference-status.json"
    state = {"pid": os.getpid(), "status": "preparing", "started_unix": time.time(),
             "model_calls": 0, "scientific_agent_trials": 0, "source_sha256": sha(__file__)}

    def update(**changes):
        state.update(changes, updated_unix=time.time())
        write(state_path, state)

    try:
        cpus = [int(value) for value in args.cpus.split(",")]
        physical = [tuple((Path(f"/sys/devices/system/cpu/cpu{cpu}/topology") / name).read_text().strip()
                          for name in ("physical_package_id", "core_id")) for cpu in cpus]
        if len(set(physical)) != len(cpus):
            raise ValueError("Requested CPUs share physical cores")
        os.sched_setaffinity(0, cpus)
        update(status="waiting_for_input_transfer", cpu_affinity=cpus)
        deadline = time.time() + 14400
        while not all((root / "data" / rel).is_file() and (root / "data" / rel).stat().st_size == size
                      for _, rel, size, _ in INPUTS):
            if not args.wait_for_inputs or time.time() > deadline:
                raise RuntimeError("Input transfer has not completed")
            time.sleep(15)
            update()
        update(status="verifying_input_hashes")
        records = checked_inputs(root)
        write(root / "setup/input-verification.json", {"verified_unix": time.time(), "inputs": records})
        out.mkdir()
        bins = root / "envs/midas/bin"
        env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ.get("PATH", ""),
                   CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1",
                   NUMBA_CACHE_DIR=str(root / "work/reference-numba"),
                   TORCH_EXTENSIONS_DIR=str(root / "work/reference-torch"))
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[name] = str(len(cpus))
        for name in list(env):
            if any(word in name for word in ("API_KEY", "AUTH_TOKEN", "ANL_USERNAME")):
                env.pop(name)
        gold = Path(records[0]["path"])
        calibration = Path(records[1]["path"])
        params = out / "calibration/Parameters.txt"
        command = [str(bins / "midas-calibrate-v2"), "--mode", "ff", "--image", str(calibration),
            "--output", str(params), "--dark-group", "/exchange/dark", "--image-group", "/exchange/data",
            "--reduce", "median", "--wavelength", str(12.398419843320026 / 63.314),
            "--initial-lsd", "900000", "--im-trans", "2", "--calibrant", "CeO2", "--n-iter", "5",
            "--device", "cpu", "--verbose", "--px", "150", "--n-pixels", "2880",
            "--lattice", "4.0782", "4.0782", "4.0782", "90", "90", "90", "--space-group", "225",
            "--raw-folder", str(gold.parent), "--file-stem", "Au_FF_box_1p1x0p025_att6",
            "--start-file-nr", "1181", "--ext", ".vrx.h5", "--padding", "6", "--dark-file", str(gold),
            "--dark-loc", "/exchange/dark", "--omega-start", "180", "--omega-step", "-0.25",
            "--skip-frame", "1", "--n-files-per-sweep", "1", "--ring-to-index", "1", "--strain-gate", "100"]
        for ring, threshold in enumerate([20, 20, 20, 20, 30], 1):
            command += ["--ring-thresh", f"{ring}:{threshold}"]
        for value in ["MinNrPx=4", "MinOmeSpotIDsToIndex=-180", "MaxOmeSpotIDsToIndex=180"]:
            command += ["--set", value]
        update(status="calibrating", numerical_started_unix=time.time())
        cal = execute(command, out / "calibration", env, 7200)
        if not params.is_file() or not params.stat().st_size:
            raise RuntimeError("Calibration returned no parameter file")
        command = [str(bins / "midas-pipeline"), "run", "--scan-mode", "ff", "--params", str(params),
                   "--result", str(out / "reconstruction"), "--n-cpus", str(len(cpus)),
                   "--device", "cpu", "--indexer-backend", "c-omp", "--refine-backend", "c-omp",
                   "--layers", "1-1", "--raw-dir", str(gold.parent)]
        update(status="reconstructing", calibration_elapsed_s=cal["elapsed_s"])
        reconstruction = execute(command, out / "pipeline", env, 7200)
        grains = out / "reconstruction/LayerNr_1/Grains.csv"
        if not grains.is_file() or grains.stat().st_mtime < reconstruction["started_unix"]:
            raise RuntimeError("Pipeline returned no fresh grain artifact")
        rows = [line.split() for line in grains.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("%")]
        if not rows or any(len(row) < 13 for row in rows):
            raise RuntimeError("Grain artifact is empty or has incomplete rows")
        if any(not math.isfinite(float(value)) for row in rows for value in row[:13]):
            raise RuntimeError("Nonfinite grain orientation/position")
        total = cal["elapsed_s"] + reconstruction["elapsed_s"]
        receipt = {"status": "execution_completed", "calibration_elapsed_s": cal["elapsed_s"],
            "reconstruction_elapsed_s": reconstruction["elapsed_s"], "total_numerical_elapsed_s": total,
            "candidate_count": len(rows), "grain_artifact": str(grains), "grain_sha256": sha(grains),
            "parameter_sha256": sha(params), "physical_validity": "unestablished",
            "proposed_agent_wall_seconds": max(3600, 300 * math.ceil((2 * total + 900) / 300)),
            "scope": "Fresh upstream execution/timing reference; candidate count is not ground truth"}
        write(out / "reference.json", receipt)
        update(**{**receipt, "status": "execution_completed_pending_scientific_audit",
                  "finished_unix": time.time()})
    except BaseException as exc:
        update(status="failed", error=f"{type(exc).__name__}: {exc}", finished_unix=time.time())
        raise


if __name__ == "__main__":
    main()
