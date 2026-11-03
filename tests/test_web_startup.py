"""Web startup and production public assets, without models or MCP processes."""
import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import apexa_profiles as profiles
import web_server as web


@pytest.fixture
def startup(monkeypatch, tmp_path):
    # All environment changes made by profiles.apply are confined to this test.
    for key in ("APEXA_LLM_PRESET", "APEXA_LLM_MODE", "APEXA_LLM_BASE_URL", "APEXA_ACTIVE_PROFILE"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("ARGO_MODEL", "gpt55")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "servers.config").write_text("core:never-started.py\n")
    calls = []
    real_init_profile = web.APEXAClient._init_profile
    loop_thread = threading.get_ident()

    class Client:
        def __init__(self):
            self.selected_model = "gpt55"

        def _init_profile(self):
            assert threading.get_ident() != loop_thread
            calls.append("profile")
            return real_init_profile(self)

        async def connect_to_multiple_servers(self, configs):
            import os
            calls.append(("connect", os.environ.get("APEXA_LLM_MODE"), configs))

    monkeypatch.setattr(web, "APEXAClient", Client)
    monkeypatch.setattr(web, "mcp_client", None)
    return calls


@pytest.mark.parametrize("legacy", [False, True])
def test_web_applies_same_anl_profile_as_cli_before_connecting(startup, monkeypatch, capsys, legacy):
    import os
    if legacy:
        monkeypatch.setenv("APEXA_LLM_MODE", "argo")
    anl = next(p for p in profiles.BUILTIN_PROFILES if p.name == "anl")
    monkeypatch.setattr(profiles, "resolve_active", lambda: (anl, "env"))
    asyncio.run(web.initialize_mcp_client())
    assert startup == ["profile", ("connect", "argo" if legacy else "proxy",
                                  [{"name": "core", "script_path": "never-started.py"}])]
    assert web.mcp_client.selected_model == "gpt55"
    assert os.environ["APEXA_ACTIVE_PROFILE"] == "anl"
    assert "profile: anl" in capsys.readouterr().out


def test_web_keeps_pinned_endpoint_and_does_not_autoselect(startup, monkeypatch):
    import os
    monkeypatch.setenv("APEXA_LLM_PRESET", "custom")
    monkeypatch.setenv("APEXA_LLM_MODE", "proxy")
    monkeypatch.setenv("APEXA_LLM_BASE_URL", "https://example.invalid/v1")
    def unexpected():
        pytest.fail("a pinned endpoint must not trigger profile selection or network probing")
    monkeypatch.setattr(profiles, "resolve_active", unexpected)
    asyncio.run(web.initialize_mcp_client())
    assert startup[1][1] == "proxy"
    assert os.environ["APEXA_LLM_BASE_URL"] == "https://example.invalid/v1"
    assert os.environ["APEXA_LLM_PRESET"] == "custom"


@pytest.fixture
def public_client(monkeypatch):
    for key in ("APEXA_WEB_TOKEN", "APEXA_DATA_ROOTS", "APEXA_WEB_HOSTS", "APEXA_WEB_MODE"):
        monkeypatch.delenv(key, raising=False)
    return TestClient(web.app)  # no lifespan / MCP initialization


def test_production_html_logo_is_served_from_a_different_cwd(public_client, monkeypatch, tmp_path):
    # A Vite-only test missed this: /assets was mounted, but /apexa-mark.svg was not.
    expected = Path(web.__file__).resolve().parent / "frontend/dist/apexa-mark.svg"
    monkeypatch.chdir(tmp_path)
    page = public_client.get("/")
    assert page.status_code == 200 and 'href="/apexa-mark.svg"' in page.text
    mark = public_client.get("/apexa-mark.svg")
    assert mark.status_code == 200 and mark.content == expected.read_bytes()
    assert mark.headers["content-type"].startswith("image/svg+xml")
    assert mark.headers["cache-control"] == "no-cache"
    assert public_client.get("/favicon.svg").status_code == 200
    # A named public asset route must not expose arbitrary repository files.
    assert public_client.get("/servers.config").status_code == 404


def test_missing_mark_is_a_404_not_an_html_fallback(public_client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_frontend_dist", tmp_path)
    response = public_client.get("/apexa-mark.svg")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_production_app_serves_connection_settings_without_starting_models(public_client, monkeypatch, tmp_path):
    monkeypatch.setenv("APEXA_HOME", str(tmp_path))
    response = public_client.get("/api/connection")
    assert response.status_code == 200
    settings = response.json()
    assert settings["saved"] is None
    assert {"preset", "base_url", "model", "username"} <= settings["active"].keys()
    argo = next(preset for preset in settings["presets"] if preset["id"] == "argo")
    assert "gpt56sol" in argo["model_suggestions"]
    assert not (tmp_path / "connection.json").exists()
