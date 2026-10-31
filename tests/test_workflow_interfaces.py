import asyncio
import json
from types import MethodType, SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from apexa_workflow_api import workflow_router, close_workflow_operations
from apexa_workflow_client import cli_command, handle_tool, runtime, pending_workflow_denial, WORKFLOW_TOOLS


@pytest_asyncio.fixture
async def workflow_client(monkeypatch, tmp_path):
    import midas_comprehensive_server as midas
    monkeypatch.setenv('APEXA_WORKFLOW_DIR', str(tmp_path / 'workflows'))
    monkeypatch.setenv('APEXA_RUN_DIR', str(tmp_path / 'runs'))
    monkeypatch.setenv('APEXA_TRACE_DB', str(tmp_path / 'traces.db'))
    schemas = await midas.mcp.list_tools()
    tools = [{'type': 'function', 'function': {'name': tool.name,
             'parameters': tool.input_schema, 'description': tool.description}} for tool in schemas]
    gate = asyncio.Event()
    calls = []
    client = SimpleNamespace(_available_tools=tools + WORKFLOW_TOOLS,
        _web_thread_lock=asyncio.Lock(), _active_web_thread='sess-workflow',
        context=SimpleNamespace(active_session='sess-workflow'))
    async def activate(session):
        client._active_web_thread = session
    async def execute(tool, arguments):
        assert await pending_workflow_denial(client, tool) is None
        calls.append((tool, arguments))
        await gate.wait()
        return json.dumps({'status': 'success', 'scientific_verdict': 'provisional'})
    client.activate_web_thread = activate
    client.execute_tool_call = execute
    app = FastAPI()
    app.include_router(workflow_router(lambda: client))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as http:
        yield client, http, calls, gate
    gate.set()
    await close_workflow_operations()
    await runtime(client).aclose()


def prepare_body():
    return {'session_id': 'sess-workflow', 'recipe': 'calibration_integration',
        'dataset_id': 'explicit-synthetic-snapshot-v1',
        'parameters': {'stages': {
            'calibrate': {'image_file': '/synthetic/cal.tif', 'output_dir': '/synthetic/out'},
            'integrate': {'parameter_file': '/synthetic/accepted.txt', 'images': ['/synthetic/sample.tif'],
                          'result_folder': '/synthetic/integrated'}}}}


@pytest.mark.asyncio
async def test_prepare_does_not_execute_and_model_cannot_bypass_gate(workflow_client):
    client, http, calls, _ = workflow_client
    body = prepare_body()
    response = await http.post('/api/workflows', json=body)
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot['orchestration_state'] == 'awaiting_review' and not calls
    assert await pending_workflow_denial(client, 'midas_auto_calibrate')
    from argo_mcp_client import APEXAClient
    denied = json.loads(await APEXAClient.execute_tool_call(client, 'midas_auto_calibrate', body['parameters']['stages']['calibrate']))
    assert denied['nothing_was_run'] and not calls
    read = json.loads(await handle_tool(client, 'get_scientific_workflow', {'workflow_id': snapshot['workflow_id']}))
    assert read['_apexa_outcome']['execution'] == 'pending'
    assert (await http.get('/api/workflows', params={'session_id': 'sess-other'})).json()['workflows'] == []


@pytest.mark.asyncio
async def test_typed_http_decision_admits_in_background_and_stale_gate_refuses(workflow_client):
    client, http, calls, gate = workflow_client
    first = (await http.post('/api/workflows', json=prepare_body())).json()
    decision = {'expected_revision': first['revision'], 'gate_id': first['gate']['gate_id'],
                'decision': 'approve', 'request_id': 'review-1', 'reason': 'synthetic validation'}
    response = await http.post(f"/api/workflows/{first['workflow_id']}/advance", json=decision)
    assert response.status_code == 202, response.text
    assert response.json()['revision'] > first['revision']
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(.01)
    assert len(calls) == 1
    again = await http.post(f"/api/workflows/{first['workflow_id']}/advance", json=decision)
    assert again.status_code == 202 and len(calls) == 1
    invalid = await http.post(f"/api/workflows/{first['workflow_id']}/advance", json={**decision, 'request_id': 'stale-other'})
    assert invalid.status_code == 409
    gate.set()
    for _ in range(100):
        current = (await http.get(f"/api/workflows/{first['workflow_id']}")).json()
        if current['orchestration_state'] == 'awaiting_review':
            break
        await asyncio.sleep(.01)
    assert current['gate']['kind'] == 'result_review'
    assert current['scientific_verdict'] == 'provisional'
    assert current['stages'][0]['run_id']
    assert len(calls) == 1  # result review must precede integration


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_before_admission', [False, True])
async def test_duplicate_queued_decision_waits_for_durable_admission(workflow_client, monkeypatch, cancel_before_admission):
    client, http, calls, _ = workflow_client
    first = (await http.post('/api/workflows', json=prepare_body())).json()
    decision = {'expected_revision': first['revision'], 'gate_id': first['gate']['gate_id'],
                'decision': 'approve', 'request_id': 'queued-review'}
    manager = runtime(client)
    original_get = manager.get
    reads = 0
    both_read = asyncio.Event()

    async def observed_get(workflow_id):
        nonlocal reads
        result = await original_get(workflow_id)
        reads += 1
        if reads >= 2:
            both_read.set()
        return result

    monkeypatch.setattr(manager, 'get', observed_get)
    await client._web_thread_lock.acquire()
    tasks = []
    try:
        path = f"/api/workflows/{first['workflow_id']}/advance"
        tasks = [asyncio.create_task(http.post(path, json=decision)) for _ in range(2)]
        await asyncio.wait_for(both_read.wait(), 2)
        await asyncio.sleep(.01)
        assert not any(task.done() for task in tasks)
        assert not calls
        if cancel_before_admission:
            response = await http.post(f"/api/workflows/{first['workflow_id']}/cancel", json={
                'expected_revision': first['revision'], 'request_id': 'cancel-queued-review'})
            assert response.status_code == 200
            assert response.json()['orchestration_state'] == 'cancelled'
    finally:
        client._web_thread_lock.release()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert [response.status_code for response in responses] == ([409, 409] if cancel_before_admission else [202, 202])
    if cancel_before_admission:
        assert not calls
    else:
        assert all(response.json()['revision'] > first['revision'] for response in responses)


@pytest.mark.asyncio
async def test_reviewed_http_dispatch_does_not_modify_stale_chat_metadata(workflow_client, tmp_path, monkeypatch):
    from argo_mcp_client import APEXAClient, ExperimentContext
    client, http, _, _ = workflow_client
    monkeypatch.delenv('APEXA_BEAMLINE', raising=False)
    context = ExperimentContext(tmp_path / 'sessions')
    context.start_new('stale-chat')
    client.context = context
    wire_calls = []
    async def wire_call(tool, arguments):
        wire_calls.append((tool, arguments))
        return SimpleNamespace(content=[{'type': 'text', 'text': json.dumps({'status': 'success'})}])
    client._tool_registry = {'midas_auto_calibrate': 'fixture'}
    client.sessions = {'fixture': SimpleNamespace(call_tool=wire_call)}
    client.execute_tool_call = MethodType(APEXAClient.execute_tool_call, client)

    # An ordinary direct call still records analysis in its active chat context.
    await client.execute_tool_call('midas_auto_calibrate', {'image_file': '/synthetic/cal.tif'})
    assert len(context.metadata['analysis_history']) == 1
    previous_history = list(context.metadata['analysis_history'])
    first = (await http.post('/api/workflows', json=prepare_body())).json()
    response = await http.post(f"/api/workflows/{first['workflow_id']}/advance", json={
        'expected_revision': first['revision'], 'gate_id': first['gate']['gate_id'],
        'decision': 'approve', 'request_id': 'metadata-review'})
    assert response.status_code in (200, 202), response.text
    for _ in range(100):
        current = await runtime(client).get(first['workflow_id'])
        if current['gate'] and current['gate']['kind'] == 'result_review':
            break
        await asyncio.sleep(.01)
    assert current['stages'][0]['state'] == 'completed'
    assert len(wire_calls) == 2
    assert context.active_session == 'stale-chat'
    assert context.metadata['analysis_history'] == previous_history
    receipt = runtime(client).jobs.get(current['stages'][0]['run_id'])
    assert receipt['result']['status'] == 'success'


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['show', 'approve', 'reject', 'cancel'])
async def test_cli_id_commands_require_the_owning_session(workflow_client, action):
    client, http, calls, gate = workflow_client
    first = (await http.post('/api/workflows', json=prepare_body())).json()
    command = f"workflow {action} {first['workflow_id']}"
    if action in ('approve', 'reject'):
        command += f" {first['gate']['gate_id']} {first['revision']} 'synthetic review'"
    elif action == 'cancel':
        command += f" {first['revision']}"
    client._active_web_thread = None
    client.context.active_session = 'sess-other'
    with pytest.raises(ValueError, match='another session'):
        await cli_command(client, command)
    unchanged = await runtime(client).get(first['workflow_id'])
    assert unchanged['revision'] == first['revision'] and not calls

    client.context.active_session = first['session_id']
    gate.set()
    result = json.loads(await cli_command(client, command))
    assert result['workflow_id'] == first['workflow_id']
    assert len(calls) == (1 if action == 'approve' else 0)
    if action in ('reject', 'cancel'):
        assert result['orchestration_state'] == {'reject': 'rejected', 'cancel': 'cancelled'}[action]


@pytest.mark.asyncio
async def test_ordinary_chat_never_enters_legacy_ff_graph(monkeypatch):
    from apexa_agents import OrchestratorAgent
    orchestrator = OrchestratorAgent.__new__(OrchestratorAgent)
    orchestrator._workflow_mode = 'graph'
    orchestrator._mode = 'single'
    def forbidden():
        raise AssertionError('ordinary text cannot resume legacy workflow gates')
    orchestrator._ensure_ffhedm = forbidden
    async def plan_only(*args, **kwargs):
        return 'prepare only'
    orchestrator._process_single_loop = plan_only
    assert await orchestrator.process('Prepare an FF-HEDM reconstruction plan. Do not start processing.', None) == 'prepare only'
    assert await orchestrator.process('ok', None) == 'prepare only'
