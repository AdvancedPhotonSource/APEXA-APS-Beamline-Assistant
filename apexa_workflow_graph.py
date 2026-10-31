"""Reviewed scientific plans executed by LangGraph and the durable job ledger.

Graph checkpoints own progression; JobStore owns possible external effects.
Replay can repeat admission/observation, never an already-started operation.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import TypedDict
import uuid

from apexa_dataset import canonical
from apexa_jobs import default_job_store
from apexa_observability import current_ids
from apexa_outcomes import classify_result
from apexa_resource_leases import process_identity, _alive


class WorkflowConflict(ValueError):
    pass


class WorkflowNotFound(KeyError):
    pass


class WorkflowUnavailable(RuntimeError):
    pass


class WorkflowState(TypedDict, total=False):
    workflow_id: str
    plan: dict
    step_index: int
    jobs: dict
    pending: dict
    phase: str


class WorkflowRuntime:
    def __init__(self, execute_tool_fn, *, tool_schemas, root=None, job_store=None):
        self.execute_tool = execute_tool_fn
        self.tool_schemas = deepcopy(tool_schemas)
        self.root = Path(root or os.environ.get("APEXA_WORKFLOW_DIR", "~/.apexa/workflows")).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / "workflows.sqlite3"
        self.jobs = job_store or default_job_store()
        self._app = None
        self._conn = None
        self._init_lock = asyncio.Lock()
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, plan TEXT NOT NULL,
                    revision INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    owner TEXT, token TEXT, lease_until REAL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE INDEX IF NOT EXISTS workflow_session ON workflows(session_id,created);
                CREATE TABLE IF NOT EXISTS workflow_actions (
                    workflow_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(workflow_id,request_id));
            """)
        self.db.chmod(0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    async def _ensure(self):
        if self._app is not None:
            return
        async with self._init_lock:
            if self._app is not None:
                return
            try:
                import aiosqlite
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                self._conn = await aiosqlite.connect(str(self.root / "checkpoints.sqlite3"))
                await self._conn.execute("PRAGMA synchronous=FULL")
                saver = AsyncSqliteSaver(self._conn)
                await saver.setup()
                self._app = self._build().compile(checkpointer=saver)
                if "durability" not in inspect.signature(self._app.ainvoke).parameters:
                    raise WorkflowUnavailable("installed LangGraph does not support synchronous checkpoint durability")
            except Exception as exc:
                self._app = None
                if self._conn is not None:
                    await self._conn.close()
                    self._conn = None
                raise WorkflowUnavailable("Durable workflow checkpoints unavailable; no scientific operation was started") from exc

    async def aclose(self):
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._app = None

    @staticmethod
    def _cfg(workflow_id):
        return {"configurable": {"thread_id": "workflow:" + workflow_id}}

    @staticmethod
    def _job_key(workflow_id, plan, step):
        return "workflow:" + workflow_id + ":" + plan["plan_hash"] + ":" + step["id"]

    def _row(self, workflow_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if row is None:
            raise WorkflowNotFound(workflow_id)
        return {**dict(row), "plan": json.loads(row["plan"]),
                "owner": json.loads(row["owner"]) if row["owner"] else None}

    def _claim(self, workflow_id, expected_revision, action=None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            if row is None:
                raise WorkflowNotFound(workflow_id)
            if action and action.get("request_id"):
                existing = db.execute("SELECT payload FROM workflow_actions WHERE workflow_id=? AND request_id=?", (workflow_id, action["request_id"])).fetchone()
                if existing:
                    if existing[0] != canonical(action):
                        raise WorkflowConflict("request_id already belongs to a different workflow action")
                    return None
            if row["revision"] != expected_revision:
                raise WorkflowConflict("stale workflow revision; reload the current snapshot")
            if row["cancel_requested"]:
                raise WorkflowConflict("workflow cancellation has been requested")
            if row["token"] and _alive(json.loads(row["owner"])) is not False:
                # Expired time alone cannot fence an alive graph writer or its
                # external effects. A second process must not steal its graph.
                raise WorkflowConflict("workflow is owned by another active operation")
            token = uuid.uuid4().hex
            db.execute("UPDATE workflows SET owner=?,token=?,lease_until=?,revision=revision+1,updated=?,error=NULL WHERE id=?",
                       (canonical(process_identity()), token, time.time() + 60, time.time(), workflow_id))
            if action:
                request_id = action.get("request_id") or uuid.uuid4().hex
                db.execute("INSERT INTO workflow_actions VALUES(?,?,?,?)", (workflow_id, request_id, canonical(action), time.time()))
            return token

    def _renew(self, workflow_id, token):
        with self._connect() as db:
            return bool(db.execute("UPDATE workflows SET lease_until=? WHERE id=? AND token=?", (time.time() + 60, workflow_id, token)).rowcount)

    def _release(self, workflow_id, token, error):
        with self._connect() as db:
            return bool(db.execute("UPDATE workflows SET owner=NULL,token=NULL,lease_until=NULL,error=?,revision=revision+1,updated=? WHERE id=? AND token=?",
                                   (error, time.time(), workflow_id, token)).rowcount)

    async def _invoke(self, workflow_id, token, input_value):
        async def renew():
            while True:
                await asyncio.sleep(10)
                if not await asyncio.to_thread(self._renew, workflow_id, token):
                    raise WorkflowConflict("workflow ownership was lost")
        heartbeat = asyncio.create_task(renew())
        error = None
        try:
            # Async durability would allow a scientific node to run before its
            # prepared payload reached disk. Use an explicit synchronous barrier.
            await self._app.ainvoke(input_value, self._cfg(workflow_id), durability="sync")
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"[:2000]
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            release = asyncio.create_task(asyncio.to_thread(self._release, workflow_id, token, error))
            while not release.done():
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    continue
            release.result()

    async def create(self, plan, session_id=""):
        from apexa_workflow_recipes import validate_plan
        if not isinstance(session_id, str) or len(session_id) > 256:
            raise ValueError("session_id must be a string up to 256 characters")
        validated = await asyncio.to_thread(validate_plan, deepcopy(plan), self.tool_schemas)
        await self._ensure()
        workflow_id = "wf-" + uuid.uuid4().hex
        def insert():
            with self._connect() as db:
                now = time.time()
                db.execute("INSERT INTO workflows(id,session_id,plan,revision,created,updated) VALUES(?,?,?,0,?,?)",
                           (workflow_id, session_id, canonical(validated), now, now))
        await asyncio.to_thread(insert)
        token = await asyncio.to_thread(self._claim, workflow_id, 0)
        await self._invoke(workflow_id, token, {"workflow_id": workflow_id, "plan": validated,
                           "step_index": 0, "jobs": {}, "phase": "plan_review"})
        return await self.get(workflow_id)

    async def list(self, session_id=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be 1..500")
        def ids():
            with self._connect() as db:
                query = "SELECT id FROM workflows"
                params = []
                if session_id is not None:
                    query += " WHERE session_id=?"
                    params.append(session_id)
                return [row[0] for row in db.execute(query + " ORDER BY created DESC LIMIT ?", (*params, limit))]
        return [await self.get(workflow_id) for workflow_id in await asyncio.to_thread(ids)]

    async def owner_for_tool(self, session_id, tool):
        """Check every persisted plan, independently of UI history pagination."""
        def candidates():
            with self._connect() as db:
                return [row['id'] for row in db.execute(
                    'SELECT id,plan FROM workflows WHERE session_id=? ORDER BY created DESC', (session_id,))
                    if any(step['tool'] == tool for step in json.loads(row['plan'])['steps'])]
        for workflow_id in await asyncio.to_thread(candidates):
            snapshot = await self.get(workflow_id)
            if snapshot['orchestration_state'] not in ('completed', 'rejected', 'cancelled'):
                return workflow_id
        return None

    async def _stage_rows(self, workflow_id, plan):
        def load():
            # Status recovery changes only ledger state, never external effects:
            # expired running work becomes uncertain; unstarted claims queue.
            self.jobs.reconcile()
            rows = []
            for step in plan["steps"]:
                try:
                    row = self.jobs.get_by_key(self._job_key(workflow_id, plan, step))
                except KeyError:
                    row = None
                if row is not None and (row["payload"].get("workflow_id") != workflow_id
                        or row["payload"].get("plan_hash") != plan["plan_hash"]
                        or row["payload"].get("step_id") != step["id"]):
                    raise WorkflowConflict("job receipt does not belong to this workflow step")
                rows.append(row)
            return rows
        return await asyncio.to_thread(load)

    @staticmethod
    def _outcome(row):
        if row is None:
            return None
        outcome = classify_result(row["result"]).model_dump() if row["result"] is not None else {
            "execution": "unknown", "scientific_verdict": "unassessed", "error": row["error"]}
        outcome["execution"] = {"queued": "pending", "claimed": "pending", "running": "running",
            "submitted": "pending", "uncertain": "unknown", "failed": "failed",
            "refused": "denied", "cancelled": "cancelled"}.get(row["state"], outcome["execution"])
        return outcome

    async def get(self, workflow_id):
        await self._ensure()
        row = await asyncio.to_thread(self._row, workflow_id)
        checkpoint = await self._app.aget_state(self._cfg(workflow_id))
        values = checkpoint.values or {}
        plan = row["plan"]
        if values and (values.get("workflow_id") != workflow_id or canonical(values.get("plan")) != canonical(plan)):
            raise WorkflowUnavailable("workflow checkpoint and immutable plan index disagree; restore matching state before resuming")
        jobs = await self._stage_rows(workflow_id, plan)
        stages = [{"id": step["id"], "tool": step["tool"], "state": job["state"] if job else "pending",
                   "run_id": job["id"] if job else None, "outcome": self._outcome(job),
                   "cancellation_status": job["cancellation_status"] if job else None}
                  for step, job in zip(plan["steps"], jobs)]
        index = min(values.get("step_index", 0), len(stages) - 1)
        current = stages[index]
        phase = values.get("phase", "missing_checkpoint")
        busy = bool(row["token"] and await asyncio.to_thread(_alive, row["owner"]) is not False)
        interrupts = bool(getattr(checkpoint, "interrupts", ()) or any(getattr(task, "interrupts", ()) for task in checkpoint.tasks))
        unresolved = any(stage["state"] in ("claimed", "running", "submitted", "uncertain") for stage in stages)
        gate = None
        if row["cancel_requested"]:
            state = "cancel_requested" if unresolved else "cancelled"
        elif busy:
            state = "running"
        elif phase in ("completed", "rejected", "cancelled") and not checkpoint.next:
            state = "blocked" if unresolved else phase
        elif not values:
            state = "blocked"
        else:
            kind = "plan_review" if "review_plan" in checkpoint.next else "result_review"
            recovery = not interrupts
            can_approve = kind == "plan_review" or (current["state"] == "completed"
                and current["outcome"]["execution"] == "completed"
                and current["outcome"]["scientific_verdict"] != "rejected")
            if current["state"] == "queued":
                can_approve = True  # lease recovery proved execution had not started
            if recovery and current["state"] in ("pending", "queued"):
                can_approve = True
            state = ("awaiting_review" if can_approve else "waiting_job" if current["state"] in ("claimed", "running", "submitted") else "blocked")
            checkpoint_id = checkpoint.config.get("configurable", {}).get("checkpoint_id", "") if recovery else ""
            gate_id = hashlib.sha256(f"{workflow_id}:{kind}:{index}:{checkpoint_id}".encode()).hexdigest()
            gate = {"gate_id": gate_id, "kind": kind, "step_id": None if kind == "plan_review" else current["id"],
                    "decisions": ["approve", "reject"] if can_approve else ["reject"], "recovery": recovery,
                    "prompt": ("Review the immutable analysis plan before executing its first stage." if kind == "plan_review" else
                               "This job has not started. Approve to execute its persisted, reviewed inputs." if current["state"] == "queued" else
                               "Resume from recorded job receipts; an already-started operation will not be repeated." if recovery else
                               "Review this stage's job receipt. Approval continues the plan and does not change its scientific verdict.")}
        outcomes = [stage["outcome"] for stage in stages if stage["outcome"]]
        verdicts = [outcome["scientific_verdict"] for outcome in outcomes]
        science = "rejected" if "rejected" in verdicts else "provisional" if "provisional" in verdicts else (
            "accepted" if len(outcomes) == len(stages) and all(verdict == "accepted" for verdict in verdicts) else "unassessed")
        return {"workflow_id": workflow_id, "session_id": row["session_id"], "revision": row["revision"],
                "orchestration_state": state, "current_step": None if state in ("completed", "rejected", "cancelled") else current["id"],
                "plan": plan, "durability": "sqlite", "gate": gate, "stages": stages,
                "job_execution": current["outcome"]["execution"] if current["outcome"] else "unknown",
                "scientific_verdict": science, "error": row["error"] or ("workflow checkpoint is missing" if not values else None)}

    async def advance(self, workflow_id, *, expected_revision, gate_id, decision, request_id=None, reason=None, on_admitted=None):
        from apexa_workflow_recipes import validate_plan
        if type(expected_revision) is not int or not isinstance(gate_id, str) or decision not in ("approve", "reject"):
            raise ValueError("advance requires a revision, gate_id and typed approve/reject decision")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ValueError("reason must be a string up to 2000 characters")
        action = {"kind": "advance", "expected_revision": expected_revision, "gate_id": gate_id,
                  "decision": decision, "request_id": request_id, "reason": reason}
        if await asyncio.to_thread(self._replayed, workflow_id, action):
            return await self.get(workflow_id)
        snapshot = await self.get(workflow_id)
        gate = snapshot["gate"]
        if snapshot["revision"] != expected_revision or not gate or gate["gate_id"] != gate_id:
            raise WorkflowConflict("stale or unavailable workflow gate; reload the current snapshot")
        if decision not in gate["decisions"]:
            raise WorkflowConflict("job execution/scientific evidence does not authorize advancing this stage")
        await asyncio.to_thread(validate_plan, snapshot["plan"], self.tool_schemas)
        token = await asyncio.to_thread(self._claim, workflow_id, expected_revision, action)
        if token is None:
            return await self.get(workflow_id)
        if on_admitted is not None:
            try:
                notified = on_admitted()
                if inspect.isawaitable(notified):
                    await notified
            except BaseException as exc:
                await asyncio.shield(asyncio.to_thread(self._release, workflow_id, token, f"admission notification failed: {type(exc).__name__}"))
                raise
        if gate["recovery"] and decision == "reject":
            await asyncio.to_thread(self._release, workflow_id, token, "recovery declined; request cancellation to stop outstanding work")
            return await self.cancel(workflow_id, expected_revision=(await self.get(workflow_id))["revision"])
        from langgraph.types import Command
        await self._invoke(workflow_id, token, None if gate["recovery"] else Command(resume=decision))
        return await self.get(workflow_id)

    def _replayed(self, workflow_id, action):
        request_id = action.get("request_id")
        if request_id is None:
            return False
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
            raise ValueError("request_id must be a nonempty string up to 256 characters")
        with self._connect() as db:
            row = db.execute("SELECT payload FROM workflow_actions WHERE workflow_id=? AND request_id=?", (workflow_id, request_id)).fetchone()
        if row and row[0] != canonical(action):
            raise WorkflowConflict("request_id already belongs to a different workflow action")
        return bool(row)

    async def cancel(self, workflow_id, *, expected_revision, request_id=None, reason=None):
        if type(expected_revision) is not int:
            raise ValueError("expected_revision must be an integer")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ValueError("reason must be a string up to 2000 characters")
        action = {"kind": "cancel", "expected_revision": expected_revision, "request_id": request_id, "reason": reason}
        if await asyncio.to_thread(self._replayed, workflow_id, action):
            return await self.get(workflow_id)
        snapshot = await self.get(workflow_id)
        if snapshot["revision"] != expected_revision:
            raise WorkflowConflict("stale workflow revision")
        if snapshot["orchestration_state"] in ("completed", "rejected", "cancelled"):
            return snapshot
        def request():
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if request_id:
                    previous = db.execute("SELECT payload FROM workflow_actions WHERE workflow_id=? AND request_id=?", (workflow_id, request_id)).fetchone()
                    if previous:
                        if previous[0] != canonical(action):
                            raise WorkflowConflict("request_id already belongs to a different workflow action")
                        return
                row = db.execute("SELECT revision FROM workflows WHERE id=?", (workflow_id,)).fetchone()
                if row is None:
                    raise WorkflowNotFound(workflow_id)
                if row[0] != expected_revision:
                    raise WorkflowConflict("stale workflow revision")
                db.execute("UPDATE workflows SET cancel_requested=1,revision=revision+1,updated=? WHERE id=?", (time.time(), workflow_id))
                db.execute("INSERT INTO workflow_actions VALUES(?,?,?,?)", (workflow_id, request_id or uuid.uuid4().hex, canonical(action), time.time()))
        await asyncio.to_thread(request)
        row = await asyncio.to_thread(self._row, workflow_id)
        for job in await self._stage_rows(workflow_id, row["plan"]):
            if job:
                await asyncio.to_thread(self.jobs.cancel, job["id"])
        return await self.get(workflow_id)

    async def _cancelled(self, workflow_id):
        return bool((await asyncio.to_thread(self._row, workflow_id))["cancel_requested"])

    def _validate_arguments(self, tool, arguments):
        from jsonschema import Draft202012Validator
        if tool not in self.tool_schemas:
            raise ValueError(f"workflow tool is not connected: {tool}")
        schema = self.tool_schemas[tool]
        schema = schema.get("inputSchema", schema.get("parameters", schema))
        if not isinstance(arguments, dict) or set(arguments) - set(schema.get("properties", {})):
            raise ValueError("resolved workflow arguments include unknown fields")
        Draft202012Validator(schema).validate(arguments)

    @staticmethod
    def _alignment_preflight_receipt(arguments, result):
        """Qualify only the completed software-preflight call, never a scan job."""
        from apexa_alignment_runtime import preflight
        receipt = json.loads(result) if isinstance(result, str) else deepcopy(result)
        if not isinstance(receipt, dict) or receipt.get("status") != "ready":
            return receipt
        expected = preflight(arguments["plan"])
        source_outcome = classify_result(receipt)
        if (any(canonical(receipt.get(key)) != canonical(expected[key]) for key in expected) or receipt.get("error")
                or receipt.get("errors") or receipt.get("success") is False
                or receipt.get("hardware_alignment_verified") is True
                or source_outcome.execution not in ("completed", "pending")
                or source_outcome.scientific_verdict == "rejected"):
            return {"status": "error", "error": "alignment software-preflight receipt does not match the approved configuration",
                    "source_receipt": receipt}
        outcome = {"schema_version": 1, "execution": "completed", "scientific_verdict": "unassessed"}
        return {**receipt, "status": "success", "preflight_status": "ready",
                "receipt_adapter": "alignment_software_preflight_v1", "source_receipt": receipt,
                "outcome": outcome, "_apexa_outcome": outcome}

    def _build(self):
        from langgraph.graph import StateGraph, START, END
        graph = StateGraph(WorkflowState)
        for name, fn in (("review_plan", self._review_plan), ("prepare", self._prepare),
                         ("submit", self._submit), ("execute", self._execute), ("review_result", self._review_result)):
            graph.add_node(name, fn)
        graph.add_edge(START, "review_plan")
        graph.add_conditional_edges("review_plan", lambda state: END if state["phase"] == "rejected" else "prepare")
        graph.add_conditional_edges("prepare", lambda state: END if state["phase"] == "cancelled" else "submit")
        graph.add_conditional_edges("submit", lambda state: END if state["phase"] == "cancelled" else "execute")
        graph.add_edge("execute", "review_result")
        graph.add_conditional_edges("review_result", lambda state: "prepare" if state["phase"] == "ready" else "execute" if state["phase"] == "admitted" else END)
        return graph

    async def _review_plan(self, state):
        from langgraph.types import interrupt
        decision = interrupt({"kind": "plan_review", "workflow_id": state["workflow_id"]})
        if decision not in ("approve", "reject"):
            raise WorkflowConflict("invalid plan decision")
        return {"phase": "ready" if decision == "approve" else "rejected"}

    async def _prepare(self, state):
        from apexa_workflow_recipes import resolve_arguments, validate_plan
        if await self._cancelled(state["workflow_id"]):
            return {"phase": "cancelled"}
        plan = await asyncio.to_thread(validate_plan, state["plan"], self.tool_schemas)
        step = plan["steps"][state["step_index"]]
        rows = await self._stage_rows(state["workflow_id"], plan)
        results = {s["id"]: row["result"] for s, row in zip(plan["steps"], rows) if row and row["state"] == "completed"}
        arguments = await asyncio.to_thread(resolve_arguments, step, results)
        await asyncio.to_thread(self._validate_arguments, step["tool"], arguments)
        receipts = {s["id"]: hashlib.sha256(canonical(row["result"]).encode()).hexdigest()
                    for s, row in zip(plan["steps"], rows) if row and row["state"] == "completed"}
        return {"phase": "prepared", "pending": {"kind": "workflow", "workflow_id": state["workflow_id"],
                "plan_hash": plan["plan_hash"], "recipe": plan["recipe"], "recipe_version": plan["recipe_version"],
                "dataset_id": plan["dataset_id"], "step_id": step["id"], "tool": step["tool"],
                "arguments": arguments, "upstream_receipts": receipts}}

    async def _submit(self, state):
        if await self._cancelled(state["workflow_id"]):
            return {"phase": "cancelled"}
        step = state["plan"]["steps"][state["step_index"]]
        run_id = await asyncio.to_thread(self.jobs.submit, state["pending"],
            idempotency_key=self._job_key(state["workflow_id"], state["plan"], step), trace_id=current_ids().get("trace_id"))
        # Cancellation may have raced the atomic job admission; observe it again
        # before the execute node gets permission to claim the queued job.
        if await self._cancelled(state["workflow_id"]):
            await asyncio.to_thread(self.jobs.cancel, run_id)
        return {"jobs": {**state.get("jobs", {}), step["id"]: run_id}, "phase": "admitted"}

    async def _execute(self, state):
        from apexa_workflow_recipes import validate_plan
        step = state["plan"]["steps"][state["step_index"]]
        run_id = state["jobs"][step["id"]]
        if await self._cancelled(state["workflow_id"]):
            await asyncio.to_thread(self.jobs.cancel, run_id)
        async def preflight(payload):
            if await self._cancelled(state["workflow_id"]):
                await asyncio.to_thread(self.jobs.cancel, run_id)
                raise ValueError("workflow cancellation requested before execution")
            # Discovery can refresh schemas after preparation/admission. An
            # argument-compatible change still invalidates the reviewed plan.
            await asyncio.to_thread(validate_plan, state["plan"], self.tool_schemas)
            await asyncio.to_thread(self._validate_arguments, payload["tool"], payload["arguments"])
        async def execute(payload, context):
            result = await self.execute_tool(payload["tool"], deepcopy(payload["arguments"]))
            if payload["tool"] == "preflight_alignment":
                return await asyncio.to_thread(self._alignment_preflight_receipt, payload["arguments"], result)
            return result
        await self.jobs.arun_one(execute, run_id=run_id, prepare=preflight)
        return {"phase": "executed"}

    async def _review_result(self, state):
        from langgraph.types import interrupt
        if await self._cancelled(state["workflow_id"]):
            return {"phase": "cancelled"}
        step = state["plan"]["steps"][state["step_index"]]
        decision = interrupt({"kind": "result_review", "step_id": step["id"], "run_id": state["jobs"][step["id"]]})
        if decision == "reject":
            return {"phase": "rejected"}
        row = await asyncio.to_thread(self.jobs.get, state["jobs"][step["id"]])
        outcome = self._outcome(row)
        if decision == "approve" and row["state"] == "queued":
            return {"phase": "admitted"}
        if decision != "approve" or row["state"] != "completed" or outcome["execution"] != "completed" or outcome["scientific_verdict"] == "rejected":
            raise WorkflowConflict("result is not eligible to advance; no next operation was started")
        following = state["step_index"] + 1
        return {"step_index": following, "phase": "completed" if following == len(state["plan"]["steps"]) else "ready"}
