"""Bounded local operational traces, independent of verbose/debug logging.

Only allowlisted measurements and identifiers enter the store. Prompt text,
arguments, file paths, credentials, stdout and exception messages are excluded.
Writes run on a bounded background queue; overflow and sink errors are visible.
This is diagnostic evidence, not the durable scientific job transaction log.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, closing
from contextvars import ContextVar
from functools import wraps
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid

_context = ContextVar("apexa_trace", default=None)
_TEXT = {"tool", "model", "agent", "operation", "execution", "scientific", "job_id",
         "dataset_id", "plan_id", "error_type", "cancel_state", "endpoint", "role"}
_NUMBER = {"status_code", "elapsed_ms", "queue_wait_ms", "pid", "returncode", "prompt_tokens",
           "response_tokens", "artifact_count", "retry_count", "event_loop_lag_ms", "rss_bytes",
           "cpu_percent", "cache_hit", "query_chars", "llm_calls", "tools"}


def safe_attributes(attributes):
    result = {}
    for key, value in attributes.items():
        if key in _TEXT and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
            result[key] = value
        elif key in _NUMBER and isinstance(value, (int, float)) and math.isfinite(value):
            result[key] = value
    return result


class TraceStore:
    def __init__(self, path: Path, max_events=20000, queue_size=2048):
        self.path = Path(path)
        self.max_events = max(1, max_events)
        self.queue = queue.Queue(maxsize=queue_size)
        self.dropped = 0
        self.failures = 0
        self.last_error_type = None
        self.thread = threading.Thread(target=self._write, daemon=True, name="apexa-trace-writer")
        self.thread.start()

    def put(self, event):
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path, timeout=1)
        os.chmod(self.path, 0o600)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS trace_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, record TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS trace_events_trace ON trace_events(trace_id,seq)")
        db.commit()
        return db

    def _write(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                if isinstance(item, threading.Event):
                    item.set()
                    continue
                with closing(self._connect()) as db, db:
                    db.execute("INSERT INTO trace_events(trace_id,record) VALUES(?,?)", (item['trace_id'], json.dumps(item)))
                    # Indexed range deletion bounds retained history. SQLite
                    # reuses freed pages; WAL growth is bounded by checkpoints.
                    db.execute("DELETE FROM trace_events WHERE seq <= (SELECT MAX(seq)-? FROM trace_events)", (self.max_events,))
            except Exception as exc:
                self.failures += 1
                self.last_error_type = type(exc).__name__
                if self.failures == 1:
                    logging.getLogger(__name__).warning("Operational trace write failed (%s); status reports dropped/failed events", type(exc).__name__)
            finally:
                self.queue.task_done()

    def flush(self, timeout=3):
        done = threading.Event()
        try:
            self.queue.put(done, timeout=timeout)
        except queue.Full:
            return False
        return done.wait(timeout)

    def events(self, trace_id, after=0, limit=200):
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id) or after < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid trace cursor")
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=1)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trace_events'").fetchone():
                return []
            rows = db.execute("SELECT seq,record FROM trace_events WHERE trace_id=? AND seq>? ORDER BY seq LIMIT ?", (trace_id, after, limit)).fetchall()
        return [{"seq": seq, **json.loads(record)} for seq, record in rows]

    def status(self):
        return {"enabled": True, "queued_events": self.queue.qsize(), "dropped_events": self.dropped,
                "failed_events": self.failures, "last_error_type": self.last_error_type,
                "retained_event_limit": self.max_events, "durability": "best_effort_local_sqlite"}


_stores = {}
_lock = threading.Lock()


def trace_store():
    path = Path(os.environ.get("APEXA_TRACE_DB", str(Path.home() / ".apexa/runtime/traces.sqlite3"))).expanduser()
    with _lock:
        if path not in _stores:
            _stores[path] = TraceStore(path)
        return _stores[path]


def current_ids():
    return dict(_context.get() or {})


@contextmanager
def incoming_trace(value):
    """Accept correlation IDs only; metadata never grants execution authority."""
    valid = isinstance(value, dict) and all(
        isinstance(value.get(key), str) and re.fullmatch(r"[0-9a-f]{32}", value[key])
        for key in ('trace_id', 'span_id'))
    token = _context.set({key: value[key] for key in ('trace_id', 'span_id')} if valid else _context.get())
    try:
        yield
    finally:
        _context.reset(token)


def emit(event, **attributes):
    ctx = current_ids()
    if not ctx:
        return
    trace_store().put({"schema_version": 1, "timestamp": time.time(), "event": event,
                      **ctx, "attributes": safe_attributes(attributes)})


@contextmanager
def span(name, **attributes):
    parent = current_ids()
    context = {"trace_id": parent.get("trace_id", uuid.uuid4().hex),
               "span_id": uuid.uuid4().hex, "parent_span_id": parent.get("span_id")}
    token = _context.set(context)
    start = time.monotonic()
    fields = dict(attributes)
    emit(name + ".started", **fields)
    try:
        yield fields
    except BaseException as exc:
        fields.update(execution="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                      error_type=type(exc).__name__)
        raise
    finally:
        fields["elapsed_ms"] = round((time.monotonic() - start) * 1000, 3)
        emit(name + ".finished", **fields)
        _context.reset(token)


def traced_tool(function):
    @wraps(function)
    async def wrapper(self, tool_name, arguments):
        with span("tool", tool=tool_name) as fields:
            result = await function(self, tool_name, arguments)
            from apexa_outcomes import classify_result
            outcome = classify_result(result)
            fields.update(execution=outcome.execution, scientific=outcome.scientific_verdict)
            return result
    return wrapper


def traced_process(function):
    @wraps(function)
    async def wrapper(*args, **kwargs):
        with span("subprocess") as fields:
            result = await function(*args, **kwargs)
            fields.update(returncode=result.returncode,
                          execution="completed" if result.returncode == 0 else "failed")
            return result
    return wrapper


class TraceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        with span('http') as fields:
            async def traced_send(message):
                if message['type'] == 'http.response.start':
                    fields['status_code'] = message['status']
                    message = {**message, 'headers': [*message.get('headers', []),
                        (b'x-apexa-trace-id', current_ids()['trace_id'].encode())]}
                await send(message)
            await self.app(scope, receive, traced_send)
