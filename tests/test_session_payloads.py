"""Lossless immutable payloads, cheap draft saves and session-bound lazy reads."""
import json

import pytest

from apexa_sessions import SessionStore


def test_draft_save_and_catalogue_do_not_read_or_encode_plot_payload(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    artifact = {"id": "large", "type": "plotly", "title": "Exact", "data": {"x": list(range(100000))}}
    store.workspace("one", {"artifacts": [artifact]}, 0)
    assert store.path("one").stat().st_size < 3000
    initial_payloads = {p: p.stat().st_mtime_ns for p in tmp_path.glob("payloads/*/*.json")}
    def forbidden(*args, **kwargs):
        pytest.fail("Draft save/catalogue must not read or serialize unchanged payloads")
    with monkeypatch.context() as m:
        m.setattr(store, "_payload", forbidden)
        m.setattr(store, "_put_payload", forbidden)
        store.workspace("one", {"draft": "Next"}, 1, artifact_slots=[{"reuse": 0}])
        assert store.list()[0]["id"] == "one"
    assert initial_payloads == {p: p.stat().st_mtime_ns for p in tmp_path.glob("payloads/*/*.json")}
    assert SessionStore(tmp_path).get("one")["workspace"]["artifacts"] == [artifact]


def test_lazy_artifact_is_bound_to_owning_session_and_integrity(tmp_path):
    store = SessionStore(tmp_path)
    artifact = {"id": "exact", "type": "plotly", "data": [1e-20, 3.141592653589793, None]}
    store.workspace("one", {"artifacts": [artifact]}, 0)
    lazy = store.get("one", lazy_artifacts=True)["workspace"]["artifacts"][0]
    assert lazy["data"] is None
    digest = lazy["artifactRef"]["digest"]
    assert store.artifact("one", digest) == artifact
    store.ensure("two")
    with pytest.raises(FileNotFoundError):
        store.artifact("two", digest)
    with pytest.raises(ValueError, match="belong"):
        store.workspace("two", {"artifacts": [lazy]}, 0)
    store.workspace("one", {"artifacts": [lazy], "draft": "restored"}, 1)
    assert store.artifact("one", digest) == artifact
    path = tmp_path / "payloads" / digest[:2] / (digest + ".json")
    path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises(ValueError, match="integrity"):
        store.artifact("one", digest)


def test_version_two_migration_preserves_exact_history_and_workspace(tmp_path):
    store = SessionStore(tmp_path)
    record = store._new("legacy")
    record.update(schema_version=2, messages=[{"role": "user", "content": "μm"}],
                  conversation_history=[{"role": "tool", "tool_call_id": "call1", "content": "result"}])
    record["workspace"] = {"artifacts": [{"id": "old", "data": [42]}]}
    store.path("legacy").write_text(json.dumps(record))
    store.workspace("legacy", {"draft": "New"}, 0, artifact_slots=[{"reuse": 0}])
    restored = store.get("legacy")
    assert restored["messages"] == record["messages"]
    assert restored["conversation_history"] == record["conversation_history"]
    assert restored["workspace"]["artifacts"] == record["workspace"]["artifacts"]
    assert json.loads(store.path("legacy").read_text())["schema_version"] == 3


def test_chat_turn_updates_reuse_large_canvas_payload(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    artifact = {'id': 'plot', 'type': 'plotly', 'data': {'x': list(range(100000))}}
    store.workspace('chat', {'artifacts': [artifact]}, 0)
    original = json.loads(store.path('chat').read_text())['workspace']['artifacts'][0]
    read = store._payload
    def guarded(ref):
        assert ref['sha256'] != original['sha256'], 'Chat must not load the unchanged Canvas plot'
        return read(ref)
    with monkeypatch.context() as m:
        m.setattr(store, '_payload', guarded)
        m.setattr(store, '_pack_artifact', lambda value: pytest.fail('Chat must not serialize the unchanged Canvas plot'))
        record = store.begin('chat', 'Analyze', 'Analyze', 'web')
        store.tool('chat', record['turn']['id'], 'fixture', {}, 'saved result')
        store.finish('chat', record['turn']['id'], 'Done', history=[], summary='', metadata={}, model='fixture', active_directory='')
        store.rename('chat', 'Saved analysis')
    assert store.get('chat')['workspace']['artifacts'] == [artifact]
