"""Shared CLI/WebUI persistence using isolated directories and fake analysis."""
import asyncio
import json
import subprocess
import sys
from types import MethodType

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apexa_sessions import SessionStore, SessionBusy, SessionConflict
from apexa_session_api import router


def complete(store, name, text='done'):
    with store.turn(name):
        record = store.begin(name, 'inspect data', 'inspect data', 'cli')
        store.tool(name, record['turn']['id'], 'inspect_dataset_file', {'path': '/data/sample.h5'}, '{"shape":[4,8,8]}')
        return store.finish(name, record['turn']['id'], text,
                            history=[{'role': 'user', 'content': 'inspect data'}, {'role': 'assistant', 'content': text}],
                            summary='sample metadata', metadata={'sample_name': 'Ni'}, model='test-model', active_directory='/data')


def test_legacy_transcript_retains_structured_memory_and_summary(tmp_path):
    store = SessionStore(tmp_path)
    history = [
        {'role': 'user', 'content': 'inspect data'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call1', 'function': {'name': 'inspect_dataset_file', 'arguments': '{"path":"/data/sample.h5"}'}}]},
        {'role': 'tool', 'tool_call_id': 'call1', 'content': '{"shape":[4,8,8]}'},
        {'role': 'assistant', 'content': 'done'},
    ]
    store.path('legacy').write_text(json.dumps({'conversation_history': history, 'running_summary': 'Ni experiment', 'sample_name': 'Ni'}))
    transcript = tmp_path / 'legacy.jsonl'
    transcript.write_text('\n'.join(json.dumps(m) for m in [history[0], history[-1]]))
    from argo_mcp_client import ExperimentContext
    context = ExperimentContext(tmp_path)
    assert context.load_session('legacy')
    assert context.loaded_conversation == history
    assert context.loaded_summary == 'Ni experiment'
    assert context.metadata['sample_name'] == 'Ni'
    record = store.get('legacy')
    assert record['messages'][-1]['toolEvents'][0]['tool'] == 'inspect_dataset_file'
    assert record['messages'][-1]['toolEvents'][0]['args']['path'] == '/data/sample.h5'
    context.save_session('legacy', history, 'Ni experiment')
    assert store.get('legacy')['conversation_history'] == history
    assert transcript.exists()


def test_legacy_web_catalogue_and_browser_import_keep_memory(tmp_path):
    legacy = tmp_path / 'web_threads'
    legacy.mkdir()
    (legacy / 'web-1.json').write_text(json.dumps({'conversation_history': [{'role': 'user', 'content': 'new'}, {'role': 'assistant', 'content': 'reply'}], 'running_summary': 'earlier work'}))
    store = SessionStore(tmp_path / 'sessions')
    assert store.list()[0]['id'] == 'web-1'
    messages = [dict(id=str(i), role=role, content=text, timestamp=i) for i, (role, text) in enumerate([('user', 'older'), ('assistant', 'older reply'), ('user', 'new'), ('assistant', 'reply')])]
    before = store.get('web-1')['conversation_history']
    record = store.import_browser({'id': 'web-1', 'messages': messages, 'title': 'Ni'})
    assert record['conversation_history'] == before
    assert len(record['messages']) == 4
    assert store.import_browser({'id': 'web-1', 'messages': messages}) == record
    assert (legacy / 'web-1.json').exists()


def test_interrupted_turn_keeps_results_without_reexecution(tmp_path):
    store = SessionStore(tmp_path)
    with store.turn('scan'):
        record = store.begin('scan', 'reconstruct', 'reconstruct', 'web')
        store.tool('scan', record['turn']['id'], 'run_ff_hedm_full_workflow', {}, '{"job_id":"123","status":"submitted"}')
        assert store.get('scan')['status'] == 'running'
    restarted = SessionStore(tmp_path)
    assert restarted.get('scan')['status'] == 'interrupted'
    assert 'No processing was restarted' in restarted.get('scan')['resume_notice']
    with restarted.turn('scan'):
        next_record = restarted.begin('scan', 'what happened?', 'what happened?', 'cli')
        assert '123' in json.dumps(next_record['conversation_history'])
        assert sum(m.get('content') == 'reconstruct' for m in next_record['conversation_history']) == 1
        interrupted = restarted.finish('scan', next_record['turn']['id'], 'network failure', history=[], summary='', metadata={}, model='', active_directory='', error=True)
    assert interrupted['status'] == 'interrupted'
    assert restarted.get('scan')['resume_notice']
    with pytest.raises(SessionConflict):
        restarted.tool('scan', 'obsolete', 'x', {}, '{}')


def test_turn_lock_excludes_another_process_and_releases(tmp_path):
    store = SessionStore(tmp_path)
    code = 'from pathlib import Path; import sys; from apexa_sessions import SessionStore; s=SessionStore(Path(sys.argv[1])); print(s.busy("scan"))'
    with store.turn('scan'):
        assert subprocess.check_output([sys.executable, '-c', code, str(tmp_path)], text=True).strip() == 'True'
        with pytest.raises(SessionBusy):
            with SessionStore(tmp_path).turn('scan'):
                pass
    assert subprocess.check_output([sys.executable, '-c', code, str(tmp_path)], text=True).strip() == 'False'


def test_cli_and_web_share_memory_metadata_canvas_and_reject_stale_saves(tmp_path):
    from argo_mcp_client import ExperimentContext
    store = SessionStore(tmp_path)
    complete(store, 'scan')
    cli = ExperimentContext(tmp_path)
    assert cli.load_session('scan')
    assert cli.loaded_model == 'test-model'
    assert cli.loaded_directory == '/data'
    assert cli.metadata['sample_name'] == 'Ni'
    workspace = {'artifacts': [{'id': 'report', 'type': 'html-report', 'data': {'path': '/data/report.html'}}], 'openIds': ['report'], 'draft': 'next step'}
    store.workspace('scan', workspace, 0)
    complete(store, 'scan', 'next result')
    with pytest.raises(SessionConflict):
        cli.save_session('scan', conversation=cli.loaded_conversation)
    assert cli.load_session('scan')
    cli.save_session('scan', conversation=cli.loaded_conversation)
    assert store.get('scan')['workspace'] == workspace
    with pytest.raises(SessionConflict):
        store.workspace('scan', {'draft': 'stale'}, 0)
    assert store.get('scan')['workspace'] == workspace


@pytest.mark.parametrize('name', ['../outside', '/tmp/unsafe', 'a/b', '', '..', 'a\\b'])
def test_session_ids_cannot_escape_root(tmp_path, name):
    with pytest.raises(ValueError):
        SessionStore(tmp_path).ensure(name)


def test_corrupt_records_are_visible_and_not_overwritten(tmp_path):
    store = SessionStore(tmp_path)
    store.path('scan').write_text('{broken')
    assert store.list()[0]['status'] == 'unreadable'
    with pytest.raises(ValueError):
        store.ensure('scan')
    assert store.path('scan').read_text() == '{broken'
    (tmp_path / 'bad.jsonl').write_text('{broken\n{"role":"user","content":"good"}')
    with pytest.raises(ValueError, match='line 1'):
        store.get('bad')


def test_archive_preserves_record_and_can_be_resumed(tmp_path):
    store = SessionStore(tmp_path)
    complete(store, 'scan')
    store.archive('scan')
    assert not store.list()
    assert store.list(archived=True)[0]['id'] == 'scan'
    complete(store, 'scan', 'resumed')
    assert store.list()[0]['id'] == 'scan'


def test_session_api_works_without_model_and_uses_workspace_cas(tmp_path, monkeypatch):
    monkeypatch.setenv('APEXA_SESSION_DIR', str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.post('/api/sessions', json={'id': 'shared'}).status_code == 200
    complete(SessionStore(tmp_path), 'shared')
    response = client.get('/api/sessions/shared').json()
    assert 'conversation_history' not in response
    assert response['messages'][-1]['toolEvents']
    assert client.get('/api/sessions').json()['sessions'][0]['id'] == 'shared'
    payload = {'workspace': {'draft': 'continue', 'openIds': []}, 'expected_revision': 0}
    assert client.put('/api/sessions/shared/workspace', json=payload).status_code == 200
    assert client.put('/api/sessions/shared/workspace', json=payload).status_code == 409
    assert client.patch('/api/sessions/shared', json={'title': 'Renamed'}).status_code == 200
    assert client.get('/api/sessions/missing').status_code == 404
    assert client.delete('/api/sessions/shared').status_code == 200
    assert client.get('/api/sessions?archived=true').json()['sessions'][0]['title'] == 'Renamed'


def fake_client(tmp_path):
    from argo_mcp_client import APEXAClient, ExperimentContext
    class Orchestrator:
        def clear_history(self): self.history, self.summary, self._active_dir = [], '', ''
        def import_history(self, history): self.history = list(history)
        def import_summary(self, summary): self.summary = summary
        def export_history(self): return self.history
        def export_summary(self): return self.summary
    client = APEXAClient.__new__(APEXAClient)
    client.context = ExperimentContext(tmp_path)
    client._active_web_thread = None
    client.selected_model = 'test-model'
    client.orchestrator = Orchestrator()
    return client


def test_client_error_autosave_preserves_executed_tools_and_can_resume_web(tmp_path):
    client = fake_client(tmp_path)
    async def failure(self, query, use_history=True, **kwargs):
        await kwargs['on_tool_result']('run_ff_hedm_full_workflow', {}, '{"job_id":"123"}')
        self.orchestrator.history.append({'role': 'assistant', 'content': 'partial'})
        raise RuntimeError('provider failed')
    client._execute_query = MethodType(failure, client)
    with pytest.raises(RuntimeError, match='provider failed'):
        asyncio.run(client.run_query('reconstruct'))
    name = client.context.active_session
    client._autosave_session()
    stored = client.context.store.get(name)
    assert '123' in json.dumps(stored['conversation_history'])
    assert 'partial' not in json.dumps(stored['conversation_history'])
    web = fake_client(tmp_path)
    async def resume(self, query, use_history=True, **kwargs):
        assert '123' in json.dumps(self.orchestrator.history)
        self.orchestrator.history.extend([{'role': 'user', 'content': query}, {'role': 'assistant', 'content': 'recorded job'}])
        return 'recorded job'
    web._execute_query = MethodType(resume, web)
    async def run():
        await web.activate_web_thread(name)
        return await web.run_query('status plus web context', display_query='status')
    assert asyncio.run(run()) == 'recorded job'
    assert web.context.store.get(name)['messages'][-2]['content'] == 'status'
    assert client.context.load_session(name)
    assert client.context.loaded_conversation[-1]['content'] == 'recorded job'
