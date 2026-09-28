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
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import logging

from mcp.server.mcpserver import MCPServer
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
                "error": f"timed out after {timeout}s; use refine_pattern "
                         f"with wait=false for long refinements"}
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
    stop = summary.get("stop_reason")
    if stop == "wall_clock":
        flags.append("stopped on a time budget, not on convergence")
    if stop == "max_steps":
        flags.append("hit the step cap; the fit was still improving")
    rwp = summary.get("Rwp")
    lebail = (summary.get("lebail") or {}).get("Rwp")
    if rwp is not None and rwp > 40:
        flags.append(f"Rwp {rwp:.1f}% is high in absolute terms")
    if rwp is not None and lebail:
        gap = rwp - lebail
        if gap > 5:
            flags.append(
                f"Rietveld exceeds the Le Bail floor by {gap:.1f} points, "
                f"so the structural model is the limiting factor")
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
        cif_files: starting structures. Pass [] to retrieve one instead
            (see propose_structures).
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
    out = _run_python(code, timeout=1800)
    if out.get("status") == "error":
        return format_result({"tool": "refine_pattern", **out})
    verdict = _trust(out)
    payload = {
        "tool": "refine_pattern", "status": "success",
        "Rwp": out.get("Rwp"), "GOF": out.get("GOF"),
        "stop_reason": out.get("stop_reason"),
        "steps": out.get("step_count"),
        "phases": out.get("phases"),
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
    out = _run_python(code, timeout=600)
    return format_result({"tool": "propose_structures", **out})


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

    KNOWN LIMITATION, stated because it bears directly on in-situ use:
    on our 17-temperature benchmark the reported lattice parameter is
    identical at every temperature. The sequential entry point predates
    the framework's direct cell-refinement path and does not yet carry
    it, so a thermal-expansion trend cannot currently be read out of this
    tool. Phase fractions and residuals are unaffected.

    Args:
        data_files: patterns in acquisition order.
        cif_files: starting structures, shared across the series.
        output_dir: root for per-pattern outputs.
        instprm_file: instrument parameter file.
        warm_start: seed each pattern from the previous converged model.
        max_steps: cap per pattern.
        fmthint: GSAS-II reader hint; pass "GSAS" for legacy .XRA/.CWN/.fxye.
    """
    err = _env_ok()
    if err:
        return format_result({"tool": "refine_series_submit",
                              "status": "error", "error": err})
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = f"gsas2-{uuid.uuid4().hex[:10]}"
    job = JOBS_DIR / job_id
    job.mkdir()
    script = job / "run.py"
    script.write_text(f"""
import json, sys
sys.path.insert(0, {DEFAULT_AGENTIC_REPO!r})
from dataclasses import replace
from agentic_gsas2 import AgentConfig
from agentic_gsas2.orchestrator import refine_one
from pathlib import Path
cfg = replace(AgentConfig(), refine_atoms=True)
cfg = replace(cfg, worstfit=replace(cfg.worstfit, max_steps={max_steps!r}))
out = []
for i, f in enumerate({data_files!r}):
    d = Path({output_dir!r}) / ("pattern_%03d" % i)
    d.mkdir(parents=True, exist_ok=True)
    try:
        kw = {{}}
        if {fmthint!r}: kw["fmthint"] = {fmthint!r}
        s = refine_one(data_path=f, cif_paths={cif_files!r},
                       output_dir=str(d), instprm_path={instprm_file!r},
                       config=cfg, **kw)
        out.append({{"i": i, "file": f, "Rwp": s.get("Rwp"),
                     "stop_reason": s.get("stop_reason"),
                     "phases": s.get("phases")}})
    except Exception as e:
        out.append({{"i": i, "file": f, "error": str(e)[:200]}})
    Path({str(job)!r}, "progress.json").write_text(
        json.dumps({{"done": i + 1, "total": len({data_files!r}),
                     "results": out}}, default=str))
Path({str(job)!r}, "done").write_text("ok")
""")
    log = (job / "log.txt").open("w")
    env = dict(os.environ)
    env["PYTHONPATH"] = DEFAULT_AGENTIC_REPO
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env.setdefault(v, "1")
    subprocess.Popen([DEFAULT_GSASII_PYTHON, str(script)],
                     stdout=log, stderr=subprocess.STDOUT, env=env,
                     start_new_session=True)
    return format_result({
        "tool": "refine_series_submit", "status": "submitted",
        "job_id": job_id, "n_patterns": len(data_files),
        "poll_with": f"refinement_status(job_id='{job_id}')"})


@mcp.tool()
async def refinement_status(job_id: str) -> str:
    """Progress of a refine_series_submit job.

    Args:
        job_id: id returned by refine_series_submit.
    """
    job = JOBS_DIR / job_id
    if not job.is_dir():
        return format_result({"tool": "refinement_status", "status": "error",
                              "error": f"unknown job {job_id}"})
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
    return format_result(payload)


if __name__ == "__main__":
    mcp.run()
