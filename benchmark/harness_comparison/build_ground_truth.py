#!/usr/bin/env python3
"""Build the rotation-series ground-truth CSV from APEXA's per-scan calibrations.

Run this where the calibrations live (the beamline host). It walks a directory of
per-exposure outputs and emits one row per scan for rotation_benchmark.py.

    python build_ground_truth.py --root /gdata/.../rotation --out summary.csv

A note on what "ground truth" means here, because it is the whole validity of the
comparison. The reference is NOT any individual fit -- it is the ROTATION STAGE. The
detector was swept in ~1 deg steps, so tz(scan) is a straight line by construction,
and each exposure's reference comes from the OTHERS (leave-one-out, in
rotation_benchmark.py). Theil-Sen is used because ~8 of 43 exposures land in false
basins up to 17 deg off-trend; a least-squares line would be dragged by them, and
hand-dropping them would mean the graded party curated its own reference.

The line is therefore valid only while the residual scatter of the clean fits stays
far below the error being detected -- ~0.03 deg scatter against ~0.5 deg for a
bound-railed fit. `--report` prints that scatter so you can check it rather than
assume it. If the commanded stage positions are recorded anywhere, prefer them: pass
them as a `commanded` column and score against that instead.
"""
from __future__ import annotations

import argparse, csv, json, math, re, statistics, sys
from pathlib import Path
from typing import Dict, List, Optional

# v1 MIDAS parameter file: "<Key> <v> [v ...]"
KEYS = {"tx": "tx", "ty": "ty", "tz": "tz", "lsd": "lsd_um", "wavelength": "wavelength_A"}


def read_v1(p: Path) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for line in p.read_text(errors="ignore").splitlines():
        t = line.split()
        if len(t) < 2:
            continue
        k = t[0].strip().lower()
        if k in KEYS:
            try:
                out[KEYS[k]] = float(t[1])
            except ValueError:
                pass
        elif k == "bc" and len(t) >= 3:
            try:
                out["bc_y"], out["bc_z"] = float(t[1]), float(t[2])
            except ValueError:
                pass
        elif k == "latticeconstant" and len(t) >= 2:
            try:
                out["a_A"] = float(t[1])
            except ValueError:
                pass
    return out


def calibrant_of(a_A: Optional[float], text: str) -> str:
    """Lattice constant first -- it is in the file; the filename is a guess."""
    if a_A is not None:
        if abs(a_A - 5.41153) < 0.02:
            return "CeO2"
        if abs(a_A - 4.15689) < 0.02:
            return "LaB6"
    low = text.lower()
    if "ceo2" in low or "ceria" in low:
        return "CeO2"
    if "lab6" in low:
        return "LaB6"
    return "unknown"


def scan_of(path: Path) -> Optional[int]:
    for part in (path.name, *[p.name for p in path.parents[:3]]):
        m = re.search(r"_(\d{6})(?:_|\.|$)", part) or re.search(r"\b(\d{6})\b", part)
        if m:
            return int(m.group(1))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
          formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("rotation_ground_truth.csv"))
    ap.add_argument("--params-glob", default="refined_MIDAS_params*.txt")
    ap.add_argument("--report", action="store_true", help="print fit scatter per group")
    a = ap.parse_args()

    rows: List[Dict] = []
    for pf in sorted(a.root.rglob(a.params_glob)):
        g = read_v1(pf)
        if "tz" not in g:
            continue
        scan = scan_of(pf)
        if scan is None:
            print(f"  skip (no scan number): {pf}", file=sys.stderr)
            continue
        r = {"scan": scan, "tz": g.get("tz"), "ty": g.get("ty"), "tx": g.get("tx"),
             "lsd_um": g.get("lsd_um"), "bc_y": g.get("bc_y"), "bc_z": g.get("bc_z"),
             "wavelength_A": g.get("wavelength_A"),
             "calibrant": calibrant_of(g.get("a_A"), str(pf)),
             "strain_ue_test": None, "gate": "", "at_bounds": "", "source": str(pf)}
        # the outcome manifest, when APEXA wrote one beside it, carries the gate verdict
        for cand in (pf.parent/"APEXA_calibration.json", pf.parent/"calibration.json"):
            if not cand.exists():
                continue
            try:
                m = json.loads(cand.read_text())
            except Exception:
                break
            cm = m.get("convergence_metrics") or {}
            r["strain_ue_test"] = cm.get("stage4_strain_uE_test") or cm.get("strain_uE_test")
            r["gate"] = str(m.get("status", ""))
            ab = cm.get("at_bounds") or m.get("at_bounds") or []
            r["at_bounds"] = ";".join(sorted(ab)) if isinstance(ab, (list, tuple)) else str(ab)
            break
        rows.append(r)

    if not rows:
        raise SystemExit(f"no '{a.params_glob}' with a tz line under {a.root}")
    rows.sort(key=lambda r: r["scan"])
    # one row per scan; a re-run beside the original would otherwise double-count
    seen, uniq = set(), []
    for r in rows:
        if r["scan"] in seen:
            print(f"  duplicate scan {r['scan']} -- keeping first: {r['source']}",
                  file=sys.stderr)
            continue
        seen.add(r["scan"]); uniq.append(r)

    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(uniq[0]))
        w.writeheader(); w.writerows(uniq)
    print(f"wrote {a.out}: {len(uniq)} exposures, "
          f"calibrants {sorted({r['calibrant'] for r in uniq})}")

    if a.report:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from rotation_benchmark import robust_line
        for grp in sorted({r["calibrant"] for r in uniq}):
            sub = [r for r in uniq if r["calibrant"] == grp]
            if len(sub) < 3:
                continue
            xs = [r["scan"] for r in sub]; ys = [r["tz"] for r in sub]
            a0, b0 = robust_line(xs, ys)
            res = sorted(abs(y - (a0 + b0*x)) for x, y in zip(xs, ys))
            med = statistics.median(res)
            # a robust sigma: the MAD-equivalent, so the 8 outliers do not inflate it
            clean = [r for r in res if r < max(0.15, 6*med)]
            print(f"  {grp}: n={len(sub)} slope={b0:.4f} deg/scan  "
                  f"median|resid|={med:.4f} deg  "
                  f"clean scatter={statistics.pstdev(clean) if len(clean)>1 else float('nan'):.4f} deg  "
                  f"off-trend(>0.5 deg)={sum(1 for r in res if r > 0.5)}")
            if med > 0.10:
                print("  ⚠ median residual exceeds the benchmark tolerance — the line "
                      "is NOT a usable reference for this group; find the commanded "
                      "stage positions instead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
