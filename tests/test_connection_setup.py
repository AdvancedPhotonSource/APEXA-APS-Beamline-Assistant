"""Portable setup and a real HTTP-shaped tool round-trip, without paid calls."""
import json
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apexa_connection as C
import apexa_llm_endpoints as E
from apexa_connection_api import router


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("APEXA_HOME", str(tmp_path))
    monkeypatch.setenv("APEXA_NETWORK", "web")
    for key in (*C._PINNED, "APEXA_LLM_API_KEY", "APEXA_LLM_KEYRING_ACCOUNT", "ANL_USERNAME",
                "APEXA_OFFLINE", "HF_HUB_OFFLINE", "APEXA_LLM_TOKEN_CMD", "ARGO_MODEL",
                *(cls().env_key for cls in E.PRESETS.values() if cls().env_key)):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.setattr(C, "_applied_revision", None)
    keys = {}
    monkeypatch.setattr(E, "keyring_get", lambda account: keys.get(account, ""))
    monkeypatch.setattr(E, "keyring_set", lambda account, key: keys.__setitem__(account, key) is None)
    return keys


def config(preset="custom", **overrides):
    return C.ConnectionConfig(preset=preset, base_url=overrides.pop("base_url", E.PRESETS[preset]().base_url),
                              model="fixture-model", **overrides)


def test_personal_startup_needs_no_anl_username(monkeypatch):
    from argo_mcp_client import APEXAClient
    monkeypatch.setenv("APEXA_SESSION_DIR", str(C.settings_path().parent / "sessions"))
    monkeypatch.setenv("APEXA_LLM_PRESET", "openai")
    monkeypatch.setenv("ARGO_MODEL", "fixture-model")
    client = APEXAClient()
    client._init_profile()
    assert client.anl_username == ""
    assert client.selected_model == "fixture-model"
    assert list(client.available_models) == ["Configured endpoint"]


def test_no_username_credential_fallback_for_personal_endpoint():
    with pytest.raises(E.EndpointRejected):
        E.PRESETS["openai"]().resolve_key("someone")
    assert not E.PRESETS["openai"]().has_credential("someone")
    assert E.PRESETS["argo"]().resolve_key("someone") == "someone"


def test_save_is_atomic_secret_free_and_does_not_switch_active_connection(isolated):
    cfg = config("openai")
    first = C.save_config(cfg, "private-one", None)
    assert "private-one" not in C.settings_path().read_text()
    assert "APEXA_LLM_PRESET" not in os.environ
    assert C.apply_saved()
    old_account = os.environ["APEXA_LLM_KEYRING_ACCOUNT"]
    C.save_config(cfg, "private-two", first["revision"])
    assert isolated[old_account] == "private-one"
    assert E.active_endpoint().resolve_key() == "private-one"
    with pytest.raises(ValueError, match="another window"):
        C.save_config(cfg, "", first["revision"])


def test_keyring_failure_preserves_saved_config(monkeypatch):
    first = C.save_config(config(), "", None)
    before = C.settings_path().read_bytes()
    monkeypatch.setattr(E, "keyring_set", lambda *args: False)
    with pytest.raises(ValueError, match="keyring"):
        C.save_config(config(), "secret", first["revision"])
    assert C.settings_path().read_bytes() == before


def test_explicit_environment_wins_over_saved_connection(monkeypatch):
    C.save_config(config("openai"), "key", None)
    monkeypatch.setenv("APEXA_LLM_PRESET", "argo")
    assert not C.apply_saved()
    assert E.active_endpoint().name == "argo"


def test_profile_switch_clears_old_url_and_scoped_credential(monkeypatch):
    import apexa_profiles as P
    monkeypatch.setenv("APEXA_LLM_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("APEXA_LLM_KEYRING_ACCOUNT", "old-account")
    P.apply(P.load_profiles()["openai-personal"])
    assert E.active_endpoint().base_url == "https://api.openai.com/v1"
    assert "APEXA_LLM_KEYRING_ACCOUNT" not in os.environ


@pytest.mark.parametrize("url", ["http://remote.example/v1", "https://key@host/v1", "https://host/v1?key=secret", "file:///tmp/key"])
def test_unsafe_endpoint_urls_refused(url):
    with pytest.raises(ValueError):
        config(base_url=url)


def test_draft_endpoint_never_inherits_active_secret(monkeypatch):
    monkeypatch.setenv("APEXA_LLM_PRESET", "openai")
    monkeypatch.setenv("APEXA_LLM_API_KEY", "private")
    with pytest.raises(ValueError, match="Enter an API key"):
        C.verification_key(config(base_url="https://other.example/v1"), "")


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["openai", "anthropic", "gemini", "custom"])
async def test_verifies_structured_result_not_transcript_text(preset):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            if preset == "anthropic":
                return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "probe-id", "name": "apexa_connection_probe", "input": {}}]})
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"type": "function", "id": "probe-id", "function": {"name": "apexa_connection_probe", "arguments": "{}"}}]}}]})
        tool = body["messages"][-1]
        if preset == "anthropic":
            assert tool["content"][0]["tool_use_id"] == "probe-id"
            return httpx.Response(200, json={"content": [{"type": "text", "text": tool["content"][0]["content"]}]})
        assert tool["role"] == "tool" and tool["tool_call_id"] == "probe-id"
        return httpx.Response(200, json={"choices": [{"message": {"content": tool["content"]}}]})
    result = await C.verify(config(preset), "fixture-key", transport=httpx.MockTransport(handler))
    assert result["ok"] and len(calls) == 2


@pytest.mark.asyncio
async def test_redirect_not_followed_and_network_policy_respected(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://other.example/v1"})
    with pytest.raises(ValueError, match="HTTP 302"):
        await C.verify(config("openai"), "private", transport=httpx.MockTransport(handler))
    assert len(calls) == 1
    monkeypatch.setenv("APEXA_NETWORK", "data")
    with pytest.raises(ValueError, match="loopback"):
        await C.verify(config("openai"), "private", transport=httpx.MockTransport(handler))
    assert len(calls) == 1


def test_api_never_returns_submitted_secrets():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        bad = client.put("/api/connection", json={"config": {"preset": "nope"}, "api_key": "private-key"})
        assert bad.status_code == 422 and "private-key" not in bad.text
        saved = client.put("/api/connection", json={"config": config("openai").model_dump(), "api_key": "private-key"})
        assert saved.status_code == 200
        assert "private-key" not in client.get("/api/connection").text


def wizard(tmp_path, answers, key="", **kwargs):
    from scripts.setup_user import run_setup
    values = iter(answers)
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        return next(values)
    result = run_setup(tmp_path, ask=ask, secret=lambda _: key, **kwargs)
    assert list(values) == [], "Wizard skipped an expected prompt"
    return result, prompts


@pytest.mark.parametrize("preset", ["openai", "anthropic", "gemini", "mag"])
def test_wizard_selects_personal_provider_without_anl_and_preserves_routing(tmp_path, isolated, preset):
    from dotenv import dotenv_values
    path = tmp_path / ".env"
    path.write_text("# Operator runtime\nAPEXA_MIDAS_BIN='/native/midas/bin'\nMIDAS_PATH='/native/MIDAS'\nMP_API_KEY='fixture-mp'\n")
    result, prompts = wizard(tmp_path, [preset, "fixture-model", "y"], "fixture-secret", connection_only=True)
    assert result == 0
    assert not any("ANL username" in prompt for prompt in prompts)
    assert C.apply_saved()
    assert E.active_endpoint().name == preset
    assert E.active_endpoint().resolve_key() == "fixture-secret"
    assert os.environ["ARGO_MODEL"] == "fixture-model"
    assert dotenv_values(path)["APEXA_MIDAS_BIN"] == "/native/midas/bin"
    assert dotenv_values(path)["MP_API_KEY"] == "fixture-mp"
    assert "fixture-secret" not in path.read_text() + C.settings_path().read_text()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_wizard_migrates_old_dotenv_selection_without_transferring_credentials(tmp_path, isolated):
    from dotenv import dotenv_values
    path = tmp_path / ".env"
    path.write_text("APEXA_LLM_PRESET=custom\nAPEXA_LLM_BASE_URL=https://old.example/v1\nAPEXA_LLM_API_KEY='old-secret'\nAPEXA_LLM_TOKEN_CMD='old-helper'\nARGO_MODEL=old-model\nAPEXA_NETWORK=web\n")
    wizard(tmp_path, ["openai", "fixture-model", "y"], "new-secret", connection_only=True)
    assert dict(dotenv_values(path)) == {"APEXA_NETWORK": "web"}
    assert C.apply_saved()
    assert E.active_endpoint().resolve_key() == "new-secret"
    assert "old-secret" not in isolated.values()


def test_wizard_no_keyring_failure_does_not_overwrite_files(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("MIDAS_PATH=/keep/this\n")
    C.save_config(config("argo", username="operator"), "", None)
    before = C.settings_path().read_bytes()
    monkeypatch.setattr(E, "keyring_set", lambda *args: False)
    with pytest.raises(ValueError, match="keyring"):
        wizard(tmp_path, ["openai", "fixture-model", "y"], "secret", connection_only=True)
    assert path.read_text() == "MIDAS_PATH=/keep/this\n"
    assert C.settings_path().read_bytes() == before
    assert not list(tmp_path.glob(".apexa-setup-*"))


def test_wizard_cancel_and_exported_override_do_not_save(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("MIDAS_PATH=/keep/this\n")
    wizard(tmp_path, ["ollama", "fixture-model", "n"], connection_only=True)
    assert not C.settings_path().exists()
    assert path.read_text() == "MIDAS_PATH=/keep/this\n"
    monkeypatch.setenv("APEXA_LLM_API_KEY", "other-endpoint-secret")
    result, prompts = wizard(tmp_path, [], connection_only=True)
    assert result == 2 and not prompts
    assert not C.settings_path().exists()


def test_wizard_argo_and_ollama_need_no_key(tmp_path):
    wizard(tmp_path, ["argo", "operator", "fixture-model", "y"], connection_only=True)
    assert C.load_saved()["config"]["username"] == "operator"
    wizard(tmp_path, ["6", "local-model", "y"], connection_only=True)
    assert C.load_saved()["config"]["preset"] == "ollama"
    assert C.apply_saved()
    assert E.active_endpoint().resolve_key() == "ollama"


def test_wizard_custom_gateway_and_headless_credential(tmp_path, monkeypatch):
    wizard(tmp_path, ["5", "https://campus.example/v1", "deployment-id", "y"], connection_only=True)
    assert C.apply_saved()
    monkeypatch.setenv("APEXA_LLM_API_KEY", "headless-secret")
    ep = E.active_endpoint()
    assert ep.base_url == "https://campus.example/v1"
    assert ep.resolve_key() == "headless-secret"


def test_wizard_full_setup_keeps_paths_and_writes_network_tier(tmp_path):
    from dotenv import dotenv_values
    path = tmp_path / ".env"
    path.write_text("APEXA_MIDAS_BIN='/existing/native/bin'\nAPEXA_ANALYSIS_HOST=worker\n")
    # Same three optional prompts on Windows (host/roots) and POSIX (bin/clone).
    wizard(tmp_path, ["openai", "fixture-model", "", "", "", "y"], "fixture-key")
    data = dotenv_values(path)
    assert data["APEXA_NETWORK"] == "web"
    assert data["APEXA_MIDAS_BIN"] == "/existing/native/bin"
    assert data["APEXA_ANALYSIS_HOST"] == "worker"


def test_env_updates_preserve_multiline_values_and_quote_special_paths():
    from dotenv import dotenv_values
    import io
    from scripts.setup_user import env_text
    original = "# Site\nexport APEXA_PROFILE='old'\nSITE_VALUE='first\nsecond'\nMIDAS_PATH=old\n"
    special = "C:\\User's data\\MIDAS #1"
    result = env_text(original, {"MIDAS_PATH": special}, ["APEXA_PROFILE"])
    data = dotenv_values(stream=io.StringIO(result))
    assert "APEXA_PROFILE" not in data
    assert data["SITE_VALUE"] == "first\nsecond"
    assert data["MIDAS_PATH"] == special
