"""Fixed, no-LLM MIDAS tilted-detector recipe. Receives no scoring reference."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ENERGY_KEV = 63.314
WAVELENGTH_A = 12.398419843320026 / ENERGY_KEV


def decision(verdict):
    return {"PASS": "accepted", "WARN": "provisional", "FAIL": "rejected"}[verdict]


def calibrant_for(filename):
    prefix = Path(filename).name.split("_")[0].lower()
    if prefix in ("ceria", "ceo2"):
        return "CeO2"
    if prefix == "lab6":
        return "LaB6"
    raise ValueError("Calibrant is not identified by the declared acquisition prefix")


def save(path, value):
    with path.open("x") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")


def preprocess(path, *, reader=None):
    import h5py
    import numpy as np
    if reader is None:
        from midas_calibrate_v2.io import read_image
        reader = read_image
    # Freeze the observed acquisition format; do not silently reinterpret a
    # different dark correction or frame count in a future dataset.
    with h5py.File(path, "r") as f:
        if f["exchange/data"].shape != (21, 2880, 2880):
            raise ValueError("Input frame shape/count differs from the declared study")
        dark = f["exchange/data_dark"][...]
        if dark.shape != (1, 2880, 2880) or not np.isfinite(dark).all() or np.any(dark != 0):
            raise ValueError("Dark differs from the inspected zero placeholder; review preprocessing")
    image, mask = reader(path, data_loc="exchange/data", skip_frame=1,
                         frame_reduce="mean", im_trans=(2,), return_mask=True)
    image = np.asarray(image, dtype=np.float64)
    mask = np.asarray(mask, dtype=bool)
    if image.shape != (2880, 2880) or mask.shape != image.shape or not np.isfinite(image).all():
        raise ValueError("Reader returned a malformed or nonfinite frame/mask")
    record = {"frame_indices": list(range(1, 21)), "frame_reduce": "mean", "im_trans_applied": [2],
              "energy_keV": ENERGY_KEV, "wavelength_A": WAVELENGTH_A, "pixel_um": 150.0,
              "dark": "all-zero placeholder; no measured correction subtracted",
              "reader_bad_pixels": int(mask.sum()),
              "oriented_frame_sha256": hashlib.sha256(image.tobytes(order="C")).hexdigest(),
              "reader_mask_sha256": hashlib.sha256(mask.tobytes(order="C")).hexdigest()}
    return image, mask, record


def execute(image_path, output):
    import importlib.metadata
    import numpy as np
    import torch
    from midas_calibrate_v2.pipelines.tilted import calibrate_tilted

    if importlib.metadata.version("midas-calibrate-v2") != "0.26.0":
        raise RuntimeError("Baseline requires the frozen MIDAS 0.26.0 solver")
    torch.set_num_threads(4)
    np.random.seed(20261106)
    torch.manual_seed(20261106)
    image, mask, inputs = preprocess(image_path)
    calibrant = calibrant_for(image_path)
    inputs["calibrant"] = calibrant
    inputs["calibrant_source"] = "acquisition filename prefix, also visible to every harness"
    save(output / "effective_inputs.json", inputs)
    result = calibrate_tilted(image, wavelength_A=WAVELENGTH_A, px_um=150.0,
                              calibrant=calibrant, mask=mask, nominal_tilt_deg=None,
                              lsd_um=None, polish=True, fallback_pv=True, verbose=True)
    save(output / "solver_result.json", result.to_dict())
    artifact = output / "adopted_geometry.json"
    save(artifact, result.geometry)
    if result.verdict != "FAIL":
        result.write_paramstest(output / "paramstest_v2.txt")
    result.write_mask(output / "mask.tif")
    status = decision(result.verdict)
    save(output / "result_manifest.json", {"schema_version": 1, "scientific_status": status,
         "artifact_path": artifact.name, "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
    print(json.dumps({"scientific_status": status, "solver_verdict": result.verdict}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.is_dir() or any(args.output.iterdir()):
        parser.error("Output must be an existing empty workspace")
    execute(args.image, args.output)


if __name__ == "__main__":
    main()
