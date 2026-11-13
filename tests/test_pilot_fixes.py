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
    assert out["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert out["manifest_sha256"] != rec["artifact_sha256"]


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


def test_finalize_task_manifest_computes_workspace_relative_hashes(tmp_path):
    from beamline_core_server import finalize_result
    artifact = tmp_path / "analysis" / "matches.tsv"
    artifact.parent.mkdir()
    artifact.write_text("row\tangle\n1\t0.2\n")
    manifest = tmp_path / "analysis" / "result_manifest.json"
    result = _result(_run(finalize_result("accepted", "Orientation consistency only",
        manifest_path=str(manifest), workspace_root=str(tmp_path),
        result_details={"task": "ff_nf_consistency", "counts": {"selected": 1}},
        artifact_paths=["analysis/matches.tsv"])))
    assert result["status"] == "success", result
    record = json.loads(manifest.read_text())
    assert record["task"] == "ff_nf_consistency"
    assert record["counts"] == {"selected": 1}
    assert record["artifacts"] == [{"path": "analysis/matches.tsv",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]
    assert record["validation"]["scientific_interpretation"] == "not_evaluated_by_hash_checks"
    assert result["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()


@pytest.mark.parametrize("problem", ["wrong_hash", "missing_file", "self_receipt"])
def test_finalize_rejects_invalid_adopted_manifest_without_overwriting_receipt(tmp_path, problem):
    from beamline_core_server import finalize_result
    artifact = tmp_path / "matches.tsv"
    artifact.write_text("1\t0.2\n")
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"prior_receipt": true}\n')
    original = receipt.read_bytes()
    entry = {"path": artifact.name, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    if problem == "wrong_hash":
        entry["sha256"] = "0" * 64
    elif problem == "missing_file":
        artifact.unlink()
    else:
        entry = {"path": receipt.name, "sha256": hashlib.sha256(original).hexdigest()}
    adopted = tmp_path / "adopted.json"
    adopted.write_text(json.dumps({"artifacts": [entry]}))
    result = _result(_run(finalize_result("accepted", artifact_path=str(adopted),
        manifest_path=str(receipt), workspace_root=str(tmp_path))))
    assert result["status"] == "error", result
    assert receipt.read_bytes() == original


@pytest.mark.parametrize("problem", ["same_adopted_file", "self_hash", "escape", "reserved_field"])
def test_finalize_rejects_unsafe_or_conflicting_task_manifest(tmp_path, problem):
    from beamline_core_server import finalize_result
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / "manifest.json"
    output.write_text("original result\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    kwargs = {"manifest_path": str(output), "workspace_root": str(workspace)}
    if problem == "same_adopted_file":
        kwargs["artifact_path"] = str(output)
    elif problem == "self_hash":
        kwargs["artifact_paths"] = [str(output)]
    elif problem == "escape":
        kwargs["artifact_paths"] = ["../outside.txt"]
    else:
        kwargs["result_details"] = {"scientific_status": "accepted"}
    result = _result(_run(finalize_result("provisional", **kwargs)))
    assert result["status"] == "error", result
    assert output.read_text() == "original result\n"


def test_finalize_checks_native_scientific_receipt_artifacts(tmp_path):
    from beamline_core_server import finalize_result
    artifact = tmp_path / "plot.png"
    artifact.write_bytes(b"scientific artifact")
    receipt = tmp_path / "comparison.json"
    receipt.write_text(json.dumps({"artifacts": {"plot": str(artifact), "receipt": str(receipt)},
        "artifacts_sha256": {str(artifact): hashlib.sha256(artifact.read_bytes()).hexdigest()}}))
    output = tmp_path / "analysis" / "manifest.json"
    kwargs = dict(manifest_path=str(output), workspace_root=str(tmp_path),
        result_details={"task": "comparison"}, artifact_paths=[str(artifact), str(receipt)])
    result = _result(_run(finalize_result("accepted", **kwargs)))
    assert result["status"] == "success", result
    original = output.read_bytes()
    artifact.write_bytes(b"changed scientific artifact")
    result = _result(_run(finalize_result("accepted", **kwargs)))
    assert result["status"] == "error" and "hash mismatch" in result["error"]
    assert output.read_bytes() == original


def test_task_manifest_cannot_include_a_receipt_that_hashes_it(tmp_path):
    from beamline_core_server import finalize_result
    manifest = tmp_path / "result_manifest.json"
    manifest.write_text('{"old_result": true}\n')
    original = manifest.read_bytes()
    receipt = tmp_path / "decision.json"
    receipt.write_text(json.dumps({"artifact_path": manifest.name,
        "artifact_sha256": hashlib.sha256(original).hexdigest()}))
    result = _result(_run(finalize_result("accepted", manifest_path=str(manifest),
        workspace_root=str(tmp_path), result_details={"task": "comparison"},
        artifact_paths=[receipt.name])))
    assert result["status"] == "error" and "Circular" in result["error"]
    assert manifest.read_bytes() == original


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
