import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apexa_science_gateway import science_gateway
from apexa_remote_exec import remote_run


def test_gateway_forwards_explicit_remote_request_without_external_network():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"name": "remote structure"})
    app = FastAPI()
    app.include_router(science_gateway("http://127.0.0.1:8020", transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    result = client.get("/api/science/structure", params={"path": "/beamline/data/sample.cif"})
    assert result.json()["name"] == "remote structure"
    assert seen[0].url.params["path"] == "/beamline/data/sample.cif"
    assert client.get("/api/science/arbitrary-command").status_code == 404


def test_gateway_disconnect_does_not_fallback_to_local_paths():
    def handler(request):
        raise httpx.ConnectError("tunnel down", request=request)
    app = FastAPI()
    app.include_router(science_gateway("http://127.0.0.1:8020", transport=httpx.MockTransport(handler)))
    response = TestClient(app).post("/api/science/sequence", json={"path": "/data"})
    assert response.status_code == 502
    assert "not used as a fallback" in response.json()["detail"]


@pytest.mark.parametrize("url", ["http://public.example:8000", "https://127.0.0.1", "http://user:pass@localhost", "http://localhost/other"])
def test_gateway_requires_loopback_tunnel(url):
    with pytest.raises(ValueError, match="loopback"):
        science_gateway(url)


def test_missing_ssh_is_a_structured_failure(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ssh not installed")
    monkeypatch.setattr("apexa_remote_exec.subprocess.run", missing)
    result = remote_run("configured-host", "true")
    assert result["ssh_failed"] and not result["success"]
    assert "Could not start SSH" in result["stderr"]


def test_report_gateway_forwards_exact_path_and_remote_errors():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(422, json={"detail": "Remote report is missing"})
    app = FastAPI()
    app.include_router(science_gateway("http://127.0.0.1:8020", transport=httpx.MockTransport(handler)), prefix="/remote")
    result = TestClient(app).get("/remote/api/science/report", params={"path": "/beamline/scan 1/report.html"})
    assert result.status_code == 422
    assert result.json()["detail"] == "Remote report is missing"
    assert seen[0].url.path == "/api/science/report"
    assert seen[0].url.params["path"] == "/beamline/scan 1/report.html"
