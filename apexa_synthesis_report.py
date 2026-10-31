"""Refinement-visualization report for an in-situ synthesis series fit.

`synth_fit_series` leaves a manifest plus the full observed/calculated/per-phase arrays
(`fit_arrays_<sample>.npz`). This module turns them into APEXA's shared report format
(`apexa_report.ReportSpec`) — one offline HTML page, and the same spec live in the UI.

The point is the collaborator's objection to GSAS-II: a fit you cannot see into is a
fit you cannot trust in an automated loop. So the report leads with *what the fit did*
— per-scan residuals, parameters pinned to a bound, phases that dropped to zero,
lattices frozen by constraints — and only then with the fractions. A converged global
fit with one bad scan or a railed lattice renders LOOK, not PASS.

Findings are derived from the recorded arrays, never hardcoded.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

MANIFEST = "apexa_synthesis_manifest.json"
# Poor-fit floor on the MEDIAN scan's Rp, set from measured fits rather than taste. The
# engine has no background term, so even good fits of EDXRD leave a lot unexplained:
# on Sample16 the collaborator's own reference fits sit at median Rp 0.42-0.47, while a
# wrong d-window converged to 0.82 in EVERY scan (so no scan was a relative outlier and
# the 2x-median test alone stayed silent). 0.60 separates the two with margin.
RP_POOR = 0.60
# Refined lattice parameters should move like thermal expansion. Phosphates/oxides sit
# at ~0.5-2e-5 /K (Sample16's two constraint-frozen phases: 0.11-0.46% over 235 K,
# i.e. ~1-2e-5 /K). An implied linear coefficient above 5e-5 /K is not thermal
# expansion: the lattice is either poorly determined by the data or the phase's
# composition is changing. On Sample16, NFPP `a` spans 2.3-3.4% (~1e-4 /K) in BOTH
# the reference and the APEXA fit.
CTE_MAX = 5e-5


def lattice_expansion(table_csv: str, phases, frozen, scans) -> list:
    """Per refined lattice parameter: span over the window and implied linear CTE."""
    import csv
    p = Path(table_csv)
    if not p.is_file():
        return []
    rows = [r for r in csv.DictReader(open(p)) if r.get("weight1") not in (None, "")]
    if len(rows) < 2:
        return []
    T = np.asarray([float(r["TC"]) for r in rows])
    dT = float(T.max() - T.min()) or 1.0
    out = []
    for i, ph in enumerate(phases):
        if ph in frozen:
            continue
        for k in "abc":
            col = f"{k}{i+1}"
            vals = [r.get(col) for r in rows]
            if any(v in (None, "") for v in vals):
                continue
            v = np.asarray(vals, float)
            span = float((v.max() - v.min()) / v.mean())
            out.append({"param": col, "phase": ph, "span_percent": 100 * span,
                        "cte_per_K": span / dT, "exceeds": span / dT > CTE_MAX})
    return out
_PHASE_COLORS = ["#0e7c86", "#c07a3e", "#5b4a9e", "#3d8c40", "#b03a48", "#6b6862"]


def _load(fit_dir: str) -> Dict[str, Any]:
    d = Path(fit_dir).expanduser()
    m = d / MANIFEST if d.is_dir() else d
    if not m.is_file():
        raise FileNotFoundError(f"no {MANIFEST} in {d} — run synth_fit_series first")
    man = json.loads(m.read_text())
    man["_dir"] = str(m.parent)
    return man


def _read_json(p: Optional[str]) -> Optional[Dict[str, Any]]:
    if not p:
        return None
    path = Path(p).expanduser()
    if not path.is_file():
        return None
    try:
        out = json.loads(path.read_text())
        return out if isinstance(out, dict) and out.get("status") == "success" else None
    except Exception:  # noqa: BLE001
        return None


def _interior_dropouts(scans, zero_scans):
    """Zero-scale scans strictly inside the window: a phase vanishing then returning,
    or vanishing mid-series, is a modelling signal; absent at an end is often physics."""
    if not zero_scans:
        return []
    lo, hi = min(scans), max(scans)
    return [s for s in zero_scans if lo < s < hi]


def findings(man: Dict[str, Any]):
    """(verdict, note, findings, actions) — pure; derived from the manifest."""
    from apexa_report import Finding, Action
    tr = man.get("transparency") or {}
    rp = np.asarray([v for v in (tr.get("per_scan_Rp") or []) if v is not None], float)
    scans = man.get("scans") or []
    recipe = man.get("recipe") or {}
    phases = recipe.get("phases") or []
    F, A, look = [], [], []

    if rp.size:
        med = float(np.median(rp))
        worst_i = int(np.argmax(rp))
        outl = [int(scans[i]) for i in np.flatnonzero(rp > 2 * med)] if med > 0 else []
        body = (f"Per-scan Rp = √(Σ(y−y_calc)²/Σy²): median {100*med:.1f}%, worst "
                f"{100*rp.max():.1f}% (scan {scans[worst_i]}, T = "
                f"{man['TC'][worst_i]:.0f} °C). Global reduced χ² = "
                f"{man.get('reduced_chi2', float('nan')):.4g} (unweighted counts, so compare "
                "between fits of this series, not in absolute terms).")
        if outl:
            look.append("residual outliers")
            body += (f" Scans {outl} exceed 2× the median Rp — the global T-quadratic model "
                     "does not describe them; look at their residual rows in the heat map.")
        poor = med > RP_POOR
        if poor:
            look.append(f"poor fit in every scan (median Rp {100*med:.0f}%)")
            body += (" The median scan is above the poor-fit floor, so this is a wrong basin "
                     "or a wrong model, not a few bad frames — check the d-window, the phase "
                     "list and whether peak widths collapsed.")
            A.append(Action("Re-check the d-window and phase list",
                            "Compare d_min/d_max with the window the reference analysis used; "
                            "re-run synth_fit_series and confirm the median Rp drops.",
                            "a uniformly poor fit is usually the window or a missing phase"))
        F.append(Finding("Fit quality across the series", body,
                         "warn" if (outl or poor) else "solid", "residual"))

    ab = tr.get("params_at_bounds") or []
    if ab:
        look.append("parameters on a bound")
        names = ", ".join(f"{b['name']} ({b['phase']}) = {b['value']:.4g} ∈ "
                          f"[{b['lo']:.4g}, {b['hi']:.4g}]" for b in ab)
        F.append(Finding("Parameters pinned to their search bound", names + ". A railed "
                         "parameter and a converged one give the same residual, so the "
                         "fraction from this fit is conditional on the bound.", "warn", "bounds"))
        if any(b["name"].startswith("sigma") for b in ab):
            A.append(Action("Widen the peak-width start", "Re-run synth_fit_series with a "
                            "different `sigma` (the bound is σ/10…10σ) or with "
                            "d_dependent_width=True.", "sigma hit its limit"))
        if any(not b["name"].startswith("sigma") for b in ab):
            A.append(Action("Re-seed the cell", "Adjust abc_scale_permil for the railed phase "
                            "(thermal expansion head start) or raise lattice_window.",
                            "p0 sits on the ±lattice_window wall around the seeded cell"))

    zero = tr.get("phase_zero_scale_scans") or {}
    for ph, zs in zero.items():
        inner = _interior_dropouts(scans, zs)
        if inner:
            look.append(f"{ph} vanishes mid-series")
            F.append(Finding(f"{ph} drops to zero inside the window",
                             f"NNLS gave {ph} zero scale at scans {inner}. Either it is truly "
                             "absent there, or another phase is absorbing its peaks.",
                             "warn", "phases"))
            A.append(Action(f"Test the phase list without/with {ph}",
                            "Re-fit with the phase removed and compare per-scan Rp.",
                            "a phase that blinks on and off is usually a correlation"))
        elif zs:
            F.append(Finding(f"{ph} absent at the window edge",
                             f"Zero scale at scans {zs} (an end of the window) — consistent with "
                             "the phase forming or being consumed there.", "solid", "phases"))

    frozen = tr.get("constraint_frozen_phases") or []
    exp = lattice_expansion((man.get("outputs") or {}).get("table_csv", ""), phases,
                            frozen, scans)
    bad = [e for e in exp if e["exceeds"]]
    if bad:
        look.append("lattice moves faster than thermal expansion")
        F.append(Finding(
            "Refined lattice changes too much to be thermal expansion",
            "; ".join(f"{e['param']} ({e['phase']}) spans {e['span_percent']:.2f}% "
                      f"(≈{e['cte_per_K']:.1e} /K)" for e in bad)
            + f". Typical phosphate/oxide expansion is 0.5–2×10⁻⁵ /K; above "
              f"{CTE_MAX:.0e} /K the cell is either poorly determined by these "
              "reflections (the p1/p2 terms absorb misfit) or the phase's composition is "
              "changing. The fractions inherit this ambiguity.", "warn", "lattice"))
        A.append(Action("Pin or constrain the suspect lattice",
                        "Re-fit with a constraints JSON for that phase (e.g. from a cooling "
                        "run), or a smaller lattice_window, and compare χ² and fractions. If "
                        "χ² barely changes, the data do not determine that cell.",
                        "distinguishes a degenerate cell from real chemistry"))
    if frozen:
        c = (recipe.get("constraints") or {}).get("file", "")
        F.append(Finding("Lattices fixed by constraints",
                         f"{', '.join(frozen)}: thermal-lattice coefficients taken from "
                         f"{Path(c).name or 'the constraints file'}, renormalised to "
                         f"<T> = {man.get('T_mean_C', float('nan')):.1f} °C and NOT refined.",
                         "solid", "constraints"))

    seed = tr.get("pc1_seed_fit") or {}
    if seed:
        w = ", ".join(f"{k} {v:.2f}" for k, v in (seed.get("weights") or {}).items()
                      if isinstance(v, (int, float)))
        F.append(Finding("Starting cells", f"Seeded by fitting the first SVD loading "
                         f"(reduced χ² {seed.get('reduced_chi2', float('nan')):.4g}; PC1 "
                         f"fractions {w}). abc_scale = {recipe.get('abc_scale_permil')} ‰.",
                         "solid", "seed"))

    F.append(Finding("Model and scope", f"{man.get('model', '')}. Fractions are of the "
                     "modelled crystalline phases only — amorphous content and unlisted "
                     "phases are invisible to this fit. Engine: "
                     f"{man.get('engine', '')}.", "warn", "scope"))
    verdict = "look" if look else "pass"
    note = ("Converged, but: " + "; ".join(look) + ".") if look else (
        f"Global fit of {len(scans)} scans, {len(phases)} phases; no railed parameters, "
        "no mid-series dropouts, no residual outliers.")
    return verdict, note, F, A


# ── plates ───────────────────────────────────────────────────────────────────
def _plates(man: Dict[str, Any], rec: Optional[dict], kin: Optional[dict]):
    from apexa_report import Plate, _mpl, _png, INK, MUTED
    out = []
    npz = (man.get("outputs") or {}).get("arrays_npz")
    if not npz or not Path(npz).is_file():
        return out
    z = np.load(npz, allow_pickle=False)
    d, X, Yc, P, T = z["d"], z["X"], z["ycalc"], z["phases"], z["TC"]
    names = [str(n) for n in z["phase_names"]]
    scans = z["scan"]
    rp = np.asarray((man.get("transparency") or {}).get("per_scan_Rp") or [], float)
    plt = _mpl()

    # 1 — observed / calculated / components / residual for four scans
    picks = []
    for i in (0, len(T) // 2, len(T) - 1, int(np.nanargmax(rp)) if rp.size else 0):
        if i not in picks:
            picks.append(i)
    fig, axes = plt.subplots(len(picks), 1, figsize=(9.5, 2.5 * len(picks)), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, i in zip(axes, picks):
        off = -0.12 * X[i].max()
        ax.plot(d, X[i], color=INK, lw=0.9, label="observed")
        ax.plot(d, Yc[i], color="#d4572a", lw=1.1, label="calculated")
        for j, n in enumerate(names):
            if np.any(P[i, j] > 0):
                ax.plot(d, P[i, j], lw=0.8, color=_PHASE_COLORS[j % 6], alpha=0.9, label=n)
        ax.plot(d, X[i] - Yc[i] + off, color=MUTED, lw=0.6, label="residual (offset)")
        tag = f"scan {scans[i]} · T = {T[i]:.0f} °C" + (f" · Rp {100*rp[i]:.1f}%" if rp.size else "")
        if rp.size and i == int(np.nanargmax(rp)):
            tag += " · worst"
        ax.set_title(tag, fontsize=9, loc="left")
        ax.set_ylabel("counts")
    axes[0].legend(fontsize=7, ncol=3, frameon=False)
    axes[-1].set_xlabel("d (Å)")
    out.append(Plate("synth_fits", "Observed vs calculated, by phase",
                     _png(fig), "First, middle, last and worst-Rp scans. The residual is the "
                     "honest part: structure in it is model error, not noise."))

    # 2 — residual heat map across the whole series
    R = X - Yc
    lim = np.nanpercentile(np.abs(R), 99) or 1.0
    fig, ax = plt.subplots(figsize=(9.5, 3.6))
    im = ax.pcolormesh(d, T, R, cmap="RdBu_r", vmin=-lim, vmax=lim, shading="nearest")
    ax.set_xlabel("d (Å)"); ax.set_ylabel("T (°C)")
    fig.colorbar(im, ax=ax, label="obs − calc (counts)")
    out.append(Plate("synth_residual_map", "Residual map — every scan, every d",
                     _png(fig), "Vertical streaks = a reflection the model misplaces at all T "
                     "(cell or missing phase); horizontal bands = scans the T-quadratic "
                     "model does not describe."))

    # 3 — fractions vs T (+ JMAK overlay)
    w = man.get("weights") or {}
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for j, n in enumerate(names):
        y = np.asarray([np.nan if v is None else v for v in w.get(n, [])], float)
        if y.size == len(T):
            ax.plot(T, y, "o-", ms=3.5, lw=1.6, color=_PHASE_COLORS[j % 6], label=n)
    if kin and kin.get("alpha_fit"):
        ax2 = ax.twinx()
        ax2.plot(kin["T_C"], kin["alpha"], "s", ms=3, color=MUTED, alpha=0.7,
                 label=f"α ({kin.get('phase_col')})")
        ax2.plot(kin["T_C"], kin["alpha_fit"], "--", color=MUTED,
                 label=f"JMAK Ea={kin.get('Ea_kJ_mol', float('nan')):.1f} kJ/mol, "
                       f"n={kin.get('n', float('nan')):.2g}")
        ax2.set_ylabel("normalized α"); ax2.set_ylim(0, 1.1)
        ax2.legend(fontsize=7, loc="center right", frameon=False)
    ax.set_xlabel("T (°C)"); ax.set_ylabel("volume fraction"); ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8, frameon=False)
    out.append(Plate("synth_fractions", "Phase fractions vs temperature",
                     _png(fig), "Volume fractions from the per-scan NNLS scales."))

    # 4 — cell volume vs T (normalised), the thermal model made visible
    tab_csv = (man.get("outputs") or {}).get("table_csv")
    if tab_csv and Path(tab_csv).is_file():
        import csv
        rows = list(csv.DictReader(open(tab_csv)))
        fig, ax = plt.subplots(figsize=(7.2, 3.6))
        for j, n in enumerate(names):
            col = f"volume{j+1}"
            pts = [(float(r["TC"]), float(r[col])) for r in rows if r.get(col) not in (None, "")]
            if pts:
                tt, vv = map(np.asarray, zip(*pts))
                ax.plot(tt, 100 * (vv / vv[0] - 1), "o-", ms=3, color=_PHASE_COLORS[j % 6], label=n)
        ax.set_xlabel("T (°C)"); ax.set_ylabel("ΔV/V₀ (%)")
        ax.legend(fontsize=8, frameon=False)
        out.append(Plate("synth_volume", "Unit-cell volume vs T",
                         _png(fig), "From the fitted p0 + p1·z + p2·z² lattice model. A flat "
                         "line for a constrained phase is expected (its lattice is frozen)."))

    # 5 — the recommender
    if rec and rec.get("replay"):
        rt = np.asarray(rec["replay"]["TC"], float)
        st = np.asarray([np.nan if v is None else v for v in rec["replay"]["steps"]], float)
        fig, ax = plt.subplots(figsize=(7.2, 3.4))
        ax.plot(rt, st, "^", color="#c07a3e", ms=5)
        ax.axhline(rec["params"]["dmin"], color=MUTED, lw=0.6, ls=":")
        ax.axhline(rec["params"]["dmax"], color=MUTED, lw=0.6, ls=":")
        ax.set_xlabel("T (°C)"); ax.set_ylabel("suggested ΔT (°C)")
        ax.set_title(f"next: {rec['current_T_C']:.0f} → {rec['recommended_next_T_C']:.0f} °C "
                     f"(ΔT = {rec['recommended_dT_C']:.1f})", fontsize=9, loc="left")
        out.append(Plate("synth_control", "Recommended temperature step (replay)",
                         _png(fig), "Advisory. Low = the pattern is about to change, slow the "
                         "ramp; high = quiet, stride. Nothing was actuated."))
    return out


def export_figure(man: Dict[str, Any], out_base: Path) -> Dict[str, str]:
    """Standalone fractions-vs-T figure as PDF + PNG(300 dpi) + editable SVG."""
    import logging
    # PDF font subsetting logs every glyph at INFO through whatever root handler the
    # host configured (the MCP server installs one) — pure noise on the server's stderr.
    logging.getLogger("fontTools").setLevel(logging.WARNING)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with matplotlib.rc_context({"svg.fonttype": "none", "pdf.fonttype": 42,
                                "font.family": "sans-serif",
                                "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
                                "axes.spines.top": False, "axes.spines.right": False}):
        fig, ax = plt.subplots(figsize=(4.5, 3.4))
        T = np.asarray(man["TC"], float)
        for j, (n, y) in enumerate((man.get("weights") or {}).items()):
            y = np.asarray([np.nan if v is None else v for v in y], float)
            ax.plot(T, y, "o-", ms=3, lw=1.4, color=_PHASE_COLORS[j % 6], label=n)
        ax.set_xlabel("T (°C)"); ax.set_ylabel("volume fraction"); ax.set_ylim(0, 1.05)
        ax.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        paths = {}
        for ext, kw in (("pdf", {}), ("png", {"dpi": 300}), ("svg", {"format": "svg"})):
            p = out_base.with_suffix("." + ext)
            fig.savefig(p, bbox_inches="tight", **kw)
            paths[ext] = str(p)
        plt.close(fig)
    return paths


def populate(spec, man: Dict[str, Any], rec=None, kin=None, *, with_plates=True):
    from apexa_report import Tile
    verdict, note, F, A = findings(man)
    recipe = man.get("recipe") or {}
    spec.title = f"In-situ synthesis fit — {man.get('sample', '')}"
    spec.eyebrow = "APEXA · Synthesis Report"
    spec.subtitle = (f"{', '.join(recipe.get('phases') or [])} · scans "
                     f"{recipe.get('window')} · d {recipe.get('d_window_A')} Å")
    spec.verdict, spec.verdict_note = verdict, note
    T = man.get("TC") or [0]
    rp = [v for v in (man.get("transparency") or {}).get("per_scan_Rp") or [] if v is not None]
    spec.tiles = [Tile("Scans", str(len(man.get("scans") or []))),
                  Tile("T range", f"{min(T):.0f}–{max(T):.0f} °C"),
                  Tile("Median Rp", f"{100*np.median(rp):.1f}%" if rp else "—"),
                  Tile("Railed params", str(len((man.get("transparency") or {}).get("params_at_bounds") or [])))]
    if rec:
        spec.tiles.append(Tile("Next ΔT (advisory)", f"{rec['recommended_dT_C']:.1f} °C",
                               f"→ {rec['recommended_next_T_C']:.0f} °C"))
    if kin:
        spec.tiles.append(Tile("JMAK Ea (approx.)", f"{kin.get('Ea_kJ_mol', float('nan')):.1f} kJ/mol",
                               f"n = {kin.get('n', float('nan')):.2g}"))
    spec.findings = F
    spec.actions = A
    spec.provenance.append({"name": "synth_fit_series", "status": man.get("status", ""),
                            "engine": man.get("engine", ""), "h5": man.get("h5", "")})
    if with_plates:
        spec.plates = _plates(man, rec, kin)


def write_report(fit_dir: str, output_path: Optional[str] = None,
                 recommend_json: Optional[str] = None,
                 kinetics_json: Optional[str] = None) -> Dict[str, Any]:
    from apexa_report import build_report, render_html
    man = _load(fit_dir)
    rec, kin = _read_json(recommend_json), _read_json(kinetics_json)
    spec = build_report(man, technique="insitu-synthesis",
                        source_path=str(Path(man["_dir"]) / MANIFEST), with_plates=False)
    populate(spec, man, rec, kin)
    out = Path(output_path).expanduser() if output_path else (
        Path(man["_dir"]) / f"synthesis_report_{man.get('sample', 'series')}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(render_html(spec), encoding="utf-8")
    tmp.replace(out)
    fig = export_figure(man, out.with_name(f"phase_fractions_{man.get('sample', 'series')}"))
    return {"status": "success", "report": str(out), "verdict": spec.verdict,
            "summary": spec.verdict_note, "figure": fig, "report_spec": spec.to_dict()}
