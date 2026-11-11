"""Tests for the fixes motivated by the FF harness pilot (Nov 2026)."""
import asyncio
import hashlib
import json

import pytest


def _run(coro):
    return asyncio.run(coro)


def _result(text):
    return json.loads(text) if isinstance(text, str) else text


# ── finalize_result: one call records the decision, never loses it ────────────
def test_finalize_result_writes_hash_verified_manifest(tmp_path):
    from beamline_core_server import finalize_result
    art = tmp_path / "LayerNr_1" / "Grains.csv"
    art.parent.mkdir()
    art.write_text("%NumGrains 1\n1 1 0 0 0 1 0 0 0 1 0 0 0\n")
    manifest = tmp_path / "result_manifest.json"
    out = _result(_run(finalize_result("accepted", "50 grains", str(art), str(manifest))))
    assert out["status"] == "success"
    rec = json.loads(manifest.read_text())
    assert rec["schema_version"] == 1 and rec["scientific_status"] == "accepted"
    assert rec["artifact_path"] == "LayerNr_1/Grains.csv"          # relative to the manifest
    assert rec["artifact_sha256"] == hashlib.sha256(art.read_bytes()).hexdigest()


def test_finalize_result_refuses_accepted_without_artifact(tmp_path):
    from beamline_core_server import finalize_result
    out = _result(_run(finalize_result("accepted", "", "", str(tmp_path / "m.json"))))
    assert out["status"] == "error" and not (tmp_path / "m.json").exists()


def test_finalize_result_allows_abstention_without_artifact(tmp_path):
    from beamline_core_server import finalize_result
    m = tmp_path / "m.json"
    out = _result(_run(finalize_result("abstained", "budget ran out", "", str(m))))
    assert out["status"] == "success"
    assert "artifact_path" not in json.loads(m.read_text())


def test_finalize_result_rejects_unknown_status(tmp_path):
    from beamline_core_server import finalize_result
    out = _result(_run(finalize_result("done", "", "", str(tmp_path / "m.json"))))
    assert out["status"] == "error"


def test_finalize_result_is_on_the_core_surface():
    from apexa_toolsurface import CORE_TOOLS
    assert "finalize_result" in CORE_TOOLS


# ── automatic procedure blocks are bounded ────────────────────────────────────
def test_cap_procedure_truncates_with_pointer(monkeypatch):
    from apexa_agents import _cap_procedure
    monkeypatch.setenv("APEXA_AUTO_PROCEDURE_MAX_CHARS", "100")
    out = _cap_procedure("x" * 500, "call learn_technique('ff-hedm')")
    assert len(out) < 250 and "learn_technique('ff-hedm')" in out and "truncated" in out
    assert _cap_procedure("short", "hint") == "short"


def test_halt_header_lets_confirmed_facts_settle_conventions():
    from apexa_agents import HALT_HEADER
    assert "confirmed" in HALT_HEADER and "assumption" in HALT_HEADER


def test_default_iteration_cap_is_raised(monkeypatch):
    import inspect
    import apexa_agents
    monkeypatch.delenv("APEXA_MAX_ITERATIONS", raising=False)
    assert '"APEXA_MAX_ITERATIONS", "80"' in inspect.getsource(apexa_agents)


# ── handbook guardrails: pipeline keys and GE-only SkipFrame ──────────────────
def _traps(tmp_path, text):
    from handbook_guardrails import evaluate_param_guardrails
    p = tmp_path / "Parameters.txt"
    p.write_text(text)
    return {t["key"]: t for t in evaluate_param_guardrails(str(p), "ff", "")}


BASE = "LatticeConstant 4.08 4.08 4.08 90 90 90\nSpaceGroup 225\nLsd 1000000\nBC 1022 1022\n"


def test_missing_padding_and_dotless_ext_are_flagged(tmp_path):
    traps = _traps(tmp_path, BASE + "RawFolder /d\nFileStem Au_FF\nExt zip\n")
    assert traps["Padding"]["severity"] == "warning"
    assert traps["Ext"]["severity"] == "warning" and "'Ext .zip'" in traps["Ext"]["message"]


def test_pipeline_ready_file_keys_are_clean(tmp_path):
    traps = _traps(tmp_path, BASE + "RawFolder /d\nFileStem Au_FF\nPadding 6\nExt .zip\n")
    assert "Padding" not in traps and "Ext" not in traps


def test_unset_skipframe_is_info_not_warning(tmp_path):
    traps = _traps(tmp_path, BASE)
    assert traps["SkipFrame"]["severity"] == "info" and "GE" in traps["SkipFrame"]["message"]
