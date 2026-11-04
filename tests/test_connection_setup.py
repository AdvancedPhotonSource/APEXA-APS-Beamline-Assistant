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
                assert body["tool_choice"] == {"type": "auto"}
                assert body["max_tokens"] == 4096
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


def test_api_read_failure_returns_actionable_json_without_private_paths(monkeypatch):
    def unreadable():
        raise PermissionError("private settings path")
    monkeypatch.setattr(C, "describe", unreadable)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/connection")
    assert response.status_code == 503
    assert "Check access to APEXA_HOME" in response.json()["detail"]
    assert "private settings path" not in response.text


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


@pytest.mark.parametrize("preset,selection", [("openai", ["2", "1"]), ("anthropic", ["2", "2"]),
                                            ("gemini", ["2", "3"]), ("mag", ["4", "2"])])
def test_wizard_selects_personal_provider_without_anl_and_preserves_routing(tmp_path, isolated, preset, selection):
    from dotenv import dotenv_values
    path = tmp_path / ".env"
    path.write_text("# Operator runtime\nAPEXA_MIDAS_BIN='/native/midas/bin'\nMIDAS_PATH='/native/MIDAS'\nMP_API_KEY='fixture-mp'\n")
    result, prompts = wizard(tmp_path, [*selection, "fixture-model", "y"], "fixture-secret", connection_only=True)
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


@pytest.mark.parametrize("selection,preset,label,model", [
    ("1", "openai", "OpenAI", "gpt-6.1-sol"),
    ("2", "anthropic", "Anthropic", "claude-sonnet-5-5"),
    ("3", "gemini", "Google Gemini", "gemini-3.8-flash"),
])
def test_first_api_provider_is_explicit_and_model_can_be_chosen_without_an_id(tmp_path, capsys, selection, preset, label, model):
    _, prompts = wizard(tmp_path, ["2", "", selection, "", "yes"],
                        "fixture-key", connection_only=True)
    assert prompts[1] == prompts[2] == "  Which provider / host?: "
    assert prompts[3] == "  Model choice [1]: "
    saved = C.load_saved()["config"]
    assert saved["preset"] == preset and saved["model"] == model
    output = capsys.readouterr().out
    assert f"Selected provider: {label}" in output
    assert f"{label} models:" in output
    assert "Paste your API key at the next prompt" in output
    assert "validation error" not in output


@pytest.mark.parametrize("preset,number,expected", [
    ("openai", "2", "gpt-6-astra"),
    ("anthropic", "2", "claude-haiku-5-5"),
    ("anthropic", "3", "claude-opus-5-5"),
    ("gemini", "2", "gemini-3.5-flash-lite"),
])
def test_named_model_number_maps_to_provider_api_id(tmp_path, preset, number, expected):
    wizard(tmp_path, [preset, number, "yes"], "fixture-key", connection_only=True)
    assert C.load_saved()["config"]["model"] == expected


def test_invalid_model_number_retries_and_saved_choice_is_kept(tmp_path, capsys):
    C.save_config(config("anthropic").model_copy(update={"model": "claude-opus-5-5"}), "stored", None)
    _, prompts = wizard(tmp_path, ["", "", "99", "", "yes"], connection_only=True)
    assert prompts[2] == prompts[3] == "  Model choice [3]: "
    assert C.load_saved()["config"]["model"] == "claude-opus-5-5"
    assert "Choose a model number from 1 to 4" in capsys.readouterr().out


def test_anthropic_key_prompt_follows_provider_before_model_selection(tmp_path, isolated, capsys):
    from scripts.setup_user import run_setup
    answers = iter(["2", "2", "4", "", "sk-ant-misplaced-credential", "fixture-api-model", "yes"])
    prompts, key_calls = [], []
    def ask(prompt):
        prompts.append(prompt)
        return next(answers)
    def secret(prompt):
        assert len(prompts) == 2 and prompts[-1] == "  Which provider / host?: "
        key_calls.append(prompt)
        return "correct-hidden-key"
    assert run_setup(tmp_path, connection_only=True, ask=ask, secret=secret) == 0
    assert list(answers) == []
    assert key_calls == ["  Paste your Anthropic API key/token (hidden): "]
    assert C.load_saved()["config"]["model"] == "fixture-api-model"
    output = capsys.readouterr().out
    assert "A model ID is required" in output
    assert "That looks like an API key" in output
    for value in ("sk-ant-misplaced-credential", "correct-hidden-key"):
        assert value not in output + C.settings_path().read_text() + (tmp_path / ".env").read_text()
    assert "sk-ant-misplaced-credential" not in isolated.values()
    assert "correct-hidden-key" in isolated.values()


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


@pytest.mark.parametrize("answer", ["n", "no", "NO"])
def test_wizard_cancel_and_exported_override_do_not_save(tmp_path, monkeypatch, answer):
    path = tmp_path / ".env"
    path.write_text("MIDAS_PATH=/keep/this\n")
    wizard(tmp_path, ["ollama", "fixture-model", answer], connection_only=True)
    assert not C.settings_path().exists()
    assert path.read_text() == "MIDAS_PATH=/keep/this\n"
    monkeypatch.setenv("APEXA_LLM_API_KEY", "other-endpoint-secret")
    result, prompts = wizard(tmp_path, [], connection_only=True)
    assert result == 2 and not prompts
    assert not C.settings_path().exists()


def test_wizard_argo_and_ollama_need_no_key(tmp_path):
    wizard(tmp_path, ["argo", "operator", "fixture-model", "y"], connection_only=True)
    assert C.load_saved()["config"]["username"] == "operator"
    wizard(tmp_path, ["3", "1", "local-model", "y"], connection_only=True)
    assert C.load_saved()["config"]["preset"] == "ollama"
    assert C.apply_saved()
    assert E.active_endpoint().resolve_key() == "ollama"


@pytest.mark.parametrize("access,model,confirmation", [
    ("", "gpt56sol", ""), ("1", "1", "yes"), ("argo", "gpt56sol", "YES"),
])
def test_argo_direct_default_and_model_choice_save_without_provider_submenu(tmp_path, access, model, confirmation):
    _, prompts = wizard(tmp_path, [access, "operator", model, confirmation], connection_only=True)
    assert len(prompts) == 4
    assert "[argo]" in prompts[0]
    assert "Enter/y/yes saves; n/no cancels" in prompts[-1]
    saved = C.load_saved()["config"]
    assert saved["preset"] == "argo" and saved["model"] == "gpt56sol"
    assert saved["username"] == "operator"
    assert saved["base_url"] == E.PRESETS["argo"]().base_url


@pytest.mark.parametrize("source", ["saved", "dotenv"])
def test_argo_keeps_existing_username_and_unlisted_model_on_enter(tmp_path, source):
    if source == "saved":
        C.save_config(config("argo", username="operator").model_copy(update={"model": "existing-deployment"}), "", None)
    else:
        (tmp_path / ".env").write_text("ANL_USERNAME=operator\nARGO_MODEL=existing-deployment\n")
    wizard(tmp_path, ["", "", "", ""], connection_only=True)
    saved = C.load_saved()["config"]
    assert saved["model"] == "existing-deployment" and saved["username"] == "operator"


def test_invalid_confirmation_reprompts_without_writing(tmp_path, capsys):
    _, prompts = wizard(tmp_path, ["argo", "operator", "gpt56sol", "maybe", "no"], connection_only=True)
    assert prompts[-1] == prompts[-2]
    assert "Please enter y/yes" in capsys.readouterr().out
    assert not C.settings_path().exists() and not (tmp_path / ".env").exists()


def test_setup_and_webui_share_recorded_argo_model_suggestions(monkeypatch):
    from argo_mcp_client import APEXAClient
    monkeypatch.setenv("APEXA_SESSION_DIR", str(C.settings_path().parent / "sessions"))
    models = {model for group in APEXAClient().available_models.values() for model in group}
    presets = {preset["id"]: preset for preset in C.describe()["presets"]}
    assert presets["argo"]["model_suggestions"] == list(E.ARGO_SETUP_MODELS)
    assert set(E.ARGO_SETUP_MODELS) <= models
    assert all(not preset["model_suggestions"] for name, preset in presets.items() if name != "argo")


def test_webui_and_terminal_share_named_model_choices():
    presets = {preset["id"]: preset for preset in C.describe()["presets"]}
    for provider in ("argo", "openai", "anthropic", "gemini", "alcf-sophia", "alcf-minerva", "mag"):
        choices = E.setup_model_choices(provider)
        assert presets[provider]["model_choices"] == [{"id": model, "label": label} for model, label in choices]
        assert all(model != label for model, label in choices)
    assert presets["custom"]["model_choices"] == []
    assert presets["ollama"]["model_choices"] == []
    # Same displayed model name uses different IDs on Argo and the public API.
    by_label = lambda provider: {choice["label"]: choice["id"] for choice in presets[provider]["model_choices"]}
    assert by_label("argo")["Claude Sonnet 5.5"] == "claudesonnet55"
    assert by_label("anthropic")["Claude Sonnet 5.5"] == "claude-sonnet-5-5"


@pytest.mark.parametrize("provider,number,label,model", [
    ("argo", "1", "GPT-5.6 Sol", "gpt56sol"),
    ("argo", "6", "Claude Sonnet 5.5", "claudesonnet55"),
    ("alcf-sophia", "3", "Gemma 4 31B", "google/gemma-4-31B-it"),
    ("alcf-minerva", "1", "Inkling (BF16)", "inkling-bf16"),
    ("mag", "2", "Claude Opus", "claude-opus"),
])
def test_named_facility_selection_stores_the_hosts_model_id(tmp_path, capsys, provider, number, label, model):
    identity = ["operator"] if provider == "argo" else []
    wizard(tmp_path, [provider, *identity, number, "yes"], connection_only=True)
    saved = C.load_saved()["config"]
    assert saved["preset"] == provider and saved["model"] == model
    assert f"{number}) {label}" in capsys.readouterr().out


def test_wizard_custom_gateway_and_headless_credential(tmp_path, monkeypatch):
    wizard(tmp_path, ["4", "3", "https://campus.example/v1", "deployment-id", "y"], connection_only=True)
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


@pytest.mark.skipif(os.name == "nt", reason="Native MIDAS paths are configured on POSIX analysis hosts")
@pytest.mark.parametrize("action", ["keep", "clear", "replace"])
def test_stale_clone_is_explained_and_never_changes_native_routing(tmp_path, monkeypatch, capsys, action):
    from pathlib import Path
    from dotenv import dotenv_values
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    native = tmp_path / "qualified" / "bin"
    native.mkdir(parents=True)
    clone = tmp_path / "Git" / "MIDAS"
    (clone / "utils").mkdir(parents=True)
    (clone / "utils" / "AutoCalibrateZarr.py").touch()
    stale = tmp_path / "missing-MIDAS"
    path = tmp_path / ".env"
    path.write_text(f"APEXA_MIDAS_BIN='{native}'\nMIDAS_PATH='{stale}'\nAPEXA_NETWORK=internal\nSITE_LABEL='keep me'\n")
    answer = {"keep": "", "clear": "-", "replace": str(clone)}[action]
    wizard(tmp_path, ["1", "operator", "gpt56sol", "", "", answer, ""])
    data = dotenv_values(path)
    assert data["APEXA_MIDAS_BIN"] == str(native)
    assert data["SITE_LABEL"] == "keep me" and data["APEXA_NETWORK"] == "internal"
    assert data.get("MIDAS_PATH") == {"keep": str(stale), "clear": None, "replace": str(clone)}[action]
    output = capsys.readouterr().out
    assert "MIDAS_PATH directory is missing" in output
    assert "optional for native pip analysis" in output
    assert f"Possible clone (not selected or qualified): {clone}" in output


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable/symlink discovery")
def test_native_discovery_excludes_bundled_env_and_preserves_entrypoint_environment(tmp_path, monkeypatch):
    from pathlib import Path
    from scripts import setup_user as setup
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = tmp_path / "apexa"
    native = tmp_path / "opt" / "envs" / "midas" / "bin"
    bundled = repo / ".venv" / "bin"
    for directory in (native, bundled):
        directory.mkdir(parents=True)
        executable = directory / "midas-pipeline"
        executable.write_text("#!/bin/sh\nexit 99\n")  # Discovery must not run it.
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bundled))
    assert setup.native_midas_candidates(repo) == [native]
    entry = tmp_path / "other-env" / "bin" / "midas-pipeline"
    entry.parent.mkdir(parents=True)
    entry.symlink_to(native / "midas-pipeline")
    monkeypatch.setenv("PATH", os.pathsep.join((str(bundled), str(entry.parent))))
    assert setup.native_midas_candidates(repo) == [native, entry.parent]
    _, prompts = wizard(repo, ["argo", "operator", "gpt56sol", "", "", "", "no"])
    assert "directory [" not in next(prompt for prompt in prompts if "Native MIDAS" in prompt)
    assert not (repo / ".env").exists()  # Neither environment was silently selected.


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


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_subscription_config_exports_real_analysis_launchers_without_credentials(tmp_path, monkeypatch, client):
    import sys
    import tomllib
    from scripts import setup_user as setup
    monkeypatch.setenv("APEXA_LLM_API_KEY", "must-not-be-exported")
    rendered = setup.client_config(client)
    parsed = tomllib.loads(rendered) if client == "codex" else json.loads(rendered)
    servers = parsed["mcp_servers" if client == "codex" else "mcpServers"]
    assert set(servers) == {"apexa-" + name for name in setup.CLIENT_SERVERS}
    assert "must-not-be-exported" not in rendered
    for name, entry in servers.items():
        assert entry["command"] == sys.executable
        assert entry["args"] == [str(setup.ROOT / "scripts" / "setup_user.py"), "--serve-mcp", name.removeprefix("apexa-")]
        assert entry["env"]["APEXA_ALLOW_UNSANDBOXED_COMMANDS"] == "0"
    assert not C.settings_path().exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("access", ["5", "tools"])
@pytest.mark.parametrize("client,choice", [("codex", "1"), ("claude", "2")])
def test_subscription_menu_is_preview_only(tmp_path, capsys, client, choice, access):
    from scripts import setup_user as setup
    (tmp_path / "servers.config").write_text("core:fixture.py\nmotor:motor.py\n")
    (tmp_path / "fixture.py").write_text("# Fixture; must never execute during configuration\n")
    result, prompts = wizard(tmp_path, [access, choice])
    assert result == 0 and len(prompts) == 2
    assert not (tmp_path / ".env").exists()
    assert not C.settings_path().exists()
    assert "Nothing was saved" in capsys.readouterr().out
    assert set(setup.client_servers(tmp_path)) == {"core"}


@pytest.mark.parametrize("cluster,model,preset,expected", [
    ("1", "1", "alcf-sophia", "openai/gpt-oss-120b"),
    ("1", "3", "alcf-sophia", "google/gemma-4-31B-it"),
    ("2", "1", "alcf-minerva", "inkling-bf16"),
    ("2", "new-deployment", "alcf-minerva", "new-deployment"),
])
def test_grouped_alcf_selects_cluster_and_its_model(tmp_path, cluster, model, preset, expected):
    wizard(tmp_path, ["4", "1", cluster, model, "y"], connection_only=True)
    saved = C.load_saved()["config"]
    assert saved["preset"] == preset and saved["model"] == expected
    assert saved["base_url"] == E.PRESETS[preset]().base_url
    assert saved["username"] == ""


@pytest.mark.parametrize("family,tag", [("1", "gemma-custom:latest"), ("2", "gpt-oss:20b"), ("3", "my-tool-model:latest")])
def test_local_model_family_requires_installed_tag(tmp_path, family, tag):
    wizard(tmp_path, ["3", "1", family, tag, "y"], connection_only=True)
    assert C.load_saved()["config"]["model"] == tag
    assert C.load_saved()["config"]["preset"] == "ollama"


def test_saved_provider_remains_the_group_default(tmp_path, capsys):
    C.save_config(config("anthropic"), "stored", None)
    _, prompts = wizard(tmp_path, ["", "", "", "y"], connection_only=True)
    assert "[anthropic]" in prompts[1]
    assert C.load_saved()["config"]["preset"] == "anthropic"
    assert C.load_saved()["config"]["model"] == "fixture-model"
    assert "Current connection: Anthropic. Setup remembers this choice." in capsys.readouterr().out


def test_public_api_setup_explains_preserved_internal_network(tmp_path, monkeypatch, capsys):
    from dotenv import dotenv_values
    monkeypatch.delenv("APEXA_NETWORK")
    path = tmp_path / ".env"
    path.write_text("APEXA_NETWORK=internal\n")
    wizard(tmp_path, ["anthropic", "", "yes"], "fixture-key", connection_only=True)
    assert dotenv_values(path)["APEXA_NETWORK"] == "internal"
    assert "blocks Verify connection" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_anthropic_verifier_explains_billing_without_echoing_provider_body():
    def handler(request):
        return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message":
            "Your credit balance is too low to access the Anthropic API. secret-echo private-prompt"}})
    with pytest.raises(ValueError, match="API account has insufficient credits") as error:
        await C.verify(config("anthropic"), "secret-echo", transport=httpx.MockTransport(handler))
    assert "subscription does not include API credits" in str(error.value)
    assert "secret-echo" not in str(error.value) and "private-prompt" not in str(error.value)


@pytest.mark.asyncio
async def test_anthropic_auto_verification_still_requires_a_real_tool_call():
    def handler(request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Connection verified!"}]})
    with pytest.raises(ValueError, match="structured tool call"):
        await C.verify(config("anthropic"), "fixture-key", transport=httpx.MockTransport(handler))


def test_changing_local_family_does_not_reuse_previous_family_tag(tmp_path):
    C.save_config(config("ollama").model_copy(update={"model": "gpt-oss:20b"}), "", None)
    _, prompts = wizard(tmp_path, ["3", "1", "1", "", "gemma-fixture:latest", "yes"], connection_only=True)
    assert prompts[3] == prompts[4] == "  Exact installed Gemma tag: "
    assert C.load_saved()["config"]["model"] == "gemma-fixture:latest"


def test_mcp_launcher_uses_repo_cwd_and_refuses_unconfigured_servers(tmp_path, monkeypatch):
    from scripts import setup_user as setup
    import sys
    (tmp_path / "servers.config").write_text("core:fixture.py\nmotor:motor.py\n")
    fixture = tmp_path / "fixture.py"
    fixture.write_text("# Fixture\n")
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setattr(sys, "argv", [])
    monkeypatch.setenv("APEXA_ALLOW_UNSANDBOXED_COMMANDS", "1")
    monkeypatch.setenv("APEXA_KB_PREWARM", "1")
    calls = []
    monkeypatch.setattr(setup.runpy, "run_path", lambda path, **kw: calls.append((path, os.getcwd(), kw)))
    setup.serve_mcp("core", tmp_path)
    assert calls == [(str(fixture.resolve()), str(tmp_path), {"run_name": "__main__"})]
    assert os.environ["APEXA_ALLOW_UNSANDBOXED_COMMANDS"] == "0"
    assert os.environ["APEXA_KB_PREWARM"] == "0"
    with pytest.raises(ValueError, match="hardware"):
        setup.serve_mcp("motor", tmp_path)
    (tmp_path / "servers.config").write_text("core:../escape.py\n")
    with pytest.raises(ValueError, match="Invalid configured"):
        setup.client_config("codex", tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("server,expected", [
    ("core", "list_directory"), ("midas", "midas_auto_calibrate"),
    ("gsas2", "powder_refine_submit"), ("tomo", "survey_tomo_stack"),
    ("synthesis", "synth_inspect_series"),
])
async def test_exported_mcp_launcher_completes_real_offline_tool_discovery(tmp_path, monkeypatch, server, expected):
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from scripts import setup_user as setup
    monkeypatch.setenv("APEXA_NETWORK", "data")
    monkeypatch.setenv("APEXA_TRACE_DB", str(tmp_path / "traces.sqlite3"))
    entry = json.loads(setup.client_config("claude"))["mcpServers"]["apexa-" + server]
    params = StdioServerParameters(command=entry["command"], args=entry["args"],
        env={**os.environ, **entry["env"]}, cwd=str(tmp_path))
    async with asyncio.timeout(45):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert any(tool.name == expected for tool in tools.tools)
    assert not C.settings_path().exists()
