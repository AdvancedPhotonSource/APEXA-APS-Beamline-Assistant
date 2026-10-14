#!/usr/bin/env python3
"""
Agentic GSAS-II MCP Server
Autonomous Rietveld refinement: derivative-driven parameter selection,
physical guardrails, structure retrieval, and a trust verdict per result.
Author: Beamline Assistant Team
Organization: Argonne National Laboratory

RELATIONSHIP TO run_gsas_refinement (midas_comprehensive_server.py)
-------------------------------------------------------------------
That tool refines MIDAS caked output (.zarr.zip) with a fixed recipe and
is the right choice inside a MIDAS pipeline. It is not superseded here.

This server is for the other case: an ordinary powder pattern from any
source, refined by an agent that chooses its own parameter order, can
retrieve its own starting structure, and reports whether the result
should be believed. Parameter names that mean the same thing in both
tools are deliberately spelled the same way, so a user who knows one can
read the other.

LONG-RUNNING BY NATURE
----------------------
A refinement takes minutes; a joint multi-instrument fit can take hours.
Tools that can exceed a few minutes therefore submit and return a job
id, and `refinement_status` polls. Blocking an agent that is driving a
beamline for the length of a Rietveld refinement is not acceptable, and
the failure mode is a stalled campaign rather than a slow answer.

Requires the GSAS-II conda environment (which provides GSASIIscriptable)
and the agentic_gsas2 package on that interpreter's path.
"""
from typing import Any, Dict, List, Optional
import json
import asyncio
import os
import subprocess
import sys
import time
import uuid
import re
import textwrap
from pathlib import Path

import logging

from mcp.server.mcpserver import MCPServer  # mcp>=2: FastMCP was renamed
from _idempotency import idempotent  # skip-if-done guard for heavy tools

logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)
logger = logging.getLogger("gsas2_server")

mcp = MCPServer("Agentic GSAS-II")

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
# The framework runs on the GSAS-II interpreter, not on whichever one is
# hosting this server. Resolution order mirrors midas_comprehensive_server's
# find_gsasii_python so the two agree on one install.
DEFAULT_GSASII_PYTHON = os.environ.get(
    "APEXA_GSASII_PYTHON",
    str(Path.home() / "miniconda3" / "envs" / "GSASII" / "bin" / "python"))
DEFAULT_AGENTIC_REPO = os.environ.get(
    "APEXA_AGENTIC_GSAS2", str(Path.home() / "Git" / "Agentic-GSAS-II"))

JOBS_DIR = Path(os.environ.get(
    "APEXA_GSAS2_JOBS", Path.home() / ".apexa" / "gsas2_jobs"))


def format_result(result: dict) -> str:
    """Format results into a readable JSON string."""
    return json.dumps(result, indent=2)


def _env_ok() -> Optional[str]:
    """Return an error string if the framework cannot be reached."""
    py = Path(DEFAULT_GSASII_PYTHON)
    if not py.exists():
        return (f"GSAS-II python not found at {py}. Set APEXA_GSASII_PYTHON "
                f"to the interpreter that has GSASIIscriptable.")
    repo = Path(DEFAULT_AGENTIC_REPO)
    if not (repo / "agentic_gsas2").is_dir():
        return (f"agentic_gsas2 not found under {repo}. Set "
                f"APEXA_AGENTIC_GSAS2 to the repository root.")
    return None


def _run_python(code: str, timeout: int = 900) -> Dict[str, Any]:
    """Run a snippet on the GSAS-II interpreter and parse its JSON stdout.

    The framework is imported in a subprocess rather than in-process on
    purpose: GSAS-II carries global state and can abort hard, and an
    agent driving a beamline should survive that.
    """
    err = _env_ok()
    if err:
        return {"status": "error", "error": err}
    env = dict(os.environ)
    env["PYTHONPATH"] = DEFAULT_AGENTIC_REPO + os.pathsep + env.get(
        "PYTHONPATH", "")
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env.setdefault(v, "1")
    try:
        p = subprocess.run([DEFAULT_GSASII_PYTHON, "-c", code],
                           capture_output=True, text=True, timeout=timeout,
                           env=env)
    except subprocess.TimeoutExpired:
        return {"status": "error",
                "error": f"Timed out after {timeout}s. Inspect output/logs before retrying. "
                         "For pattern series use refine_series_submit and refinement_status."}
    if p.returncode != 0:
        return {"status": "error", "error": (p.stderr or "")[-1500:]}
    # The framework prints progress to stdout, so take the last JSON object.
    for line in reversed(p.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:                          # noqa: BLE001
                continue
    return {"status": "error", "error": "no JSON payload on stdout",
            "stdout_tail": p.stdout[-800:]}


# ---------------------------------------------------------------------------
# Trust verdict
# ---------------------------------------------------------------------------
def _trust(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a refinement summary into an act-on-it / look-at-it verdict.

    An automated fit always returns something. In a closed loop the
    question is not "what is Rwp" but "should the next experiment be
    chosen from this", and those are different questions -- a wrong cell
    that stops predicting any peak costs almost nothing in the residual.

    Flags are deliberately conservative and each names the specific
    reason, so the agent can act on the reason rather than on a score.
    """
    flags: List[str] = []
    if summary.get("scientific_status") != "qualified":
        flags.append("scientific qualification is " + str(summary.get("scientific_status", "unavailable")))
    # Findings the framework itself recorded, each a symptom named in
    # Toby's textbook with the cause it assigns. Carried through rather
    # than re-derived here, so the MCP layer and the framework cannot
    # drift into disagreeing about the same run.
    diag = summary.get("diagnostics") or {}
    for f in diag.get("findings", []):
        if f.get("severity") in ("blocker", "warning"):
            flags.append(f"{f.get('what')} -- {f.get('means')}")
    stop = summary.get("stop_reason")
    if stop == "wall_clock":
        flags.append("stopped on a time budget, not on convergence")
    if stop == "max_steps":
        flags.append("hit the step cap; the fit was still improving")
    rwp = summary.get("Rwp")
    if rwp is not None and rwp > 40:
        flags.append(f"Rwp {rwp:.1f}% is high in absolute terms")
    # The Le Bail comparison is made by agentic_gsas2.diagnostics and
    # arrives above; it is not repeated here.
    cr = summary.get("cell_route") or {}
    if cr.get("rolled_back"):
        flags.append("direct cell refinement was rolled back")
    moved = summary.get("history") or []
    if not moved:
        flags.append("no parameter was accepted; the model did not move")
    return {"trustworthy": not flags,
            "flags": flags,
            "note": ("act on this result" if not flags else
                     "inspect before acting; reasons listed")}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
@idempotent(
    tool="refine_pattern",
    anchor=lambda kw: kw.get("output_dir") or "",
    salient=lambda kw: {k: kw.get(k) for k in (
        "data_file", "cif_files", "instprm_file", "bkg_terms",
        "two_theta_limits", "wavelength_A", "fmthint", "refine_atoms",
        "max_steps")},
)
async def refine_pattern(
    data_file: str,
    cif_files: List[str],
    output_dir: str = "refinement",
    instprm_file: Optional[str] = None,
    bkg_terms: int = 6,
    two_theta_limits: Optional[List[float]] = None,
    wavelength_A: Optional[float] = None,
    fmthint: Optional[str] = None,
    refine_atoms: bool = True,
    max_steps: int = 100,
    max_wall_seconds: float = 0.0,
    return_trace: bool = False,
    force: bool = False,
) -> str:
    """Refine one powder pattern autonomously and report whether to trust it.

    Chooses its own parameter order from the engine's chi-squared
    derivatives under physical guardrails; no recipe is supplied. Accepts
    any powder format GSAS-II reads (.xye, .fxye, .dat, GSAS native) --
    for MIDAS caked .zarr.zip use run_gsas_refinement instead.

    Args:
        data_file: powder pattern.
        cif_files: starting structures; retrieve a candidate pool with propose_structures first.
        output_dir: where the GPX, CIF, plots and decision trace are written.
        instprm_file: GSAS-II instrument parameter file.
        bkg_terms: Chebyshev background terms.
        two_theta_limits: [min, max] refinement window.
        wavelength_A: override the wavelength in the instrument file.
        fmthint: GSAS-II reader hint. Required for the GSAS legacy formats
            (.XRA, .CWN, .fxye) -- pass "GSAS". Leave unset for two-column
            text and .xye, which are auto-detected. Without it a legacy
            file is handed to the text reader and fails on its header.
        refine_atoms: allow atomic coordinates and displacement parameters.
        max_steps: cap on accepted refinement steps.
        max_wall_seconds: throughput bound; 0 disables. A run stopped this
            way is reported as such and is NOT a converged result.
        return_trace: include the full per-step decision history.
        force: bypass the skip-if-done guard.

    Returns JSON: Rwp, GOF, cell, phase fractions, stop reason, and a
    trust verdict naming any reason the result should be inspected.
    """
    if not cif_files:
        return format_result({"tool": "refine_pattern", "status": "error",
                              "error": "Supply CIFs. Use propose_structures for retrieval, then powder_refine_submit(mode='search') to compare mixtures."})
    code = f"""
import json, sys
sys.path.insert(0, {DEFAULT_AGENTIC_REPO!r})
from dataclasses import replace
from agentic_gsas2 import AgentConfig
from agentic_gsas2.orchestrator import refine_one
cfg = replace(AgentConfig(), refine_atoms={refine_atoms!r})
cfg = replace(cfg, worstfit=replace(cfg.worstfit, max_steps={max_steps!r},
              max_wall_seconds={max_wall_seconds!r}))
cfg = replace(cfg, bkg_terms={bkg_terms!r})
kw = {{}}
if {two_theta_limits!r}: kw["limits"] = tuple({two_theta_limits!r})
if {wavelength_A!r} is not None: kw["wavelength_A"] = {wavelength_A!r}
if {fmthint!r}: kw["fmthint"] = {fmthint!r}
s = refine_one(data_path={data_file!r}, cif_paths={cif_files!r},
               output_dir={output_dir!r}, instprm_path={instprm_file!r},
               config=cfg, **kw)

# What the run declined to touch, and on whose authority. A refinement
# that silently omits a parameter cannot be told apart from one that
# never considered it, so the policy is reported beside the result.
try:
    from agentic_gsas2.experiment import CITATION
    offered = list(cfg.structure.instprm_parameters)
    reasons = {{"Z": CITATION["profile"], "Zero": CITATION["zero"],
               "X": CITATION["broadening"], "Y": CITATION["broadening"],
               "SH/L": CITATION["profile"]}}
    s["instrument_policy"] = {{
        "refined": offered,
        "withheld": {{k: v for k, v in reasons.items() if k not in offered}},
    }}
except Exception:
    pass
print(json.dumps(s, default=str))
"""
    out = await asyncio.to_thread(_run_python, code, timeout=1800)
    if out.get("status") == "error":
        return format_result({"tool": "refine_pattern", **out})
    verdict = _trust(out)
    payload = {
        "tool": "refine_pattern", "status": "success",
        "Rwp": out.get("Rwp"), "GOF": out.get("GOF"),
        "stop_reason": out.get("stop_reason"),
        "steps": out.get("step_count"),
        "phases": out.get("phases"),
        **{k: out.get(k) for k in ("weight_fractions", "phase_fraction_reports", "effective_cells",
                                   "execution_status", "numerical_status", "scientific_status", "qualification",
                                   "evidence_coverage", "input_identity", "gpx_path", "provenance", "diagnostics")},
        "output_dir": output_dir,
        "trust": verdict,
        "instrument_policy": out.get("instrument_policy"),
    }
    if return_trace:
        # Each step carries both the parameter ComputeWorstFit ranked
        # and the terms actually released, which differ for the grouped
        # categories: a step logged against ":0:Zero" may have refined
        # U, V and W and left Zero fixed.
        payload["decision_trace"] = out.get("history")
    return format_result(payload)


@mcp.tool()
async def propose_structures(
    data_file: str,
    text: str,
    top_n: int = 3,
    wavelength_A: float = 1.5406,
    out_dir: str = "cod_candidates",
    fmthint: Optional[str] = None,
    instprm_file: Optional[str] = None,
) -> str:
    """Find candidate structures for a pattern when you have no CIF.

    Queries the Crystallography Open Database and ranks hits by de Wolff
    M20 against the peak positions in this pattern. Only the text hint
    and the observed peaks influence the ranking.

    M20 doubles as a confidence signal that needs no ground truth: on a
    240-pattern mineral benchmark, candidates scoring M20 >= 20 were the
    wrong phase 3.2% of the time against 17.6% below M20 = 5. Treat a low
    M20 as a reason to widen the search rather than to refine.

    Args:
        data_file: powder pattern, two columns or any format numpy loads.
            For a GSAS legacy file (.XRA, .CWN, .fxye) pass fmthint too.
        text: what the sample is thought to be, e.g. a phase or mineral name.
        top_n: how many candidates to fetch.
        wavelength_A: wavelength of the measurement.
        out_dir: where the retrieved CIFs are written.
        fmthint: GSAS-II reader hint, as for refine_pattern. Pass "GSAS"
            for the legacy formats; leave unset for two-column text.
        instprm_file: instrument parameter file, used only when fmthint
            is given, because the GSAS-II reader wants one to load a
            histogram.

    Returns JSON: ranked candidates with COD id, space group, cell, M20
    and the local CIF path for each.
    """
    from apexa_network import tool_unavailable_reason
    unavailable = tool_unavailable_reason("propose_structures")
    if unavailable:
        return format_result({"tool": "propose_structures", "status": "error", "error": unavailable})
    code = f"""
import json, sys
import numpy as np
sys.path.insert(0, {DEFAULT_AGENTIC_REPO!r})
from agentic_gsas2.cod import propose_structures
# A GSAS legacy file (.XRA, .CWN, .fxye) has a text header that
# np.loadtxt cannot parse -- it dies on "could not convert string
# 'CPD' to float64". With a fmthint, hand it to the GSAS-II reader
# instead, which is the same path refine_pattern takes. No phase is
# needed just to read the pattern.
if {fmthint!r}:
    from agentic_gsas2.project import Project, HistogramSpec
    import tempfile
    from pathlib import Path
    _scratch = Path(tempfile.mkdtemp())
    _spec = HistogramSpec(data_path={data_file!r},
                          instprm_path={instprm_file!r},
                          limits=None, fmthint={fmthint!r})
    _pat = Project.single_histogram(
        _spec, [], _scratch / "read.gpx").pattern(0)
    x, y = np.asarray(_pat["x"]), np.asarray(_pat["yobs"])
else:
    d = np.loadtxt({data_file!r})
    x, y = d[:, 0], d[:, 1]
c = propose_structures(x, y, {wavelength_A!r}, text={text!r},
                       top_n={top_n!r}, out_dir={out_dir!r})
print(json.dumps({{"status": "success", "candidates": [
    {{"rank": z.rank, "cod_id": z.entry.cod_id,
      "spacegroup": z.entry.spacegroup, "M20": round(z.fom["M"], 3),
      "cell": list(z.entry.cell), "cif": str(z.cif_path)}} for z in c]}},
    default=str))
"""
    out = await asyncio.to_thread(_run_python, code, timeout=600)
    return format_result({"tool": "propose_structures", **out})


@mcp.tool()
async def plan_refinement(
    data_file: str,
    instprm_file: Optional[str] = None,
    fmthint: Optional[str] = None,
    geometry: Optional[str] = None,
    space_group: Optional[str] = None,
    instrument_profile_calibrated: Optional[bool] = None,
    n_phases: int = 1,
) -> str:
    """Say how this pattern should be refined, before refining it.

    Reads the measurement, works out which parameters it physically
    supports, and returns the staged plan with the reason and the
    textbook citation for each stage. Call this first when the
    instrument or geometry is unfamiliar, or whenever a user asks why
    a parameter was or was not refined.

    The plan can come back incomplete. Diffraction geometry is not
    recoverable from an X-ray pattern, and it decides which
    sample-displacement parameter exists at all -- Shift in
    Bragg-Brentano, DisplaceX in Debye-Scherrer. Rather than guess,
    the plan marks that category blocked and returns the question in
    "blocking_questions". Put that question to the user, then call
    again with "geometry" set. Everything not depending on the answer
    stays available meanwhile, so an unanswered question costs the
    displacement term and not the refinement.

    Args:
        data_file: powder pattern.
        instprm_file: GSAS-II instrument parameter file.
        fmthint: reader hint; pass "GSAS" for .XRA/.CWN/.fxye.
        geometry: bragg_brentano | debye_scherrer | area_detector |
            time_of_flight. Supply it when known; omit it to be asked.
        space_group: main phase, Hermann-Mauguin. Fixes how many
            independent anisotropic microstrain terms exist.
        instrument_profile_calibrated: true if U, V, W come from a
            standard on this instrument, in which case they are held
            rather than refined against this sample.
        n_phases: number of phases to be refined.

    Returns JSON: strategy, ordered stages with parameters and
    citations, blocking questions, parameters withheld and why, and
    the experiment context the plan was built from.
    """
    code = f"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, {DEFAULT_AGENTIC_REPO!r})
from dataclasses import replace
from agentic_gsas2.experiment import (ExperimentContext, Geometry,
                                      infer_from_project)
from agentic_gsas2.recommend import recommend
from agentic_gsas2.project import Project, HistogramSpec

spec = HistogramSpec(data_path={data_file!r},
                     instprm_path={instprm_file!r}, limits=None,
                     fmthint=({fmthint!r} or "xye"))
proj = Project.single_histogram(spec, [],
                                Path(tempfile.mkdtemp()) / "plan.gpx")
ctx = infer_from_project(proj, space_group={space_group!r})
if {geometry!r}:
    ctx = replace(ctx, geometry=Geometry({geometry!r}))
plan = recommend(ctx, n_histograms=1, n_phases={n_phases!r},
                 instrument_profile_calibrated=(
                     {instrument_profile_calibrated!r}))
print(json.dumps(plan.to_dict(), default=str))
"""
    out = await asyncio.to_thread(_run_python, code, timeout=300)
    if out.get("status") == "error":
        return format_result({"tool": "plan_refinement", **out})
    return format_result({"tool": "plan_refinement", "status": "success",
                          **out})


@mcp.tool()
async def assess_refinement(output_dir: str) -> str:
    """Re-read a finished refinement and return only the trust verdict.

    For deciding whether an earlier result should still be acted on --
    for example before a campaign reuses it to choose the next condition.

    Args:
        output_dir: directory a previous refine_pattern call wrote to.
    """
    p = Path(output_dir)
    cand = [p / "refinement_summary.json", p / "summary.json"]
    src = next((c for c in cand if c.is_file()), None)
    if src is None:
        return format_result({"tool": "assess_refinement", "status": "error",
                              "error": f"no refinement summary under {p}"})
    try:
        s = json.loads(src.read_text())
    except Exception as e:                             # noqa: BLE001
        return format_result({"tool": "assess_refinement", "status": "error",
                              "error": f"{type(e).__name__}: {e}"})
    return format_result({"tool": "assess_refinement", "status": "success",
                          "source": str(src), "Rwp": s.get("Rwp"),
                          "stop_reason": s.get("stop_reason"),
                          "trust": _trust(s)})


@mcp.tool()
async def refine_series_submit(
    data_files: List[str],
    cif_files: List[str],
    output_dir: str = "series_refinement",
    instprm_file: Optional[str] = None,
    warm_start: bool = True,
    max_steps: int = 100,
    fmthint: Optional[str] = None,
) -> str:
    """Start a sequential refinement over a stack of patterns; returns a job id.

    For an in-situ series -- a temperature ramp, a gas change, a
    composition sweep -- where each pattern is refined in order and can
    warm-start from the previous converged model.

    Submits rather than blocks: a stack takes minutes per pattern and an
    agent driving an instrument must not wait on it. Poll with
    refinement_status.

    Warm starts transfer compatible model state into each new observation.
    Reported reference and effective cells remain provisional until validated.

    Args:
        data_files: patterns in acquisition order.
        cif_files: starting structures, shared across the series.
        output_dir: root for per-pattern outputs.
        instprm_file: instrument parameter file.
        warm_start: seed each pattern from the previous converged model.
        max_steps: cap per pattern.
        fmthint: GSAS-II reader hint; pass "GSAS" for legacy .XRA/.CWN/.fxye.
    """
    if not data_files or not cif_files or not instprm_file:
        return format_result({"tool": "refine_series_submit", "status": "error",
                              "error": "Supply data, candidate CIFs and an instrument file."})
    observations = [{"data_path": f, "instrument_path": instprm_file,
                     "cif_paths": cif_files, "format": fmthint or "xye",
                     "frame_id": str(i)} for i, f in enumerate(data_files)]
    return await powder_refine_submit(observations, mode="sequential" if warm_start else "independent",
                                      output_dir=output_dir, max_steps=max_steps)


@mcp.tool()
async def refinement_status(job_id: str) -> str:
    """Progress of a refine_series_submit job.

    Args:
        job_id: id returned by refine_series_submit.
    """
    if re.fullmatch(r"[0-9a-f]{24}", job_id):
        from apexa_powder import status
        try:
            current = await asyncio.to_thread(status, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO,
                                              JOBS_DIR / "engine", [job_id])
            job = current["jobs"][0]
            return format_result({"tool": "refinement_status", "job_id": job_id,
                                  "status": job["state"], "jobs": [job]})
        except Exception as exc:
            return format_result({"tool": "refinement_status", "status": "error", "error": str(exc)})
    if not re.fullmatch(r"gsas2-[0-9a-f]{10}", job_id):
        return format_result({"tool": "refinement_status", "status": "error", "error": "Invalid job id"})
    job = JOBS_DIR / job_id
    if not job.is_dir():
        return format_result({"tool": "refinement_status", "status": "error",
                              "error": f"unknown job {job_id}"})
    manifest = job / "engine.json"
    if manifest.is_file():
        from apexa_powder import status
        info = json.loads(manifest.read_text())
        try:
            current = await asyncio.to_thread(status, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO,
                                              info["store"], info["job_ids"])
            jobs = current["jobs"]
            terminal = all(j["state"] in ("completed", "failed", "cancelled") for j in jobs)
            failed = sum(j["state"] in ("failed", "cancelled") for j in jobs)
            return format_result({"tool": "refinement_status", "job_id": job_id,
                                  "status": ("error" if failed == len(jobs) else "partial" if failed else "complete") if terminal else "running",
                                  "done": sum(j["state"] == "completed" for j in jobs), "total": len(jobs),
                                  "failed_patterns": failed, "jobs": jobs})
        except Exception as exc:
            return format_result({"tool": "refinement_status", "status": "error", "error": str(exc)})
    prog = job / "progress.json"
    done = (job / "done").exists()
    payload: Dict[str, Any] = {"tool": "refinement_status", "job_id": job_id,
                               "status": "complete" if done else "running"}
    if prog.is_file():
        try:
            p = json.loads(prog.read_text())
            payload.update(done=p.get("done"), total=p.get("total"),
                           results=p.get("results"))
        except Exception:                              # noqa: BLE001
            pass
    elif not done:
        payload["note"] = "started; no pattern has finished yet"
    results = payload.get("results") or []
    failed = sum(bool(r.get("error")) for r in results)
    payload["failed_patterns"] = failed
    if done and failed:
        payload["status"] = "error" if failed == len(results) else "partial"
    if (job / "error.json").exists():
        payload.update(status="error", error=json.loads((job / "error.json").read_text()).get("error"))
    elif not done and (job / "process.json").exists():
        try:
            pid = json.loads((job / "process.json").read_text())["pid"]
            os.kill(pid, 0)
        except ProcessLookupError:
            payload.update(status="error", error="Worker exited without completing; inspect log.txt before retrying.")
        except (OSError, ValueError, KeyError):
            payload.update(status="unknown", note="Worker state could not be established; inspect log.txt before retrying.")
    return format_result(payload)


@mcp.tool()
async def powder_refine_submit(
    observations: List[Dict[str, Any]], mode: str = "independent",
    output_dir: str = "powder_refinement", max_steps: int = 100, workers: int = 2,
) -> str:
    """Submit heterogeneous powder observations as independent, sequential, joint or phase-search jobs.

    Each observation requires data_path, instrument_path and cif_paths. Optional
    axis/units, format, source, sample_id, frame_id, conditions, calibration_id,
    limits and intensity_semantics preserve laboratory/synchrotron provenance.
    Joint mode requires explicit matching sample/condition/phase identity.
    Search treats cif_paths as the bounded pool. Poll refinement_status.
    """
    from apexa_powder import submit
    if mode not in ("independent", "sequential", "joint", "search") or not 1 <= workers <= 8 or not 1 <= max_steps <= 1000:
        return format_result({"tool": "powder_refine_submit", "status": "error", "error": "Invalid mode or resource budget"})
    error = _env_ok()
    if error:
        return format_result({"tool": "powder_refine_submit", "status": "error", "error": error})
    job_id = "gsas2-" + uuid.uuid4().hex[:10]
    folder = JOBS_DIR / job_id
    folder.mkdir(parents=True)
    try:
        result = await asyncio.to_thread(submit, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO,
                                         JOBS_DIR / "engine", observations, mode=mode, workers=workers,
                                         policy={"max_steps": max_steps})
        (folder / "engine.json").write_text(json.dumps(result))
        export = Path(output_dir).expanduser().resolve()
        export.mkdir(parents=True, exist_ok=True)
        (export / (job_id + ".json")).write_text(json.dumps(result, indent=2))
        return format_result({"tool": "powder_refine_submit", "status": "submitted", "job_id": job_id,
                              "mode": mode, **result, "poll_with": "refinement_status"})
    except Exception as exc:
        (folder / "error.json").write_text(json.dumps({"error": str(exc)}))
        return format_result({"tool": "powder_refine_submit", "status": "error", "error": str(exc)})


@mcp.tool()
async def powder_job_control(job_id: str, action: str) -> str:
    """Cancel, retry or resume a powder job group or individual live job."""
    if not re.fullmatch(r"(?:gsas2-[0-9a-f]{10}|[0-9a-f]{24})", job_id) or action not in ("cancel", "retry", "resume"):
        return format_result({"status": "error", "error": "Invalid job or action"})
    from apexa_powder import engine_call, start_broker
    manifest = JOBS_DIR / job_id / "engine.json"
    if len(job_id) == 24:
        info = {"store": str(JOBS_DIR / "engine"), "job_ids": [job_id]}
    elif not manifest.is_file():
        return format_result({"status": "error", "error": "No durable job manifest"})
    else:
        info = json.loads(manifest.read_text())
    try:
        if action != "resume":
            code = "from agentic_gsas2.jobs import JobStore\ns=JobStore(payload['store'])\nprint(json.dumps({i:getattr(s,payload['action'])(i) for i in payload['job_ids']}))"
            await asyncio.to_thread(engine_call, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO, code, {**info, "action": action})
        if action != "cancel":
            start_broker(DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO, info["store"])
        return format_result({"status": "success", "job_id": job_id, "action": action})
    except Exception as exc:
        return format_result({"status": "error", "error": str(exc)})


@mcp.tool()
async def powder_report(job_id: str, output_path: str = "powder_report.html") -> str:
    """Write a self-contained APEXA report with fit, coverage and scientific statuses."""
    from apexa_report import build_report, render_html
    try:
        current = json.loads(await refinement_status(job_id))
        if not current.get("jobs"):
            raise ValueError(current.get("error", "No durable powder results available"))
        spec = await asyncio.to_thread(build_report, current, technique="powder-refinement")
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_html(spec))
        return format_result({"tool": "powder_report", "status": "success", "path": str(path),
                              "verdict": spec.verdict, "summary": spec.verdict_note})
    except Exception as exc:
        return format_result({"tool": "powder_report", "status": "error", "error": str(exc)})


@mcp.tool()
async def powder_trial(evidence_json: str, proposal: Dict[str, Any], output_dir: str, shadow: bool = True) -> str:
    """Evaluate a typed multimodal or numerical proposal against its immutable evidence snapshot."""
    from apexa_powder import engine_call
    code = "from agentic_gsas2.trials import Proposal,evaluate_proposal\nfrom pathlib import Path\nb=json.loads(Path(payload['evidence']).read_text())\nprint(json.dumps(evaluate_proposal(Proposal(**payload['proposal']),b,payload['output'],shadow=payload['shadow'])))"
    try:
        result = await asyncio.to_thread(engine_call, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO, code,
                                        {"evidence": evidence_json, "proposal": proposal, "output": output_dir, "shadow": shadow}, 180)
        return format_result({"tool": "powder_trial", **result})
    except Exception as exc:
        return format_result({"tool": "powder_trial", "status": "error", "error": str(exc)})


@mcp.tool()
async def powder_evidence(gpx_path: str, observation_id: str, output_dir: str) -> str:
    """Create immutable global/peak-window evidence for numerical or image-based diagnosis."""
    from apexa_powder import engine_call
    code = "from agentic_gsas2.project import Project\nfrom agentic_gsas2.core import import_gsasii\nfrom agentic_gsas2.trials import evidence_bundle\nfrom pathlib import Path\np=Project(import_gsasii().G2Project(gpxfile=payload['gpx']),Path(payload['gpx']),[],[])\nprint(json.dumps(evidence_bundle(p,payload['observation_id'],payload['output'])))"
    try:
        result = await asyncio.to_thread(engine_call, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO, code,
                                        {"gpx": gpx_path, "observation_id": observation_id, "output": output_dir}, 120)
        return format_result({"tool": "powder_evidence", "status": "success", **result})
    except Exception as exc:
        return format_result({"tool": "powder_evidence", "status": "error", "error": str(exc)})


@mcp.tool()
async def powder_advise(evidence_json: str, output_dir: str, model: Optional[str] = None,
                        username: Optional[str] = None, evaluate_trials: bool = True) -> str:
    """Ask the configured image-capable structured provider for up to three evidence-bound proposals.

    Evaluations run in shadow mode and preserve the current model. A configured
    endpoint/model is required. Numerical batch refinement works without it.
    """
    model = model or os.environ.get("APEXA_POWDER_VISION_MODEL")
    username = username or os.environ.get("ARGO_USER") or os.environ.get("ANL_USERNAME")
    if not model or not username:
        return format_result({"tool": "powder_advise", "status": "unavailable",
                              "error": "Configure an image-capable model and APEXA/Argo username before a real vision trial."})
    from apexa_powder import advise, engine_call
    from apexa_provider_openai import OpenAICompatProvider
    provider = OpenAICompatProvider(username, model)
    try:
        bundle = json.loads(Path(evidence_json).read_text())
        result = await advise(provider, bundle)
        code = "from agentic_gsas2.trials import Proposal\nprint(json.dumps([Proposal(**p).__dict__ for p in payload['proposals']]))"
        await asyncio.to_thread(engine_call, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO, code, result)
        result['model'] = model
        if evaluate_trials:
            result['trials'] = [json.loads(await powder_trial(evidence_json, proposal, output_dir, True)) for proposal in result['proposals']]
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        artifact = directory / ('advisor_' + uuid.uuid4().hex + '.json')
        artifact.write_text(json.dumps(result, indent=2))
        return format_result({"tool": "powder_advise", "status": "success", "artifact": str(artifact), **result})
    except Exception as exc:
        return format_result({"tool": "powder_advise", "status": "error", "error": str(exc)})
    finally:
        await provider.close()


@mcp.tool()
async def powder_live_ingest(observation: Dict[str, Any], series: str, frame: int,
                             completion_marker: Optional[str] = None) -> str:
    """Submit a ready powder observation to durable fast tracking and periodic full diagnosis."""
    from apexa_powder import engine_call, start_broker
    code = "from agentic_gsas2.jobs import JobStore\nfrom agentic_gsas2.live import LiveRefinement\nfrom agentic_gsas2.observations import Observation\ns=JobStore(payload['root'])\nlive=LiveRefinement(s,payload['series'])\no=Observation(**payload['observation'])\nready=live.ready(o.data_path,completion_marker=payload['marker'])\nresult=live.ingest(o,payload['frame']) if ready else {}\nprint(json.dumps({'status':'submitted' if ready else 'waiting_for_completion','jobs':result,'diagnostic_jobs':live.inspect_completed()}))"
    try:
        root = JOBS_DIR / "engine"
        result = await asyncio.to_thread(engine_call, DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO, code,
                                        {"root": str(root), "series": series, "frame": frame,
                                         "observation": observation, "marker": completion_marker})
        start_broker(DEFAULT_GSASII_PYTHON, DEFAULT_AGENTIC_REPO, root)
        return format_result({"tool": "powder_live_ingest", **result, "poll_with": "refinement_status"})
    except Exception as exc:
        return format_result({"tool": "powder_live_ingest", "status": "error", "error": str(exc)})


if __name__ == "__main__":
    mcp.run()
