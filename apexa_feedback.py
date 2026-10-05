#!/usr/bin/env python3
"""User feedback capture for APEXA.

Feedback without context is noise. "It gave a wrong answer" is unactionable a week
later; "run X, model Y, engine `native` because APEXA_MIDAS_BIN was unset, these
four tool calls" is a bug report. So every record carries the turn's machine
context automatically, and the UI shows the user exactly what that is before
anything is written.

Design constraints that are specific to APEXA, not generic product advice:

* **Local-first, always.** Beamline hosts run at network tier `internal` or `data`
  and may reach nothing outside ANL. Feedback is appended to JSONL under
  ``~/.apexa/feedback/`` and never leaves the machine. There is no service to be
  down and nothing to fail closed.
* **The web UI has no authentication** (``docs/ALCF_MULTIUSER.md`` says so
  plainly), so a record states the OS user it was written by and is not treated as
  an identity claim.
* **Paths are the payload.** A beamline path can name an unpublished experiment,
  so the caller passes context explicitly and `redact()` is available; nothing is
  scraped from the environment behind the user's back.

Append-only JSONL, one file per month, stdlib only. Never raises into a request
path: `record()` returns ``(ok, detail)``.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = 1

#: Kinds, coarsest first. The one-click signals carry no prose; `report` does.
KINDS = ("up", "down", "report")

#: Categories for a `report`. Deliberately short — a long list makes people pick
#: "other", and the free text is where the value is anyway.
CATEGORIES = (
    "wrong_result",      # it ran and the answer is wrong
    "failed_to_run",     # it refused or errored when it should not have
    "misleading",        # the wording led me somewhere wrong
    "slow",
    "feature_request",
    "other",
)

MAX_COMMENT = 8000
MAX_CONTEXT_BYTES = 64_000


def feedback_dir() -> Path:
    """Where records live. ``APEXA_FEEDBACK_DIR`` overrides, for a shared triage
    location on a beamline host where several users share an install."""
    d = os.environ.get("APEXA_FEEDBACK_DIR", "").strip()
    return Path(d).expanduser() if d else Path.home() / ".apexa" / "feedback"


def _month_file(when: Optional[datetime] = None) -> Path:
    when = when or datetime.now(timezone.utc)
    return feedback_dir() / f"feedback-{when:%Y-%m}.jsonl"


_HOME = re.compile(re.escape(str(Path.home())))


def redact(text: str) -> str:
    """Soften the two things that leak most often: the home directory and
    anything that looks like an email. Not a security boundary -- the user can see
    and edit the payload before sending; this only lowers the cost of being
    careless."""
    if not text:
        return text
    text = _HOME.sub("~", text)
    return re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "<email>", text)


def _clip(obj: Any, budget: int = MAX_CONTEXT_BYTES) -> Any:
    """Keep a context blob bounded. A tool result can be megabytes; a feedback
    record that large is a denial of service against the person triaging it."""
    try:
        s = json.dumps(obj, default=str)
    except Exception:
        return {"_unserializable": True}
    if len(s) <= budget:
        return obj
    return {"_truncated": True, "_original_bytes": len(s),
            "_head": s[: budget // 2], "_tail": s[-budget // 4:]}


def record(
    kind: str,
    *,
    comment: str = "",
    category: str = "",
    session_id: str = "",
    message_id: str = "",
    model: str = "",
    context: Optional[Dict[str, Any]] = None,
    user: str = "",
    do_redact: bool = True,
) -> Tuple[bool, str]:
    """Append one feedback record. Returns ``(ok, detail)``; never raises."""
    try:
        if kind not in KINDS:
            return False, f"kind must be one of {KINDS}"
        if category and category not in CATEGORIES:
            return False, f"category must be one of {CATEGORIES}"
        if kind == "report" and not comment.strip():
            return False, "a report needs a comment"

        comment = (comment or "")[:MAX_COMMENT]
        ctx = _clip(context or {})
        if do_redact:
            comment = redact(comment)
            try:
                ctx = json.loads(redact(json.dumps(ctx, default=str)))
            except Exception:
                pass

        rec = {
            "schema": SCHEMA,
            "id": uuid.uuid4().hex[:12],
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "category": category or None,
            "comment": comment or None,
            "session_id": session_id or None,
            "message_id": message_id or None,
            "model": model or None,
            # Not an identity claim: the web UI has no authentication.
            "os_user": user or os.environ.get("USER") or "unknown",
            "host": os.uname().nodename if hasattr(os, "uname") else "",
            "apexa": _apexa_versions(),
            "context": ctx,
        }
        path = _month_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
        return True, rec["id"]
    except Exception as e:                      # must never break a request
        return False, f"{type(e).__name__}: {e}"


_VERSIONS: Optional[Dict[str, str]] = None


def _apexa_versions() -> Dict[str, str]:
    """The few versions that actually explain a bug report here."""
    global _VERSIONS
    if _VERSIONS is not None:
        return _VERSIONS
    out: Dict[str, str] = {}
    try:
        import importlib.metadata as md
        for p in ("mcp", "midas-suite", "midas-calibrate-v2", "midas-pipeline"):
            try:
                out[p] = md.version(p)
            except Exception:
                pass
    except Exception:
        pass
    for var in ("APEXA_LLM_MODE", "APEXA_NETWORK", "APEXA_AGENT_MODE",
                "APEXA_BEAMLINE"):
        v = os.environ.get(var)
        if v:
            out[var] = v
    out["midas_bin_set"] = "yes" if os.environ.get("APEXA_MIDAS_BIN") else "no"
    _VERSIONS = out
    return out


def read_all(limit: int = 0) -> List[Dict[str, Any]]:
    """Every record, newest last. Bad lines are skipped, not fatal."""
    out: List[Dict[str, Any]] = []
    d = feedback_dir()
    if not d.exists():
        return out
    for f in sorted(d.glob("feedback-*.jsonl")):
        try:
            for ln in f.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
        except Exception:
            continue
    return out[-limit:] if limit else out


def summarize() -> Dict[str, Any]:
    """Counts a maintainer actually wants: volume, direction, and what is being
    reported against."""
    recs = read_all()
    by_kind: Dict[str, int] = {}
    by_cat: Dict[str, int] = {}
    by_tool: Dict[str, int] = {}
    for r in recs:
        by_kind[r.get("kind", "?")] = by_kind.get(r.get("kind", "?"), 0) + 1
        if r.get("category"):
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        for t in (r.get("context") or {}).get("tools", []) or []:
            n = t.get("name") if isinstance(t, dict) else str(t)
            if n:
                by_tool[n] = by_tool.get(n, 0) + 1
    total = len(recs)
    pos, neg = by_kind.get("up", 0), by_kind.get("down", 0)
    return {
        "total": total,
        "by_kind": by_kind,
        "by_category": by_cat,
        "tools_mentioned": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])[:15]),
        "satisfaction": (round(pos / (pos + neg), 3) if (pos + neg) else None),
        "dir": str(feedback_dir()),
    }


if __name__ == "__main__":                       # tiny triage CLI
    import argparse
    ap = argparse.ArgumentParser(description="APEXA feedback triage")
    ap.add_argument("cmd", choices=("summary", "list", "export"))
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "summary":
        print(json.dumps(summarize(), indent=2))
    elif a.cmd == "list":
        for r in read_all(limit=a.n):
            mark = {"up": "+", "down": "-", "report": "!"}.get(r.get("kind"), "?")
            print(f"{mark} {r.get('ts','')[:19]}  {r.get('category') or r.get('kind'):<16}"
                  f" {(r.get('comment') or '')[:72]}")
    else:
        print(json.dumps(read_all(), indent=2, default=str))
