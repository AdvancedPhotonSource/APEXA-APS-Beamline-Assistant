"""Durable graph/worker boundary faults; no scientific tools or models execute."""
import asyncio
from copy import deepcopy
import hashlib
import json
import subprocess
import sys
from types import ModuleType

import pytest

from apexa_dataset import canonical
from apexa_jobs import JobStore
from apexa_workflow_graph import WorkflowRuntime, WorkflowConflict, WorkflowUnavailable


@pytest.fixture
def plan(monkeypatch):
    # Recipe scientific validation has its own tests. This fixture gives the
    # orchestration boundary a canonical validated plan and a deterministic bind.
    recipes = ModuleType("apexa_workflow_recipes")
    def validate(value, schemas):
        result = deepcopy(value)
        digest = result.pop("plan_hash")
        if digest != hashlib.sha256(canonical(result).encode()).hexdigest():
            raise ValueError("plan hash changed")
        result["plan_hash"] = digest
        return result
    recipes.validate_plan = validate
    recipes.resolve_arguments = lambda step, results: deepcopy(step["arguments"])
    monkeypatch.setitem(sys.modules, "apexa_workflow_recipes", recipes)
    value = {"schema_version": 1, "recipe": "fixture", "recipe_version": "1",
             "dataset_id": "dataset-sha256-fixture", "steps": [
                 {"id": "first", "tool": "fixture_analysis", "arguments": {"value": 1}, "review_after": True},
                 {"id": "second", "tool": "fixture_analysis", "arguments": {"value": 2}, "review_after": True}]}
    value["plan_hash"] = hashlib.sha256(canonical(value).encode()).hexdigest()
    return value


def runtime(tmp_path, execute):
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    return WorkflowRuntime(execute, tool_schemas={"fixture_analysis": schema}, root=tmp_path / "workflows", job_store=JobStore(tmp_path / "jobs"))


async def approve(runner, snapshot, **kwargs):
    return await runner.advance(snapshot["workflow_id"], expected_revision=snapshot["revision"],
                                gate_id=snapshot["gate"]["gate_id"], decision="approve", **kwargs)


@pytest.mark.asyncio
async def test_graph_requires_typed_gates_and_preserves_provisional_science(tmp_path, plan):
    calls = []
    owner_loop = asyncio.get_running_loop()
    async def execute(tool, arguments):
        assert asyncio.get_running_loop() is owner_loop
        calls.append((tool, arguments))
        return {"status": "success", "scientific_verdict": "provisional"}
    runner = runtime(tmp_path, execute)
    try:
        initial = await runner.create(plan, "session-1")
        assert not calls and initial["durability"] == "sqlite"
        assert initial["gate"]["kind"] == "plan_review"
        assert initial["orchestration_state"] == "awaiting_review"
        with pytest.raises(ValueError):
            await runner.advance(initial["workflow_id"], expected_revision=initial["revision"], gate_id=initial["gate"]["gate_id"], decision="hello unrelated chat")
        result = await approve(runner, initial, request_id="start")
        assert len(calls) == 1 and result["gate"]["kind"] == "result_review"
        assert result["stages"][0]["state"] == "completed"
        assert result["scientific_verdict"] == "provisional"
        replay = await approve(runner, initial, request_id="start")
        assert replay["revision"] == result["revision"] and len(calls) == 1
        with pytest.raises(WorkflowConflict):
            await approve(runner, initial)
        result = await approve(runner, result)
        assert len(calls) == 2
        result = await approve(runner, result)
        assert result["orchestration_state"] == "completed"
        assert result["scientific_verdict"] == "provisional"
        assert result["job_execution"] == "completed"
        assert len(await runner.list(session_id="session-1")) == 1
        assert await runner.list(session_id="other-session") == []
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_restart_after_completed_job_before_graph_checkpoint_does_not_rerun(tmp_path, plan, monkeypatch):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
        return {"status": "success"}
    original = WorkflowRuntime._execute
    once = True
    async def crash_after_result(self, state):
        nonlocal once
        result = await original(self, state)
        if once:
            once = False
            raise RuntimeError("crash after durable job completion, before graph checkpoint")
        return result
    monkeypatch.setattr(WorkflowRuntime, "_execute", crash_after_result)
    first = runtime(tmp_path, execute)
    initial = await first.create(plan)
    with pytest.raises(RuntimeError, match="crash after durable"):
        await approve(first, initial)
    await first.aclose()
    second = runtime(tmp_path, execute)
    try:
        snapshot = await second.get(initial["workflow_id"])
        assert snapshot["stages"][0]["state"] == "completed"
        assert snapshot["gate"]["recovery"]
        recovered = await approve(second, snapshot)
        assert recovered["gate"]["kind"] == "result_review"
        assert not recovered["gate"]["recovery"]
        assert len(calls) == 1
    finally:
        await second.aclose()


@pytest.mark.asyncio
async def test_restart_after_admission_before_checkpoint_reuses_same_job(tmp_path, plan, monkeypatch):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
        return {"status": "success"}
    original = WorkflowRuntime._submit
    once = True
    async def crash_after_submit(self, state):
        nonlocal once
        result = await original(self, state)
        if once:
            once = False
            raise RuntimeError("admitted before checkpoint")
        return result
    monkeypatch.setattr(WorkflowRuntime, "_submit", crash_after_submit)
    first = runtime(tmp_path, execute)
    initial = await first.create(plan)
    with pytest.raises(RuntimeError, match="admitted"):
        await approve(first, initial)
    receipt = first.jobs.list()[0]
    assert receipt["state"] == "queued"
    await first.aclose()
    second = runtime(tmp_path, execute)
    try:
        pending = await second.get(initial["workflow_id"])
        completed = await approve(second, pending)
        assert completed["stages"][0]["run_id"] == receipt["id"]
        assert len(calls) == 1 and len(second.jobs.list()) == 1
    finally:
        await second.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt,state", [
    ({"status": "submitted", "job_id": "group", "job_ids": ["external"]}, "submitted"),
    ({"status": "error", "error": "known failure"}, "failed"),
    ({"status": "error", "remote_state": "unknown"}, "uncertain"),
    ({"status": "success", "scientific_verdict": "rejected"}, "completed"),
    ({"status": "invalid", "errors": []}, "failed"),
    ({"status": "halt"}, "refused"),
    ({"status": "success", "valid": False}, "failed"),
    ({"status": "success", "ok": False}, "failed"),
])
async def test_unresolved_or_rejected_receipts_never_advance(tmp_path, plan, receipt, state):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
        return receipt
    runner = runtime(tmp_path, execute)
    try:
        snapshot = await approve(runner, await runner.create(plan))
        assert snapshot["stages"][0]["state"] == state
        assert snapshot["gate"]["decisions"] == ["reject"]
        with pytest.raises(WorkflowConflict, match="does not authorize"):
            await approve(runner, snapshot)
        assert len(calls) == 1
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_verified_external_receipt_can_resume_without_resubmission(tmp_path, plan):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
        return {"status": "submitted", "job_id": "group", "job_ids": ["external"]}
    runner = runtime(tmp_path, execute)
    try:
        waiting = await approve(runner, await runner.create(plan))
        runner.jobs.resolve(waiting["stages"][0]["run_id"], state="completed", evidence="qualified engine receipt",
                            result={"status": "success", "scientific_verdict": "accepted"})
        refreshed = await runner.get(waiting["workflow_id"])
        assert "approve" in refreshed["gate"]["decisions"]
        await approve(runner, refreshed)
        assert len(calls) == 2  # only the next step is submitted
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_cancellation_stops_progress_and_preserves_unknown_effects(tmp_path, plan):
    entered = asyncio.Event()
    cleaned = asyncio.Event()
    async def execute(tool, arguments):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    runner = runtime(tmp_path, execute)
    try:
        initial = await runner.create(plan)
        advancing = asyncio.create_task(approve(runner, initial))
        await asyncio.wait_for(entered.wait(), 3)
        running = await runner.get(initial["workflow_id"])
        assert running["orchestration_state"] == "running"
        cancelled = await runner.cancel(initial["workflow_id"], expected_revision=running["revision"], request_id="stop")
        assert cancelled["orchestration_state"] == "cancel_requested"
        result = await asyncio.wait_for(advancing, 3)
        assert cleaned.is_set()
        assert result["orchestration_state"] == "cancel_requested"
        assert result["stages"][0]["state"] == "uncertain"
        assert result["stages"][1]["state"] == "pending"
        runner.jobs.resolve(result["stages"][0]["run_id"], state="cancelled", evidence="test executor stopped")
        assert (await runner.get(initial["workflow_id"]))["orchestration_state"] == "cancelled"
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_concurrent_advance_cannot_execute_same_gate_twice(tmp_path, plan):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
        await asyncio.sleep(0.05)
        return {"status": "success"}
    first, second = runtime(tmp_path, execute), runtime(tmp_path, execute)
    try:
        initial = await first.create(plan)
        results = await asyncio.gather(approve(first, initial), approve(second, initial), return_exceptions=True)
        assert sum(isinstance(result, WorkflowConflict) for result in results) == 1
        assert len(calls) == 1
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_checkpoint_unavailable_does_not_execute_or_downgrade(tmp_path, plan):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
    runner = runtime(tmp_path, execute)
    (runner.root / "checkpoints.sqlite3").mkdir()
    with pytest.raises(WorkflowUnavailable):
        await runner.create(plan)
    assert not calls and runner._app is None


@pytest.mark.asyncio
async def test_async_job_cancelled_after_possible_effect_is_not_retried(tmp_path):
    jobs = JobStore(tmp_path)
    run_id = jobs.submit({"operation": "fixture"})
    entered = asyncio.Event()
    stopped = asyncio.Event()
    async def execute(payload, context):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task = asyncio.create_task(jobs.arun_one(execute, run_id=run_id))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert jobs.get(run_id)["state"] == "uncertain"
    result = await jobs.arun_one(execute, run_id=run_id)
    assert result["state"] == "uncertain"


@pytest.mark.asyncio
async def test_independent_process_owner_is_not_stolen_on_lease_expiry(tmp_path, plan):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
        return {"status": "success"}
    runner = runtime(tmp_path, execute)
    initial = await runner.create(plan)
    script = '''import json,sys
from apexa_workflow_graph import WorkflowRuntime
from apexa_jobs import JobStore
async def execute(*args): raise AssertionError("no tool in ownership process")
r=WorkflowRuntime(execute,tool_schemas={},root=sys.argv[1],job_store=JobStore(sys.argv[2]))
token=r._claim(sys.argv[3],int(sys.argv[4]))
print(json.dumps({"claimed":bool(token)}),flush=True)
sys.stdin.read(1)
'''
    owner = subprocess.Popen([sys.executable, "-c", script, str(runner.root), str(runner.jobs.root),
                              initial["workflow_id"], str(initial["revision"])],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(await asyncio.to_thread(owner.stdout.readline))["claimed"]
        current = runner._row(initial["workflow_id"])
        with runner._connect() as db:
            db.execute("UPDATE workflows SET lease_until=0 WHERE id=?", (initial["workflow_id"],))
        with pytest.raises(WorkflowConflict, match="active operation"):
            runner._claim(initial["workflow_id"], current["revision"])
        assert (await runner.get(initial["workflow_id"]))["gate"] is None
        stdout, stderr = await asyncio.to_thread(owner.communicate, "x", timeout=5)
        assert owner.returncode == 0, stderr
        # The dead owner cannot write another checkpoint. The job ledger still
        # governs possible effects; this owner had not started any operation.
        snapshot = await runner.get(initial["workflow_id"])
        assert snapshot["gate"]["kind"] == "plan_review"
        await approve(runner, snapshot)
        assert len(calls) == 1
    finally:
        if owner.poll() is None:
            owner.kill()
            await asyncio.to_thread(owner.communicate, timeout=5)
        await runner.aclose()


@pytest.mark.asyncio
async def test_checkpoint_and_plan_index_mismatch_refuses_resume(tmp_path, plan):
    async def execute(*args):
        pytest.fail("mismatched checkpoint executed")
    runner = runtime(tmp_path, execute)
    try:
        snapshot = await runner.create(plan)
        wrong = deepcopy(plan)
        wrong["dataset_id"] = "different-data"
        with runner._connect() as db:
            db.execute("UPDATE workflows SET plan=? WHERE id=?", (canonical(wrong), snapshot["workflow_id"]))
        with pytest.raises(WorkflowUnavailable, match="disagree"):
            await runner.get(snapshot["workflow_id"])
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_admission_signal_follows_claim_and_stale_actions_never_signal(tmp_path, plan):
    order = []
    admitted, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(tool, arguments):
        order.append("execute")
        entered.set()
        await release.wait()
        return {"status": "success"}
    def notify():
        order.append("admitted")
        admitted.set()
    runner = runtime(tmp_path, execute)
    try:
        initial = await runner.create(plan)
        advancing = asyncio.create_task(approve(runner, initial, on_admitted=notify))
        await asyncio.wait_for(admitted.wait(), 3)
        running = await runner.get(initial["workflow_id"])
        assert running["revision"] > initial["revision"]
        assert running["orchestration_state"] == "running"
        wrong = asyncio.Event()
        with pytest.raises(WorkflowConflict):
            await approve(runner, initial, on_admitted=wrong.set)
        assert not wrong.is_set()
        await asyncio.wait_for(entered.wait(), 3)
        assert order == ["admitted", "execute"]
        release.set()
        await asyncio.wait_for(advancing, 3)
    finally:
        release.set()
        await runner.aclose()


@pytest.mark.asyncio
async def test_real_recipe_compiler_and_verified_artifact_binding(tmp_path):
    import apexa_workflow_recipes as recipes
    schemas = {
        "midas_auto_calibrate": {"type": "object", "properties": {"image_file": {"type": "string"}, "output_dir": {"type": "string"}}, "required": ["image_file"]},
        "midas_integrate_series": {"type": "object", "properties": {"parameter_file": {"type": "string"}, "images": {"type": "array", "items": {"type": "string"}}, "result_folder": {"type": "string"}}, "required": ["parameter_file"]},
    }
    plan = recipes.compile_plan("calibration_integration", {"stages": {
        "calibrate": {"image_file": "/data/calibration.h5", "output_dir": "/out/calibration"},
        "integrate": {"images": ["/data/sample.h5"], "result_folder": "/out/integrated"}},
        "bindings": {"integrate": {"parameter_file": {"step": "calibrate", "path": ["refined_param_file"]}}}},
        tool_schemas=schemas, dataset_id="declared-dataset-v1")
    calls = []
    async def execute(tool, arguments):
        calls.append((tool, arguments))
        if tool == "midas_auto_calibrate":
            return json.dumps({"status": "success", "refined_param_file": "/out/calibration/refined.txt", "gate_passed": True})
        assert arguments["parameter_file"] == "/out/calibration/refined.txt"
        return {"status": "success"}
    runner = WorkflowRuntime(execute, tool_schemas=schemas, root=tmp_path / "graphs", job_store=JobStore(tmp_path / "jobs"))
    try:
        snapshot = await runner.create(plan)
        snapshot = await approve(runner, snapshot)
        assert snapshot["stages"][0]["outcome"]["scientific_verdict"] == "accepted"
        snapshot = await approve(runner, snapshot)
        second = runner.jobs.get(snapshot["stages"][1]["run_id"])
        assert set(second["payload"]["upstream_receipts"]) == {"calibrate"}
        assert second["payload"]["dataset_id"] == "declared-dataset-v1"
        assert (await approve(runner, snapshot))["orchestration_state"] == "completed"
        assert len(calls) == 2
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_schema_refresh_after_admission_refuses_dispatch(tmp_path, monkeypatch):
    import apexa_workflow_recipes as recipes
    schemas = {
        "midas_auto_calibrate": {"type": "object", "properties": {"image_file": {"type": "string"}, "output_dir": {"type": "string"}}, "required": ["image_file"]},
        "midas_integrate_series": {"type": "object", "properties": {"parameter_file": {"type": "string"}, "images": {"type": "array", "items": {"type": "string"}}, "result_folder": {"type": "string"}}, "required": ["parameter_file"]},
    }
    plan = recipes.compile_plan("calibration_integration", {"stages": {
        "calibrate": {"image_file": "/data/calibration.h5", "output_dir": "/out/calibration"},
        "integrate": {"parameter_file": "/data/accepted.txt", "images": ["/data/sample.h5"], "result_folder": "/out/integrated"}}},
        tool_schemas=schemas, dataset_id="declared-dataset-v1")
    calls = []
    async def execute(tool, arguments):
        calls.append((tool, arguments))
        return {"status": "success"}
    original_submit = WorkflowRuntime._submit
    async def refresh_after_submission(self, state):
        result = await original_submit(self, state)
        refreshed = deepcopy(self.tool_schemas)
        # Arguments still validate; only the immutable schema fingerprint catches
        # a changed tool contract discovered after the plan was prepared.
        refreshed["midas_auto_calibrate"]["properties"]["image_file"]["description"] = "Changed input contract"
        self.tool_schemas = refreshed
        return result
    monkeypatch.setattr(WorkflowRuntime, "_submit", refresh_after_submission)
    runner = WorkflowRuntime(execute, tool_schemas=schemas, root=tmp_path / "graphs", job_store=JobStore(tmp_path / "jobs"))
    try:
        snapshot = await approve(runner, await runner.create(plan))
        assert not calls
        job = runner.jobs.get(snapshot["stages"][0]["run_id"])
        assert job["state"] == "refused"
        assert "schemas changed" in job["error"]
        assert snapshot["gate"]["decisions"] == ["reject"]
    finally:
        await runner.aclose()


def test_qualified_alignment_preflight_does_not_upgrade_scan_pending():
    from apexa_alignment_runtime import preflight
    from apexa_jobs import _result_state
    plan = {"task": "diode_max_1d_scan", "mode": "shadow", "source_id": "fixture",
            "instrument": {"beamline": "SIM", "motor": "SIM:m1", "monitor": "SIM:diode", "position_units": "mm", "signal_units": "A", "configuration_revision": "1", "configuration": {"energy_kev": 65}},
            "scan": {"bounds": [-5, 5], "device_limits": [-5, 5], "initial_position": -5, "max_step": 1, "max_total_travel": 10, "grid_points": 21},
            "budget": {"max_evaluations": 21, "max_seconds": 60},
            "observation": {"signal_bounds": [0, 200], "settle_seconds": 0.1, "max_age_seconds": 1}}
    receipt = preflight(plan)
    receipt["outcome"] = {"execution": "pending", "scientific_verdict": "unassessed"}
    assert _result_state(receipt) == "uncertain"
    verified = WorkflowRuntime._alignment_preflight_receipt({"plan": plan}, receipt)
    assert _result_state(verified) == "completed"
    assert verified["outcome"]["scientific_verdict"] == "unassessed"
    assert not verified["hardware_preflight_verified"] and not verified["motion_executed"]
    assert verified["source_receipt"]["outcome"]["execution"] == "pending"
    receipt["configuration_hash"] = "wrong"
    assert _result_state(WorkflowRuntime._alignment_preflight_receipt({"plan": plan}, receipt)) == "failed"
    assert _result_state({"status": "waiting_observation", "run_id": "external", "outcome": {"execution": "pending"}}) == "uncertain"


@pytest.mark.asyncio
async def test_expired_unstarted_claim_can_be_explicitly_resumed(tmp_path, plan, monkeypatch):
    calls = []
    async def execute(tool, arguments):
        calls.append(arguments)
        return {"status": "success"}
    original = WorkflowRuntime._execute
    first = True
    async def abandoned_claim(self, state):
        nonlocal first
        if first:
            first = False
            run_id = state["jobs"]["first"]
            assert self.jobs.claim(owner="lost-before-start", run_id=run_id, now=100, lease_seconds=1)
            return {"phase": "executed"}
        return await original(self, state)
    monkeypatch.setattr(WorkflowRuntime, "_execute", abandoned_claim)
    runner = runtime(tmp_path, execute)
    try:
        queued = await approve(runner, await runner.create(plan))
        assert queued["stages"][0]["state"] == "queued" and not calls
        completed = await approve(runner, queued)
        assert completed["stages"][0]["state"] == "completed"
        assert len(calls) == 1
    finally:
        await runner.aclose()
