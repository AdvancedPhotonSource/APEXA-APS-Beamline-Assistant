"""Run reports — one self-contained HTML file a scientist can glance at.

The problem this solves is not "there is no plot". It is that a run's most
decision-relevant fact is often recorded and never shown. `_calibrate_runner`
has always written `at_bounds` -- every refined parameter with the interval it
was allowed to move in -- and nothing ever displayed it. That is how 23 of the
35 gate-passed calibrations in the 20-ID rotation series (66%) finished with a
parameter pinned to a search bound without anyone noticing. The acceptance
number was fine. The acceptance number was not the question.

So the report leads with a verdict, then shows **what the verdict does not
cover**, then offers the specific lever for each problem it found.

Design constraints, each load-bearing:

* **One file, nothing external.** Plates are matplotlib PNGs inlined as
  base64. APEXA's default network tier is `internal` -- copland has no public
  internet -- so a CDN reference renders as a blank box on the machine this is
  for. Measured: plotly-from-CDN 7 KiB but empty offline; plotly inlined
  4.7 MiB; a 512² detector PNG 258 KiB. PNG wins twice.
* **`string.Template`, never `str.format`.** The CSS is full of braces.
* **House style is not invented here.** Palette, colormaps, the dual
  `prefers-color-scheme` + `data-theme` rule and always-light figure plates come
  from `knowledge_base/logbooks/Reconstruction_Reports.md` §7.
* **Findings are derived from the data**, never hardcoded, following
  `MIDAS/utils/midas_ff_report.py`.
* **Shaped like the `beamreport` contract** (Finding / Plate / Provenance /
  Results) that MIDAS is converging on, so when that package ships APEXA swaps
  the builder rather than discarding a divergent format.
"""
from __future__ import annotations

import base64
import html
import io
import json
import math
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["Finding", "Plate", "Tile", "Action", "ReportSpec",
           "build_report", "render_html", "PAPER", "INK", "TEAL", "COPPER"]

# Reconstruction_Reports.md §7
PAPER, INK, TEAL, COPPER, MUTED = "#f7f6f3", "#131619", "#0e7c86", "#c07a3e", "#6b6862"
GRIDC = "#d9d6cf"


# ── spec ──────────────────────────────────────────────────────────────────────
@dataclass
class Finding:
    title: str
    body: str
    severity: str = "solid"          # solid | warn | roadmap
    tag: str = ""


@dataclass
class Action:
    """A concrete next step. Rendered copy-to-clipboard in the file, and as a
    live button when the same spec is shown inside the APEXA UI."""
    label: str
    instruction: str
    why: str = ""


@dataclass
class Tile:
    key: str
    value: str
    sub: str = ""


@dataclass
class Plate:
    key: str
    title: str
    png_b64: str = ""
    subtitle: str = ""


@dataclass
class ReportSpec:
    title: str = "Run report"
    subtitle: str = ""
    eyebrow: str = "APEXA · Run Report"
    header_pairs: List[Tuple[str, str]] = field(default_factory=list)
    tiles: List[Tile] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    plates: List[Plate] = field(default_factory=list)
    logbook: Dict[str, Any] = field(default_factory=dict)
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    verdict: str = "unknown"         # pass | look | fail | unknown
    verdict_note: str = ""
    source_path: str = ""
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["header_pairs"] = [list(p) for p in self.header_pairs]
        return d


# ── plotting helpers ──────────────────────────────────────────────────────────
def _mpl():
    """Agg, imported inside the call. Matches the house pattern and keeps
    importing this module cheap for callers that only want the spec."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": PAPER, "axes.facecolor": PAPER,
        "savefig.facecolor": PAPER, "text.color": INK,
        "axes.labelcolor": INK, "axes.edgecolor": GRIDC,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "grid.color": GRIDC, "font.size": 9.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "figure.dpi": 130,
    })
    return plt


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _railed(b: Dict[str, Any], frac_tol: float = 0.01) -> bool:
    """Is this parameter sitting on the wall of its own interval?

    One definition, used by both the plate and the finding that counts them.
    They disagreed when they each decided separately: `_calibrate_runner` only
    records railed parameters, so the finding trusted the list length -- but the
    plate re-derives it, and any caller passing a FULL parameter set (which the
    plate supports, and is the more useful input) made the two disagree in the
    same report.
    """
    lo, hi = (list(b.get("bounds") or [None, None]) + [None, None])[:2]
    lo, hi, v = _f(lo), _f(hi), _f(b.get("value"))
    if lo is None or hi is None or v is None or hi <= lo:
        return False
    frac = (v - lo) / (hi - lo)
    return frac <= frac_tol or frac >= 1.0 - frac_tol


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ── the headline plate ────────────────────────────────────────────────────────
def plate_parameter_rail(at_bounds: Sequence[Dict[str, Any]],
                         all_params: Optional[Dict[str, Any]] = None) -> Optional[Plate]:
    """Every refined parameter drawn as its position inside its own interval.

    This is the plate the whole report exists for. A converged fit and a fit
    that hit the wall produce the same residual, so a scalar cannot separate
    them -- but on this axis a railed parameter sits visibly at the edge, and
    the eye gets it in under a second. The data has always been on disk.

    Each row is normalised to its own [lo, hi], because the parameters are in
    different units (degrees, µm, dimensionless distortion terms) and a shared
    axis would be meaningless.
    """
    rows = [b for b in (at_bounds or []) if isinstance(b, dict)]
    if not rows:
        return None
    plt = _mpl()
    rows = sorted(rows, key=lambda b: str(b.get("parameter", "")))
    n = len(rows)
    fig, ax = plt.subplots(figsize=(7.6, max(1.6, 0.42 * n + 0.9)))

    labels: List[str] = []
    for i, b in enumerate(rows):
        lo, hi = (list(b.get("bounds") or [None, None]) + [None, None])[:2]
        lo, hi, v = _f(lo), _f(hi), _f(b.get("value"))
        name = str(b.get("parameter", "?"))
        if lo is None or hi is None or v is None or hi <= lo:
            labels.append(name)
            continue
        frac = (v - lo) / (hi - lo)
        railed = _railed(b)
        ax.plot([0, 1], [i, i], color=GRIDC, lw=5, solid_capstyle="round", zorder=1)
        for edge in (0.0, 1.0):
            ax.plot([edge], [i], marker="|", ms=11, color=MUTED, zorder=2)
        ax.plot([frac], [i], marker="o", ms=10 if railed else 7,
                color=COPPER if railed else TEAL, zorder=3,
                markeredgecolor=PAPER, markeredgewidth=1.2)
        ax.annotate(f"{v:,.4g}", (frac, i), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=8,
                    color=COPPER if railed else INK,
                    fontweight="bold" if railed else "normal")
        labels.append(f"{name}  " + ("⚠" if railed else ""))

    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xticks([0, 0.5, 1])
    ax.set_xticklabels(["lower bound", "centre of allowed range", "upper bound"],
                       fontsize=8.5)
    ax.set_xlim(-0.06, 1.06)
    ax.set_ylim(-0.7, n - 0.3)
    ax.invert_yaxis()
    for s in ("left", "bottom"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    n_railed = sum(1 for lb in labels if "⚠" in lb)
    ax.set_title(
        f"{n_railed} of {n} refined parameters finished ON a bound"
        if n_railed else f"all {n} refined parameters converged inside their bounds",
        fontsize=10.5, color=COPPER if n_railed else TEAL,
        fontweight="bold", loc="left", pad=12)
    return Plate(key="parameter_rail", title="Where each parameter landed",
                 subtitle="a marker at the edge means the optimiser stopped at the "
                          "wall, not at a minimum — the residual cannot tell you this",
                 png_b64=_png(fig))


def plate_series_status(results: Sequence[Dict[str, Any]]) -> Optional[Plate]:
    """Per-file outcome across a series, in file order."""
    rows = [r for r in (results or []) if isinstance(r, dict)]
    if not rows:
        return None
    plt = _mpl()
    ok = [str(r.get("status", "")).lower() in ("success", "ok", "true")
          or bool(r.get("success")) for r in rows]
    fig, ax = plt.subplots(figsize=(7.6, 1.5))
    for i, good in enumerate(ok):
        ax.bar(i, 1, width=1.0, color=TEAL if good else COPPER,
               edgecolor=PAPER, linewidth=0.4)
    ax.set_xlim(-0.5, len(ok) - 0.5)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xlabel("file index, in series order")
    nbad = sum(1 for g in ok if not g)
    ax.set_title(f"{len(ok) - nbad} integrated, {nbad} failed",
                 fontsize=10.5, color=COPPER if nbad else TEAL,
                 fontweight="bold", loc="left", pad=10)
    for s in ("left", "bottom"):
        ax.spines[s].set_visible(False)
    return Plate(key="series_status", title="Per-file outcome",
                 subtitle="every file in the series; copper is a file that produced "
                          "no lineout",
                 png_b64=_png(fig))


def plate_detector(image_path: str, rings_px: Optional[Sequence[float]] = None,
                   centre: Optional[Tuple[float, float]] = None) -> Optional[Plate]:
    """The frame itself, log-scaled, with predicted rings if the geometry is known.

    Best-effort: a report without the detector image is still useful, so every
    failure here returns None rather than raising.
    """
    try:
        import numpy as np
        arr = _load_image(image_path)
        if arr is None:
            return None
        plt = _mpl()
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return None
        lo, hi = np.percentile(finite, (1.0, 99.5))
        shown = np.clip(arr, max(lo, 0), max(hi, lo + 1.0))
        fig, ax = plt.subplots(figsize=(5.6, 5.6))
        ax.imshow(np.log1p(shown - shown.min()), cmap="cividis", origin="lower",
                  interpolation="nearest")
        if rings_px and centre:
            cy, cx = centre[1], centre[0]
            th = np.linspace(0, 2 * np.pi, 360)
            for r in rings_px:
                rr = _f(r)
                if rr:
                    ax.plot(cx + rr * np.cos(th), cy + rr * np.sin(th),
                            lw=0.7, color=COPPER, alpha=0.85)
            ax.plot([cx], [cy], marker="+", ms=11, color=COPPER)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_title(Path(image_path).name, fontsize=9, color=MUTED, loc="left")
        return Plate(key="detector", title="Detector frame",
                     subtitle="log intensity, 1–99.5 % clip"
                              + (" · predicted rings in copper" if rings_px and centre else ""),
                     png_b64=_png(fig))
    except Exception:
        return None


def _load_image(path: str):
    """Read a 2-D frame from whatever the beamline wrote. None on any failure."""
    p = Path(path)
    if not p.is_file():
        return None
    ext = p.suffix.lower()
    try:
        import numpy as np
        if ext in (".tif", ".tiff"):
            import tifffile
            a = tifffile.imread(str(p))
        elif ext in (".h5", ".hdf5", ".nxs"):
            import h5py
            with h5py.File(str(p), "r") as f:
                ds = None
                for key in ("exchange/data", "entry/data/data", "data"):
                    if key in f:
                        ds = f[key]
                        break
                if ds is None:
                    return None
                a = np.asarray(ds[0] if getattr(ds, "ndim", 2) > 2 else ds[:])
        else:
            return None
        a = np.asarray(a, dtype="float64")
        while a.ndim > 2:
            a = a[0]
        return a if a.ndim == 2 else None
    except Exception:
        return None


# ── findings: derived from the data, never hardcoded ──────────────────────────
def _calibration_findings(m: Dict[str, Any]) -> Tuple[List[Finding], List[Action], str, str]:
    """Read a calibration manifest the way an experienced operator would."""
    finds: List[Finding] = []
    acts: List[Action] = []
    cm = m.get("convergence_metrics") or {}
    status = str(m.get("status", "")).lower()

    strain = _f(cm.get("stage4_strain_uE_test")) or _f(cm.get("final_mean_strain"))
    gate = _f(cm.get("strain_gate_ue")) or 100.0
    at_bounds = [b for b in (cm.get("at_bounds") or m.get("at_bounds") or [])
                 if isinstance(b, dict) and _railed(b)]

    verdict = "pass" if status in ("success", "ok") else (
        "fail" if status in ("error", "failed") else "look")
    note = ""

    if strain is not None:
        inside = strain < gate
        finds.append(Finding(
            title=f"Held-out strain {strain:,.1f} µε",
            body=(f"Against a {gate:,.0f} µε gate. "
                  + ("Inside the gate." if inside else "Outside the gate.")
                  + " Note the gate is a *fraction* (|1 − R_obs/R_pred|), so a short "
                    "sample-to-detector distance reads higher for the same real "
                    "precision — read it against the geometry, not as a universal bar."),
            severity="solid" if inside else "warn", tag="gate"))

    # The point of the whole report.
    if at_bounds:
        names = [str(b.get("parameter", "?")) for b in at_bounds]
        tilts = [n for n in names if n in ("tx", "ty", "tz")]
        dist = [n for n in names if re.fullmatch(r"(p\d+|a_\d+|phi_\d+)", n or "")]
        lsd = [n for n in names if n.lower() in ("lsd", "lsd_um")]
        if verdict == "pass":
            verdict, note = "look", (
                f"accepted by the gate, but {len(at_bounds)} parameter(s) stopped on a bound")
        finds.append(Finding(
            title=f"{len(at_bounds)} refined parameter(s) finished ON a bound",
            body=("The optimiser stopped at the edge of what it was allowed to "
                  "explore, not at a minimum. A bound-railed fit and a converged one "
                  "produce the same residual, so the acceptance number cannot "
                  "distinguish them: " + ", ".join(names)),
            severity="warn", tag="bounds"))
        # Which lever depends on WHICH parameter railed. Getting this wrong wastes
        # runs — at 20-ID a railed tz was read as a distortion problem and the
        # full → radial → none ladder was tried three times, none of which can
        # move a tilt bound.
        if tilts:
            acts.append(Action(
                label=f"Widen the tilt bound and re-run ({', '.join(tilts)} railed)",
                instruction=("Re-run midas_auto_calibrate on this image with "
                             "tol_tilts_deg=10, keeping every other argument the same."),
                why=("The default tilt tolerance is ±3°. A detector genuinely tilted "
                     "past that rails at exactly the bound and still reports a "
                     "plausible strain.")))
        if dist:
            acts.append(Action(
                label=f"Refine only the distortion the azimuth supports ({len(dist)} terms railed)",
                instruction=('Re-run midas_auto_calibrate with refine_distortion="radial", '
                             'then with "none", and compare.'),
                why=("Hard rule 11. Railed azimuthal harmonics are indistinguishable "
                     "from real detector distortion in the output file.")))
        if lsd:
            acts.append(Action(
                label="Widen the distance bound and re-run (Lsd railed)",
                instruction="Re-run midas_auto_calibrate with a larger lsd_tol_um.",
                why="A railed Lsd usually means the nominal distance was wrong, or "
                    "the wavelength is — the two are degenerate (hard rule 9)."))

    rung = cm.get("threshold_rung", cm.get("seed", {}).get("threshold_rung")
                  if isinstance(cm.get("seed"), dict) else None)
    if _f(rung) and _f(rung) > 0:
        finds.append(Finding(
            title=f"Seed threshold relaxed to rung {int(_f(rung))}",
            body=("make_seed found nothing at the strict threshold. Relaxed far "
                  "enough, the arc finder fits noise — check the ring overlay."),
            severity="warn", tag="seed"))

    if not finds:
        finds.append(Finding(title="No convergence metrics recorded",
                             body="The manifest carried no gate or bound information.",
                             severity="roadmap", tag="sparse"))
    return finds, acts, verdict, note


def _series_findings(m: Dict[str, Any]) -> Tuple[List[Finding], List[Action], str, str]:
    finds: List[Finding] = []
    acts: List[Action] = []
    ok, bad = _f(m.get("succeeded")) or 0, _f(m.get("failed")) or 0
    status = str(m.get("status", "")).lower()
    verdict = {"success": "pass", "partial": "look", "error": "fail"}.get(status, "look")
    note = f"{int(bad)} of {int(ok + bad)} files produced no lineout" if bad else ""

    finds.append(Finding(
        title=f"{int(ok)} integrated, {int(bad)} failed",
        body=f"Out of {int(m.get('matched_files') or ok + bad)} matched files.",
        severity="solid" if not bad else "warn", tag="throughput"))

    # A dark that was found but did nothing: the manifest knows, nobody looks.
    dmean = _f(m.get("dark_first_frame_mean"))
    if m.get("dark_dataset") and (dmean == 0.0 or m.get("dark_applied") is False):
        if verdict == "pass":
            verdict, note = "look", "a dark was found but subtraction was a no-op"
        finds.append(Finding(
            title="Dark subtraction was a no-op",
            body=(f"A dark was located ({m.get('dark_dataset')}) but its first frame "
                  f"means {dmean}. Nothing was subtracted, and the integration "
                  f"reports success either way."),
            severity="warn", tag="dark"))
        acts.append(Action(
            label="Point the integrator at the real dark and re-run",
            instruction=("Re-run midas_integrate_series with dark_file set explicitly "
                         "and dark_location='exchange/data' (a separate .h5 dark holds "
                         "its frame there, not at the integrator's default "
                         "'exchange/dark')."),
            why="A zero dark silently leaves the detector pedestal in every lineout."))

    if m.get("output_location_warning"):
        finds.append(Finding(title="Output went to a default location",
                             body=str(m["output_location_warning"]),
                             severity="roadmap", tag="output"))
    return finds, acts, verdict, note


# ── capsule knowledge ─────────────────────────────────────────────────────────
def _capsule_findings(technique: str, limit: int = 6) -> List[Finding]:
    """The technique's own halts and traps, as symptom → where to look.

    Not generated prose: these are the vendored handbook's own rows, so the
    report tells the operator what WOULD indicate a problem, not merely what
    happened.
    """
    out: List[Finding] = []
    if not technique:
        return out
    try:
        import capsule_registry as cr
    except Exception:
        return out
    try:
        for h in (cr.halt_conditions(technique) or [])[:limit]:
            cond, why = h.get("condition"), h.get("why", "")
            if cond:
                out.append(Finding(title=f"Halt: {_strip_md(cond)}",
                                   body=_strip_md(why), severity="roadmap",
                                   tag="halt"))
        for t in (cr.traps(technique) or [])[:limit]:
            trap, sym, where = t.get("trap"), t.get("symptom", ""), t.get("where", "")
            if trap:
                out.append(Finding(
                    title=f"Trap: {_strip_md(trap)}",
                    body=(f"Symptom — {_strip_md(sym)}." if sym else "")
                         + (f" Test: {_strip_md(where)}." if where else ""),
                    severity="roadmap", tag="trap"))
    except Exception:
        return out
    return out


def _strip_md(s: Any) -> str:
    t = re.sub(r"[`*_]+", "", str(s or ""))
    return re.sub(r"\s+", " ", t).strip()


# ── build ─────────────────────────────────────────────────────────────────────
_STAGE_FOR_TOOL = {
    "midas_auto_calibrate": "calibration", "run_ff_calibration": "calibration",
    "midas_integrate_series": "integration", "midas_integrate_2d_to_1d": "integration",
    "midas_batch_integrate": "integration",
    "run_ff_hedm_full_workflow": "ff-reconstruction",
    "run_nf_hedm_reconstruction": "nf-reconstruction",
    "run_pf_hedm_workflow": "pf-reconstruction",
    "refinement_status": "powder-refinement", "refine_pattern": "powder-refinement",
    "powder_refine_submit": "powder-refinement", "powder_live_ingest": "powder-refinement",
}


def build_report(manifest: Dict[str, Any], *, esaf=None, logbook=None,
                 ledger_entries: Optional[Sequence[Dict[str, Any]]] = None,
                 technique: str = "", source_path: str = "",
                 with_plates: bool = True) -> ReportSpec:
    """Turn a run manifest into a ReportSpec. Pure: writes nothing."""
    tool = str(manifest.get("tool", "") or "")
    stage = _STAGE_FOR_TOOL.get(tool, "run")
    if not technique:
        try:
            import capsule_registry as cr
            technique = cr.technique_for_tool(tool) or ""
        except Exception:
            technique = ""

    if stage == "calibration":
        finds, acts, verdict, note = _calibration_findings(manifest)
    elif stage == "integration":
        finds, acts, verdict, note = _series_findings(manifest)
    else:
        status = str(manifest.get("status", "")).lower()
        verdict = {"success": "pass", "partial": "look",
                   "error": "fail", "warning": "look"}.get(status, "unknown")
        note = str(manifest.get("run_note") or manifest.get("message") or "")
        finds = [Finding(title=f"{tool or 'run'} — {status or 'no status recorded'}",
                         body=note or "No per-stage interpretation for this tool yet; "
                                      "the numbers below are read straight from the "
                                      "manifest.",
                         severity="solid" if status == "success" else "warn",
                         tag=stage)]
        acts = []

    spec = ReportSpec(
        title=(esaf.title if esaf and esaf.title else None) or f"{stage.title()} report",
        subtitle=_subtitle(manifest, stage),
        header_pairs=list(esaf.header_pairs()) if esaf else [],
        tiles=_tiles(manifest, stage),
        findings=finds + _capsule_findings(technique),
        actions=acts,
        verdict=verdict, verdict_note=note,
        source_path=source_path or str(manifest.get("outcome_manifest", "")),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    if stage == "powder-refinement" or technique == "powder-refinement":
        from apexa_powder_report import populate
        populate(spec, manifest, with_plates=with_plates)

    if logbook is not None:
        spec.logbook = logbook if isinstance(logbook, dict) else logbook.to_dict()

    for e in (ledger_entries or []):
        if isinstance(e, dict):
            spec.provenance.append({
                "name": e.get("name") or e.get("tool") or "?",
                "status": e.get("status", ""),
                "elapsed_s": e.get("elapsed_s", e.get("elapsed", "")),
            })

    if with_plates:
        cm = manifest.get("convergence_metrics") or {}
        rail = plate_parameter_rail(cm.get("at_bounds") or manifest.get("at_bounds") or [])
        if rail:
            spec.plates.append(rail)
        if stage == "integration":
            st = plate_series_status(manifest.get("results") or [])
            if st:
                spec.plates.append(st)
        img = manifest.get("image_file") or manifest.get("data_file")
        if img:
            det = plate_detector(str(img))
            if det:
                spec.plates.append(det)
    return spec


def _subtitle(m: Dict[str, Any], stage: str) -> str:
    bits = [b for b in (m.get("engine"), m.get("calibrant"),
                        Path(str(m.get("image_file", ""))).name or None) if b]
    return " · ".join(str(b) for b in bits) or stage


def _tiles(m: Dict[str, Any], stage: str) -> List[Tile]:
    t: List[Tile] = []
    cm = m.get("convergence_metrics") or {}
    cp = m.get("calibrated_parameters") or {}
    if stage == "calibration":
        lsd = _f(cp.get("lsd")) or _f(cp.get("Lsd"))
        if lsd:
            t.append(Tile("Distance", f"{lsd / 1000:,.3f} mm", "refined Lsd"))
        bx, by = _f(cp.get("bc_x")), _f(cp.get("bc_y"))
        if bx is not None and by is not None:
            t.append(Tile("Beam centre", f"{bx:,.2f}, {by:,.2f}", "px"))
        for nm in ("ty", "tz"):
            v = _f(cp.get(nm))
            if v is not None:
                t.append(Tile(f"Tilt {nm}", f"{v:,.4f}°", ""))
        s = _f(cm.get("stage4_strain_uE_test")) or _f(cm.get("final_mean_strain"))
        if s is not None:
            t.append(Tile("Held-out strain", f"{s:,.1f} µε", "gate 100 µε"))
        nb = len(cm.get("at_bounds") or m.get("at_bounds") or [])
        t.append(Tile("On a bound", str(nb),
                      "parameters stopped at the wall" if nb else "none"))
    elif stage == "integration":
        for k, lab in (("succeeded", "Integrated"), ("failed", "Failed"),
                       ("matched_files", "Matched")):
            v = _f(m.get(k))
            if v is not None:
                t.append(Tile(lab, f"{int(v)}", ""))
        if m.get("dark_dataset"):
            dm = _f(m.get("dark_first_frame_mean"))
            t.append(Tile("Dark applied",
                          "yes" if m.get("dark_applied") else "NO",
                          f"first-frame mean {dm}" if dm is not None else ""))
    if not t:
        for k, v in list(m.items()):
            if isinstance(v, (int, float)) and not isinstance(v, bool) and len(t) < 6:
                t.append(Tile(k.replace("_", " "), f"{v:,.4g}", ""))
    return t


# ── HTML ──────────────────────────────────────────────────────────────────────
def _e(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


_VERDICT = {
    "pass": ("PASS", "ok", "accepted, and nothing flagged"),
    "look": ("LOOK", "warn", "accepted — but read the flags before using this"),
    "fail": ("FAIL", "bad", "not usable as a result"),
    "unknown": ("—", "mut", "no status recorded"),
}


def render_html(spec: ReportSpec) -> str:
    """One self-contained page. No network, no fonts, no CDN, no JS framework."""
    vlabel, vclass, vsub = _VERDICT.get(spec.verdict, _VERDICT["unknown"])

    hdr = "".join(
        f'<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>' for k, v in spec.header_pairs
    ) or '<div><dt>Experiment</dt><dd>no ESAF found</dd></div>'

    tiles = "".join(
        f'<div class="tile"><div class="tk">{_e(t.key)}</div>'
        f'<div class="tv">{_e(t.value)}</div>'
        + (f'<div class="ts">{_e(t.sub)}</div>' if t.sub else "")
        + "</div>" for t in spec.tiles)

    def _finds(sev: str) -> str:
        return "".join(
            f'<div class="find {"warn" if f.severity == "warn" else ""}">'
            + (f'<span class="tag {"warn" if f.severity == "warn" else ""}">{_e(f.tag)}</span>'
               if f.tag else "")
            + f'<h3>{_e(f.title)}</h3><p>{_e(f.body)}</p></div>'
            for f in spec.findings if f.severity == sev)

    acts = "".join(
        f'<div class="act"><div class="act-h"><h3>{_e(a.label)}</h3>'
        f'<button class="copy" data-cmd="{_e(a.instruction)}">copy</button></div>'
        f'<pre class="cmd">{_e(a.instruction)}</pre>'
        + (f'<p class="why">{_e(a.why)}</p>' if a.why else "")
        + "</div>" for a in spec.actions)

    plates = "".join(
        f'<figure class="plate"><figcaption class="pcap">'
        f'<span class="pnum">{i + 1:02d}</span>'
        f'<span class="ptitle">{_e(p.title)}</span>'
        f'<span class="psub">{_e(p.subtitle)}</span></figcaption>'
        f'<div class="pimg"><img alt="{_e(p.title)}" '
        f'src="data:image/png;base64,{p.png_b64}"></div></figure>'
        for i, p in enumerate(spec.plates))

    lb = spec.logbook or {}
    lb_html = ""
    if lb.get("entries"):
        rows = "".join(
            f'<div class="le">'
            + (f'<div class="lt">{_e(e.get("timestamp"))}</div>' if e.get("timestamp") else "")
            + f'<pre class="lx">{_e(e.get("text"))}</pre></div>'
            for e in lb["entries"])
        src = _e(((lb.get("host") + ":") if lb.get("host") else "") + str(lb.get("source", "")))
        trunc = (' · earlier entries not shown (tailed)' if lb.get("truncated") else "")
        summary = (f'<div class="gen"><span class="tag">generated</span>'
                   f'<p>{_e(lb["summary"])}</p></div>' if lb.get("summary") else "")
        lb_html = (
            '<section class="sec"><div class="sec-h"><h2>Logbook</h2>'
            f'<span class="k">{len(lb["entries"])} most recent entries · verbatim</span></div>'
            f'{summary}<div class="logs">{rows}</div>'
            f'<p class="src">{src}{trunc}</p></section>')
    elif lb.get("error"):
        lb_html = ('<section class="sec"><div class="sec-h"><h2>Logbook</h2>'
                   '<span class="k">unavailable</span></div>'
                   f'<div class="find warn"><p>{_e(lb["error"])}</p></div></section>')

    prov = "".join(
        f'<li><code>{_e(p["name"])}</code> <span class="pstat">{_e(p.get("status"))}</span>'
        + (f' <span class="pel">{_e(p.get("elapsed_s"))}s</span>' if p.get("elapsed_s") not in (None, "") else "")
        + "</li>" for p in spec.provenance)

    body = Template(_TEMPLATE).substitute(
        title=_e(spec.title), subtitle=_e(spec.subtitle), eyebrow=_e(spec.eyebrow),
        vlabel=vlabel, vclass=vclass,
        vsub=_e(spec.verdict_note or vsub),
        hdr=hdr, tiles=tiles,
        solid=_finds("solid"), warn=_finds("warn"), road=_finds("roadmap"),
        acts=(f'<section class="sec"><div class="sec-h"><h2>What to do next</h2>'
              f'<span class="k">diagnosis → lever</span></div>'
              f'<div class="acts">{acts}</div></section>' if acts else ""),
        logbook=lb_html,
        plates=(f'<section class="sec"><div class="sec-h"><h2>Figures</h2>'
                f'<span class="k">{len(spec.plates)} plates</span></div>{plates}</section>'
               if plates else ""),
        prov=(f'<section class="sec"><div class="sec-h"><h2>What produced this</h2>'
              f'<span class="k">executed tool calls</span></div>'
              f'<ul class="prv">{prov}</ul></section>' if prov else ""),
        src=_e(spec.source_path), gen=_e(spec.generated_at),
    )
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_e(spec.title)}</title>{body}</html>")


# string.Template, NOT str.format -- the CSS braces collide. Documented gotcha in
# knowledge_base/logbooks/Reconstruction_Reports.md §10.
_TEMPLATE = r'''<style>
:root{--paper:#f4f2ee;--panel:#fff;--ink:#16191c;--ink2:#3d4045;--mut:#6b6862;--line:#e4e0d8;--hair:#efece5;
--teal:#0e7c86;--teal-ink:#0b5f67;--copper:#b06f33;--bad:#a8392b;--plate:#f7f6f3;--plate-line:#d7d3ca;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#101315;--panel:#181c20;--ink:#e9e7e2;--ink2:#c3c1ba;--mut:#8f8d85;--line:#282d33;--hair:#20252a;--teal:#33aab9;--teal-ink:#7fd0da;--copper:#d69157;--bad:#e2705f;}}
:root[data-theme="dark"]{--paper:#101315;--panel:#181c20;--ink:#e9e7e2;--ink2:#c3c1ba;--mut:#8f8d85;--line:#282d33;--hair:#20252a;--teal:#33aab9;--teal-ink:#7fd0da;--copper:#d69157;--bad:#e2705f;}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.55}
.wrap{max-width:1180px;margin:0 auto;padding:clamp(20px,4vw,52px) clamp(16px,4vw,40px) 80px}
.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--teal);font-weight:650}
h1{font-size:clamp(26px,4vw,42px);line-height:1.06;margin:.28em 0 .1em;letter-spacing:-.02em;text-wrap:balance;font-weight:750}
.lede{font-size:clamp(14px,1.6vw,17px);color:var(--ink2);max-width:72ch;margin:.2em 0 0}
.verdict{display:flex;align-items:center;gap:16px;margin:26px 0 0;padding:16px 20px;border:1px solid var(--line);border-radius:14px;background:var(--panel);border-left-width:5px}
.verdict.ok{border-left-color:var(--teal)}.verdict.warn{border-left-color:var(--copper)}
.verdict.bad{border-left-color:var(--bad)}.verdict.mut{border-left-color:var(--mut)}
.vl{font-family:var(--mono);font-size:26px;font-weight:750;letter-spacing:.04em}
.verdict.ok .vl{color:var(--teal)}.verdict.warn .vl{color:var(--copper)}.verdict.bad .vl{color:var(--bad)}.verdict.mut .vl{color:var(--mut)}
.vs{font-size:14px;color:var(--ink2)}
.prov{margin-top:20px;border:1px solid var(--line);border-radius:12px;background:var(--panel);display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1px;overflow:hidden}
.prov div{padding:12px 16px;background:var(--panel)}.prov dt{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:0 0 3px}
.prov dd{margin:0;font-family:var(--mono);font-size:12.5px;color:var(--ink);word-break:break-word}
.sec{margin-top:46px}.sec-h{display:flex;align-items:baseline;gap:12px;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.sec-h h2{font-size:20px;margin:0;font-weight:700}.sec-h .k{font-family:var(--mono);font-size:12px;color:var(--teal)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px}
.tile{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:16px;position:relative;overflow:hidden}
.tile::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--teal);opacity:.85}
.tk{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut)}
.tv{font-size:24px;font-weight:720;margin-top:6px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.ts{font-size:12px;color:var(--ink2);margin-top:4px;font-variant-numeric:tabular-nums}
.finds{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}
.find{border:1px solid var(--line);border-left:3px solid var(--teal);border-radius:0 12px 12px 0;background:var(--panel);padding:15px 18px}
.find.warn{border-left-color:var(--copper)}.find h3{margin:0 0 5px;font-size:14.5px;font-weight:680}
.find p{margin:0;font-size:13.5px;color:var(--ink2)}
.tag{display:inline-block;font-family:var(--mono);font-size:10.5px;padding:2px 7px;border-radius:5px;background:color-mix(in srgb,var(--teal) 16%,transparent);color:var(--teal-ink);margin-bottom:8px}
.tag.warn{background:color-mix(in srgb,var(--copper) 20%,transparent);color:var(--copper)}
.acts{display:grid;gap:14px}
.act{border:1px solid var(--line);border-left:3px solid var(--copper);border-radius:0 12px 12px 0;background:var(--panel);padding:15px 18px}
.act-h{display:flex;align-items:center;justify-content:space-between;gap:12px}
.act h3{margin:0;font-size:14.5px;font-weight:680}
.copy{font-family:var(--mono);font-size:11.5px;padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--teal-ink);cursor:pointer}
.copy:hover{background:color-mix(in srgb,var(--teal) 12%,transparent)}
.copy:focus-visible{outline:2px solid var(--teal);outline-offset:2px}
.cmd{margin:10px 0 0;padding:11px 13px;background:var(--plate);border:1px solid var(--plate-line);border-radius:9px;font-family:var(--mono);font-size:12.5px;color:#16191c;white-space:pre-wrap;overflow-x:auto}
.why{margin:9px 0 0;font-size:12.5px;color:var(--mut)}
.logs{display:grid;gap:10px}
.le{border:1px solid var(--line);border-radius:10px;background:var(--panel);padding:11px 14px}
.lt{font-family:var(--mono);font-size:11.5px;color:var(--teal);margin-bottom:4px}
.lx{margin:0;font-size:13px;color:var(--ink2);white-space:pre-wrap;font-family:var(--sans);overflow-x:auto}
.gen{border:1px dashed var(--line);border-radius:10px;padding:12px 15px;margin-bottom:14px;background:var(--panel)}
.gen p{margin:0;font-size:13.5px;color:var(--ink2)}
.src{font-family:var(--mono);font-size:11.5px;color:var(--mut);margin-top:10px;word-break:break-all}
.plate{margin:0 0 26px;border:1px solid var(--line);border-radius:14px;background:var(--panel);overflow:hidden}
.pcap{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;padding:15px 20px 13px;border-bottom:1px solid var(--hair)}
.pnum{font-family:var(--mono);font-size:12px;color:var(--teal);font-weight:600}
.ptitle{font-size:16px;font-weight:700}.psub{font-size:12.5px;color:var(--mut)}
.pimg{background:var(--plate);padding:14px;border-top:1px solid var(--plate-line);overflow-x:auto}
.pimg img{display:block;width:100%;max-width:100%;height:auto;min-width:560px;margin:0 auto}
.prv{list-style:none;padding:0;margin:0;display:grid;gap:6px}
.prv li{font-family:var(--mono);font-size:12.5px;color:var(--ink2)}
.pstat{color:var(--teal)}.pel{color:var(--mut)}
footer{margin-top:46px;padding-top:20px;border-top:1px solid var(--line);color:var(--mut);font-size:12px}
footer .fp{font-family:var(--mono);font-size:11.5px;word-break:break-all}
@media (max-width:640px){.wrap{padding-left:16px;padding-right:16px}.pimg img{min-width:0}}
</style>
<div class="wrap">
<header><div class="eyebrow">${eyebrow}</div>
<h1>${title}</h1>
<p class="lede">${subtitle}</p>
<div class="verdict ${vclass}"><span class="vl">${vlabel}</span><span class="vs">${vsub}</span></div>
<dl class="prov">${hdr}</dl></header>
<section class="sec"><div class="sec-h"><h2>Summary</h2><span class="k">from the run manifest</span></div><div class="tiles">${tiles}</div></section>
<section class="sec"><div class="sec-h"><h2>What the data says</h2><span class="k">derived from this run</span></div><div class="finds">${solid}${warn}</div></section>
${acts}
${plates}
${logbook}
<section class="sec"><div class="sec-h"><h2>What to watch for</h2><span class="k">handbook halts &amp; traps</span></div><div class="finds">${road}</div></section>
${prov}
<footer><div class="fp">${src}</div><div style="margin-top:6px">Generated by APEXA · ${gen}</div></footer>
</div>
<script>
document.querySelectorAll(".copy").forEach(function(b){
  b.addEventListener("click", function(){
    var t = b.getAttribute("data-cmd") || "";
    var done = function(){ var o=b.textContent; b.textContent="copied"; setTimeout(function(){b.textContent=o;},1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(done, function(){ fallback(t, done); });
    } else { fallback(t, done); }
  });
});
function fallback(t, done){
  // file:// pages have no clipboard API in several browsers, and this report is
  // meant to be opened straight off disk on a beamline machine.
  var a=document.createElement("textarea"); a.value=t;
  a.style.position="fixed"; a.style.opacity="0"; document.body.appendChild(a);
  a.select(); try{document.execCommand("copy"); done();}catch(e){a.remove(); return;}
  a.remove();
}
</script>'''
