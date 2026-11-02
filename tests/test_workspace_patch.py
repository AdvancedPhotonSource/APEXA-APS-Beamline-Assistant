"""Lossless workspace transport using isolated storage and no model/engine."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apexa_sessions
from apexa_session_api import router
from apexa_sessions import SessionStore


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("APEXA_SESSION_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    client.post("/api/sessions", json={"id": "scan"})
    return client, SessionStore(tmp_path)


def patch(client, slots, revision, *, session="scan", **settings):
    return client.patch(f"/api/sessions/{session}/workspace", json={
        "workspace": settings, "artifact_slots": slots, "expected_revision": revision,
    })


def test_plot_values_survive_draft_edits_reordering_replacement_and_restart(sessions):
    client, store = sessions
    plot = {"id": "plot", "type": "plotly", "data": {"data": [{
        "x": list(range(10000)), "y": [1e-12, -3.141592653589793, None, 1e12],
    }]}}
    report = {"id": "report", "type": "html-report", "data": {"path": "/synthetic/μ/report.html"}}
    assert patch(client, [{"value": plot}, {"value": report}], 0, draft="Original").status_code == 200
    first_revision = store.get("scan")["workspaceRevision"]
    assert patch(client, [{"reuse": 1}, {"reuse": 0}], first_revision,
                 draft="Next analysis", openIds=["plot"]).status_code == 200
    # A fresh store reads a normal complete snapshot, not unresolved references.
    stored = SessionStore(store.root).get("scan")
    assert stored["workspace"]["artifacts"] == [report, plot]
    assert stored["workspace"]["draft"] == "Next analysis"
    assert client.get("/api/sessions/scan").json()["workspace"] == stored["workspace"]
    changed = {**plot, "data": {"data": [{"x": [0], "y": [42]}]}}
    assert patch(client, [{"reuse": 0}, {"value": changed}], 2).status_code == 200
    assert store.get("scan")["workspace"]["artifacts"] == [report, changed]
    assert patch(client, [], 3).status_code == 200
    assert store.get("scan")["workspace"]["artifacts"] == []


@pytest.mark.parametrize("slot", [
    {"reuse": 9}, {"reuse": -1}, {"reuse": True}, {"reuse": "0"},
    {"reuse": 0, "value": {}}, {"value": []}, {},
])
def test_bad_references_leave_the_saved_snapshot_untouched(sessions, slot):
    client, store = sessions
    assert patch(client, [{"value": {"id": "kept", "data": [1, 2]}}], 0).status_code == 200
    before = store.path("scan").read_bytes()
    assert patch(client, [slot], 1).status_code == 422
    assert store.path("scan").read_bytes() == before


def test_stale_revision_cannot_reuse_another_windows_changed_artifact(sessions):
    client, store = sessions
    assert patch(client, [{"value": {"id": "one", "data": [1]}}], 0).status_code == 200
    # An older client still uses the supported full-snapshot PUT route.
    updated = {"artifacts": [{"id": "two", "data": [2]}], "draft": "Other window"}
    assert client.put("/api/sessions/scan/workspace", json={
        "workspace": updated, "expected_revision": 1,
    }).status_code == 200
    assert patch(client, [{"reuse": 0}], 1, draft="Stale").status_code == 409
    assert store.get("scan")["workspace"] == updated


def test_reference_is_scoped_to_the_target_session(sessions):
    client, store = sessions
    assert patch(client, [{"value": {"id": "private", "data": [3]}}], 0).status_code == 200
    assert patch(client, [{"reuse": 0}], 0, session="different").status_code == 422
    assert not store.path("different").exists()


def test_reused_artifacts_still_count_toward_the_workspace_size_limit(sessions, monkeypatch):
    client, store = sessions
    assert patch(client, [{"value": {"id": "points", "data": list(range(500))}}], 0).status_code == 200
    before = store.path("scan").read_bytes()
    size = len(json.dumps(store.get("scan")["workspace"]).encode())
    monkeypatch.setattr(apexa_sessions, "MAX_WORKSPACE_BYTES", size + 20)
    assert patch(client, [{"reuse": 0}], 1, draft="x" * 100).status_code == 422
    assert store.path("scan").read_bytes() == before


def test_two_writers_cannot_both_patch_the_same_revision(sessions):
    client, store = sessions
    assert patch(client, [{"value": {"id": "kept", "data": [1, 2]}}], 0).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda draft: patch(client, [{"reuse": 0}], 1, draft=draft), ["A", "B"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert store.get("scan")["workspaceRevision"] == 2
    assert store.get("scan")["workspace"]["artifacts"][0]["data"] == [1, 2]


def test_patch_cannot_silently_ignore_an_inline_artifact_list(sessions):
    client, store = sessions
    before = store.path("scan").read_bytes()
    assert patch(client, [], 0, artifacts=[{"id": "ambiguous"}]).status_code == 422
    assert store.path("scan").read_bytes() == before
