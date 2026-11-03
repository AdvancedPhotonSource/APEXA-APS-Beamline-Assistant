#!/usr/bin/env python3
"""Portable offline acceptance of storage, child ownership and HTTP assets."""
import asyncio
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def check():
    # Standalone process: redirect all writable state before importing APEXA.
    with tempfile.TemporaryDirectory(prefix="apexa-platform-") as temporary:
        os.environ.update(APEXA_HOME=temporary, APEXA_SESSION_DIR=str(Path(temporary) / "sessions"),
            APEXA_RESOURCE_DB=str(Path(temporary) / "resources.sqlite3"),
            APEXA_TRACE_DB=str(Path(temporary) / "traces.sqlite3"),
            APEXA_UPLOAD_DIR=str(Path(temporary) / "uploads"), PYTHON_DOTENV_DISABLED="1")
        from apexa_sessions import SessionStore
        from apexa_subprocess import run
        store = SessionStore()
        store.workspace("platform", {"artifacts": [{"id": "fixture", "data": [1, 2, 3]}]}, 0)
        assert SessionStore().get("platform")["workspace"]["artifacts"][0]["data"] == [1, 2, 3]
        child = await run([sys.executable, "-c", "print('owned child')"], timeout=10)
        assert child.returncode == 0 and child.stdout.strip() == "owned child"
        from fastapi.testclient import TestClient
        import web_server
        # No context manager: no lifespan, MCP servers, inference or engines.
        client = TestClient(web_server.app)
        assert client.get("/").status_code == 200
        assert client.get("/api/connection").status_code == 200
        assert client.get("/apexa-mark.svg").status_code == 200
        from apexa_observability import trace_store
        await asyncio.to_thread(trace_store().flush)
    return {"platform": platform.platform(), "python": platform.python_version(),
            "is_wsl": "microsoft" in platform.release().lower(), "software_smoke": "passed",
            "checks": ["session_write_reload", "owned_subprocess", "HTTP_assets", "connection_setup"],
            "native_engines": "not_tested", "live_llm": "not_tested", "hardware": "not_tested"}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check()), indent=2))
