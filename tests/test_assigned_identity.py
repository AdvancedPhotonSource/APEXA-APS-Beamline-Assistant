from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
import pytest

from apexa_web_security import SingleOperatorBoundary


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APEXA_OPERATOR_ID", "alice@example.edu")
    monkeypatch.setenv("APEXA_PROPOSAL_ID", "proposal-123")
    monkeypatch.setenv("APEXA_DATA_ROOTS", "/data/proposal-123")
    monkeypatch.setenv("APEXA_WEB_TOKEN", "backend-fixture")
    app = FastAPI()
    app.add_middleware(SingleOperatorBoundary)
    @app.get("/")
    def home(): return {"operator": "alice"}
    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("alice")
    return TestClient(app)


def test_proxy_principal_and_backend_token_both_required(client):
    assert client.get("/", headers={"X-APEXA-Principal": "alice@example.edu"}).status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer backend-fixture"}).status_code == 403
    for principal, status in [("bob@example.edu", 403), ("alice@example.edu", 200)]:
        assert client.get("/", headers={"Authorization": "Bearer backend-fixture", "X-APEXA-Principal": principal}).status_code == status


def test_websocket_checks_the_same_assigned_identity(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"Authorization": "Bearer backend-fixture", "X-APEXA-Principal": "bob@example.edu"}):
            pass
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer backend-fixture", "X-APEXA-Principal": "alice@example.edu"}) as socket:
        assert socket.receive_text() == "alice"
