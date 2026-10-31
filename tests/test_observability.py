import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import apexa_observability as obs


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv('APEXA_TRACE_DB', str(tmp_path / 'traces.sqlite3'))
    sink = obs.trace_store()
    yield sink
    sink.flush()


def test_trace_is_correlated_and_redacts_content_without_debug(store, monkeypatch):
    from apexa_timing import query_scope, record_llm_call
    monkeypatch.delenv('APEXA_SHOW_TIMING', raising=False)
    with query_scope('secret prompt /sensitive/data') as query:
        with obs.span('tool', tool='test_tool', arguments={'token': 'secret'}) as fields:
            obs.emit('llm.test', prompt='secret prompt', path='/sensitive/data', token='secret', prompt_tokens=10)
            fields.update(execution='failed', scientific='rejected')
        record_llm_call(0.1, 10, 5)
    assert store.flush()
    records = store.events(query.query_id)
    assert len(records) == 6
    serialized = json.dumps(records)
    assert 'secret' not in serialized and '/sensitive' not in serialized
    assert all(row['trace_id'] == query.query_id for row in records)
    assert next(row for row in records if row['event'] == 'tool.finished')['attributes']['scientific'] == 'rejected'
    assert any(row['event'] == 'llm.completed' for row in records)


def test_retention_and_cursor_survive_reopen(tmp_path):
    sink = obs.TraceStore(tmp_path / 'trace.db', max_events=3)
    for i in range(6):
        sink.put({'trace_id': 'a' * 32, 'event': str(i)})
    assert sink.flush()
    records = sink.events('a' * 32)
    assert [row['event'] for row in records] == ['3', '4', '5']
    reopened = obs.TraceStore(sink.path, max_events=3)
    assert [row['event'] for row in reopened.events('a' * 32, after=records[0]['seq'])] == ['4', '5']


def test_sink_failure_is_visible_not_fatal(tmp_path):
    bad = tmp_path / 'not-a-directory'
    bad.write_text('occupied')
    sink = obs.TraceStore(bad / 'trace.db')
    sink.put({'trace_id': 'a' * 32, 'event': 'started'})
    assert sink.flush()
    assert sink.status()['failed_events'] == 1


@pytest.mark.asyncio
async def test_mcp_metadata_links_subprocess_without_exporting_args(store):
    from apexa_mcp import TracedMCPServer
    from apexa_subprocess import run
    from mcp.types import RequestParamsMeta
    from mcp.server.mcpserver.context import Context
    server = TracedMCPServer('test')
    @server.tool()
    async def probe(secret: str) -> dict:
        result = await run([sys.executable, '-c', 'print("private-scientific-output")'])
        assert 'private' in result.stdout
        return {'status': 'success', 'scientific_verdict': 'provisional'}
    with obs.span('client'):
        ids = obs.current_ids()
        context = Context(mcp_server=server, request_context=SimpleNamespace(
            meta=RequestParamsMeta(apexa_trace=ids)))
        await server.call_tool('probe', {'secret': 'private-token'}, context)
    assert store.flush()
    records = store.events(ids['trace_id'])
    names = [row['event'] for row in records]
    assert 'mcp.tool.finished' in names and 'subprocess.spawned' in names
    assert 'private' not in json.dumps(records)
    tool = next(row for row in records if row['event'] == 'mcp.tool.finished')
    assert tool['parent_span_id'] == ids['span_id']
    assert tool['attributes']['scientific'] == 'provisional'


@pytest.mark.asyncio
async def test_child_output_is_bounded_and_noisy_process_is_stopped(store):
    from apexa_subprocess import run, OutputLimitExceeded
    import psutil
    with obs.span('test'):
        trace_id = obs.current_ids()['trace_id']
        with pytest.raises(OutputLimitExceeded) as error:
            await run([sys.executable, '-c',
                'import os,time; os.write(1,b"x"*10000000); time.sleep(30)'], max_output_bytes=1024, timeout=5)
    assert len(error.value.stdout) + len(error.value.stderr) <= 1024
    assert store.flush()
    child = next(row for row in store.events(trace_id) if row['event'] == 'subprocess.spawned')
    assert not psutil.pid_exists(child['attributes']['pid'])


def test_interaction_log_omits_scientist_content(tmp_path):
    from interaction_logger import InteractionLogger
    logger = InteractionLogger(str(tmp_path))
    entry = logger.start('secret query', 'sensitive-user', 'model')
    entry.add_tool_call('calibrate', {'token': 'secret'}, 'secret results', False, 4)
    entry.finish('secret summary', 1)
    logger.save(entry)
    text = logger.log_file.read_text()
    assert 'secret' not in text and 'sensitive-user' not in text
    assert logger.stats()['total'] == 1


def test_runtime_health_pagination_and_run_replay(store, monkeypatch, tmp_path):
    import web_server as web
    from apexa_jobs import default_job_store
    monkeypatch.delenv('APEXA_WEB_TOKEN', raising=False)
    monkeypatch.setenv('APEXA_DATA_ROOTS', str(tmp_path))
    monkeypatch.setenv('APEXA_RUN_DIR', str(tmp_path / 'runs'))
    monkeypatch.setattr(web, 'mcp_client', SimpleNamespace(sessions={}))
    client = TestClient(web.app)
    assert client.get('/api/health/live').status_code == 200
    assert client.get('/api/health/ready').status_code == 503
    monkeypatch.setattr(web, 'mcp_client', SimpleNamespace(sessions={'core': object(), 'midas': object()}))
    response = client.get('/api/health/ready')
    assert response.status_code == 200
    assert len(response.headers['x-apexa-trace-id']) == 32
    (tmp_path / '.hidden').write_text('')
    for i in range(5):
        (tmp_path / f'{i}.tif').write_text('data')
    first = client.get('/api/browse', params={'path': str(tmp_path), 'limit': 2}).json()
    second = client.get('/api/browse', params={'path': str(tmp_path), 'limit': 2, 'offset': first['next_offset']}).json()
    assert first['has_more'] and {e['name'] for e in first['entries']}.isdisjoint(e['name'] for e in second['entries'])
    assert not any(e['name'].startswith('.') for e in first['entries'] + second['entries'])
    assert client.get('/api/browse', params={'limit': 501}).status_code == 422
    run_id = default_job_store().submit({'operation': 'test'}, idempotency_key='test')
    events = client.get(f'/api/runs/{run_id}/events').json()
    assert events['events']
    assert client.get(f'/api/runs/{run_id}/events', params={'after': events['next_cursor']}).json()['events'] == []
    result = client.post(f'/api/runs/{run_id}/cancel').json()
    assert result['state'] == 'cancelled'
