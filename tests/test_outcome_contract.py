import json
from types import SimpleNamespace

import pytest

from apexa_ledger import ToolLedger
from apexa_outcomes import classify_result, normalize_mcp_result, result_response


@pytest.mark.parametrize("payload", [
    {"error": "engine unavailable"}, {"success": False, "return_code": 1},
    {"status": "failed"}, {"completed": False, "timed_out": True},
    {"status": "success", "isError": True}, {"status": "success", "returncode": 2},
    {"message": "x" * 1000, "status": "failed"},
])
def test_explicit_failure_cannot_be_success(payload):
    ledger = ToolLedger()
    ledger.dispatch("a", "calibrate")
    ledger.complete("a", json.dumps(payload))
    assert ledger.entries[0].status == "error"
    assert not result_response(payload)["success"]


@pytest.mark.parametrize("payload,execution,science", [
    ({"status": "queued"}, "pending", "unassessed"),
    ({"status": "running"}, "running", "unassessed"),
    ({"status": "success", "gate_passed": False}, "completed", "rejected"),
    ({"execution_state": "completed", "scientific_verdict": "provisional"}, "completed", "provisional"),
    ({"execution_status": "completed", "scientific_status": "failed"}, "completed", "rejected"),
])
def test_execution_and_science_remain_distinct(payload, execution, science):
    outcome = classify_result(payload)
    assert (outcome.execution, outcome.scientific_verdict) == (execution, science)
    response = result_response(payload)
    assert not response["success"]
    assert classify_result(response) == outcome


def test_mcp_protocol_error_and_all_content_survive():
    result = SimpleNamespace(isError=True, structuredContent={"status": "success", "value": 3},
        content=[SimpleNamespace(type="text", text="first"),
                 SimpleNamespace(type="text", text="actual error"),
                 SimpleNamespace(type="image", mimeType="image/png", data="stub")])
    decoded = json.loads(normalize_mcp_result(result))
    assert len(decoded["mcp_content"]) == 3
    assert decoded["structuredContent"]["value"] == 3
    assert classify_result(decoded).execution == "failed"


def test_completed_prose_does_not_validate_science():
    assert classify_result("Process completed").scientific_verdict == "unassessed"


def test_empty_mcp_result_is_failure():
    assert classify_result(normalize_mcp_result(SimpleNamespace(content=[]))).execution == "failed"


@pytest.mark.parametrize("structured", [None, {"status": "success"}])
def test_error_in_any_mcp_block_wins_over_optimistic_metadata(structured):
    result = SimpleNamespace(isError=False, structuredContent=structured, content=[
        SimpleNamespace(type="text", text='{"error":"actual failure"}'),
        SimpleNamespace(type="text", text="additional diagnostics"),
    ])
    assert classify_result(normalize_mcp_result(result)).execution == "failed"


def test_canonical_only_scientific_rejection_survives():
    result = {"value": 3, "_apexa_outcome": {"execution": "completed", "scientific_verdict": "rejected"}}
    assert not result_response(result)["success"]
    assert ToolLedger._infer_status(json.dumps(result)) == "error"


@pytest.mark.parametrize("result,execution,verdict", [
    ({"status": "completed", "gate_passed": True, "_apexa_outcome": {"execution": "completed", "scientific_verdict": "rejected"}}, "completed", "rejected"),
    ({"status": "pending", "_apexa_outcome": {"execution": "completed"}}, "pending", "unassessed"),
    ({"outcome": {"execution": "completed", "scientific_verdict": "rejected"}, "result": {"status": "completed"}}, "completed", "rejected"),
    ({"execution_state": "completed", "status": "failed"}, "failed", "unassessed"),
    ({"status": "resource_unavailable", "success": False, "nothing_was_run": True}, "denied", "unassessed"),
])
def test_conflicting_optimistic_aliases_never_upgrade_outcomes(result, execution, verdict):
    outcome = classify_result(result)
    assert (outcome.execution, outcome.scientific_verdict) == (execution, verdict)
    assert not outcome.successful
