"""Feature capture uses persistent local feedback, with no MCP/model startup."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import apexa_feedback as feedback
import web_server as web


@pytest.fixture
def feedback_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APEXA_FEEDBACK_DIR", str(tmp_path / "feedback"))
    # No TestClient context manager: do not enter the live application lifespan.
    return TestClient(web.app)


def test_feature_request_persists_and_is_exported_by_a_fresh_process(feedback_client):
    response = feedback_client.post("/api/feedback", json={
        "kind": "report", "category": "feature_request",
        "comment": "Compare reconstructions from multiple scans", "context": {},
    })
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["ok"] and receipt["id"]
    feedback_client.post("/api/feedback", json={"kind": "up", "message_id": "answer-1"})

    script = str(Path(feedback.__file__).resolve())
    exported = subprocess.check_output([
        sys.executable, script, "export", "--category", "feature_request",
    ], text=True)
    records = json.loads(exported)
    assert len(records) == 1
    assert records[0]["id"] == receipt["id"]
    assert records[0]["comment"] == "Compare reconstructions from multiple scans"
    assert records[0]["context"] == {}
    assert records[0]["message_id"] is None
    assert records[0]["ts"]
    listing = subprocess.check_output([
        sys.executable, script, "list", "--category", "feature_request", "-n", "1",
    ], text=True)
    assert receipt["id"] in listing and "answer-1" not in listing


@pytest.mark.parametrize("comment", ["", "   \n "])
def test_empty_request_is_not_saved(feedback_client, comment):
    response = feedback_client.post("/api/feedback", json={
        "kind": "report", "category": "feature_request", "comment": comment,
    })
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert not feedback.read_all()


def test_failed_write_returns_no_success_receipt(feedback_client):
    # A file at the directory path makes the write fail regardless of permissions.
    feedback.feedback_dir().write_text("not a directory")
    response = feedback_client.post("/api/feedback", json={
        "kind": "report", "category": "feature_request", "comment": "Export comparison",
    })
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "id" not in response.json()


def test_category_filter_precedes_limit_and_skips_malformed_records(feedback_client):
    feedback.record("report", category="feature_request", comment="First request")
    feedback.record("report", category="feature_request", comment="Second request")
    feedback.record("report", category="wrong_result", comment="Unrelated issue")
    feedback.record("up")
    path = next(feedback.feedback_dir().glob("feedback-*.jsonl"))
    with path.open("a") as stream:
        stream.write('\nnot json\n[]\nnull\n')
    requests = feedback.read_all(limit=1, category="feature_request")
    assert [r["comment"] for r in requests] == ["Second request"]
    assert feedback.summarize(category="feature_request")["total"] == 2
    assert feedback.summarize()["total"] == 4
