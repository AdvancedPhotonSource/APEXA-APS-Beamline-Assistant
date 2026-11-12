"""Tests for the handbook-internalization feature:

  1. Width/MarginRadius µm-unit lint (`_lint_handbook_traps`) — the classic
     `Width 7.5` (px-for-µm) mistake must produce a BLOCKING `error` trap.
  2. The FF workflow pre-dispatch gate — a `Width 7.5` param file is blocked
     before any MIDAS dispatch; `ignore_handbook_traps=True` overrides it.
  3. The shared skill registry loader (`skill_registry`) — the tool→skill map,
     SKILL.md body loading, and the injectable context block.
  4. Drift guard — every handbook § cited in the FF/NF skills points at a
     manuals dir that exists (skipped when the MIDAS checkout is absent).

None of these run MIDAS. Run: python -m pytest tests/test_handbook_skills.py
"""
import os
import re
import sys
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import midas_comprehensive_server as midas
import skill_registry


def _write_param(tmpdir, body):
    p = Path(tmpdir) / "Parameters.txt"
    p.write_text(body, encoding="utf-8")
    return str(p)


# ── 1. Lint: Width / MarginRadius µm-unit check ──────────────────────────────

def _traps_for(body):
    with tempfile.TemporaryDirectory() as d:
        return midas._lint_handbook_traps(_write_param(d, body), "ff")


def _find(traps, key):
    return [t for t in traps if t["key"] == key]


def test_width_pixels_is_blocking_error():
    traps = _traps_for("Width 7.5\n")
    w = _find(traps, "Width")
    assert w, "expected a Width trap for Width 7.5"
    assert w[0]["severity"] == "error", "px-for-µm Width must be a blocking error"


def test_width_micron_default_ok():
    assert not _find(_traps_for("Width 1500\n"), "Width")


def test_marginradius_pixels_is_blocking_error():
    m = _find(_traps_for("MarginRadius 2.5\n"), "MarginRadius")
    assert m and m[0]["severity"] == "error"


def test_marginradius_micron_default_ok():
    assert not _find(_traps_for("MarginRadius 500\n"), "MarginRadius")


# ── 1b. Lint: BoxSize collapsed-span check ───────────────────────────────────

def test_boxsize_collapsed_span_is_blocking_error():
    # Zero Z-span (Zmin == Zmax) → keep-box rejects every spot → block.
    b = _find(_traps_for("BoxSize -10000 0 0 0\n"), "BoxSize")
    assert b and b[0]["severity"] == "error"


def test_boxsize_permissive_ok():
    assert not _find(_traps_for("BoxSize -1000000 1000000 -1000000 1000000\n"), "BoxSize")


def test_boxsize_absent_not_flagged():
    # fit_setup skips the box filter when BoxSizes is empty — absence is safe.
    assert not _find(_traps_for("Width 1500\n"), "BoxSize")


# ── 1c. Lint: seed ω-window (Min/MaxOmeSpotIDsToIndex) check ──────────────────

def test_omega_window_both_absent_is_blocking_error():
    o = _find(_traps_for("Width 1500\n"), "MinOmeSpotIDsToIndex")
    assert o and o[0]["severity"] == "error"


def test_omega_window_degenerate_min_ge_max_is_blocking_error():
    o = _find(_traps_for("MinOmeSpotIDsToIndex 0\nMaxOmeSpotIDsToIndex 0\n"),
              "MinOmeSpotIDsToIndex")
    assert o and o[0]["severity"] == "error"


def test_omega_window_full_sweep_ok():
    assert not _find(
        _traps_for("MinOmeSpotIDsToIndex -180\nMaxOmeSpotIDsToIndex 180\n"),
        "MinOmeSpotIDsToIndex")


def test_omega_window_one_sided_is_warning():
    o = _find(_traps_for("MinOmeSpotIDsToIndex -180\n"), "MinOmeSpotIDsToIndex")
    assert o and o[0]["severity"] == "warning"


# ── 2. FF workflow pre-dispatch gate ─────────────────────────────────────────

def _run_ff(param_body, **kw):
    with tempfile.TemporaryDirectory() as d:
        pf = _write_param(d, param_body)
        out = Path(d) / "out"
        res = asyncio.run(midas.run_ff_hedm_full_workflow(
            result_folder=str(out), param_file=pf, **kw))
    return res


def test_ff_gate_blocks_width_pixels():
    res = _run_ff("Width 7.5\n")
    assert '"status": "error"' in res
    assert "Blocked by handbook lint" in res
    assert "handbook_traps" in res


def test_ff_gate_override_bypasses_block():
    # With the override the run proceeds PAST the lint gate. It then fails for a
    # different, benign reason (midas-pipeline missing / no data) — the point is
    # only that it is no longer the lint block.
    res = _run_ff("Width 7.5\n", ignore_handbook_traps=True)
    assert "Blocked by handbook lint" not in res


# ── 3. Skill registry loader ─────────────────────────────────────────────────

def test_skills_for_tools_resolves_ff():
    assert skill_registry.skills_for_tools(
        ["run_ff_hedm_full_workflow"]) == ["midas-ff-hedm"]


def test_load_skill_text_strips_frontmatter():
    body = skill_registry.load_skill_text("midas-ff-hedm")
    assert body, "midas-ff-hedm SKILL.md should load"
    assert not body.lstrip().startswith("---"), "YAML frontmatter must be stripped"
    assert "name:" not in body.split("\n", 1)[0]


def test_load_skill_text_missing_is_empty():
    assert skill_registry.load_skill_text("does-not-exist") == ""


def test_skill_context_block_bounded_and_nonempty():
    ctx = skill_registry.skill_context_for_tools(
        ["run_ff_hedm_full_workflow", "run_gsas_refinement"])
    assert ctx and "CANONICAL PROCEDURE" in ctx
    assert "midas-ff-hedm" in ctx
    # Bounded — a couple of skills should be tens of KB, not runaway.
    assert len(ctx) < 60_000, f"skill block unexpectedly large: {len(ctx)} chars"


def test_unified_agent_empty_tools_yields_no_block():
    assert skill_registry.skill_context_for_tools([]) == ""


# ── 4. Drift guard: cited handbook sections point at real manuals ────────────

_MANUALS = Path("/Users/b324240/Git/MIDAS/manuals")


def test_skill_citations_reference_existing_manuals():
    if not _MANUALS.is_dir():
        import pytest
        pytest.skip("MIDAS manuals not checked out on this machine")
    skills = ["midas-ff-hedm", "midas-hedm"]
    # Any "FF §..." / "NF §..." citation implies the corresponding handbook file.
    wanted = {"FF": _MANUALS / "FF_HEDM_Handbook.md",
              "NF": _MANUALS / "NF_HEDM_Handbook.md"}
    for name in skills:
        text = (skill_registry.SKILLS_DIR / name / "SKILL.md").read_text("utf-8")
        for fam in set(re.findall(r"\b(FF|NF) §", text)):
            assert wanted[fam].exists(), (
                f"{name} cites {fam} § but {wanted[fam]} is missing")


# ── 5. Generic guardrail engine: handbook facts loader (A1) ──────────────────

import handbook_guardrails as hg


def test_facts_width_is_micron_with_default():
    f = hg.load_param_facts("ff")["width"]
    assert f["units"] == "µm"
    assert f["default"] == 1500        # authoritative Defaults-summary value


def test_facts_marginradius_default_is_none_not_500():
    # The drift fix: the *parser* default is `—` (None); 500 is only the Example
    # recommended value, sourced separately — never baked in as the fact default.
    f = hg.load_param_facts("ff")["marginradius"]
    assert f["units"] == "µm"
    assert f["default"] is None


def test_facts_boxsize_is_arity4_micron():
    f = hg.load_param_facts("ff")["boxsize"]
    assert f["arity"] == 4
    assert f["units"] == "µm"


def test_facts_omega_window_is_degrees():
    facts = hg.load_param_facts("ff")
    assert facts["minomespotidstoindex"]["units"] == "deg"
    assert facts["maxomespotidstoindex"]["units"] == "deg"


def test_recommended_values_supply_a_real_magnitude():
    # Magnitude sourcing for the µm-floor must resolve to a genuinely-large length.
    rec = hg.load_recommended_values()
    assert rec["width"] >= 100
    assert rec["marginradius"] >= 100


# ── 5b. Generic µm-floor generality (A2) — not just Width/MarginRadius ────────

def test_um_floor_generalises_to_marginradial():
    # MarginRadial has no parser default (—); its magnitude comes from the
    # Example recommended value (500). A px-sized entry must still block.
    m = _find(_traps_for("MarginRadial 3\n"), "MarginRadial")
    assert m and m[0]["severity"] == "error"


def test_um_floor_scoped_to_documented_family_only():
    # StepSizePos is µm but NOT in the documented px-for-µm offender family, so a
    # small value must NOT produce a µm-floor trap (no over-firing).
    assert not _find(_traps_for("StepSizePos 5\n"), "StepSizePos")


# ── 6. Post-stage output verifier (B) ────────────────────────────────────────

def _layer(tmpdir, files):
    """Create result_folder/LayerNr_0/<name> for each (name, bytes) in files."""
    ld = Path(tmpdir) / "LayerNr_0"
    ld.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        p = ld / name
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data, encoding="utf-8")
    return str(tmpdir)


def test_verify_missing_folder_is_error():
    r = hg.verify_ff_outputs("/no/such/folder/xyz")
    assert r["status"] == "error"


def test_verify_empty_folder_is_incomplete():
    with tempfile.TemporaryDirectory() as d:
        r = hg.verify_ff_outputs(d)
    assert r["status"] == "incomplete"


def test_verify_seed_only_is_incomplete():
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {"SpotsToIndex.csv": "1\n"})
        result = hg.verify_ff_outputs(rf)
    assert result["status"] == "incomplete"
    assert "Grains.csv is missing" in result["summary"]


def test_verify_empty_inputall_is_fail():
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {"InputAll.csv": ""})       # 0 bytes
        r = hg.verify_ff_outputs(rf)
    assert r["status"] == "fail"
    bad = [c for lr in r["layers"] for c in lr["checks"]
           if c["name"] == "InputAll" and c["ok"] is False]
    assert bad


def test_verify_zero_seed_spotstoindex_is_fail():
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {"SpotsToIndex.csv": "# header only, no data\n"})
        r = hg.verify_ff_outputs(rf)
    assert r["status"] == "fail"


def test_verify_four_byte_indexbest_is_fail():
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {"IndexBest_all.bin": b"\x00\x00\x00\x00"})  # 4 bytes
        r = hg.verify_ff_outputs(rf)
    assert r["status"] == "fail"


def test_verify_healthy_layer_is_ok():
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {
            "InputAll.csv": "1.0 2.0 3.0\n4.0 5.0 6.0\n",
            "SpotsToIndex.csv": "1\n2\n3\n",
            "Grains.csv": "%header\n1 0.1 0.2 0.3\n",
        })
        r = hg.verify_ff_outputs(rf)
    assert r["status"] == "ok"


# ── 7. Stage-scoped guardrail loop helpers (C1 + C2) ─────────────────────────

import apexa_agents


def test_stage_skill_msg_injects_ff_once_then_dedups():
    injected = set()
    m1 = apexa_agents.AgentRunner._maybe_stage_skill_msg(
        "run_ff_hedm_full_workflow", injected)
    assert m1 and m1["role"] == "system"
    assert "CANONICAL PROCEDURE" in m1["content"]
    assert "midas-ff-hedm" in injected
    # Second use of a same-skill tool → no re-injection (set dedup).
    m2 = apexa_agents.AgentRunner._maybe_stage_skill_msg(
        "run_ff_hedm_full_workflow", injected)
    assert m2 is None


def test_stage_skill_msg_unknown_tool_is_none():
    assert apexa_agents.AgentRunner._maybe_stage_skill_msg(
        "definitely_not_a_tool", set()) is None


def test_automatic_delivery_off_keeps_explicit_document_access_and_verifier(monkeypatch):
    monkeypatch.setenv("APEXA_AUTOMATIC_PROCEDURES", "0")
    assert apexa_agents.AgentRunner._maybe_stage_skill_msg("run_ff_hedm_full_workflow", set()) is None
    assert apexa_agents.AgentRunner._maybe_capsule_msg("run_ff_hedm_full_workflow", set()) is None
    assert apexa_agents.load_skill_text("midas-ff-hedm")
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {"InputAll.csv": ""})
        result = apexa_agents.AgentRunner._maybe_verifier_msg("run_ff_hedm_full_workflow", {"result_folder": rf})
    assert result and "INDEPENDENT OUTPUT CHECK" in result["content"]


def test_verifier_msg_fires_on_failed_ff_reconstruction():
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {"InputAll.csv": ""})       # degenerate → fail
        m = apexa_agents.AgentRunner._maybe_verifier_msg(
            "run_ff_hedm_full_workflow", {"result_folder": rf})
    assert m and m["role"] == "user"
    assert "INDEPENDENT OUTPUT CHECK" in m["content"]


def test_verifier_msg_silent_on_healthy_ff_reconstruction():
    with tempfile.TemporaryDirectory() as d:
        rf = _layer(d, {
            "InputAll.csv": "1.0 2.0\n",
            "SpotsToIndex.csv": "1\n2\n",
            "Grains.csv": "%h\n1 0.1\n",
        })
        m = apexa_agents.AgentRunner._maybe_verifier_msg(
            "run_ff_hedm_full_workflow", {"result_folder": rf})
    assert m is None


def test_verifier_msg_ignores_non_ff_tools():
    assert apexa_agents.AgentRunner._maybe_verifier_msg(
        "midas_integrate_2d_to_1d", {"result_folder": "/tmp"}) is None


def test_verifier_msg_none_without_result_folder():
    assert apexa_agents.AgentRunner._maybe_verifier_msg(
        "run_ff_hedm_full_workflow", {}) is None


# ── 8. Facts loader tracks the LIVE handbook (drift guard) ───────────────────

def test_facts_loaded_from_live_handbook_when_present():
    ref = hg._reference_md()
    if not ref or not ref.exists():
        import pytest
        pytest.skip("FF_Parameters_Reference.md not checked out on this machine")
    # When the live reference is present, the parsed Width default must match the
    # handbook's authoritative Defaults summary — a drift here means the parser
    # fell back to the embedded snapshot or the handbook table changed shape.
    assert hg.load_param_facts("ff")["width"]["default"] == 1500
