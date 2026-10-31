import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import web_server as web


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("APEXA_WEB_TOKEN", raising=False)
    monkeypatch.delenv("APEXA_DATA_ROOTS", raising=False)
    monkeypatch.delenv("APEXA_WEB_HOSTS", raising=False)
    monkeypatch.delenv("APEXA_WEB_MODE", raising=False)
    monkeypatch.setattr(web, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(web, "image_cache", {"selected": np.ones((2, 2)), "foreign": np.ones((2, 2))})
    monkeypatch.setattr(web, "image_paths", {"selected": str(tmp_path / "selected.tif"), "foreign": "/secret/other-session.tif"})
    monkeypatch.setattr(web, "calibration_cache", {})
    calls = []
    async def execute(name, arguments):
        calls.append((name, arguments))
        return '{"error":"engine unavailable"}'
    async def query(text, **kw):
        calls.append(("query", text))
        return "No calibration ran."
    fake = SimpleNamespace(sessions={}, _tool_registry={}, available_models={},
        selected_model="test", execute_tool_call=execute, run_query=query,
        _web_thread_lock=asyncio.Lock(), _save_web_thread=lambda: None)
    async def activate(_):
        pass
    fake.activate_web_thread = activate
    monkeypatch.setattr(web, "mcp_client", fake)
    return TestClient(web.app), calls


def test_foreign_websocket_origin_is_rejected(client):
    http, _ = client
    with pytest.raises(WebSocketDisconnect) as exc:
        with http.websocket_connect("/ws", headers={"origin": "https://untrusted.invalid"}):
            pass
    assert exc.value.code == 1008


def test_foreign_http_origin_and_host_are_rejected(client):
    http, _ = client
    assert http.get("/api/status", headers={"origin": "https://untrusted.invalid"}).status_code == 403
    assert http.get("/api/status", headers={"host": "evil.invalid"}).status_code == 403


def test_token_covers_http_and_websocket(client, monkeypatch):
    http, _ = client
    monkeypatch.setenv("APEXA_WEB_TOKEN", "test-only-secret")
    assert http.get("/api/status").status_code == 401
    assert http.get("/api/status", headers={"Authorization": "Bearer test-only-secret"}).status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with http.websocket_connect("/ws"):
            pass


def test_remote_host_requires_token_even_behind_loopback_proxy(client, monkeypatch, tmp_path):
    http, _ = client
    monkeypatch.setenv("APEXA_WEB_HOSTS", "beamline.example")
    monkeypatch.setenv("APEXA_DATA_ROOTS", str(tmp_path))
    assert http.get("/api/status", headers={"host": "beamline.example"}).status_code == 401


@pytest.mark.parametrize("route", ["/api/upload", "/api/viewer/load", "/api/viewer/load_calibration"])
def test_all_upload_routes_reject_parent_paths(client, tmp_path, route):
    http, _ = client
    response = http.post(route, files={"file": ("../escaped.txt", b"Lsd 900000\n", "text/plain")})
    assert response.status_code == 400
    assert not (tmp_path / "escaped.txt").exists()


def test_repeated_filenames_are_isolated_and_bounded(client, monkeypatch, tmp_path):
    http, _ = client
    one = http.post("/api/upload", files={"file": ("same.txt", b"one", "text/plain")}).json()
    two = http.post("/api/upload", files={"file": ("same.txt", b"two", "text/plain")}).json()
    assert one["saved_path"] != two["saved_path"]
    monkeypatch.setenv("APEXA_UPLOAD_MAX_BYTES", "100")
    assert http.post("/api/upload", files={"file": ("large.txt", b"x" * 1000)}).status_code == 413


def test_no_servers_is_not_ready(client):
    http, _ = client
    assert http.get("/api/status").json()["mcp_client_connected"] is False


def test_viewer_cache_has_a_byte_budget(client, monkeypatch):
    monkeypatch.setenv("APEXA_IMAGE_CACHE_BYTES", "64")
    web._store_image("first", np.ones((2, 2)), "/first")
    web._store_image("second", np.ones((2, 2)), "/second")
    web._store_image("third", np.ones((2, 2)), "/third")
    assert "first" not in web.image_cache
    assert "first" not in web.image_paths
    assert sum(a.nbytes for a in web.image_cache.values()) <= 64


@pytest.mark.parametrize("route,tool,data", [
    ("/api/analysis/phases", "midas_identify_crystalline_phases", {"file_id": "selected", "elements": "Au"}),
    ("/api/analysis/peaks", "midas_analyze_diffraction_peaks", {"file_id": "selected"}),
])
def test_legacy_analysis_never_reports_unavailable_tool_as_success(client, route, tool, data):
    http, calls = client
    assert http.post(route, data=data).status_code == 503
    assert not calls
    web.mcp_client._tool_registry[tool] = "stub"
    response = http.post(route, data=data)
    assert response.json()["success"] is False
    assert response.json()["outcome"]["execution"] == "failed"


def test_calibration_prose_cannot_become_completed(client):
    http, calls = client
    response = http.post("/api/calibrate", data={"file_id": "selected"})
    assert response.status_code == 422
    assert "No calibration was run" in response.json()["detail"]
    assert not calls


def test_selected_canvas_does_not_include_other_cached_images(client):
    http, calls = client
    with http.websocket_connect("/ws") as ws:
        ws.send_json({"type": "chat", "message": "Inspect this", "viewer_context": {"path": "/chosen/image.tif"}})
        assert ws.receive_json()["type"] == "chat_response"
    query = calls[0][1]
    assert "/chosen/image.tif" in query
    assert "other-session" not in query
    assert "selected.tif" not in query


def test_configured_data_roots_apply_to_browser_and_science_api(client, monkeypatch, tmp_path):
    http, _ = client
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    blocked = tmp_path / "private.html"
    blocked.write_text("<h1>private</h1>")
    monkeypatch.setenv("APEXA_DATA_ROOTS", str(allowed))
    assert http.get("/api/browse", params={"path": str(tmp_path)}).status_code == 403
    assert http.get("/local/api/science/report", params={"path": str(blocked)}).status_code == 403


def test_auto_calibration_uses_isolated_canonical_adapter(client, monkeypatch, tmp_path):
    http, calls = client
    web.mcp_client._tool_registry["midas_auto_calibrate"] = "stub"
    monkeypatch.setattr(web, "load_diffraction_image", lambda p: np.ones((3, 4)))
    data = {"image_file_id": "selected", "space_group": 225,
            "lattice_a": 5.4, "lattice_b": 5.4, "lattice_c": 5.4,
            "lattice_alpha": 90, "lattice_beta": 90, "lattice_gamma": 90,
            "lsd": 900000, "lsd_guess": 900000, "bc_x": 2, "bc_y": 1.5,
            "px": 150, "wavelength": 0.2}
    first = http.post("/api/auto_calibrate", data=data)
    second = http.post("/api/auto_calibrate", data=data)
    assert first.status_code == second.status_code == 200
    assert not first.json()["success"]
    assert calls[0][0] == "midas_auto_calibrate"
    assert calls[0][1]["output_dir"] != calls[1][1]["output_dir"]
    assert calls[0][1]["px_um"] == 150
    assert calls[0][1]["image_transform"] == "0"
