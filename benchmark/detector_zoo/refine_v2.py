"""V2 refinement driver: NaN-safe + dict-aware cell + NIST starting CIF.

Usage:
    python refine_v2.py <detector_name>

detector_name is one of: varex_distortion, varex_aero, pilatus, ge.

Requires GSAS-II and MIDAS's gsas_ii_refine to be importable. If they are not on
PYTHONPATH, point at them with:
    GSASII_PATH=/path/to/GSAS-II      # dir containing the GSASII package
    MIDAS_PATH=/path/to/MIDAS         # module is read from $MIDAS_PATH/utils

Inputs, resolved relative to this file:
    <detector>/integration/*.caked.hdf.zarr.zip   integrated pattern
    <detector>/refinement/instrument.instprm      GSAS-II instrument parameters
    CeO2_NIST_5p41165.cif                         starting cell (NIST SRM 674b)

The integrated patterns (~95 MB) are deposited with this paper's data release,
MDF DOI 10.18126/tgg4-1m26, rather than in the source repository. They are also
regenerable from MIDAS's bundled CeO2 example frames
(MIDAS/FF_HEDM/Example/Calibration/) via the documented calibration and
integration steps.

Outputs <detector>/refinement_v2/{hist_NNNN.gpx,summary.json}.
"""
import json
import os
import sys
from pathlib import Path

# Paths come from the environment, never from this file. GSASII_PATH and
# MIDAS_PATH are only needed when those packages are not already importable.
for _var, _sub in (("GSASII_PATH", ""), ("MIDAS_PATH", "utils")):
    _p = os.environ.get(_var, "").strip()
    if _p:
        sys.path.insert(0, str(Path(_p) / _sub) if _sub else _p)

import numpy as np
try:
    import GSASII.GSASIIscriptable as G2sc
except ImportError:
    sys.exit("GSAS-II not importable. Install it, or set GSASII_PATH to the "
             "directory containing the GSASII package.")
try:
    from gsas_ii_refine import _open_zarr, build_refinement_recipe
except ImportError:
    sys.exit("gsas_ii_refine not importable. Set MIDAS_PATH to your MIDAS "
             "checkout (the module lives in $MIDAS_PATH/utils).")

# This script lives in detector_zoo/, so the data root is its own directory.
ROOT = Path(__file__).resolve().parent
CIF = ROOT / "CeO2_NIST_5p41165.cif"

CONFIG = {
    "varex_distortion": dict(
        zarr=ROOT/"varex_distortion/integration/CeO2_10s_1000mm_42keV_000718.tiff.caked.hdf.zarr.zip",
        limits=[3.0, 13.5],
    ),
    "varex_aero": dict(
        zarr=ROOT/"varex_aero/integration/Ceria_63keV_900mm_100x100_0p5s_aero_0_001137.tif.caked.hdf.zarr.zip",
        limits=[2.0, 11.0],
    ),
    "pilatus": dict(
        zarr=ROOT/"pilatus/integration/CeO2_Pil_100x100_att000_650mm_71p676keV_001956.tif.caked.hdf.zarr.zip",
        limits=[2.0, 12.0],
    ),
    "ge": dict(
        zarr=ROOT/"ge/integration/CeO2_1s_65pt351keV_1860mm_000007.edf.ge1.caked.hdf.zarr.zip",
        limits=[2.5, 11.5],
    ),
}


def extract_nan_safe(zarr_path: str):
    fp = _open_zarr(zarr_path)
    remap = np.array(fp["REtaMap"])
    tth_map = remap[1]
    area_map = remap[3]
    Nbins, Nazim = tth_map.shape

    osf = fp["OmegaSumFrame"]
    keys = sorted(osf.keys())
    out = []
    for fkey in keys:
        frame = np.array(osf[fkey])
        for j in range(Nazim):
            mask = (area_map[:, j] > 0) & np.isfinite(frame[:, j])
            if mask.sum() < 50:
                continue
            tth = tth_map[mask, j]
            raw = frame[mask, j]
            area = area_map[mask, j]
            inten = raw / area
            sigma = np.sqrt(np.maximum(np.abs(raw), 1.0)) / np.maximum(area, 1.0)
            out.append((tth, inten, sigma))
    return out


def refine_one(idx, tth, inten, sigma, out_dir, instprm, limits):
    gpx_path = str(out_dir / f"hist_{idx:04d}.gpx")
    xye = str(out_dir / f"_temp_{idx:04d}.xye")
    np.savetxt(xye, np.column_stack([tth, inten, sigma]), fmt="%.6f %.6f %.6f")

    res = {"i": idx, "gpx": gpx_path}
    try:
        gpx = G2sc.G2Project(newgpx=gpx_path)
        gpx.add_powder_histogram(xye, str(instprm), fmthint="xye")
        if not gpx.histograms():
            res["status"] = "skipped"
            return res
        gpx.add_phase(str(CIF), phasename="CeO2", histograms=gpx.histograms(), fmthint="CIF")
        recipe = build_refinement_recipe(bkg_terms=6, refine_atoms=False, two_theta_limits=limits)
        for stage in recipe:
            try:
                gpx.do_refinements([stage])
            except Exception as e:
                res.setdefault("warns", []).append(str(e)[:120])
        h0 = gpx.histogram(0)
        res["Rwp"] = h0.get_wR()
        for p in gpx.phases():
            c = p.get_cell()
            res["a"] = c["length_a"] if isinstance(c, dict) else c[0]
            res["volume"] = c["volume"] if isinstance(c, dict) else c[6]
        gpx.save()
        res["status"] = "success"
    except Exception as e:
        res["status"] = "error"
        res["error"] = str(e)[:200]
    finally:
        try:
            os.remove(xye)
        except OSError:
            pass
    return res


def main(det):
    cfg = CONFIG[det]
    out_dir = ROOT / det / "refinement_v2"
    out_dir.mkdir(exist_ok=True)
    instprm = ROOT / det / "refinement" / "instrument.instprm"
    if not instprm.exists():
        print(f"missing instprm: {instprm}", file=sys.stderr)
        sys.exit(2)

    print(f"[{det}] extracting histograms (NaN-safe)…", flush=True)
    hists = extract_nan_safe(str(cfg["zarr"]))
    print(f"[{det}] {len(hists)} histograms", flush=True)

    results = []
    for i, (tth, inten, sigma) in enumerate(hists):
        r = refine_one(i, tth, inten, sigma, out_dir, instprm, cfg["limits"])
        results.append(r)
        if (i + 1) % 50 == 0:
            ok = sum(1 for x in results if x.get("status") == "success")
            print(f"[{det}] {i+1}/{len(hists)} ({ok} ok so far)", flush=True)

    a_vals = np.array([r["a"] for r in results
                       if r.get("status") == "success"
                       and r.get("a") is not None
                       and 4.5 < r["a"] < 6.5])
    rwps = np.array([r["Rwp"] for r in results
                     if r.get("status") == "success" and r.get("Rwp") is not None])

    summary = {
        "detector": det,
        "n_total": len(hists),
        "n_success": int((np.array([r.get("status") for r in results]) == "success").sum()),
        "n_a_in_window": int(len(a_vals)),
        "median_Rwp": float(np.median(rwps)) if len(rwps) else None,
        "mean_Rwp": float(np.mean(rwps)) if len(rwps) else None,
        "mean_a": float(np.mean(a_vals)) if len(a_vals) else None,
        "median_a": float(np.median(a_vals)) if len(a_vals) else None,
        "std_a": float(np.std(a_vals)) if len(a_vals) else None,
        "abs_da_mA": float(abs(np.mean(a_vals) - 5.41165) * 1000) if len(a_vals) else None,
        "limits": cfg["limits"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in CONFIG:
        print(f"usage: {sys.argv[0]} {{{'/'.join(CONFIG)}}}", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
