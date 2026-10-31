"""Execution ledger — APEXA's execution-integrity primitive.

What this replaces
------------------
APEXA's integrity property is: *never surface a result that is not backed by an
executed tool call.* Until now that property was approximated by a cluster of
regex heuristics over the model's prose (``_looks_like_hallucinated_result``,
``_check_count_hallucination``, ``_PHANTOM_ASYNC_RE``, ``_REFUSAL_RE``, the
strategy gate, the thrash floor, …). Those grew because the text ``TOOL_CALL:``
protocol let models *narrate* tool use instead of performing it.

They had a structural flaw: **they fired on prose patterns regardless of what had
actually executed.** So a legitimate multi-step investigation could trip them —
observed live, when the thrash floor cut off an FF-HEDM debugging session with
``⚠ thrash floor: 5× run_command — forcing answer``.

This module inverts that. Every check here has an **exact precondition drawn from
recorded execution state**, never from prose alone:

===========================  ====================================================
check                        exact precondition
===========================  ====================================================
``unexecuted_tool_calls``    model emitted N calls, fewer than N reached the
                             execution chokepoint (no silent drops)
``zero_execution_claim``     the ledger for this turn is EMPTY
``ungrounded_paths``         a path in the answer appears in NO tool output and
                             in NO user message (set difference over real data)
===========================  ====================================================

Because the precondition is a fact rather than a pattern, a turn that genuinely
ran tools cannot be interrupted by these checks — which is the specific
regression the old guards caused.

Pure Python, no MCP spin-up, no network: unit-testable in isolation (same pattern
as ``skill_registry.py`` and ``handbook_guardrails.py``).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Cap on how much of each result we retain for grounding checks. Generous enough
# to cover directory listings and CSV heads; bounded so a 3 GB read cannot blow
# up memory.
_MAX_GROUNDING_CHARS = 200_000


@dataclass
class LedgerEntry:
    """One tool invocation that reached the execution chokepoint."""
    call_id: str
    name: str
    arguments: Dict[str, Any]
    status: str = "dispatched"      # dispatched | ok | error | denied
    result_digest: str = ""
    result_chars: int = 0
    elapsed_s: float = 0.0
    started_at: float = field(default_factory=time.time)

    @property
    def executed(self) -> bool:
        return self.status != "dispatched"

    @property
    def succeeded(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
            "status": self.status,
            "result_digest": self.result_digest,
            "result_chars": self.result_chars,
            "elapsed_s": round(self.elapsed_s, 3),
        }


# A concrete filesystem path: absolute, or relative with a directory separator and
# a file extension. Deliberately narrow — a bare word like "calibration" is not a
# claim, but "/data/scan5/Grains.csv" is.
_PATH_RE = re.compile(r"(?:(?<=\s)|^|[`'\"(\[])((?:/|\.{1,2}/)?(?:[\w.\-+]+/)+[\w.\-+]+\.[A-Za-z0-9]{1,6})")
_ABS_PATH_RE = re.compile(r"(/(?:[\w.\-+]+/)*[\w.\-+]+)")

# Paths that are ambient rather than claims about this turn's data.
_PATH_ALLOWLIST_PARTS = (
    "http://", "https://", "doi.org", "github.com",
)

# URLs must be removed BEFORE path scanning: the absolute-path pattern otherwise
# harvests "/example.com/a/b.html" out of "https://example.com/a/b.html", which
# would raise a false ungrounded-path violation on any answer citing a link.
_URL_RE = re.compile(r"\b(?:https?|ftp|s3|globus)://\S+", re.IGNORECASE)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def extract_paths(text: str) -> set[str]:
    """Concrete filesystem paths asserted in ``text``.

    Conservative by construction: only paths with a separator *and* an extension,
    or absolute paths with at least two components. URLs are excluded.
    """
    if not text:
        return set()
    text = _URL_RE.sub(" ", text)
    out: set[str] = set()
    for m in _PATH_RE.finditer(text):
        p = m.group(1)
        if any(a in p for a in _PATH_ALLOWLIST_PARTS):
            continue
        out.add(p)
    for m in _ABS_PATH_RE.finditer(text):
        p = m.group(1)
        if p.count("/") >= 2 and not any(a in p for a in _PATH_ALLOWLIST_PARTS):
            out.add(p)
    return out


class ToolLedger:
    """Records what actually executed during one turn.

    Single source of truth for execution integrity, session transcripts, and the
    benchmark's trace output.
    """

    def __init__(self) -> None:
        self._entries: List[LedgerEntry] = []
        self._by_id: Dict[str, LedgerEntry] = {}
        self._grounding: List[str] = []
        self._emitted_call_ids: set[str] = set()

    # ── recording ───────────────────────────────────────────────────────────

    def note_emitted(self, call_ids: Iterable[str]) -> None:
        """Register calls the model asked for, before dispatch.

        Lets ``unexecuted_tool_calls`` detect a call that was requested but never
        reached the chokepoint — a silent drop, which would otherwise look to the
        model like a tool that returned nothing.
        """
        self._emitted_call_ids.update(c for c in call_ids if c)

    def dispatch(self, call_id: str, name: str,
                 arguments: Optional[Dict[str, Any]] = None) -> LedgerEntry:
        entry = LedgerEntry(call_id=call_id or f"call_{len(self._entries)}",
                            name=name, arguments=dict(arguments or {}))
        self._entries.append(entry)
        self._by_id[entry.call_id] = entry
        self._emitted_call_ids.add(entry.call_id)
        return entry

    def complete(self, call_id: str, result: str, *,
                 status: Optional[str] = None,
                 elapsed_s: float = 0.0) -> None:
        """Record a tool's outcome and retain its text for grounding checks."""
        entry = self._by_id.get(call_id)
        if entry is None:
            entry = self.dispatch(call_id, name="<unknown>")
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        entry.status = status or self._infer_status(text)
        entry.result_digest = _digest(text)
        entry.result_chars = len(text)
        entry.elapsed_s = elapsed_s
        if len(text) > _MAX_GROUNDING_CHARS:
            text = text[:_MAX_GROUNDING_CHARS]
        self._grounding.append(text)

    @staticmethod
    def _infer_status(text: str) -> str:
        from apexa_outcomes import classify_result
        outcome = classify_result(text)
        if outcome.execution == "completed":
            return {"rejected": "error", "provisional": "warning"}.get(outcome.scientific_verdict, "ok")
        return "error" if outcome.execution == "failed" else outcome.execution

    def add_grounding(self, text: str) -> None:
        """Register text that legitimately grounds a claim but isn't a tool result
        (e.g. the user's own message naming a path)."""
        if text:
            self._grounding.append(text[:_MAX_GROUNDING_CHARS])

    # ── inspection ──────────────────────────────────────────────────────────

    @property
    def entries(self) -> Sequence[LedgerEntry]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def executed(self) -> List[LedgerEntry]:
        return [e for e in self._entries if e.executed]

    @property
    def succeeded(self) -> List[LedgerEntry]:
        return [e for e in self._entries if e.succeeded]

    def names(self) -> List[str]:
        return [e.name for e in self._entries]

    def unexecuted_call_ids(self) -> List[str]:
        return sorted(cid for cid in self._emitted_call_ids
                      if cid not in self._by_id or not self._by_id[cid].executed)

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    def summary(self) -> str:
        if not self._entries:
            return "no tools executed"
        ok = len(self.succeeded)
        return (f"{len(self._entries)} tool call(s): {ok} ok, "
                f"{len(self._entries) - ok} error/denied — "
                + ", ".join(dict.fromkeys(self.names())))

    # ── the integrity gate ──────────────────────────────────────────────────

    def check_final_answer(self, text: str) -> List[Dict[str, str]]:
        """Verify a proposed final answer against what actually executed.

        Returns a list of ``{code, message}`` violations (empty = clean). Every
        check is predicated on an exact ledger fact, so a turn that genuinely ran
        tools is never interrupted for merely *sounding* like a summary.
        """
        violations: List[Dict[str, str]] = []
        text = text or ""

        # 1. Silent drops — exact: the model asked for calls that never executed.
        missing = self.unexecuted_call_ids()
        if missing:
            violations.append({
                "code": "unexecuted_tool_calls",
                "message": (f"{len(missing)} tool call(s) were requested but never "
                            f"executed: {', '.join(missing[:5])}. Do not report results "
                            f"for them; re-issue the calls."),
            })

        # 2. Zero-execution claim — exact precondition: the ledger is EMPTY. Only
        #    then do we look at the prose at all, and only for concrete artifacts.
        if not self._entries:
            claimed = extract_paths(text)
            if claimed:
                violations.append({
                    "code": "zero_execution_claim",
                    "message": (f"No tool ran this turn, yet the answer cites concrete "
                                f"paths ({', '.join(sorted(claimed)[:3])}). Call the tool "
                                f"and report only what it returns."),
                })

        # 3. Ungrounded paths — exact: set difference against real tool output.
        elif (claimed := extract_paths(text)):
            haystack = "\n".join(self._grounding)
            ungrounded = sorted(p for p in claimed if p not in haystack)
            if ungrounded:
                violations.append({
                    "code": "ungrounded_paths",
                    "message": (f"These paths appear in the answer but in no tool output: "
                                f"{', '.join(ungrounded[:5])}. Either verify them with a "
                                f"tool or drop them."),
                })

        return violations
