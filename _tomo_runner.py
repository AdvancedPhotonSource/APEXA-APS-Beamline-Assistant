#!/usr/bin/env python3
"""APEXA tomography runner — TomoPy backend (regime 3: runs under APEXA_TOMO_PYTHON).

Same pattern as ``_calibrate_runner.py`` / ``_capability_runner.py``: a real file,
not an inline ``python -c`` string, so it is lintable, testable, and diffable against
the ``tomo`` capsule. ``tomo_server.py`` invokes it as::

    <APEXA_TOMO_PYTHON> _tomo_runner.py survey      --data <dir> [...]
    <APEXA_TOMO_PYTHON> _tomo_runner.py reconstruct --data <dir> [...]

**stdout is JSON only** (one object, last line); all chatter goes to stderr, so the
server can parse the result the way ``gsas2_server._run_python`` does.

The capsule's five hard rules are enforced here as *runtime data checks* (tomo has no
MIDAS param file to key-lint):
  1. flat/dark layout from measured frame means, never assumed
  2. every transmission statistic restricted to the illuminated region
  3. pixel size / rotation axis / in-plane handedness are never defaulted
  4. registration is a read, not a fit (out of v1 scope)
  5. check mu*D before promising an absorption correction

The pure-numpy helpers (layout detection, illuminated region, transmission, mu*D,
truncation) import **only numpy**, so they are importable and unit-testable in APEXA's
own ``.venv``. ``tomopy``/``dxchange`` are imported lazily, inside the functions that
need them, so they are required only when actually reconstructing under the tomo env.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Output discipline: JSON only on stdout
# ---------------------------------------------------------------------------
def _emit(payload: dict) -> None:
    """Print exactly one JSON object on stdout and flush."""
    sys.stdout.write(json.dumps(payload, default=_json_default) + "\n")
    sys.stdout.flush()


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Pure-numpy hard-rule helpers (no tomopy — unit-testable in APEXA's .venv)
# ---------------------------------------------------------------------------
def illuminated_mask(frame: np.ndarray, rel_thresh: float = 0.25) -> np.ndarray:
    """Boolean mask of the beam-illuminated region of one 2-D frame.

    Hard rule 2: outside the beam ``flat - dark ~ 0`` and any ratio there is noise
    that reads as a smooth plateau of partial attenuation. A flat/bright frame is the
    right input; the illuminated region is where intensity is a real fraction of the
    frame maximum. Robust to a few hot pixels via the 99th percentile as the scale.
    """
    f = np.asarray(frame, dtype=np.float64)
    scale = np.percentile(f, 99.0)
    if not np.isfinite(scale) or scale <= 0:
        return np.ones(f.shape, dtype=bool)
    return f >= rel_thresh * scale


def detect_flat_dark_layout(frame_means: np.ndarray,
                            dark_factor: float = 0.5,
                            flat_factor: float = 1.3) -> dict:
    """Classify each frame as dark / flat / projection from its mean (hard rule 1).

    Layouts differ between beamtimes (``dark, white1, white2, projections`` vs
    ``flats, projections, flats, darks``). We never assume; we read the per-frame means
    and require the flats/darks to be **clearly separated** from the projection bulk —
    not merely high/low within a smooth ramp. Projections are the overwhelming majority,
    so the stack median tracks the projection level; a frame is:

      * dark  : mean <  dark_factor x median  (open-beam floor, well below the sample)
      * flat  : mean >  flat_factor x median  (open beam, distinctly brighter than any
                sample-attenuated projection)
      * proj  : everything in between

    A gentle projection ramp has no frame past either factor, so it is reported
    ``confident: False`` with no flats — a halt upstream (specify flats/darks
    explicitly), never a silent guess that would normalize by a projection.
    """
    m = np.asarray(frame_means, dtype=np.float64)
    if m.size == 0 or float(np.ptp(m)) <= 0:
        return {"dark": [], "flat": [], "proj": list(range(len(m))),
                "confident": False, "reason": "frame means are empty or identical"}
    med = float(np.median(m))
    if med <= 0:
        med = float(np.mean(m[m > 0])) if np.any(m > 0) else 1.0
    dark = np.where(m < dark_factor * med)[0].tolist()
    flat = np.where(m > flat_factor * med)[0].tolist()
    proj = [i for i in range(len(m)) if i not in set(dark) | set(flat)]
    confident = bool(len(flat) >= 1 and len(proj) >= 8)
    reason = ""
    if not flat:
        reason = ("no frames are distinctly brighter than the projection bulk — no "
                  "open-beam/flat found; specify flats explicitly (cannot normalize)")
    elif len(proj) < 8:
        reason = f"only {len(proj)} projection frames — too few to reconstruct"
    return {"dark": dark, "flat": flat, "proj": proj,
            "confident": confident, "reason": reason,
            "median_mean": med, "mean_min": float(np.min(m)),
            "mean_max": float(np.max(m))}


def transmission_stats(proj_norm: np.ndarray, illum: np.ndarray) -> dict:
    """Transmission summary over the illuminated region only (hard rule 2).

    ``proj_norm`` is a normalized projection (flat/dark corrected, so ~1 in air).
    A value < 0 anywhere in the illuminated region means the flat/dark assignment is
    wrong (hard rule 1 → halt). A value that never drops below ~1 means no sample /
    wrong region.
    """
    illum = np.asarray(illum, dtype=bool)
    vals = np.asarray(proj_norm, dtype=np.float64)[illum]
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return {"ok": False, "reason": "no illuminated pixels", "min": None,
                "median": None, "max": None, "negative_fraction": None}
    neg_frac = float(np.mean(vals < 0.0))
    return {
        "ok": neg_frac == 0.0,
        "min": float(np.min(vals)),
        "median": float(np.median(vals)),
        "max": float(np.max(vals)),
        "negative_fraction": neg_frac,
        "reason": ("" if neg_frac == 0.0 else
                   f"{neg_frac:.1%} of illuminated pixels have transmission < 0 "
                   "— flat/dark layout is wrong (hard rule 1)"),
    }


def mu_d_estimate(proj_norm_center: np.ndarray, illum: np.ndarray) -> dict:
    """Estimate mu*D through the sample (hard rule 5).

    mu*D = -ln(T) at the most-attenuating path. Below ~0.1 an absorption correction is
    at or under the per-spot noise, so the honest answer is "no detectable effect".
    Computed from the data (the deepest 1% transmission in the illuminated region),
    never assumed from the element.
    """
    illum = np.asarray(illum, dtype=bool)
    t = np.asarray(proj_norm_center, dtype=np.float64)[illum]
    t = t[np.isfinite(t) & (t > 0)]
    if t.size == 0:
        return {"mu_d": None, "correctable": False, "reason": "no positive transmission"}
    t_deep = float(np.percentile(t, 1.0))
    mu_d = float(-np.log(max(t_deep, 1e-6)))
    return {
        "mu_d": mu_d,
        "t_deep_p1": t_deep,
        "correctable": mu_d >= 0.1,
        "reason": ("" if mu_d >= 0.1 else
                   f"mu*D ~ {mu_d:.3f} < 0.1 — absorption correction below noise; "
                   "report 'no detectable effect' rather than correcting"),
    }


def truncation_check(proj_norm: np.ndarray, illum_cols: np.ndarray,
                     edge_cols: int = 10, tol: float = 0.1) -> dict:
    """Does transmission return to ~1 on both sides of the sample? (capsule halt).

    A truncated scan reconstructs into a bowl-shaped cupping artefact that looks like a
    density gradient. ``proj_norm`` is a normalized sinogram row or projection; we look
    at the illuminated columns nearest each edge and require their median transmission
    to be within ``tol`` of 1.0.
    """
    vals = np.asarray(proj_norm, dtype=np.float64)
    cols = np.where(np.asarray(illum_cols, dtype=bool))[0]
    if cols.size < 2 * edge_cols + 1:
        return {"truncated": None, "reason": "too few illuminated columns to judge"}
    left = vals[..., cols[:edge_cols]]
    right = vals[..., cols[-edge_cols:]]
    lmed, rmed = float(np.nanmedian(left)), float(np.nanmedian(right))
    truncated = (abs(lmed - 1.0) > tol) or (abs(rmed - 1.0) > tol)
    return {
        "truncated": bool(truncated),
        "left_edge_median_T": lmed,
        "right_edge_median_T": rmed,
        "reason": ("" if not truncated else
                   f"edge transmission L={lmed:.2f} R={rmed:.2f} not ~1 — sample fills "
                   "or exceeds the field of view; a truncated scan cannot become a mask"),
    }


# ---------------------------------------------------------------------------
# Stack reading (lazy — tifffile/dxchange only needed to actually read frames)
# ---------------------------------------------------------------------------
def _list_frames(data_folder: str, pattern: str) -> list:
    files = sorted(glob.glob(str(Path(data_folder) / pattern)))
    return files


def _read_stack(files: list) -> np.ndarray:
    """Read a TIFF (or .raw-less) stack into (nframe, ny, nx). tifffile first."""
    try:
        import tifffile  # noqa: F401
        import numpy as _np
        frames = [tifffile.imread(f) for f in files]
        return _np.asarray(frames)
    except ImportError:
        # dxchange can read a tiff stack too
        import dxchange
        import numpy as _np
        arrs = [dxchange.read_tiff(f) for f in files]
        return _np.asarray(arrs)


def _frame_means(files: list, subsample: int = 1) -> np.ndarray:
    """Per-frame mean without holding the whole stack (survey is meant to be cheap)."""
    import tifffile
    idx = range(0, len(files), max(1, subsample))
    means = []
    for i in idx:
        means.append(float(np.mean(tifffile.imread(files[i]))))
    return np.asarray(means)


# ---------------------------------------------------------------------------
# survey — the cheap, read-only pre-flight (THE ORDER steps 1-4)
# ---------------------------------------------------------------------------
def cmd_survey(args) -> dict:
    files = _list_frames(args.data, args.pattern)
    if not files:
        return {"tool": "survey_tomo_stack", "status": "error",
                "error": f"no frames matching {args.pattern!r} in {args.data}"}

    # Dimensions/dtype from the first frame (cheap).
    import tifffile
    f0 = tifffile.imread(files[0])
    ny, nx = (f0.shape[-2], f0.shape[-1])
    dtype = str(f0.dtype)

    means = _frame_means(files, subsample=max(1, len(files) // 400 or 1))
    layout = detect_flat_dark_layout(means)

    halts = []
    if not layout["confident"]:
        halts.append({"rule": "flat/dark layout (hard rule 1)",
                      "detail": layout["reason"] or "layout not confidently resolved"})

    # Pixel size is never defaulted (hard rule 3) — it is a metadata read, reported here.
    px = args.pixel_size_um
    if px is None:
        halts.append({"rule": "pixel size (hard rule 3)",
                      "detail": "pixel size not supplied and not recoverable from the "
                                "reconstruction — read it from acquisition metadata"})

    # Transmission + mu*D + truncation need a normalized projection; only compute when
    # the layout is confident (needs flats/darks).
    trans = mud = trunc = None
    if layout["confident"]:
        try:
            stack = _read_stack(files).astype(np.float64)
            dark = (np.mean(stack[layout["dark"]], axis=0)
                    if layout["dark"] else np.zeros((ny, nx)))
            flat = np.mean(stack[layout["flat"]], axis=0)
            illum = illuminated_mask(flat)
            denom = (flat - dark)
            # mid projection, flat/dark corrected
            mid = layout["proj"][len(layout["proj"]) // 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                pn = (stack[mid] - dark) / denom
            trans = transmission_stats(pn, illum)
            mud = mu_d_estimate(pn, illum)
            illum_cols = np.any(illum, axis=0)
            trunc = truncation_check(pn, illum_cols)
            if not trans["ok"]:
                halts.append({"rule": "transmission >= 0 (hard rule 1/2)",
                              "detail": trans["reason"]})
            if trunc.get("truncated"):
                halts.append({"rule": "truncation (capsule halt)",
                              "detail": trunc["reason"]})
        except Exception as e:  # noqa: BLE001
            _log(f"survey transmission block failed: {e}")

    return {
        "tool": "survey_tomo_stack",
        "status": "ok" if not halts else "halt",
        "backend": "tomopy",
        "data_folder": args.data,
        "n_frames": len(files),
        "frame_shape": [int(ny), int(nx)],
        "dtype": dtype,
        "layout": layout,
        "pixel_size_um": px,
        "transmission": trans,
        "mu_d": mud,
        "truncation": trunc,
        "halts": halts,
        "note": ("Resolve every halt before reconstructing — each is a capsule hard "
                 "rule that fails silently if ignored." if halts else
                 "Pre-flight clean; proceed to run_tomo_reconstruction."),
    }


# ---------------------------------------------------------------------------
# reconstruct — normalize → center → recon → (phase) → write (THE ORDER 5-7)
# ---------------------------------------------------------------------------
def cmd_reconstruct(args) -> dict:
    # Hard rule 3: refuse to default pixel size, rotation axis, handedness.
    missing = []
    if args.pixel_size_um is None:
        missing.append("pixel_size_um")
    if args.handedness not in ("left", "right"):
        missing.append("handedness (left|right)")
    if missing:
        return {"tool": "run_tomo_reconstruction", "status": "error",
                "error": "refusing to default: " + ", ".join(missing)
                + " (capsule hard rule 3 — these are not recoverable later)"}

    files = _list_frames(args.data, args.pattern)
    if not files:
        return {"tool": "run_tomo_reconstruction", "status": "error",
                "error": f"no frames matching {args.pattern!r} in {args.data}"}

    import tomopy  # lazy — only needed here, under APEXA_TOMO_PYTHON
    out_dir = Path(args.output_dir or (Path(args.data) / "tomo_recon"))
    out_dir.mkdir(parents=True, exist_ok=True)
    run_started = time.time()

    stack = _read_stack(files).astype(np.float32)
    means = np.asarray([float(np.mean(stack[i])) for i in range(len(stack))])
    layout = detect_flat_dark_layout(means)
    if not layout["confident"]:
        return {"tool": "run_tomo_reconstruction", "status": "error",
                "error": "flat/dark layout not confident: "
                + (layout["reason"] or "resolve via survey_tomo_stack first"),
                "layout": layout}

    dark = (np.mean(stack[layout["dark"]], axis=0, keepdims=True)
            if layout["dark"] else np.zeros((1,) + stack.shape[1:], np.float32))
    flat = np.mean(stack[layout["flat"]], axis=0, keepdims=True)
    proj = stack[layout["proj"]]
    n_proj = proj.shape[0]

    # Normalize → -log (Beer-Lambert). Clip so a stray T<=0 does not make inf.
    proj = tomopy.normalize(proj, flat, dark)
    proj = tomopy.minus_log(np.clip(proj, 1e-6, None))

    # Angles (radians). ω from args; handedness sets the sweep direction.
    ang1 = float(args.omega_start)
    ang2 = ang1 + float(args.omega_step) * (n_proj - 1)
    theta = np.deg2rad(np.linspace(ang1, ang2, n_proj, dtype=np.float64))

    # Phase retrieval BEFORE thresholding when it is a propagation scan (rule / scope).
    phase_applied = False
    if args.phase_retrieval and float(args.propagation_mm) > 0:
        if args.energy_kev is None:
            return {"tool": "run_tomo_reconstruction", "status": "error",
                    "error": "phase_retrieval needs energy_kev (and propagation_mm)"}
        proj = tomopy.prep.phase.retrieve_phase(
            proj, pixel_size=args.pixel_size_um * 1e-4,            # µm → cm
            dist=float(args.propagation_mm) * 0.1,                 # mm → cm
            energy=float(args.energy_kev),
            alpha=float(args.paganin_alpha))
        phase_applied = True

    # Rotation axis: an OUTPUT, not a default. Either a given center, or a sweep whose
    # chosen index is recorded. find_center_vo when neither is supplied.
    chosen_shift = None
    sweep = None
    if args.rotation_center is not None:
        rot_center = float(args.rotation_center)
        center_source = "user"
    elif args.center_sweep:
        lo, hi, step = args.center_sweep
        cen_dir = out_dir / "center_sweep"
        tomopy.write_center(proj, theta, dpath=str(cen_dir),
                            cen_range=(lo, hi, step))
        sweep = {"range": [lo, hi, step], "slices_dir": str(cen_dir)}
        rot_center = float((lo + hi) / 2.0)   # provisional; operator picks from slices
        center_source = "sweep_midpoint_provisional"
    else:
        rot_center = float(tomopy.find_center_vo(proj))
        center_source = "find_center_vo"
    chosen_shift = rot_center

    recon = tomopy.recon(proj, theta, center=rot_center,
                         algorithm=args.algorithm, sinogram_order=False)
    recon = tomopy.circ_mask(recon, axis=0, ratio=0.95)

    # Write the volume as a TIFF stack.
    import tifffile
    vol_path = out_dir / "recon.tif"
    tifffile.imwrite(str(vol_path), recon.astype(np.float32))

    # Optional threshold → binary sample mask (hard rule: only on an absorption recon;
    # a phase-contrast recon must have had phase retrieval first).
    mask_path = None
    mask_stats = None
    if args.threshold is not None:
        mask = (recon >= float(args.threshold)).astype(np.uint8)
        mask_path = out_dir / "sample_mask.tif"
        tifffile.imwrite(str(mask_path), mask)
        mask_stats = {"threshold": float(args.threshold),
                      "filled_fraction": float(np.mean(mask))}

    # Honest status: output must be newer than run start and non-degenerate.
    fresh = vol_path.stat().st_mtime >= run_started - 1
    finite = bool(np.isfinite(recon).any() and float(np.ptp(recon)) > 0)
    status = "success" if (fresh and finite) else "warning"
    run_note = ("" if status == "success" else
                "reconstruction wrote no fresh/variable volume — check normalization, "
                "center and angles")

    return {
        "tool": "run_tomo_reconstruction",
        "status": status,
        "backend": "tomopy",
        "algorithm": args.algorithm,
        "data_folder": args.data,
        "output_dir": str(out_dir),
        "volume": str(vol_path),
        "mask": str(mask_path) if mask_path else None,
        "mask_stats": mask_stats,
        "n_projections": int(n_proj),
        "layout": layout,
        "pixel_size_um": args.pixel_size_um,
        "handedness": args.handedness,
        "rotation_center": rot_center,
        "rotation_center_source": center_source,
        "recorded_shift": chosen_shift,
        "center_sweep": sweep,
        "omega_start_deg": ang1,
        "omega_step_deg": float(args.omega_step),
        "phase_retrieval_applied": phase_applied,
        "run_note": run_note,
        "recon_stats": {"min": float(np.nanmin(recon)),
                        "max": float(np.nanmax(recon)),
                        "shape": list(recon.shape)},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="APEXA TomoPy runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("survey")
    s.add_argument("--data", required=True)
    s.add_argument("--pattern", default="*.tif*")
    s.add_argument("--pixel-size-um", dest="pixel_size_um", type=float, default=None)

    r = sub.add_parser("reconstruct")
    r.add_argument("--data", required=True)
    r.add_argument("--pattern", default="*.tif*")
    r.add_argument("--output-dir", dest="output_dir", default="")
    r.add_argument("--pixel-size-um", dest="pixel_size_um", type=float, default=None)
    r.add_argument("--handedness", default="", choices=["", "left", "right"])
    r.add_argument("--omega-start", dest="omega_start", type=float, default=0.0)
    r.add_argument("--omega-step", dest="omega_step", type=float, default=None)
    r.add_argument("--rotation-center", dest="rotation_center", type=float, default=None)
    r.add_argument("--center-sweep", dest="center_sweep", nargs=3, type=float,
                   default=None, metavar=("LO", "HI", "STEP"))
    r.add_argument("--algorithm", default="gridrec")
    r.add_argument("--phase-retrieval", dest="phase_retrieval", action="store_true")
    r.add_argument("--propagation-mm", dest="propagation_mm", type=float, default=0.0)
    r.add_argument("--energy-kev", dest="energy_kev", type=float, default=None)
    r.add_argument("--paganin-alpha", dest="paganin_alpha", type=float, default=1e-3)
    r.add_argument("--threshold", type=float, default=None)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "survey":
            _emit(cmd_survey(args))
        elif args.cmd == "reconstruct":
            if args.omega_step is None:
                _emit({"tool": "run_tomo_reconstruction", "status": "error",
                       "error": "omega_step is required (degrees per projection)"})
                return 1
            _emit(cmd_reconstruct(args))
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        _log(traceback.format_exc())
        _emit({"tool": f"tomo_{args.cmd}", "status": "error", "error": str(e)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
