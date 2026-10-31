#!/usr/bin/env python3
"""Headless runner for the `synthesis` MCP server (in-situ thermal-synthesis XRD).

Wraps the in-situ synthesis analysis methods of I. A. Shkrob (ANL CSE) so they run
on a whole temperature series without a GUI. Executed by `synthesis_server.py` under
**`APEXA_SYNTH_PYTHON`** (a Python 3.12 env with gemmi + lmfit + numpy>=2 + h5py +
pandas/tables + deap — deap only because `XRD_gemmi` imports it at module top).

WHAT IS HIS CODE AND WHAT IS OURS
---------------------------------
His code is unreleased and is **not vendored** here. The multiphase engine is his
`XRD_gemmi.Gemmi`, imported *unmodified* from the directory in `APEXA_SYNTH_CODE`
(it is the one module with no tkinter / GSAS-II / Windows imports at load time).

His other modules build `tk.Tk()` at import and pull GSAS-II through `gsas2_driver`,
so the few pieces needed from them are re-implemented here, each cited to its
source line so a reader can diff the two:

* processed-HDF5 reader  ← `XRD_postanalysis.read_processed_h5` (:186-273)
* PCA map                ← `XRD_postanalysis.massage_map` (:363-401)
* next-ΔT recommender    ← `XRD_control.active_control.control_step_box` (:185-229)
                           and `.replay` (:46-182)
* series fit recipe      ← `XRD_postanalysis.gemmi_RR_map` (:984-1074) +
                           `gsas2_driver.load_phases_gemmi` (:1459-1475)

The recommender port is *faithful by default* — it reproduces his `TC step` column
exactly, including the grid quirk at :202 (`n` is overwritten by the scan count).
Departures are opt-in and always reported in `fidelity_notes`.

CONTRACT
--------
One subcommand per tool; exactly ONE single-line JSON object on stdout (the server
parses the last `{` line); all chatter — including his module's verbose prints —
goes to stderr.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

_REAL_STDOUT = sys.stdout
R_GAS = 8.314462618  # J/(mol K)


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, np.floating):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.bool_):
            return bool(o)
        return str(o)


def _clean(o):
    """NaN/inf → None, recursively: JSON has no NaN and the UI must not see one."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, np.floating):
        return _clean(float(o))
    return o


def _output(data: Dict[str, Any]) -> None:
    _REAL_STDOUT.write(json.dumps(_clean(data), cls=_Enc, separators=(",", ":")) + "\n")
    _REAL_STDOUT.flush()


# ═════════════════════════════════════════════════════════════════════════════
# Pure pieces (numpy only) — unit-tested directly
# ═════════════════════════════════════════════════════════════════════════════
def smart_norm(v: np.ndarray) -> np.ndarray:
    """Divide by the largest-|v| element (XRD_postanalysis.smart_norm :288)."""
    v = np.asarray(v, dtype=float).copy()
    return v / v[np.nanargmax(np.abs(v))]


def svd_unit_scores(mp: np.ndarray, n: int = 2) -> np.ndarray:
    """XRD_control.active_control.svd (:39-44): U*S/S, i.e. the unit-norm U[:, :n]."""
    n = max(2, n)
    U, S, _ = np.linalg.svd(np.asarray(mp, dtype=float), full_matrices=False)
    return U[:, :n].copy()


def control_step_box(x: Sequence[float], scores: np.ndarray, nt: int, box: int = 5,
                     degree: int = 2, change: float = 5, dmin: float = 2,
                     dmax: float = 10, grid: Optional[int] = None):
    """Port of XRD_control.active_control.control_step_box (:185-229).

    Fit a degree-`degree` polynomial to the last `box` points of every PC trajectory
    (x shifted so the current point is 0), extrapolate forward over [0, dmax], and
    return the first step at which the mean |Δscore|/σ reaches `change` percent —
    i.e. *slow down where the pattern is about to change, stride where it is not*.

    `grid=None` is faithful: his :202 overwrites `n` with `len(scores)`, so the step
    is quantised to dmax/(len(scores)-1) — coarse early in a series. Pass an int to
    use a fixed grid instead (reported as a fidelity departure by the caller).
    """
    x = np.asarray(x, dtype=float)
    scores = np.asarray(scores, dtype=float)
    x0 = x[-1]
    dx = x[-box:] - x0
    degree = min(degree, box - 1)
    fit = np.polyfit(dx, scores[-box:], degree)          # (degree+1, n_pc)
    n = len(scores) if grid is None else int(grid)
    sigma = np.ptp(scores, axis=0) * np.sqrt(nt / len(scores))
    sigma[sigma == 0] = 1.0
    ds = np.linspace(0, dmax, n)
    ys = np.stack([np.polyval(fit[:, k], ds) for k in range(fit.shape[1])], axis=1)
    y0 = np.polyval(fit, 0)
    D = np.mean(np.abs((ys - y0) / sigma), axis=1)
    ind = np.flatnonzero(D >= 0.01 * change)
    d = dmax if len(ind) == 0 else ds[ind[0]]
    d = max(d, dmin)
    return float(d), np.polyval(fit, d), x0 + ds, ys, D, ds


def replay_control(X: Optional[np.ndarray], x: Sequence[float], *, box: int = 5,
                   change: float = 5, dmin: float = 2, dmax: float = 10,
                   grid: Optional[int] = None, scores_full: Optional[np.ndarray] = None,
                   n_pcs: int = 2) -> Dict[str, Any]:
    """Port of active_control.replay (:46-182) minus the movie.

    For every scan i ≥ box, recompute the PCs on scans 0..i only (what would have been
    known live) and ask control_step_box for the next step. With `scores_full` given
    instead of a map, the trajectories are taken as-is (e.g. fitted phase fractions).
    """
    x = np.asarray(x, dtype=float)
    nt = len(x) if X is None else np.asarray(X).shape[0]
    steps: List[Optional[float]] = [None] * min(box, nt)
    detail: Dict[str, Any] = {}
    if nt < box:
        return {"steps": steps, "nt": nt, "too_short": True}
    for i in range(box, nt):
        sc = (svd_unit_scores(X[: i + 1], n_pcs) if X is not None
              else np.asarray(scores_full, dtype=float)[: i + 1])
        d, y_new, xs, ys, D, ds = control_step_box(
            x[: i + 1], sc, nt, box=box, change=change, dmin=dmin, dmax=dmax, grid=grid)
        steps.append(d)
        if i == nt - 1:
            detail = {"x_now": float(x[i]), "x_next": float(x[i] + d),
                      "extrapolated_x": xs, "extrapolated_scores": ys,
                      "change_metric": D, "threshold": 0.01 * change,
                      "scores_partial": sc}
    return {"steps": steps, "nt": nt, "last": detail}


def quick_stats(d: np.ndarray, X: np.ndarray, *, prominence_sigma: float = 4.0,
                smooth_px: float = 2.0) -> Dict[str, Any]:
    """Model-free quick look (his stats_Sample*.jpg): integrated counts + peak count.

    Integral: Σy per scan (counts, axis-independent). Peak count: scipy find_peaks on
    a lightly smoothed trace with prominence ≥ `prominence_sigma` × a robust noise
    estimate (MAD of the first difference). His figure used the GSAS-II peak finder,
    which this does not reproduce exactly — the *trend* is the signal.
    """
    from scipy.ndimage import gaussian_filter1d
    from scipy.signal import find_peaks
    X = np.asarray(X, dtype=float)
    total = X.sum(axis=1)
    npk = []
    for y in X:
        ys = gaussian_filter1d(y, smooth_px) if smooth_px else y
        noise = 1.4826 * np.median(np.abs(np.diff(y) - np.median(np.diff(y)))) / math.sqrt(2)
        p, _ = find_peaks(ys, prominence=max(prominence_sigma * noise, 1e-12))
        npk.append(int(len(p)))
    return {"total_counts": total, "n_peaks": np.asarray(npk),
            "peak_method": f"scipy.find_peaks, prominence ≥ {prominence_sigma}·σ_noise "
                           f"(MAD of first difference), gaussian smooth {smooth_px} px — "
                           "APEXA's estimator, not his GSAS-II peak finder"}


def jmak_model(t: np.ndarray, T_K: np.ndarray, lnk_ref: float, Ea_kJ: float, n: float,
               T_ref: float) -> np.ndarray:
    """Remaining fraction under non-isothermal JMAK: α = exp(−Θⁿ), Θ(t) = ∫₀ᵗ k(T(t′)) dt′,
    k = k_ref·exp(−Ea/R·(1/T − 1/T_ref)).

    The rate is integrated along the measured T(t) — a ramp, not an isotherm — and the
    Arrhenius term is centred at T_ref (the window's mean T) so Ea and the prefactor are
    not near-perfectly correlated over a ~250 K window.
    """
    from scipy.integrate import cumulative_trapezoid
    k = np.exp(lnk_ref - Ea_kJ * 1e3 / R_GAS * (1.0 / T_K - 1.0 / T_ref))
    theta = cumulative_trapezoid(k, t, initial=0.0)
    return np.exp(-np.power(np.clip(theta, 0, None), n))


def jmak_fit(t: Sequence[float], T_C: Sequence[float], frac: Sequence[float], *,
             direction: str = "decay", n_fixed: Optional[float] = None,
             n_norm: int = 3) -> Dict[str, Any]:
    """Fit normalized conversion α to non-isothermal JMAK (replaces his Igor step).

    decay:  α = f / f₀ — a phase being consumed (his alpha1/alpha2).
    growth: α = 1 − f / f_end.
    f₀ (f_end) is the MEDIAN of the first (last) `n_norm` points, not the single edge
    scan: on Sample16 the first fitted scan is an outlier (NFPP 0.235 vs a 0.17
    plateau), and normalising by it alone distorts α. The exact Igor form behind his
    Ea/n is unknown, so the result is approximate and the caller must say so.
    """
    from scipy.optimize import curve_fit
    t = np.asarray(t, dtype=float)
    T_K = np.asarray(T_C, dtype=float) + 273.15
    f = np.asarray(frac, dtype=float)
    ok = np.isfinite(t) & np.isfinite(T_K) & np.isfinite(f)
    t, T_K, f = t[ok], T_K[ok], f[ok]
    if len(f) < 4:
        return {"status": "error", "error": f"need ≥4 finite points, have {len(f)}"}
    t = t - t[0]
    m = max(1, min(int(n_norm), len(f) // 2))
    if direction == "decay":
        ref = float(np.median(f[:m]))
        alpha = f / ref
    else:
        ref = float(np.median(f[-m:]))
        alpha = 1.0 - f / ref
    if not np.isfinite(ref) or ref <= 0:
        return {"status": "error", "error": "normalisation reference is not positive"}
    T_ref = float(np.mean(T_K))
    # seed k_ref from the half-conversion time: Θ(t½) = (ln 2)^(1/n)
    n0 = n_fixed or 2.0
    half = np.flatnonzero(alpha <= 0.5)
    t_half = t[half[0]] if len(half) else t[-1]
    lnk0 = math.log((math.log(2) ** (1 / n0)) / max(t_half, 1.0))
    X = np.vstack([t, T_K])
    if n_fixed is None:
        fun = lambda X, lnk, Ea, n: jmak_model(X[0], X[1], lnk, Ea, n, T_ref)
        p0, lo, hi = [lnk0, 30.0, n0], [-40, 0.0, 0.3], [5, 600, 8]
    else:
        fun = lambda X, lnk, Ea: jmak_model(X[0], X[1], lnk, Ea, n_fixed, T_ref)
        p0, lo, hi = [lnk0, 30.0], [-40, 0.0], [5, 600]
    try:
        popt, pcov = curve_fit(fun, X, alpha, p0=p0, bounds=(lo, hi), maxfev=50000)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"JMAK fit did not converge: {e}"}
    perr = (np.sqrt(np.clip(np.diag(pcov), 0, None)) if np.all(np.isfinite(pcov))
            else [None] * len(popt))
    fitc = fun(X, *popt)
    names = ["lnk_ref", "Ea_kJ_mol", "n"] if n_fixed is None else ["lnk_ref", "Ea_kJ_mol"]
    out: Dict[str, Any] = {k: float(v) for k, v in zip(names, popt)}
    out.update({f"{k}_err": (float(e) if e is not None else None) for k, e in zip(names, perr)})
    if n_fixed is not None:
        out["n"], out["n_fixed"] = float(n_fixed), True
    out["T_ref_C"] = T_ref - 273.15
    out["lnA"] = out["lnk_ref"] + out["Ea_kJ_mol"] * 1e3 / (R_GAS * T_ref)
    at_bound = [nm for nm, v, a, b in zip(names, popt, lo, hi)
                if min(v - a, b - v) < 1e-3 * (b - a)]
    out.update({"status": "success", "alpha": alpha, "alpha_fit": fitc,
                "normalisation": {"reference": ref, "points": m, "direction": direction},
                "rms_residual": float(np.sqrt(np.mean((alpha - fitc) ** 2))),
                "T_C": T_K - 273.15, "t_s": t, "params_at_bounds": at_bound})
    return out


def residual_metrics(X: np.ndarray, Yc: np.ndarray) -> Dict[str, Any]:
    """Per-scan transparency numbers: Rp = √(Σ(y-yc)²/Σy²) and RMS residual."""
    X, Yc = np.asarray(X, float), np.asarray(Yc, float)
    r = X - Yc
    rp = np.sqrt(np.sum(r ** 2, axis=1) / np.maximum(np.sum(X ** 2, axis=1), 1e-30))
    return {"Rp": rp, "rms": np.sqrt(np.mean(r ** 2, axis=1))}


# ═════════════════════════════════════════════════════════════════════════════
# I/O
# ═════════════════════════════════════════════════════════════════════════════
def read_processed_h5(path: str, d_min: float = 0.0, d_max: Optional[float] = None) -> Dict[str, Any]:
    """Read a processed_<sample>.h5 (schema written by his save_processed_h5).

    Root attrs = experiment params; root datasets `d`, `t2` (and `mask`); one group
    `scanN` per pattern with dataset `y`; pandas table at key `table` (has `TC`,
    `t`, ...). Same d-window clamp as his reader, *without* its `processed_` filename
    sys.exit, its per-scan CSV dump, or the dmin/dmax mix-up at :232.
    """
    import h5py
    import pandas as pd
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"no such HDF5 file: {p}")
    params: Dict[str, Any] = {}
    with h5py.File(p, "r") as g:
        for k, v in g.attrs.items():
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:  # noqa: BLE001
                    pass
            params[k] = v.item() if isinstance(v, np.generic) else v
        if "d" not in g:
            raise ValueError(f"{p.name} has no root dataset 'd' — not a processed series "
                             "HDF5 (expected the processed_<sample>.h5 layout)")
        d = g["d"][:]
        lo = max(float(d_min or 0), float(np.nanmin(d)))
        hi = min(float(d_max) if d_max else np.inf, float(np.nanmax(d)))
        keep = (d >= lo) & (d <= hi)
        t2 = g["t2"][:][keep] if "t2" in g else None
        rows, i = [], 0
        while f"scan{i}" in g:
            rows.append(g[f"scan{i}"]["y"][:][keep])
            i += 1
    if not rows:
        raise ValueError(f"{p.name} contains no scanN groups")
    try:
        table = pd.read_hdf(p, key="table")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not read the pandas 'table' key from {p.name}: {e}")
    X = np.vstack(rows).astype(float)
    if len(table) != X.shape[0]:
        raise ValueError(f"table has {len(table)} rows but {X.shape[0]} scans")
    name = p.stem[len("processed_"):] if p.stem.startswith("processed_") else p.stem
    return {"path": str(p), "name": name, "dir": str(p.parent), "params": params,
            "d": d[keep], "t2": t2, "X": X, "table": table.reset_index(drop=True),
            "d_window": [lo, hi]}


def _temperature(table) -> np.ndarray:
    for c in ("TC", "T", "temperature"):
        if c in table.columns:
            return table[c].to_numpy(dtype=float)
    raise ValueError("table has no temperature column (looked for TC, T, temperature)")


def _frames(n: int, begin: Optional[int], end: Optional[int]):
    b = 0 if begin is None else int(begin)
    e = n if end is None or int(end) > n else int(end)
    if not 0 <= b < e:
        raise ValueError(f"empty scan window begin={begin} end={end} for {n} scans")
    return b, e


def _out_dir(arg: Optional[str], src: Dict[str, Any], sub: str) -> Path:
    o = Path(arg).expanduser() if arg else Path(src["dir"]) / "apexa_synthesis" / sub
    o.mkdir(parents=True, exist_ok=True)
    return o


# ═════════════════════════════════════════════════════════════════════════════
# Subcommands
# ═════════════════════════════════════════════════════════════════════════════
def cmd_inspect(a) -> Dict[str, Any]:
    s = read_processed_h5(a.h5, a.d_min, a.d_max)
    T = _temperature(s["table"])
    st = quick_stats(s["d"], s["X"])
    out_dir = _out_dir(a.output_dir, s, "inspect")
    import pandas as pd
    df = pd.DataFrame({"scan": np.arange(len(T)), "TC": T,
                       "total_counts": st["total_counts"], "n_peaks": st["n_peaks"]})
    csv = out_dir / f"quicklook_{s['name']}.csv"
    df.to_csv(csv, index=False)
    keep_params = {k: v for k, v in s["params"].items()
                   if isinstance(v, (int, float, str)) and k not in ("dir",)}
    return {
        "status": "success", "tool": "synth_inspect_series", "sample": s["name"],
        "h5": s["path"], "n_scans": int(s["X"].shape[0]), "n_points": int(s["X"].shape[1]),
        "d_window_A": s["d_window"], "T_range_C": [float(np.nanmin(T)), float(np.nanmax(T))],
        "table_columns": list(map(str, s["table"].columns)), "params": keep_params,
        "TC": T, "total_counts": st["total_counts"], "n_peaks": st["n_peaks"],
        "peak_method": st["peak_method"], "quicklook_csv": str(csv),
        "title": f"{s['name']} — quick look vs T",
        "plotly": {"data": [
            {"type": "scatter", "mode": "lines", "name": "Σ counts", "x": T, "y": st["total_counts"]},
            {"type": "scatter", "mode": "markers", "name": "peaks", "x": T, "y": st["n_peaks"],
             "yaxis": "y2"}],
            "layout": {"xaxis": {"title": "T (°C)"}, "yaxis": {"title": "total counts"},
                       "yaxis2": {"title": "number of peaks", "overlaying": "y", "side": "right"}}},
        "note": "Model-free: a drop in counts with few peaks = amorphous/transforming; "
                "a rise in peak count = crystallization. Use it to choose the fit window "
                "(begin/end) before synth_fit_series.",
    }


def cmd_pca(a) -> Dict[str, Any]:
    s = read_processed_h5(a.h5, a.d_min, a.d_max)
    T = _temperature(s["table"])
    b, e = _frames(len(T), a.begin, a.end)
    X = s["X"][b:e]
    ymax = X.max()
    keep = X.max(axis=1) > a.accept * ymax       # massage_map :373-377
    U, S, VT = np.linalg.svd(X[keep], full_matrices=False)
    n = max(1, min(a.n_pcs, len(S)))
    scores = np.full((len(T), n), np.nan)
    frames = np.arange(b, e)[keep]
    sc = U[:, :n] * S[:n]
    for k in range(n):
        scores[frames, k] = smart_norm(sc[:, k])
    out_dir = _out_dir(a.output_dir, s, "pca")
    tab = s["table"].copy()
    for k in range(n):
        tab[f"PC{k+1}"] = scores[:, k]
    csv = out_dir / f"SVD_table_{s['name']}.csv"
    tab.to_csv(csv, index=False)
    ev = (S ** 2) / np.sum(S ** 2)
    return {
        "status": "success", "tool": "synth_pca_map", "sample": s["name"],
        "window": [b, e], "accepted_scans": int(keep.sum()), "rejected_scans": int((~keep).sum()),
        "explained_variance": ev[:n], "TC": T, "scores": scores.T, "svd_table_csv": str(csv),
        "title": f"{s['name']} — principal components vs T",
        "plotly": {"data": [{"type": "scatter", "mode": "lines+markers", "name": f"PC{k+1}",
                             "x": T, "y": scores[:, k]} for k in range(n)],
                   "layout": {"xaxis": {"title": "T (°C)"},
                              "yaxis": {"title": "normalized score"}}},
        "note": "Same normalisation as his massage_map (each PC divided by its largest-|value| "
                "score). A kink or sign change in PC2 marks where the pattern changes character.",
    }


def _cif_shim(cif_dir: Path, phases: List[str], tmp: Path) -> Dict[str, str]:
    """`Gemmi.load_phases` opens `<dir>/<phase>.CIF` (uppercase, flat). Resolve each
    phase case-insensitively and recursively under cif_dir; symlink it in."""
    found: Dict[str, str] = {}
    cands = [p for p in cif_dir.rglob("*") if p.suffix.lower() == ".cif" and p.is_file()]
    for ph in phases:
        hits = sorted([c for c in cands if c.stem.lower() == ph.lower()],
                      key=lambda c: (len(c.relative_to(cif_dir).parts), str(c)))
        if not hits:
            raise FileNotFoundError(
                f"no CIF for phase '{ph}' under {cif_dir} (searched recursively, "
                f"case-insensitive). Available: {sorted({c.stem for c in cands})[:30]}")
        (tmp / f"{ph}.CIF").symlink_to(hits[0].resolve())
        found[ph] = str(hits[0])
    return found


def _import_gemmi_engine():
    code = os.environ.get("APEXA_SYNTH_CODE", "")
    if not code or not (Path(code) / "XRD_gemmi.py").is_file():
        raise ImportError("APEXA_SYNTH_CODE must point at the directory holding the "
                          f"collaborator's XRD_gemmi.py (got {code!r})")
    sys.path.insert(0, code)
    import XRD_gemmi  # noqa: E402  — his module, unmodified
    return XRD_gemmi


def cmd_fit(a) -> Dict[str, Any]:
    import copy
    import pandas as pd
    s = read_processed_h5(a.h5, a.d_min, a.d_max)
    T = _temperature(s["table"])
    b, e = _frames(len(T), a.begin, a.end)
    phases = [p.strip() for p in a.phases.split(",") if p.strip()]
    abc = [float(v) for v in a.abc_scale.split(",")] if a.abc_scale else None
    if abc is not None and len(abc) != len(phases):
        raise ValueError(f"abc_scale has {len(abc)} values for {len(phases)} phases")
    cif_dir = Path(a.cif_dir).expanduser() if a.cif_dir else Path(s["dir"])
    out_dir = _out_dir(a.output_dir, s, "fit")
    G = _import_gemmi_engine()

    with tempfile.TemporaryDirectory(prefix="apexa_synth_") as td:
        tmp = Path(td)
        cif_map = _cif_shim(cif_dir, phases, tmp)
        cstem, cinfo = None, None
        if a.constraints:
            cp = Path(a.constraints).expanduser()
            if not cp.is_file():
                cp = cif_dir / (a.constraints if a.constraints.endswith(".json") else a.constraints + ".json")
            if not cp.is_file():
                raise FileNotFoundError(f"constraints JSON not found: {a.constraints}")
            cstem = "apexa_constraints"
            (tmp / f"{cstem}.json").symlink_to(cp.resolve())
            cinfo = {"file": str(cp), "phases": [v for k, v in json.load(open(cp)).items()
                                                  if str(k).startswith("Phase-")]}

        gm = G.Gemmi()                              # load_phases_gemmi :1467-1475
        gm.dir, gm.verbose = str(tmp), 1
        gm.sample = s["name"]
        gm.l = float(s["params"].get("lambda", gm.l))
        gm.trace = {"d": s["d"], "t2": s["t2"], "y": None}
        if not gm.load_phases(phases, abc_scale=abc):
            raise RuntimeError("Gemmi.load_phases failed (see stderr)")
        if len(gm.phases) != len(phases):
            dropped = sorted(set(phases) - set(gm.phases))
            raise RuntimeError(f"phases {dropped} have no reflections in the d-window "
                               f"{s['d_window']} Å — widen d_min/d_max or drop them")
        seed_cells = copy.deepcopy(gm.cs)

        Xw = s["X"][b:e]                             # gemmi_RR_map :1008-1016
        _, _, VT = np.linalg.svd(Xw, full_matrices=False)
        gm.trace["y"] = np.abs(VT[0, :])
        seed = gm.fit_pattern_LM(keep=False, SD=a.sd, sigma=a.sigma)
        pc1_cells = copy.deepcopy(gm.cs)
        gm.cs_ = copy.deepcopy(gm.cs)

        r, rt = gm.fit_map_LM(Xw, T[b:e], eps=a.eps, constraints=cstem,
                              sigma=a.sigma, SD=a.sd)
        Yc = np.asarray(gm.map["ycalc"])
        comps = np.asarray(gm.map["phases"])
        scales = np.asarray(gm.map["scales"])
        pinfo = list(gm.pinfo)
        lattice = list(gm.lattice)
        frozen = []
        if cstem:
            C = json.load(open(tmp / f"{cstem}.json"))
            names = [v for k, v in C.items() if str(k).startswith("Phase-")]
            for i, ph in enumerate(gm.phases):
                if ph in names:
                    frozen.append(ph)

    # ── transparency: what the fit actually did ────────────────────────────
    rm = residual_metrics(Xw, Yc)
    at_bounds = []
    for i, ph in enumerate(phases):
        for k in lattice:
            key0 = f"{k}{i+1}_0"
            if key0 not in r:
                continue
            u = pc1_cells[i][k]
            lo, hi = (1 - a.eps) * u, (1 + a.eps) * u
            v = r[key0]
            if ph not in frozen and min(v - lo, hi - v) < 1e-3 * (hi - lo):
                at_bounds.append({"name": key0, "phase": ph, "value": v, "lo": lo, "hi": hi})
        sg = r.get(f"sigma{i+1}")
        if sg is not None and min(sg - a.sigma / 10, a.sigma * 10 - sg) < 1e-3 * a.sigma * 9.9:
            at_bounds.append({"name": f"sigma{i+1}", "phase": ph, "value": sg,
                              "lo": a.sigma / 10, "hi": a.sigma * 10})
    absent = {ph: [int(b + j) for j in np.flatnonzero(scales[:, i] <= 0)]
              for i, ph in enumerate(phases)}

    tab = s["table"].copy()
    for j, q in enumerate(rt):
        for k, v in q.items():
            if k == "TC":
                continue
            tab.loc[b + j, k] = v
    tab.loc[b:e - 1, "Rp"] = rm["Rp"]
    tab_csv = out_dir / f"fit_series_table_{s['name']}.csv"
    tab.to_csv(tab_csv, index=False)
    js = out_dir / f"fit_series_{s['name']}.json"
    json.dump(_clean(r), open(js, "w"), indent=2)
    npz = out_dir / f"fit_arrays_{s['name']}.npz"
    np.savez_compressed(npz, d=s["d"], X=Xw, ycalc=Yc, phases=comps, scales=scales,
                        TC=T[b:e], scan=np.arange(b, e), phase_names=np.array(phases))

    weights = {ph: [q.get(f"weight{i+1}") for q in rt] for i, ph in enumerate(phases)}
    worst = int(b + np.argmax(rm["Rp"]))
    manifest = {
        "status": "success", "tool": "synth_fit_series", "sample": s["name"],
        "h5": s["path"], "engine": "XRD_gemmi.Gemmi.fit_map_LM (I. A. Shkrob, unmodified)",
        "engine_path": str(Path(G.__file__).resolve()),
        "recipe": {"phases": phases, "abc_scale_permil": abc, "d_window_A": s["d_window"],
                   "window": [b, e], "sigma_A": a.sigma, "SD": a.sd, "eps": a.eps,
                   "constraints": cinfo, "wavelength_A": gm.l, "cif_files": cif_map},
        "model": "lattice p = p0 + p1·z + p2·z², z = T/<T> − 1; peak width shared per phase; "
                 "per-scan phase scales by NNLS; volume fraction = scale·V / Σ scale·V",
        "reduced_chi2": r.get("reduced chi^2"), "T_mean_C": r.get("<T>, C"),
        "global_params": r, "TC": T[b:e], "scans": list(range(b, e)), "weights": weights,
        "transparency": {
            "pc1_seed_fit": {"reduced_chi2": seed.get("reduced chi^2"),
                             "weights": {ph: seed.get(f"weight{i+1}") for i, ph in enumerate(phases)},
                             "note": "Cells seeded by fitting |VT[0]| (the first SVD loading) "
                                     "before the global map fit, as in his gemmi_RR_map."},
            "seed_cells_after_abc_scale": seed_cells, "cells_after_pc1_seed": pc1_cells,
            "per_scan_Rp": rm["Rp"], "per_scan_rms": rm["rms"], "worst_scan": worst,
            "params_at_bounds": at_bounds,
            "constraint_frozen_phases": frozen,
            "phase_zero_scale_scans": {k: v for k, v in absent.items() if v},
        },
        "outputs": {"table_csv": str(tab_csv), "global_json": str(js), "arrays_npz": str(npz)},
        "title": f"{s['name']} — phase fractions vs T (gemmi map fit)",
        "plotly": {"data": [{"type": "scatter", "mode": "lines+markers", "name": ph,
                             "x": T[b:e], "y": weights[ph]} for ph in phases],
                   "layout": {"xaxis": {"title": "T (°C)"},
                              "yaxis": {"title": "volume fraction", "range": [0, 1.05]}}},
    }
    json.dump(_clean(manifest), open(out_dir / "apexa_synthesis_manifest.json", "w"), indent=1)
    manifest["outputs"]["manifest"] = str(out_dir / "apexa_synthesis_manifest.json")
    return manifest


def _load_table(path: str):
    import pandas as pd
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"no such table: {p}")
    return pd.read_csv(p)


def cmd_recommend(a) -> Dict[str, Any]:
    notes = []
    if a.grid:
        notes.append(f"fixed step grid of {a.grid} points (his code quantises to "
                     "dmax/(n_scans-1) — XRD_control.py:202)")
    if a.signal == "pca":
        s = read_processed_h5(a.h5, a.d_min, a.d_max)
        T = _temperature(s["table"])
        b, e = _frames(len(T), a.begin, a.end)
        xs = T[b:e]
        res = replay_control(s["X"][b:e], xs, box=a.box, change=a.change,
                             dmin=a.dmin, dmax=a.dmax, grid=a.grid, n_pcs=2)
        src = {"h5": s["path"], "d_window_A": s["d_window"], "window": [b, e]}
        sample = s["name"]
    else:
        tab = _load_table(a.table)
        cols = [c for c in tab.columns if c.startswith("weight")]
        if not cols:
            raise ValueError("signal='fractions' needs weightN columns (a synth_fit_series table)")
        sub = tab.dropna(subset=cols)
        xs = sub["TC"].to_numpy(float)
        res = replay_control(None, xs, box=a.box, change=a.change, dmin=a.dmin,
                             dmax=a.dmax, grid=a.grid, scores_full=sub[cols].to_numpy(float))
        notes.append("signal='fractions' is an APEXA extension: his control step runs on "
                     "SVD scores; here it runs on fitted phase fractions directly")
        src = {"table": a.table, "columns": cols}
        sample = Path(a.table).stem
    if res.get("too_short"):
        return {"status": "error", "tool": "synth_recommend_step",
                "error": f"need at least box={a.box} scans, have {res['nt']}"}
    last = res["last"]
    rec = res["steps"][-1]
    near_min = rec <= a.dmin + 1e-9
    near_max = rec >= a.dmax - 1e-9
    why = ("pattern is changing fast — hold small steps" if near_min else
           "pattern is quiet over the look-ahead — stride at the maximum step" if near_max else
           "change expected within the look-ahead — moderate step")
    out = {
        "status": "success", "tool": "synth_recommend_step", "mode": "advisory",
        "actuated": False, "sample": sample, "signal": a.signal, "source": src,
        "params": {"box": a.box, "change_percent": a.change, "dmin": a.dmin, "dmax": a.dmax,
                   "degree": 2, "grid": a.grid or "faithful (n = scans so far)"},
        "current_T_C": last["x_now"], "recommended_dT_C": rec,
        "recommended_next_T_C": last["x_next"], "rationale": why,
        "method": "Shkrob control_step_box: quadratic extrapolation of the last `box` PC "
                  "trajectories; step = first look-ahead ΔT where mean |Δscore|/σ ≥ change%; "
                  "clamped to [dmin, dmax].",
        "fidelity_notes": notes or ["faithful port — reproduces his SVD_control_table 'TC step'"],
        "note": "Advisory only — nothing was actuated. APEXA has no furnace/gas channel; the "
                "operator applies the step.",
    }
    if a.replay:
        out["replay"] = {"TC": xs, "steps": res["steps"]}
        out["title"] = f"{sample} — recommended ΔT replay"
        out["plotly"] = {"data": [{"type": "scatter", "mode": "markers", "name": "suggested ΔT",
                                   "x": xs, "y": res["steps"]}],
                         "layout": {"xaxis": {"title": "T (°C)"},
                                    "yaxis": {"title": "suggested ΔT (°C)",
                                              "range": [0, a.dmax * 1.05]}}}
        out["replay_csv"] = None
        if a.output_dir:
            import pandas as pd
            o = Path(a.output_dir).expanduser()
            o.mkdir(parents=True, exist_ok=True)
            f = o / f"control_replay_{sample}.csv"
            pd.DataFrame({"TC": xs, "TC_step": res["steps"]}).to_csv(f, index=False)
            out["replay_csv"] = str(f)
    return out


def cmd_kinetics(a) -> Dict[str, Any]:
    tab = _load_table(a.table)
    col = a.phase_col
    if col not in tab.columns:
        # allow a phase *name*: map via formulaN? the fit manifest names phases by order
        raise ValueError(f"column {col!r} not in table; weight columns: "
                         f"{[c for c in tab.columns if c.startswith('weight')]}")
    sub = tab.dropna(subset=[col])
    if a.T_col not in sub.columns or a.t_col not in sub.columns:
        raise ValueError(f"table needs '{a.T_col}' and '{a.t_col}' columns")
    res = jmak_fit(sub[a.t_col], sub[a.T_col], sub[col], direction=a.direction,
                   n_fixed=a.n_fixed)
    res.update({"tool": "synth_fit_kinetics", "table": a.table, "phase_col": col,
                "direction": a.direction,
                "formula": "α = exp(−Θⁿ), Θ = ∫k(T(t))dt along the measured ramp, "
                           "k = k_ref·exp(−Ea/R·(1/T − 1/T_ref)) (non-isothermal JMAK). "
                           "APPROXIMATE — confirm against the Igor form in crystals.pxp "
                           "before comparing Ea/n."})
    if res.get("status") == "success":
        res["title"] = f"JMAK fit — {col}"
        res["plotly"] = {"data": [
            {"type": "scatter", "mode": "markers", "name": "α (data)", "x": res["T_C"], "y": res["alpha"]},
            {"type": "scatter", "mode": "lines", "name": "JMAK fit", "x": res["T_C"], "y": res["alpha_fit"]}],
            "layout": {"xaxis": {"title": "T (°C)"}, "yaxis": {"title": "normalized fraction α"}}}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)

    def common(p, h5=True):
        if h5:
            p.add_argument("--h5", required=True)
            p.add_argument("--d-min", type=float, default=0.0)
            p.add_argument("--d-max", type=float, default=None)
        p.add_argument("--begin", type=int, default=None)
        p.add_argument("--end", type=int, default=None)
        p.add_argument("--output-dir", default=None)

    p = sp.add_parser("inspect"); common(p)
    p = sp.add_parser("pca"); common(p)
    p.add_argument("--n-pcs", type=int, default=3)
    p.add_argument("--accept", type=float, default=0.0)
    p = sp.add_parser("fit"); common(p)
    p.add_argument("--phases", required=True)
    p.add_argument("--abc-scale", default=None)
    p.add_argument("--cif-dir", default=None)
    p.add_argument("--sigma", type=float, default=0.01)
    p.add_argument("--sd", action="store_true")
    p.add_argument("--eps", type=float, default=0.05)
    p.add_argument("--constraints", default=None)
    p = sp.add_parser("recommend"); common(p, h5=False)
    p.add_argument("--signal", choices=["pca", "fractions"], default="pca")
    p.add_argument("--h5", default=None)
    p.add_argument("--table", default=None)
    p.add_argument("--d-min", type=float, default=2.3)
    p.add_argument("--d-max", type=float, default=None)
    p.add_argument("--box", type=int, default=5)
    p.add_argument("--change", type=float, default=2.0)
    p.add_argument("--dmin", type=float, default=2.0)
    p.add_argument("--dmax", type=float, default=20.0)
    p.add_argument("--grid", type=int, default=None)
    p.add_argument("--replay", action="store_true")
    p = sp.add_parser("kinetics")
    p.add_argument("--table", required=True)
    p.add_argument("--phase-col", required=True)
    p.add_argument("--direction", choices=["decay", "growth"], default="decay")
    p.add_argument("--n-fixed", type=float, default=None)
    p.add_argument("--T-col", default="TC")
    p.add_argument("--t-col", default="t")

    a = ap.parse_args(argv)
    sys.stdout = sys.stderr                      # his verbose prints must not corrupt JSON
    try:
        if a.cmd == "recommend":
            if a.signal == "pca" and not a.h5:
                raise ValueError("signal='pca' needs --h5")
            if a.signal == "fractions" and not a.table:
                raise ValueError("signal='fractions' needs --table")
        fn = {"inspect": cmd_inspect, "pca": cmd_pca, "fit": cmd_fit,
              "recommend": cmd_recommend, "kinetics": cmd_kinetics}[a.cmd]
        _output(fn(a))
        return 0
    except ImportError as e:
        _output({"status": "error", "error": str(e), "package_import_error": True})
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        _output({"status": "error", "error": f"{type(e).__name__}: {e}"})
    return 1


if __name__ == "__main__":
    sys.exit(main())
