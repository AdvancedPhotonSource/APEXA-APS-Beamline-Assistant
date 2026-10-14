"""Shared CLI/WebUI sessions, with atomic snapshots and one writer per turn.

Model memory and the human transcript are separate: restoring a display-only
transcript must never discard structured tool calls or the compacted summary.
Existing JSON/JSONL sessions and web_threads are imported without deleting them.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

SCHEMA_VERSION = 2
MAX_RECORD_BYTES = 64_000_000
MAX_WORKSPACE_BYTES = 20_000_000


class SessionConflict(ValueError):
    pass


class SessionBusy(SessionConflict):
    pass


@contextmanager
def file_lock(path: Path, *, blocking=True):
    """Process-scoped advisory lock on local Mac, Linux and Windows filesystems."""
    with path.open("a+b") as stream:
        if os.name == "nt":
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (13, 11, 36):
                        raise
                    if not blocking:
                        raise SessionBusy("This session is in use by another CLI or WebUI request. Wait for that turn to finish, then reload the session.") from exc
                    time.sleep(0.02)
        else:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise SessionBusy("This session is in use by another CLI or WebUI request. Wait for that turn to finish, then reload the session.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def session_root() -> Path:
    return Path(os.environ.get("APEXA_SESSION_DIR", str(Path.home() / ".apexa" / "sessions"))).expanduser()


def valid_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError("Session ID must be 1–128 letters, digits, underscores, dots or hyphens, starting with a letter, digit or underscore")
    return name


def _now():
    return int(time.time() * 1000)


def _timestamp(value, fallback):
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except (TypeError, ValueError):
        return fallback


def _display_text(value):
    if not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False) if value is not None else ""
    return value.split("\n\n[Web UI context:", 1)[0]


def _messages(history, created):
    """Recover UI messages and recorded tool outputs from a legacy model history."""
    messages, events, tool_names = [], [], {}
    for i, entry in enumerate(history):
        if not isinstance(entry, dict):
            continue
        for call in entry.get("tool_calls", []) or []:
            fn = call.get("function", {})
            args = fn.get("arguments", {})
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                args = {}
            tool_names[call.get("id")] = (fn.get("name", "unknown"), args)
        role, content = entry.get("role"), entry.get("content")
        if role == "tool":
            name, args = tool_names.get(entry.get("tool_call_id"), (entry.get("name", "unknown"), {}))
            events.append({"tool": name, "args": args, "result": content or ""})
            continue
        if role == "user" and isinstance(content, str) and content.startswith("[Tool Result for "):
            match = re.match(r"\[Tool Result for ([^\]]+)\]\s*(.*)", content, re.S)
            if match:
                events.append({"tool": match[1], "args": {}, "result": match[2]})
            continue
        if role not in ("user", "assistant", "system") or not content or entry.get("tool_calls"):
            continue
        msg = {"id": f"legacy-{i}", "role": role, "content": _display_text(content),
               "timestamp": _timestamp(entry.get("ts"), created + i)}
        if role == "assistant" and events:
            msg["toolEvents"], events = events, []
        messages.append(msg)
    if events:
        messages.append({"id": "legacy-tools", "role": "assistant", "content": "Recorded tool results; no final reply was saved.",
                         "timestamp": created + len(history), "toolEvents": events})
    return messages


def memory_from_messages(messages):
    history = []
    for message in messages:
        if message.get("role") not in ("user", "assistant", "system"):
            continue
        for event in message.get("toolEvents", []):
            history.append({"role": "user", "content": f"[Tool Result for {event['tool']}]\n{event['result']}"})
        for result in message.get("toolResults", []):
            history.append({"role": "user", "content": f"[Tool Result for {result.get('tool', 'unknown')}]\n{json.dumps(result.get('data', {}))}"})
        if message.get("content"):
            history.append({"role": message["role"], "content": message["content"]})
    return history


class SessionStore:
    def __init__(self, root: Path | None = None, legacy_web_root: Path | None = None):
        self.root = Path(root) if root is not None else session_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.legacy_web_root = legacy_web_root or self.root.parent / "web_threads"

    def path(self, name):
        return self.root / f"{valid_name(name)}.json"

    @contextmanager
    def _lock(self, name, *, turn=False, blocking=True):
        valid_name(name)
        path = self.root / f".{name}.{'turn' if turn else 'record'}.lock"
        with file_lock(path, blocking=blocking):
            yield

    def busy(self, name):
        try:
            with self._lock(name, turn=True, blocking=False):
                return False
        except SessionBusy:
            return True

    def _new(self, name, origin="web", title="New chat"):
        now = _now()
        return {"schema_version": SCHEMA_VERSION, "id": valid_name(name), "title": title,
                "createdAt": now, "updatedAt": now, "origin": origin, "messages": [],
                "conversation_history": [], "running_summary": "", "metadata": {},
                "workspace": {}, "workspaceRevision": 0, "memoryRevision": 0, "revision": 0,
                "status": "idle", "archived": False, "model": "", "active_directory": ""}

    def _json(self, path):
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ValueError(f"Session exceeds the {MAX_RECORD_BYTES // 1_000_000} MB read limit")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Session file is not a JSON object")
        return data

    def _load(self, name, create=False, origin="web"):
        path = self.path(name)
        transcript = path.with_suffix(".jsonl")
        legacy_web = self.legacy_web_root / path.name
        legacy = False
        if path.exists():
            data = self._json(path)
            if data.get("schema_version") == SCHEMA_VERSION:
                return data
            if "schema_version" in data:
                raise ValueError("Unsupported session schema version; the saved session was not modified")
        elif transcript.exists():
            data = {}
        elif legacy_web.exists():
            data, legacy = self._json(legacy_web), True
        elif create:
            return self._new(name, origin)
        else:
            raise FileNotFoundError(name)
        created = _timestamp(data.get("start_time"), int((path if path.exists() else transcript if transcript.exists() else legacy_web).stat().st_mtime * 1000))
        raw = []
        if transcript.exists():
            if transcript.stat().st_size > MAX_RECORD_BYTES:
                raise ValueError("Legacy transcript exceeds the 64 MB read limit")
            lines = transcript.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict) and "role" in item and "content" in item:
                        raw.append(item)
                except ValueError as exc:
                    if index != len(lines) - 1:
                        raise ValueError(f"Unreadable legacy transcript at line {index + 1}; original file retained") from exc
                    # A process may have stopped halfway through its final append.
        record = self._new(name, "web" if legacy else "cli", name)
        history = data.get("conversation_history") or raw
        record.update(createdAt=created, updatedAt=_timestamp(data.get("saved_at"), created),
                      conversation_history=history, running_summary=data.get("running_summary", ""),
                      metadata={k: v for k, v in data.items() if k not in ("conversation_history", "running_summary")},
                      messages=_messages(raw or history, created), active_directory=data.get("current_directory", ""))
        # Enrich the full human transcript with tools present in the saved memory.
        if raw:
            rich = _messages(history, created)
            for message in record["messages"]:
                match = next((m for m in reversed(rich) if m["content"] == message["content"] and m.get("toolEvents")), None)
                if match:
                    message["toolEvents"] = match["toolEvents"]
        if record["messages"] and record["messages"][0]["role"] == "user":
            record["title"] = record["messages"][0]["content"][:80] or name
        return record

    def _write(self, record):
        record["revision"] += 1
        record["updatedAt"] = _now()
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("Session exceeds 64 MB; no saved data was overwritten")
        fd, temporary = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path(record["id"]))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def change(self, name, update, *, create=False, origin="web"):
        with self._lock(name):
            record = self._load(name, create=create, origin=origin)
            update(record)
            self._write(record)
            return copy.deepcopy(record)

    def get(self, name):
        with self._lock(name):
            record = self._load(name)
        if record.get("status") == "running" and not self.busy(name):
            record["status"] = "interrupted"
        if record.get("status") == "interrupted":
            record["resume_notice"] = "The previous turn was interrupted. Recorded tool results are retained; check any external jobs before retrying. No processing was restarted."
        elif record.get("status") == "running":
            record["resume_notice"] = "A turn is running in another connection. Reload after it finishes."
        return record

    def ensure(self, name, origin="web"):
        with self._lock(name):
            record = self._load(name, create=True, origin=origin)
            if not self.path(name).exists() or record["revision"] == 0:
                self._write(record)
            return record

    def list(self, archived=False):
        names = {p.stem for p in self.root.glob("*.json")} | {p.stem for p in self.root.glob("*.jsonl")}
        names |= {p.stem for p in self.legacy_web_root.glob("*.json")}
        rows = []
        for name in names:
            try:
                valid_name(name)
            except ValueError:
                continue
            try:
                record = self.get(name)
                if record.get("archived", False) != archived:
                    continue
                rows.append({k: record.get(k) for k in ("id", "title", "createdAt", "updatedAt", "origin", "status", "revision", "model", "resume_notice")} | {"messageCount": len(record["messages"]), "messages": []})
            except (OSError, ValueError) as exc:
                rows.append({"id": name, "title": name, "status": "unreadable", "error": str(exc), "messages": [], "messageCount": 0, "updatedAt": 0, "createdAt": 0})
        return sorted(rows, key=lambda row: row["updatedAt"], reverse=True)

    @contextmanager
    def turn(self, name):
        with self._lock(name, turn=True, blocking=False):
            yield

    def begin(self, name, query, display, origin):
        turn_id = uuid.uuid4().hex
        def update(record):
            previous = record.get("turn")
            if record.get("status") in ("running", "interrupted") and previous:
                self._interrupt(record, "Previous turn interrupted; inspect recorded results and external jobs before retrying.")
            now = _now()
            user = {"id": f"user-{turn_id}", "role": "user", "content": display, "timestamp": now}
            answer = {"id": f"answer-{turn_id}", "role": "assistant", "content": "", "timestamp": now, "toolEvents": []}
            record["messages"].extend([user, answer])
            record.update(status="running", archived=False, turn={"id": turn_id, "query": query, "answer_id": answer["id"], "origin": origin})
            if record["title"] in ("New chat", name):
                record["title"] = display.strip().replace("\n", " ")[:80]
        return self.change(name, update, create=True, origin=origin)

    def tool(self, name, turn_id, tool, args, result):
        def update(record):
            if (record.get("turn") or {}).get("id") != turn_id:
                raise SessionConflict("Session turn changed while recording a tool result")
            record["messages"][-1].setdefault("toolEvents", []).append({"tool": tool, "args": args, "result": result})
        return self.change(name, update)

    def _interrupt(self, record, reason):
        turn = record.get("turn")
        if not turn:
            return
        answer = next(m for m in record["messages"] if m["id"] == turn["answer_id"])
        answer["content"] = reason
        # A crashed model turn may not have committed its tool exchanges. Retain
        # the executed outputs in plain tool-result messages, without dangling IDs.
        record["conversation_history"].extend([
            {"role": "user", "content": turn["query"]},
            *memory_from_messages([answer]),
        ])
        record["status"] = "interrupted"
        record["turn"] = None
        record["memoryRevision"] += 1

    def finish(self, name, turn_id, response, *, history, summary, metadata, model, active_directory, error=False):
        def update(record):
            if (record.get("turn") or {}).get("id") != turn_id:
                raise SessionConflict("Session turn changed before it could be saved")
            if error:
                self._interrupt(record, response)
            else:
                record["messages"][-1]["content"] = response
                record.update(conversation_history=history, running_summary=summary, status="idle", turn=None)
                record["memoryRevision"] += 1
            record.update(metadata=metadata, model=model, active_directory=active_directory)
        return self.change(name, update)

    def workspace(self, name, workspace, expected):
        if len(json.dumps(workspace, allow_nan=False).encode()) > MAX_WORKSPACE_BYTES:
            raise ValueError("Workspace exceeds 20 MB. Export large plots before saving; the previous workspace is intact.")
        def update(record):
            if expected != record["workspaceRevision"]:
                raise SessionConflict("Workspace changed in another window. Reload the session before saving your canvas changes.")
            record["workspace"] = workspace
            record["workspaceRevision"] += 1
        return self.change(name, update, create=True)

    def rename(self, name, title):
        if not title.strip():
            raise ValueError("A session title is required")
        return self.change(name, lambda r: r.update(title=title.strip()[:160]))

    def archive(self, name):
        with self.turn(name):
            return self.change(name, lambda r: r.update(archived=True))

    def import_browser(self, session):
        name = valid_name(session["id"])
        with self._lock(name):
            try:
                record = self._load(name)
            except FileNotFoundError:
                record = self._new(name)
            if record.get("browser_imported"):
                return record
            incoming = session.get("messages", [])
            if not record["messages"]:
                record["messages"] = incoming
                record["conversation_history"] = memory_from_messages(incoming)
            else:
                # Old web buckets own model memory. Browser records supply the
                # clean display transcript when it agrees with the saved turns.
                unmatched = list(incoming)
                for message in record["messages"]:
                    match = next((m for m in unmatched if m.get("role") == message["role"] and m.get("content") == message["content"]), None)
                    if match:
                        unmatched.remove(match)
                        for key in ("artifacts", "toolResults"):
                            if match.get(key):
                                message[key] = match[key]
                # The browser may retain turns outside the compacted model
                # window. Keep them for display without changing model memory.
                record["messages"].extend(unmatched)
                record["messages"].sort(key=lambda m: m.get("timestamp", 0))
            record["title"] = session.get("title") or record["title"]
            record["createdAt"] = min(record["createdAt"], session.get("createdAt", record["createdAt"]))
            record["browser_imported"] = True
            self._write(record)
            return record
