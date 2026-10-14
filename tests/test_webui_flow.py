"""In-process web integration; no model, beamline service, or listening socket."""
import asyncio
import json
import threading
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import web_server as web
from apexa_canvas import science_view

CIF = str(Path(__file__).resolve().parents[1] / "Ni_mp-23.cif")


@pytest.fixture
def client(monkeypatch):
    class Client:
        _web_thread_lock = asyncio.Lock()
        selected_model = "offline-test"
        sessions = {"core": object()}
        available_models = {}

        async def activate_web_thread(self, thread_id):
            self.thread_id = thread_id

        def _save_web_thread(self):
            pass

        async def run_query(self, query, on_tool_result=None, on_tool_start=None, **kwargs):
            assert "open_science_view" in query
            args = {"path": CIF, "kind": "structure"}
            if on_tool_start:
                await on_tool_start("open_science_view", args)
            result = await asyncio.to_thread(science_view, **args)
            await on_tool_result("open_science_view", args, json.dumps(result))
            return "Structure sent to the canvas."

    monkeypatch.setattr(web, "mcp_client", Client())
    # No lifespan startup: real MCP/model services are intentionally absent.
    return TestClient(web.app)


def test_ws_chat_tool_artifact_and_structure_api(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "chat", "message": "Show the CIF", "thread_id": "demo"})
        assert ws.receive_json()["type"] == "tool_start"
        event = ws.receive_json()
        assert event["type"] == "tool_result"
        canvas = json.loads(event["result"])["canvas"]
        assert canvas["type"] == "structure"
        response = client.get("/local/api/science/structure", params={"path": canvas["data"]["path"]})
        assert response.status_code == 200
        assert len(response.json()["atoms"]) == 4
        assert ws.receive_json()["type"] == "chat_response"
    assert web.manager.active_connections == []


def test_http_fallback_keeps_structured_artifacts(client):
    response = client.post("/api/chat", data={"message": "Show CIF", "thread_id": "demo"})
    assert response.status_code == 200
    artifact = json.loads(response.json()["tool_results"][0]["result"])["canvas"]
    assert artifact["data"]["path"] == CIF


def test_static_report_and_page_cache_contract(client, tmp_path):
    report = tmp_path / "report.html"
    report.write_text("<html><body>Scientific report</body></html>")
    result = client.get("/local/api/science/report", params={"path": str(report)})
    assert result.status_code == 200
    assert "Scientific report" in result.json()["html"]
    assert client.get("/local/api/science/report", params={"path": CIF}).status_code == 422
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_uploaded_html_report_is_readable_in_canvas(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "upload_dir", tmp_path)
    upload = client.post("/api/upload", files={"file": ("review.html", b"<h1>Uploaded scientific report</h1>", "text/html")})
    assert upload.status_code == 200
    path = upload.json()["saved_path"]
    response = client.get("/local/api/science/report", params={"path": path})
    assert response.status_code == 200
    assert "Uploaded scientific report" in response.json()["html"]


def test_ws_report_tool_to_canvas_api(client, monkeypatch, tmp_path):
    path = tmp_path / "report.html"
    path.write_text("<h1>Measured reconstruction review</h1>")

    async def report_query(query, on_tool_result=None, on_tool_start=None, **kwargs):
        args = {"path": str(path), "kind": "report"}
        if on_tool_start:
            await on_tool_start("open_science_view", args)
        await on_tool_result("open_science_view", args, json.dumps(science_view(**args)))
        return "Report sent to canvas"

    monkeypatch.setattr(web.mcp_client, "run_query", report_query)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "chat", "message": "Open my report", "thread_id": "reports"})
        assert ws.receive_json()["type"] == "tool_start"
        canvas = json.loads(ws.receive_json()["result"])["canvas"]
        assert canvas["type"] == "html-report"
        assert client.get("/local/api/science/report", params={"path": canvas["data"]["path"]}).status_code == 200
        assert ws.receive_json()["type"] == "chat_response"


def test_explicit_remote_report_never_silently_reads_local_file(client, tmp_path):
    if not any(getattr(route, "path", None) == "/remote/api/science/report" for route in web.app.routes):
        pytest.skip("Remote science gateway configured")
    path = tmp_path / "report.html"
    path.write_text("<h1>Local file must not be used</h1>")
    result = client.get("/remote/api/science/report", params={"path": str(path)})
    assert result.status_code == 503
    assert "No remote science service" in result.json()["detail"]


def test_selected_canvas_context_is_bounded_metadata_only():
    context = web.selected_canvas_context({"path": CIF, "frame_index": 3, "preview": "x" * 1_000_000})
    assert CIF in context and '"frame_index": 3' in context
    assert len(context) < 1000


def test_malformed_ws_does_not_drop_connection(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not JSON")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "chat", "message": "Show CIF"})
        assert ws.receive_json()["type"] == "tool_start"
        assert ws.receive_json()["type"] == "tool_result"
        assert ws.receive_json()["type"] == "chat_response"


def test_slow_image_read_does_not_block_status(client, monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    path = tmp_path / "image.tif"
    path.touch()

    def slow_read(_):
        started.set()
        release.wait(timeout=3)
        return np.ones((16, 16), dtype=np.float32)

    monkeypatch.setattr(web, "load_diffraction_image", slow_read)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as http:
            task = asyncio.create_task(http.post("/api/viewer/load_path", data={"path": str(path)}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                status = await asyncio.wait_for(http.get("/api/status"), timeout=1)
                assert status.status_code == 200
                assert not task.done(), "Image work blocked the event loop"
            finally:
                release.set()
                await task

    asyncio.run(run())
