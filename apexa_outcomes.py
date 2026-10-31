"""Common execution/scientific status interpretation at application boundaries.

Legacy tool payloads remain intact. A completed tool invocation is never, by
itself, evidence that a scientific result passed validation.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel


class ToolOutcome(BaseModel):
    schema_version: int = 1
    execution: Literal["completed", "failed", "denied", "cancelled", "pending", "running", "unknown"]
    scientific_verdict: Literal["accepted", "rejected", "provisional", "unassessed"] = "unassessed"
    error: str | None = None

    @property
    def successful(self) -> bool:
        return self.execution == "completed" and self.scientific_verdict not in ("rejected", "provisional")


def payload_dict(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            pass
    return None


def _combine_outcomes(*outcomes: ToolOutcome) -> ToolOutcome:
    execution_priority = {"completed": 0, "unknown": 1, "pending": 2, "running": 3,
                          "cancelled": 4, "denied": 5, "failed": 6}
    science_priority = {"unassessed": 0, "accepted": 1, "provisional": 2, "rejected": 3}
    result = max(outcomes, key=lambda o: execution_priority[o.execution]).model_copy()
    result.scientific_verdict = max((o.scientific_verdict for o in outcomes), key=science_priority.get)
    return result


def classify_result(value: Any, *, protocol_error: bool = False) -> ToolOutcome:
    """Aggregate explicit evidence conservatively; prose never validates science."""
    data = payload_dict(value)
    if data is None:
        text = str(value or "").strip()
        if protocol_error or re.match(r"(?is)^(error\b|traceback\b|failed\b)", text):
            return ToolOutcome(execution="failed", error=text[:1000] or "MCP tool error")
        if "⛔" in text[:400] or "deletion not run" in text[:400].lower():
            return ToolOutcome(execution="denied", error=text[:1000])
        return ToolOutcome(execution="completed" if text else "unknown")

    canonical = []
    for key in ("_apexa_outcome", "outcome"):
        if isinstance(data.get(key), dict):
            try:
                canonical.append(ToolOutcome.model_validate(data[key]))
            except ValueError:
                return ToolOutcome(execution="failed", error="Invalid tool outcome contract")
    if isinstance(data.get("outcome"), dict) and "result" in data:
        # HTTP success=False also covers pending/provisional/rejected science.
        # Preserve both the underlying payload and the declared envelope verdict.
        return _combine_outcomes(*canonical, classify_result(data["result"], protocol_error=protocol_error))

    statuses = {str(data.get(k, "")).lower() for k in ("execution_state", "execution_status", "status")}
    verdict = str(data.get("scientific_verdict", data.get("scientific_status", "unassessed"))).lower()
    verdict = {"passed": "accepted", "pass": "accepted", "valid": "accepted",
               "failed": "rejected", "fail": "rejected", "invalid": "rejected",
               "warning": "provisional", "needs_review": "provisional"}.get(verdict, verdict)
    if verdict not in ("accepted", "rejected", "provisional", "unassessed"):
        verdict = "unassessed"
    if data.get("gate_passed") is False or data.get("scientifically_valid") is False:
        verdict = "rejected"
    elif (data.get("gate_passed") is True or data.get("scientifically_valid") is True) and verdict == "unassessed":
        verdict = "accepted"
    if statuses & {"warning", "provisional", "needs_review", "needs_selection"} and verdict != "rejected":
        verdict = "provisional"
    errors = data.get("error") or data.get("errors")
    nonzero_exit = any(data.get(k) is not None and data.get(k) != 0
                       for k in ("returncode", "return_code", "exit_code"))
    error = None
    if protocol_error or data.get("isError") is True:
        execution, error = "failed", str(errors or "MCP tool error")
    elif statuses & {"denied", "blocked", "permission_denied", "resource_unavailable"} or data.get("nothing_was_run") is True:
        execution, error = "denied", str(errors or data.get("reason") or "Execution refused")
    elif (errors or nonzero_exit or data.get("success") is False or data.get("timed_out") is True
            or statuses & {"error", "failed", "failure", "timeout", "timed_out"}):
        execution, error = "failed", str(errors or ("timed out" if data.get("timed_out") else "Tool execution failed"))
    elif statuses & {"cancelled", "canceled"}:
        execution = "cancelled"
    elif statuses & {"running", "in_progress", "started"}:
        execution = "running"
    elif statuses & {"queued", "pending", "submitted"} or data.get("completed") is False:
        execution = "pending"
    else:
        execution = "completed"
    return _combine_outcomes(ToolOutcome(execution=execution, scientific_verdict=verdict, error=error), *canonical)



def normalize_mcp_result(result: Any) -> str:
    """Preserve all MCP content and structured data, including protocol errors.

    A single ordinary text block retains its wire representation for legacy
    callers. Multi-block/structured results carry the complete content array.
    """
    blocks = []
    for block in getattr(result, "content", None) or []:
        if isinstance(block, dict):
            blocks.append(block)
        elif hasattr(block, "model_dump"):
            blocks.append(block.model_dump(mode="json", exclude_none=True))
        else:
            blocks.append(vars(block))
    structured = getattr(result, "structuredContent", None)
    protocol_error = bool(getattr(result, "isError", False))
    text = "\n".join(str(b.get("text", "")) for b in blocks if b.get("type", "text") == "text")
    if structured is None and len(blocks) == 1 and "text" in blocks[0] and not protocol_error:
        return text
    data = dict(structured) if isinstance(structured, dict) else dict(payload_dict(text) or {})
    if not data and text:
        data["message"] = text
    if structured is not None:
        data["structuredContent"] = structured
    data["mcp_content"] = blocks
    data["isError"] = protocol_error
    if not blocks and structured is None:
        data["error"] = "MCP tool returned no content"
    outcomes = [classify_result(data, protocol_error=protocol_error)]
    outcomes.extend(classify_result(b.get("text", "")) for b in blocks if "text" in b)
    data["_apexa_outcome"] = _combine_outcomes(*outcomes).model_dump()
    return json.dumps(data, ensure_ascii=False, default=str)


def result_response(value: Any) -> dict:
    """HTTP adapter that cannot turn tool failures or provisional science green."""
    outcome = classify_result(value)
    response = {"success": outcome.successful, "result": payload_dict(value) or value,
                "outcome": outcome.model_dump()}
    if outcome.error:
        response["error"] = outcome.error
    return response
