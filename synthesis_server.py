#!/usr/bin/env python3
"""
APEXA In-situ Synthesis MCP Server — batch XRD analysis of thermal synthesis series
Phase evolution vs temperature for in-situ solid-state synthesis (e.g. Na-ion cathode
Na–Fe–P–O: NFPP / Na2FeP2O7 / NaFePO4), APS EDXRD, BNL and Rigaku lab data.

WHOSE METHODS
-------------
The analysis methods are I. A. Shkrob's (ANL CSE). His multiphase engine
(`XRD_gemmi.Gemmi`: gemmi structure factors + lmfit, global T-quadratic lattice model,
per-scan NNLS scales) is imported **unmodified** from `APEXA_SYNTH_CODE` — it is not
vendored, because it is unreleased. The pieces of his other modules that cannot be
imported headless (they build `tk.Tk()` and pull GSAS-II at import) are re-implemented
in `_synthesis_runner.py`, each cited to its source line; the next-ΔT recommender port
reproduces his own `SVD_control_table` output exactly.

WHY A SEPARATE SERVER (and not gsas2_server)
--------------------------------------------
He moved off GSAS-II for everything but indexing because it was a black box he could
not see into during automated processing. So this server's contract is transparency:
every fit returns its per-scan residuals, which parameters ended on a bound, which
lattices were frozen by constraints and which phases fell to zero — not just fractions.
And it works on **batches** (a whole temperature series per call), never pattern by
pattern.

ROUTE-TO-NATIVE
---------------
Like tomo (`APEXA_TOMO_PYTHON`) and GSAS-II (`APEXA_GSASII_PYTHON`), the heavy stack
lives in its own interpreter, `APEXA_SYNTH_PYTHON` (Python 3.12: gemmi, lmfit,
numpy>=2, h5py, pandas+tables, deap). Nothing enters APEXA's base `.venv`. Absent env →
structured error, never a crash. Setup: docs/SYNTHESIS_SETUP.md.

NOTHING IS ACTUATED. `synth_recommend_step` is advisory; APEXA has no furnace or gas
channel.
"""
from typing import Any, Dict, List, Optional
import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

from mcp.server.mcpserver import MCPServer  # mcp>=2: FastMCP was renamed

logging.getLogger("mcp").setLevel(logging.WARNING)
logger = logging.getLogger("synthesis_server")

mcp = MCPServer("APEXA In-situ Synthesis XRD")

# The APEXA client spawns servers with the MCP SDK's default environment (HOME, PATH,
# USER, ... only), so APEXA_* variables exported in the operator's shell never arrive.
# Configuration therefore comes from .env, as for the MIDAS server; existing variables
# still win (load_dotenv does not override).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:  # noqa: BLE001 — absent python-dotenv: env vars or defaults only
    pass

DEFAULT_SYNTH_PYTHON = os.environ.get(
    "APEXA_SYNTH_PYTHON", str(Path.home() / ".apexa" / "envs" / "synth" / "bin" / "python"))
SYNTH_CODE = os.environ.get("APEXA_SYNTH_CODE", "")

_RUNNER = str(Path(__file__).resolve().parent / "_synthesis_runner.py")
_ENV_PROBE_CACHE: Optional[str] = None


def format_result(result: dict) -> str:
    return json.dumps(result, indent=2)


def _env_ok(need_engine: bool = False) -> Optional[str]:
    """Actionable error string if the synthesis env cannot run, else None.

    `need_engine` additionally requires his XRD_gemmi.py (only synth_fit_series uses
    it; inspect / pca / recommend / kinetics are APEXA code and run without it).
    """
    global _ENV_PROBE_CACHE
    py = Path(DEFAULT_SYNTH_PYTHON)
    if not py.exists():
        return (f"Synthesis python not found at {py}. Set APEXA_SYNTH_PYTHON to a Python "
                "3.12 env with gemmi, lmfit, numpy>=2, h5py, pandas, tables, deap. "
                "See docs/SYNTHESIS_SETUP.md.")
    if _ENV_PROBE_CACHE is None:
        try:
            p = subprocess.run(
                [str(py), "-I", "-c", "import numpy, h5py, pandas, tables, scipy, gemmi, lmfit; print('ok')"],
                capture_output=True, text=True, timeout=60)
            _ENV_PROBE_CACHE = "" if p.returncode == 0 else (
                "APEXA_SYNTH_PYTHON cannot import its stack: " + (p.stderr or "").strip()[-400:])
        except Exception as e:  # noqa: BLE001
            _ENV_PROBE_CACHE = f"probe of APEXA_SYNTH_PYTHON failed: {e}"
    if _ENV_PROBE_CACHE:
        return _ENV_PROBE_CACHE
    if need_engine and not (SYNTH_CODE and (Path(SYNTH_CODE) / "XRD_gemmi.py").is_file()):
        return ("APEXA_SYNTH_CODE must point at the directory containing the collaborator's "
                f"XRD_gemmi.py (currently {SYNTH_CODE or 'unset'}). The multiphase fit uses his "
                "engine unmodified and it is not shipped with APEXA.")
    return None


def _run_runner(subcmd: str, args: List[str], timeout: int = 600,
                need_engine: bool = False) -> Dict[str, Any]:
    """Run `_synthesis_runner.py <subcmd>` on the synthesis interpreter; parse the
    last `{` line of stdout (same contract as tomo_server._run_runner)."""
    err = _env_ok(need_engine)
    if err:
        return {"status": "error", "error": err}
    env = dict(os.environ, MPLBACKEND="Agg", PYTHONNOUSERSITE="1")
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env.setdefault(v, "4")
    try:
        # -I: isolated mode, so nothing in a data directory can shadow a stdlib module.
        p = subprocess.run([DEFAULT_SYNTH_PYTHON, "-I", _RUNNER, subcmd, *args],
                           capture_output=True, text=True, timeout=timeout, env=env,
                           cwd=str(Path.home()))
    except subprocess.TimeoutExpired:
        return {"status": "error",
                "error": f"Timed out after {timeout}s. A global map fit scales with scans × "
                         "phases × reflections — narrow the window (begin/end), the d range, "
                         "or the phase list, then retry."}
    if p.returncode != 0 and not p.stdout:
        return {"status": "error", "error": (p.stderr or "")[-1500:]}
    for line in reversed(p.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:  # noqa: BLE001
                continue
    return {"status": "error", "error": "no JSON payload on stdout",
            "stdout_tail": p.stdout[-800:], "stderr_tail": (p.stderr or "")[-800:]}


def _window(args: List[str], begin: Optional[int], end: Optional[int],
            output_dir: str) -> List[str]:
    if begin is not None:
        args += ["--begin", str(begin)]
    if end is not None:
        args += ["--end", str(end)]
    if output_dir:
        args += ["--output-dir", output_dir]
    return args


def _tag(tool: str, out: Dict[str, Any]) -> Dict[str, Any]:
    out.setdefault("tool", tool)
    if out.get("status") == "error":
        out.setdefault("note", "Nothing was computed — do not report fractions, steps or "
                               "kinetics for this call.")
    return out


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def synth_inspect_series(h5_path: str, d_min: float = 0.0,
                               d_max: Optional[float] = None,
                               output_dir: str = "") -> str:
    """Read a processed in-situ synthesis series and give a model-free quick look.

    Input is a `processed_<sample>.h5` (one group per scan with its diffractogram on a
    shared d-spacing axis, plus a table with temperature `TC` and time `t`). Returns the
    scan count, T and d ranges, metadata, and per-scan total counts + number of peaks vs
    T — the cheap signal for amorphization (counts drop, few peaks) and crystallization
    (peak count rises). Use it FIRST to choose the fit window (`begin`) for
    synth_fit_series. Read-only except a small quick-look CSV.

    Args:
        h5_path: path to processed_<sample>.h5.
        d_min, d_max: d-spacing window in Å (clamped to the data's range).
        output_dir: where the quick-look CSV goes (default <data>/apexa_synthesis/inspect).
    """
    args = ["--h5", h5_path, "--d-min", str(d_min)]
    if d_max is not None:
        args += ["--d-max", str(d_max)]
    out = await asyncio.to_thread(_run_runner, "inspect", _window(args, None, None, output_dir), 300)
    return format_result(_tag("synth_inspect_series", out))


@mcp.tool()
async def synth_pca_map(h5_path: str, n_pcs: int = 3, d_min: float = 0.0,
                        d_max: Optional[float] = None, begin: Optional[int] = None,
                        end: Optional[int] = None, accept: float = 0.0,
                        output_dir: str = "") -> str:
    """SVD/PCA of the (scans × d) map — where does the pattern change, vs temperature?

    Same normalisation as the collaborator's map analysis: each PC score divided by its
    largest-|value| entry. Kinks or sign changes in PC1/PC2 mark transitions. Frames whose
    maximum is below `accept` × global max are excluded. Writes SVD_table_<sample>.csv.

    Args:
        h5_path: processed_<sample>.h5.
        n_pcs: number of components to report.
        d_min, d_max: d window in Å (his temperature map used d_min=2.3).
        begin, end: scan index window.
        accept: frame-acceptance fraction of the global max (0 keeps all).
        output_dir: default <data>/apexa_synthesis/pca.
    """
    args = ["--h5", h5_path, "--n-pcs", str(n_pcs), "--d-min", str(d_min), "--accept", str(accept)]
    if d_max is not None:
        args += ["--d-max", str(d_max)]
    out = await asyncio.to_thread(_run_runner, "pca", _window(args, begin, end, output_dir), 300)
    return format_result(_tag("synth_pca_map", out))


@mcp.tool()
async def synth_fit_series(h5_path: str, phases: List[str],
                           abc_scale_permil: Optional[List[float]] = None,
                           begin: Optional[int] = None, end: Optional[int] = None,
                           d_min: float = 0.0, d_max: Optional[float] = None,
                           cif_dir: str = "", constraints: str = "",
                           sigma: float = 0.01, d_dependent_width: bool = False,
                           lattice_window: float = 0.05, output_dir: str = "") -> str:
    """Global multiphase fit of a WHOLE temperature series in one call (batch, not per scan).

    Runs the collaborator's engine (XRD_gemmi.fit_map_LM, unmodified): every lattice
    parameter follows p0 + p1·z + p2·z² with z = T/<T> − 1, peak width is shared per
    phase, and each scan's phase scales are solved by non-negative least squares. Cells
    are first seeded by fitting the first SVD loading, as in his recipe. Returns volume
    fractions, cells and volumes per scan vs T, and a TRANSPARENCY block: per-scan Rp and
    RMS residual, the worst scan, parameters that ended on a bound, lattices frozen by
    constraints, phases with zero scale. Writes a table CSV, the global-parameter JSON,
    an arrays .npz (observed/calculated/per-phase patterns) and a manifest for
    synth_report. Can take minutes.

    His Sample16 heating call: phases ["NFPP","Na2FeP2O7","NaFePO4"],
    abc_scale_permil [5,10,-10], begin 28, constraints "cooling", d_dependent_width True.

    Args:
        h5_path: processed_<sample>.h5.
        phases: CIF base names, e.g. ["NFPP", "Na2FeP2O7", "NaFePO4"]; resolved
            case-insensitively and recursively under cif_dir.
        abc_scale_permil: per-phase starting-cell inflation in parts per THOUSAND
            (thermal expansion head start; negative shrinks). Same length as phases.
        begin, end: scan window — fit only where the phases exist (use the quick look).
        d_min, d_max: d window in Å.
        cif_dir: directory holding the CIFs (default: the data directory).
        constraints: optional thermal-lattice constraints JSON (path, or a base name in
            cif_dir, e.g. "cooling") from a previous fit; matching phases' lattices are
            renormalised to this <T> and frozen.
        sigma: initial Gaussian peak width in Å (bounded to [σ/10, 10σ]).
        d_dependent_width: add a quadratic-in-d width term (his `SD`).
        lattice_window: ± fractional window for p0 around the seeded cell.
        output_dir: default <data>/apexa_synthesis/fit.
    """
    if not phases:
        return format_result({"tool": "synth_fit_series", "status": "error",
                              "error": "phases is required (CIF base names)."})
    if abc_scale_permil is not None and len(abc_scale_permil) != len(phases):
        return format_result({"tool": "synth_fit_series", "status": "error",
                              "error": f"abc_scale_permil has {len(abc_scale_permil)} values "
                                       f"for {len(phases)} phases."})
    args = ["--h5", h5_path, "--phases", ",".join(phases), "--d-min", str(d_min),
            "--sigma", str(sigma), "--eps", str(lattice_window)]
    if d_max is not None:
        args += ["--d-max", str(d_max)]
    if abc_scale_permil is not None:
        args += ["--abc-scale", ",".join(str(v) for v in abc_scale_permil)]
    if cif_dir:
        args += ["--cif-dir", cif_dir]
    if constraints:
        args += ["--constraints", constraints]
    if d_dependent_width:
        args += ["--sd"]
    out = await asyncio.to_thread(_run_runner, "fit", _window(args, begin, end, output_dir),
                                  3600, True)
    return format_result(_tag("synth_fit_series", out))


@mcp.tool()
async def synth_recommend_step(signal: str = "pca", h5_path: str = "", table_csv: str = "",
                               box: int = 5, change_percent: float = 2.0,
                               min_step: float = 2.0, max_step: float = 20.0,
                               d_min: float = 2.3, begin: Optional[int] = None,
                               end: Optional[int] = None, replay: bool = False,
                               fixed_grid: Optional[int] = None,
                               output_dir: str = "") -> str:
    """Recommend the next temperature step from how the diffraction is evolving. ADVISORY.

    The collaborator's control rule (a faithful port that reproduces his own output):
    take the last `box` points of the PC trajectories, fit a quadratic, extrapolate
    forward, and step to the first ΔT where the mean |Δscore|/σ reaches
    `change_percent` — small steps where the pattern is about to change, large steps
    where it is quiet — clamped to [min_step, max_step]. `replay=True` also back-tests
    the rule over the recorded series (what it would have suggested at each scan).
    Nothing is actuated: APEXA has no furnace/gas channel; the operator applies it.

    Args:
        signal: "pca" (his method, needs h5_path) or "fractions" (APEXA extension: run
            the same rule on fitted phase fractions, needs a synth_fit_series table_csv).
        h5_path: processed_<sample>.h5 for signal="pca".
        table_csv: fit_series_table_<sample>.csv for signal="fractions".
        box: sliding window of recent scans for the extrapolation.
        change_percent: change threshold (his runs used 2).
        min_step, max_step: step clamp in °C (his runs: 2 and 20).
        d_min: d window floor for the PCA (his control runs used 2.3 Å).
        begin, end: scan window.
        replay: back-test over the whole series.
        fixed_grid: use a fixed step grid instead of his scan-count grid (reported).
        output_dir: where a replay CSV is written.
    """
    if signal not in ("pca", "fractions"):
        return format_result({"tool": "synth_recommend_step", "status": "error",
                              "error": "signal must be 'pca' or 'fractions'."})
    args = ["--signal", signal, "--box", str(box), "--change", str(change_percent),
            "--dmin", str(min_step), "--dmax", str(max_step), "--d-min", str(d_min)]
    if h5_path:
        args += ["--h5", h5_path]
    if table_csv:
        args += ["--table", table_csv]
    if replay:
        args += ["--replay"]
    if fixed_grid:
        args += ["--grid", str(fixed_grid)]
    out = await asyncio.to_thread(_run_runner, "recommend", _window(args, begin, end, output_dir), 300)
    return format_result(_tag("synth_recommend_step", out))


@mcp.tool()
async def synth_fit_kinetics(table_csv: str, phase_column: str, direction: str = "decay",
                             avrami_n: Optional[float] = None, temperature_column: str = "TC",
                             time_column: str = "t") -> str:
    """Fit Avrami/JMAK kinetics to a phase fraction vs temperature (replaces the Igor step).

    Normalises the fraction to α (decay: f/f_first — a phase being consumed), then fits
    α = exp(−(k·t)^n) with k = A·exp(−Ea/RT), evaluating T per scan (non-isothermal).
    Returns Ea (kJ/mol), n, their errors, the fitted curve and residual. The form is
    APPROXIMATE: the collaborator's exact Igor model is not known, so compare Ea/n with
    his values only with that caveat.

    Args:
        table_csv: a synth_fit_series table (fit_series_table_<sample>.csv).
        phase_column: the fraction column, e.g. "weight1" (phase order = fit order).
        direction: "decay" (reactant consumed — usual) or "growth".
        avrami_n: fix n (e.g. 2 or 3) instead of fitting it.
        temperature_column, time_column: column names (°C, seconds).
    """
    args = ["--table", table_csv, "--phase-col", phase_column, "--direction", direction,
            "--T-col", temperature_column, "--t-col", time_column]
    if avrami_n is not None:
        args += ["--n-fixed", str(avrami_n)]
    out = await asyncio.to_thread(_run_runner, "kinetics", args, 120)
    return format_result(_tag("synth_fit_kinetics", out))


@mcp.tool()
async def synth_report(fit_dir: str, output_path: str = "",
                       recommend_json: str = "", kinetics_json: str = "") -> str:
    """Write the refinement-visualization report for a synth_fit_series run.

    One self-contained offline HTML (and a `report_spec` the APEXA UI renders live):
    observed vs calculated with per-phase components and residual for the first, middle,
    last and worst scans; a residual heat map across the whole series; phase fractions
    vs T; lattice volumes vs T; and the verdict — LOOK when a parameter railed on a
    bound, a phase vanished mid-series or the fit is poor in some scans, each with the
    lever to pull.

    Args:
        fit_dir: the synth_fit_series output directory (holds apexa_synthesis_manifest.json).
        output_path: HTML path (default <fit_dir>/synthesis_report_<sample>.html).
        recommend_json, kinetics_json: optional saved results of synth_recommend_step /
            synth_fit_kinetics to include.
    """
    try:
        from apexa_synthesis_report import write_report
        out = await asyncio.to_thread(write_report, fit_dir, output_path or None,
                                      recommend_json or None, kinetics_json or None)
        return format_result({"tool": "synth_report", **out})
    except Exception as e:  # noqa: BLE001
        return format_result({"tool": "synth_report", "status": "error", "error": str(e)})


if __name__ == "__main__":
    mcp.run()
