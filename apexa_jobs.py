"""Durable local run admission and event log, independent of scientific engines.

SQLite owns submission identity and claims. An expired running claim becomes
uncertain, never automatically rerun: the external effect and this database
cannot be committed atomically. This is not a facility-wide scheduler.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import asyncio
import inspect
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from apexa_dataset import canonical

TERMINAL = {"completed", "failed", "refused", "cancelled"}


def default_job_store():
    return JobStore(os.environ.get("APEXA_RUN_DIR", "~/.apexa/runs"))


class JobStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / "runs.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    owner TEXT, token TEXT, lease_until REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    cancellation_status TEXT NOT NULL DEFAULT 'not_requested',
                    result TEXT, error TEXT, trace_id TEXT);
                CREATE INDEX IF NOT EXISTS run_state ON runs(state, created);
                CREATE TABLE IF NOT EXISTS run_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL,
                    trace_id TEXT, FOREIGN KEY(run_id) REFERENCES runs(id));
                CREATE INDEX IF NOT EXISTS run_event_cursor ON run_events(run_id, seq);
            """)
        self.db.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _event(self, db, run_id, kind, data=None, *, now=None):
        trace = db.execute("SELECT trace_id FROM runs WHERE id=?", (run_id,)).fetchone()[0]
        db.execute("INSERT INTO run_events(run_id,kind,at,data,trace_id) VALUES(?,?,?,?,?)",
                   (run_id, kind, time.time() if now is None else now, canonical(data or {}), trace))

    def submit(self, payload, *, idempotency_key=None, trace_id=None, _connection=None):
        """Idempotent admission. Reusing a key for different input is an error."""
        if not isinstance(payload, dict) or not payload:
            raise ValueError("job payload must be a nonempty object")
        encoded = canonical(payload)
        if trace_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trace_id):
            raise ValueError("trace_id must be a safe identifier")
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key):
            raise ValueError("idempotency_key must be a nonempty string")
        identity = hashlib.sha256(("key:" + idempotency_key if idempotency_key is not None else "payload:" + encoded).encode()).hexdigest()
        run_id = "run-" + identity
        with (self.connect() if _connection is None else nullcontext(_connection)) as db:
            if _connection is None:
                db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload FROM runs WHERE identity=?", (identity,)).fetchone()
            if existing:
                if existing[0] != encoded:
                    raise ValueError("idempotency key already belongs to a different payload")
                return run_id
            now = time.time()
            db.execute("INSERT INTO runs(id,identity,payload,state,created,updated,trace_id) VALUES(?,?,?,'queued',?,?,?)",
                       (run_id, identity, encoded, now, now, trace_id))
            self._event(db, run_id, "submitted", {"state": "queued"}, now=now)
        return run_id

    @staticmethod
    def _decode(row):
        row = dict(row)
        row["payload"] = json.loads(row["payload"])
        row["result"] = json.loads(row["result"]) if row["result"] is not None else None
        # Claim tokens are private worker credentials, not web response fields.
        row.pop("token", None)
        return row

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._decode(row)

    status = get

    def get_by_key(self, idempotency_key):
        """Recover admission after a caller crashed before recording its ID."""
        identity = hashlib.sha256(("key:" + idempotency_key).encode()).hexdigest()
        return self.get("run-" + identity)

    def list(self, *, limit=100, states=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        query, params = "SELECT * FROM runs", []
        if states:
            query += " WHERE state IN (" + ",".join("?" for _ in states) + ")"
            params.extend(states)
        query += " ORDER BY created DESC,id LIMIT ?"
        with self.connect() as db:
            return [self._decode(row) for row in db.execute(query, (*params, limit))]

    def events(self, run_id, *, after=0, limit=500):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("after must be nonnegative and limit 1..1000")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError(run_id)
            rows = db.execute("SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (run_id, after, limit))
            return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def claim(self, *, owner, lease_seconds=60, run_id=None, now=None):
        if not owner or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("claim requires an owner and finite positive lease")
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM runs WHERE state='queued'"
            row = db.execute(query + (" AND id=?" if run_id else "") + " ORDER BY created,id LIMIT 1", (run_id,) if run_id else ()).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE runs SET state='claimed',owner=?,token=?,lease_until=?,updated=?,attempts=attempts+1 WHERE id=?",
                       (owner, token, now + lease_seconds, now, row["id"]))
            self._event(db, row["id"], "claimed", {"owner": owner}, now=now)
            return row["id"], token

    def start(self, run_id, token, *, now=None):
        """Persist possible execution before calling an external adapter."""
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE runs SET state='running',updated=? WHERE id=? AND token=? AND state='claimed' AND lease_until>?",
                                 (now, run_id, token, now)).rowcount
            if changed:
                self._event(db, run_id, "execution_started", now=now)
            return bool(changed)

    def heartbeat(self, run_id, token, *, lease_seconds=60, now=None):
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        now = time.time() if now is None else now
        with self.connect() as db:
            return bool(db.execute("UPDATE runs SET lease_until=? WHERE id=? AND token=? AND state IN ('claimed','running') AND lease_until>?",
                                   (now + lease_seconds, run_id, token, now)).rowcount)

    def finish(self, run_id, token, state, *, result=None, error=None, now=None):
        if state not in TERMINAL | {"uncertain", "submitted"}:
            raise ValueError("invalid completion state")
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM runs WHERE id=? AND token=? AND state IN ('claimed','running') AND lease_until>?",
                             (run_id, token, now)).fetchone()
            if not row:
                return False
            if row["state"] == "claimed" and state not in ("refused", "cancelled"):
                raise ValueError("unstarted work can only be refused or cancelled")
            db.execute("UPDATE runs SET state=?,result=?,error=?,updated=?,token=NULL,lease_until=NULL,cancellation_status=CASE WHEN ?='cancelled' THEN 'stopped' ELSE cancellation_status END WHERE id=?",
                       (state, canonical(result) if result is not None else None, error, now, state, run_id))
            self._event(db, run_id, state, {"error": error} if error else {}, now=now)
            return True

    def cancel(self, run_id):
        """Request cancellation. Only unstarted jobs can be stopped here."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,cancellation_status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["state"] in TERMINAL or row["cancellation_status"] != "not_requested":
                pass
            elif row["state"] in ("queued", "claimed"):
                db.execute("UPDATE runs SET state='cancelled',cancellation_status='stopped',token=NULL,lease_until=NULL,updated=? WHERE id=?", (time.time(), run_id))
                self._event(db, run_id, "cancelled", {"execution_started": False})
            else:
                db.execute("UPDATE runs SET cancellation_status='requested',updated=? WHERE id=?", (time.time(), run_id))
                self._event(db, run_id, "cancellation_requested", {"execution_stopped": False})
        return self.get(run_id)

    def acknowledge_cancel(self, run_id, token):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE runs SET cancellation_status='acknowledged',updated=? WHERE id=? AND token=? AND state='running' AND cancellation_status='requested' AND lease_until>?",
                                 (time.time(), run_id, token, time.time())).rowcount
            if changed:
                self._event(db, run_id, "cancellation_acknowledged", {"execution_stopped": False})
            return bool(changed)

    def reconcile(self, *, now=None):
        """Recover expired leases. No external effects are retried here."""
        now = time.time() if now is None else now
        changed = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,state FROM runs WHERE state IN ('claimed','running') AND lease_until<=?", (now,)).fetchall()
            for row in rows:
                state = "queued" if row["state"] == "claimed" else "uncertain"
                reason = "lease expired before execution" if state == "queued" else "worker lease expired; external execution outcome unknown"
                db.execute("UPDATE runs SET state=?,error=?,updated=?,token=NULL,lease_until=NULL WHERE id=?", (state, reason, now, row["id"]))
                self._event(db, row["id"], "reconciled", {"state": state, "reason": reason}, now=now)
                changed.append(row["id"])
        return [self.get(run_id) for run_id in changed]

    def resolve(self, run_id, *, state, evidence, result=None):
        """Record externally verified outcome; never starts or requeues work."""
        if state not in TERMINAL or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("resolution requires a terminal state and explicit evidence")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] not in ("uncertain", "submitted"):
                raise ValueError("only uncertain or externally submitted work can be resolved")
            db.execute("UPDATE runs SET state=?,result=COALESCE(?,result),error=NULL,updated=?,cancellation_status=CASE WHEN ?='cancelled' THEN 'stopped' ELSE cancellation_status END WHERE id=?",
                       (state, canonical(result) if result is not None else None, time.time(), state, run_id))
            self._event(db, run_id, "resolved", {"state": state, "evidence": evidence})
        return self.get(run_id)

    def run_one(self, executor, *, run_id=None, prepare=None, lease_seconds=60):
        """Run a local adapter(payload, context), with a renewable claim lease.

        ``prepare`` must have no external execution effects. Executor exceptions
        and malformed receipts become uncertain. This is deliberately not an
        automatic retry loop. The injectable adapter supports local fault tests.
        """
        self.reconcile()
        claim = self.claim(owner=f"pid-{os.getpid()}-{uuid.uuid4().hex}", lease_seconds=lease_seconds, run_id=run_id)
        if claim is None:
            return None
        run_id, token = claim
        context = JobContext(self, run_id, token)
        stop = threading.Event()
        def renew():
            while not stop.wait(max(0.01, lease_seconds / 3)):
                try:
                    if not self.heartbeat(run_id, token, lease_seconds=lease_seconds):
                        return
                except sqlite3.Error:
                    return  # lease expiry prevents stale publication
        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            payload = self.get(run_id)["payload"]
            try:
                if prepare is not None:
                    prepare(payload)
            except Exception as exc:
                self.finish(run_id, token, "refused", error=f"preflight: {type(exc).__name__}: {exc}")
                return self.get(run_id)
            if not self.start(run_id, token):
                return self.get(run_id)
            try:
                result = executor(payload, context)
                if isinstance(result, str):
                    result = json.loads(result)
                state = _result_state(result)
                self.finish(run_id, token, state, result=result,
                            error=result.get("error") if isinstance(result, dict) else "invalid executor receipt")
            except Exception as exc:
                self.finish(run_id, token, "uncertain", error=f"{type(exc).__name__}: {exc}")
            return self.get(run_id)
        finally:
            stop.set()
            thread.join(timeout=2)

    async def arun_one(self, executor, *, run_id, prepare=None, lease_seconds=60):
        """Async equivalent for loop-owned MCP clients; database I/O is offloaded.

        The executor is awaited on the caller's loop. Cancellation is propagated
        and drained, but without a verified stopped receipt its outcome remains
        uncertain. An expired/lost lease never authorizes another execution.
        """
        await asyncio.to_thread(self.reconcile)
        claim = await asyncio.to_thread(self.claim, owner=f"async-{os.getpid()}-{uuid.uuid4().hex}",
                                       lease_seconds=lease_seconds, run_id=run_id)
        if claim is None:
            return await asyncio.to_thread(self.get, run_id)
        _, token = claim
        context = JobContext(self, run_id, token)
        lost = asyncio.Event()
        async def renew():
            while True:
                await asyncio.sleep(max(0.01, lease_seconds / 3))
                try:
                    if not await asyncio.to_thread(self.heartbeat, run_id, token, lease_seconds=lease_seconds):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return
        heartbeat = asyncio.create_task(renew())
        operation = None
        async def drain(task):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            return task.result()
        async def stop_operation():
            if operation is not None:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        async def finish(state, **kwargs):
            return await drain(asyncio.create_task(asyncio.to_thread(self.finish, run_id, token, state, **kwargs)))
        try:
            payload = (await asyncio.to_thread(self.get, run_id))["payload"]
            try:
                if prepare is not None:
                    if inspect.iscoroutinefunction(prepare):
                        await prepare(payload)
                    else:
                        await asyncio.to_thread(prepare, payload)
            except Exception as exc:
                await finish("refused", error=f"preflight: {type(exc).__name__}: {exc}")
                return await asyncio.to_thread(self.get, run_id)
            if lost.is_set() or not await asyncio.to_thread(self.start, run_id, token):
                return await asyncio.to_thread(self.get, run_id)
            operation = asyncio.create_task(executor(payload, context))
            while not operation.done():
                row = await asyncio.to_thread(self.get, run_id)
                if operation.done():
                    break  # a received outcome wins over a simultaneous stop request
                if row["cancellation_status"] != "not_requested" or lost.is_set() or row["state"] == "uncertain":
                    if row["cancellation_status"] == "requested":
                        await asyncio.to_thread(self.acknowledge_cancel, run_id, token)
                    await drain(asyncio.create_task(stop_operation()))
                    await finish("uncertain", error="execution interrupted; external stopped state requires verification")
                    return await asyncio.to_thread(self.get, run_id)
                await asyncio.wait({operation}, timeout=0.2)
            result = await operation
            if isinstance(result, str):
                result = json.loads(result)
            await finish(_result_state(result), result=result,
                         error=result.get("error") if isinstance(result, dict) else "invalid executor receipt")
            return await asyncio.to_thread(self.get, run_id)
        except asyncio.CancelledError:
            await drain(asyncio.create_task(stop_operation()))
            row = await asyncio.to_thread(self.get, run_id)
            await finish("cancelled" if row["state"] == "claimed" else "uncertain",
                         error="caller cancelled; external execution outcome not assumed stopped")
            raise
        except Exception as exc:
            await drain(asyncio.create_task(stop_operation()))
            row = await asyncio.to_thread(self.get, run_id)
            await finish("refused" if row["state"] == "claimed" else "uncertain", error=f"{type(exc).__name__}: {exc}")
            return await asyncio.to_thread(self.get, run_id)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)


class JobContext:
    def __init__(self, store, run_id, token):
        self.store, self.run_id, self.token = store, run_id, token

    @property
    def cancellation_requested(self):
        return self.store.get(self.run_id)["cancellation_status"] != "not_requested"

    def acknowledge_cancel(self):
        return self.store.acknowledge_cancel(self.run_id, self.token)


def _result_state(result):
    if not isinstance(result, dict):
        return "uncertain"
    status = result.get("status")
    engine_receipt = (isinstance(result.get("job_id"), str) and bool(result["job_id"])
                      and isinstance(result.get("job_ids"), list) and bool(result["job_ids"])
                      and all(isinstance(job_id, str) and job_id for job_id in result["job_ids"]))
    workbench_receipt = (isinstance(result.get("workflow_dir"), str) and Path(result["workflow_dir"]).is_absolute()
                         and result.get("poll_with") == "gsas2_workflow_status")
    external_receipt = engine_receipt or workbench_receipt
    if result.get("requires_reconciliation") or result.get("remote_state") == "unknown":
        return "uncertain"
    if status == "halt":
        return "refused"
    if status == "invalid" or result.get("valid") is False or result.get("ok") is False:
        return "failed"
    if status == "cancelled" and result.get("execution_stopped") is True:
        return "cancelled"
    for key in ("_apexa_outcome", "outcome"):
        outcome = result.get(key)
        if isinstance(outcome, dict):
            execution = outcome.get("execution")
            if execution == "failed":
                return "failed"
            if execution == "denied":
                return "refused"
            if execution in ("unknown", "cancelled") or (execution in ("running", "pending") and not (status == "submitted" and external_receipt)):
                return "uncertain"
    if status in ("success", "ok", "completed", "complete", "valid", "ready") and result.get("success") is not False and not result.get("error") and not result.get("errors") and not result.get("failed"):
        return "completed"
    if status == "submitted" and engine_receipt and not result.get("error"):
        return "submitted"  # engine receipt is admission, not scientific success
    if status == "submitted" and workbench_receipt and not result.get("error"):
        return "submitted"
    if status == "cancelled" and result.get("execution_stopped") is True:
        return "cancelled"
    if result.get("nothing_was_run") is True:
        return "refused"
    if status in ("error", "failed", "partial"):
        return "failed"
    return "uncertain"
