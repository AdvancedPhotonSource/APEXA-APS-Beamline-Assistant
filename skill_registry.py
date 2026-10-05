"""Shared Agent-Skill registry + loader.

Single source of truth for the tool -> Agent Skill mapping and for reading a
skill's canonical procedure (``.agents/skills/<name>/SKILL.md``) into a model's
context.

History / why this module exists: the mapping used to live only in
``midas_comprehensive_server.py`` and was used solely to *name* skills in
``recommend_workflow`` output — nothing ever read the SKILL.md body into the
agent's context, so the "load the canonical procedure before you run" guidance
was a pointer the model could ignore. This module lets the agent loop
(``apexa_agents.py``) actually pre-load the relevant skill(s) into a specialist's
system prompt, while the server imports the same ``SKILL_FOR_TOOL`` map so the
two never drift.

Kept intentionally dependency-free (stdlib only) so importing it into the agent
loop never spins up the MCP server.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

# Anchor to THIS module's directory, not the process CWD — the agent loop uses
# Path.cwd() for the working dir, which is not necessarily the repo root.
SKILLS_DIR = Path(__file__).resolve().parent / ".agents" / "skills"


# tool name -> skill dir name. First token of a tool string is matched by
# ``skill_for_tool`` so "read_grains_summary / compute_grain_stress" resolves on
# the head.
SKILL_FOR_TOOL = {
    # calibration
    "midas_auto_calibrate": "midas-calibrate",
    "run_ff_calibration": "midas-calibrate",
    "estimate_parameters_from_image": "midas-calibrate",
    # integration
    "midas_integrate_series": "midas-integrate",
    "midas_batch_integrate": "midas-integrate",
    "midas_integrate_2d_to_1d": "midas-integrate",
    # FF-HEDM
    "run_ff_hedm_full_workflow": "midas-ff-hedm",
    "run_ff_pipeline": "midas-ffpipeline",
    "refine_grain_lattice": "midas-ffpipeline",
    # NF / PF / general HEDM
    "run_nf_hedm_reconstruction": "midas-hedm",
    "convert_nf_to_dream3d": "midas-hedm",
    "extract_grain_centroids": "midas-hedm",
    "preprocess_nf_data": "midas-hedm",
    "run_pf_hedm_workflow": "midas-hedm",
    # forward simulation (FF ForwardSimulationCompressed / NF simulateNF) —
    # dedicated skill carrying manuals/Forward_Simulation.md procedure + traps
    "run_forward_simulation": "midas-forward-sim",
    "run_nf_forward_simulation": "midas-forward-sim",
    "match_grains": "midas-hedm",
    "calculate_misorientation": "midas-hedm",
    "read_grains_summary": "midas-hedm",
    "compute_grain_stress": "midas-hedm",
    "analyze_slip_systems": "midas-hedm",
    # detector masking
    "build_detector_mask": "midas-mask",
    # phase / refinement
    "run_gsas_refinement": "midas-gsasii",
    # autonomous refinement (gsas2_server.py) -- a separate skill from
    # midas-gsasii because the input formats do not overlap: that tool
    # takes MIDAS caked .zarr.zip, these take any powder pattern.
    "refine_pattern": "gsas2-agentic",
    "propose_structures": "gsas2-agentic",
    "refine_series_submit": "gsas2-agentic",
    "refinement_status": "gsas2-agentic",
    "assess_refinement": "gsas2-agentic",
    # inspect / validate
    "recommend_workflow": "midas-validate",
    "inspect_dataset_file": "midas-validate",
    "validate_parameter_file": "midas-validate",
    "diagnose_parameter_file": "midas-validate",
    # visualize
    "run_midas_viewer": "midas-visualize",
    # motor
    "get_motor_position": "motor-control",
    "move_motor_absolute": "motor-control",
    "move_motor_relative": "motor-control",
    "jog_motor": "motor-control",
    "tweak_motor": "motor-control",
}


def skill_for_tool(tool: str) -> str | None:
    """Resolve the Agent Skill dir for a tool string (matches on the first token)."""
    if not tool:
        return None
    head = tool.strip().split()[0].split("/")[0].strip()
    return SKILL_FOR_TOOL.get(head)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (``---\\n...\\n---``) if present."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl != -1:
                return text[nl + 1:].lstrip("\n")
    return text


@lru_cache(maxsize=None)
def load_skill_text(skill_name: str) -> str:
    """Return a skill's SKILL.md body (frontmatter stripped), or "" if missing.

    Cached per process — each SKILL.md is read at most once. Never raises into
    the agent loop; a missing/unreadable file yields "".
    """
    if not skill_name:
        return ""
    path = SKILLS_DIR / skill_name / "SKILL.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return _strip_frontmatter(raw).strip()


def skills_for_tools(tool_names) -> List[str]:
    """Distinct skill dirs for a list of tool names, order preserved."""
    seen: List[str] = []
    for t in tool_names or []:
        sk = skill_for_tool(t)
        if sk and sk not in seen:
            seen.append(sk)
    return seen


_HEADER = (
    "=== CANONICAL PROCEDURE (Agent Skills) — follow before editing parameter "
    "files or running tools ===\n"
    "These are the verified MIDAS handbook procedures for the tools you can call. "
    "They are the source of truth for exact flags, units, paths, and traps. "
    "Prefer them over recall.\n"
)


def skill_context_for_tools(tool_names) -> str:
    """Assemble the injectable skill block for a specialist's tool set.

    Returns "" when no tool maps to a skill (e.g. the unified agent whose
    ``tool_names`` is empty). Otherwise: a short header followed by each relevant
    SKILL.md body.
    """
    names = skills_for_tools(tool_names)
    bodies = []
    for name in names:
        body = load_skill_text(name)
        if body:
            bodies.append(f"\n----- Skill: {name} -----\n{body}")
    if not bodies:
        return ""
    return "\n\n" + _HEADER + "".join(bodies) + "\n"
