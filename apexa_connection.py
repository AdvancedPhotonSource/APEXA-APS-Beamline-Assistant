"""Portable, single-operator connection setup. No inference on read or save.

Non-secret settings are atomic JSON; credentials are bound to an endpoint in
the OS keyring. Saving takes effect on restart, never midway through a run.
Verification uses an isolated HTTP client, not process-wide provider variables.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apexa_llm_endpoints import PRESETS, SUPPORTED_WIRES, EndpointRejected
import apexa_llm_endpoints as endpoints

_lock = threading.RLock()
_applied_revision: str | None = None
_PINNED = ("APEXA_PROFILE", "APEXA_LLM_PRESET", "APEXA_LLM_BASE_URL", "APEXA_LLM_MODE")


def settings_path() -> Path:
    return Path(os.environ.get("APEXA_HOME", str(Path.home() / ".apexa"))) / "connection.json"


def is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    preset: str
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    username: str = Field(default="", max_length=200)

    @field_validator("preset")
    @classmethod
    def supported(cls, value):
        if value not in PRESETS:
            raise ValueError("Unknown connection preset")
        ep = PRESETS[value]()
        if not ep.tool_calling or ep.wire not in SUPPORTED_WIRES:
            raise ValueError("This transport does not support APEXA tool calls")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        try:
            u = urlsplit(value)
            port = u.port
        except ValueError:
            raise ValueError("Invalid endpoint URL") from None
        if (not u.hostname or u.username or u.password or u.query or u.fragment
                or u.scheme not in ("http", "https") or port == 0
                or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError("Use an HTTP(S) base URL without credentials, query or fragment")
        if u.scheme == "http" and not is_loopback(value):
            raise ValueError("Remote endpoints require HTTPS; HTTP is only allowed on loopback")
        return value.rstrip("/")

    @field_validator("model", "username")
    @classmethod
    def single_line(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Use a single-line value")
        return value

    @model_validator(mode="after")
    def model_and_identity(self):
        from apexa_model_policy import assert_allowed
        assert_allowed(self.model, role="LLM")
        if self.preset == "argo" and not self.username:
            raise ValueError("Argo requires an ANL username")
        # Provider presets must not carry a vendor key to an unrelated host.
        # Institutional gateways use the explicit custom preset.
        if self.preset != "custom":
            expected = PRESETS[self.preset]().base_url.rstrip("/")
            if self.base_url != expected:
                raise ValueError("Use the custom preset for a different endpoint URL")
        return self

    def keyring_account(self, revision: str = "") -> str:
        digest = hashlib.sha256(f"{self.preset}\n{self.base_url}".encode()).hexdigest()
        return "connection:" + digest + (":" + revision if revision else "")


def load_saved() -> dict | None:
    path = settings_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        config = ConnectionConfig.model_validate(data["config"])
        if data["schema_version"] != 1 or not isinstance(data["revision"], str):
            raise ValueError("Invalid settings version")
        return {"schema_version": 1, "revision": data["revision"], "config": config.model_dump()}
    except (ValueError, KeyError, TypeError):
        raise EndpointRejected("Saved connection settings are invalid; move connection.json out of APEXA_HOME, then reopen Connection settings") from None


def environment_pinned() -> bool:
    return _applied_revision is None and any(os.environ.get(k, "").strip() for k in _PINNED)


def apply_saved() -> bool:
    """Called before profile auto-selection. Explicit site configuration wins."""
    global _applied_revision
    if environment_pinned():
        return False
    saved = load_saved()
    if not saved:
        return False
    config = ConnectionConfig.model_validate(saved["config"])
    os.environ.update(APEXA_LLM_PRESET=config.preset, APEXA_LLM_BASE_URL=config.base_url,
        APEXA_LLM_MODE="proxy", APEXA_LLM_STRICT="1", ARGO_MODEL=config.model,
        APEXA_LLM_KEYRING_ACCOUNT=config.keyring_account(saved["revision"]))
    if config.preset == "argo":
        os.environ["ANL_USERNAME"] = config.username
    _applied_revision = saved["revision"]
    return True


def save_config(config: ConnectionConfig, api_key: str, expected_revision: str | None) -> dict:
    """Compare revision and atomically replace non-secret settings.

    One server owns setup; separate processes should use distinct APEXA_HOME.
    A keyring failure never falls back to plaintext storage.
    """
    with _lock:
        old = load_saved()
        if (old["revision"] if old else None) != expected_revision:
            raise ValueError("Connection settings changed in another window. Reload before saving.")
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": 1, "revision": secrets.token_hex(16), "config": config.model_dump()}
        if not api_key and old:
            previous = ConnectionConfig.model_validate(old["config"])
            if previous.keyring_account() == config.keyring_account():
                api_key = endpoints.keyring_get(previous.keyring_account(old["revision"]))
        # Version the key too: saving cannot rotate the running instance's key.
        account = config.keyring_account(data["revision"])
        if api_key and not endpoints.keyring_set(account, api_key):
            raise ValueError("No usable OS keyring. Supply APEXA_LLM_API_KEY in the server environment instead.")
        fd, name = tempfile.mkstemp(prefix=".connection-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
        return {"revision": data["revision"], "restart_required": True,
                "environment_override": environment_pinned()}


def describe() -> dict:
    from apexa_llm_endpoints import ARGO_SETUP_MODELS
    error = None
    try:
        saved = load_saved()
    except EndpointRejected as exc:
        saved, error = None, str(exc)
    # Do not resolve tokens, call providers, or return any credentials here.
    preset = os.environ.get("APEXA_LLM_PRESET", "argo")
    ep = PRESETS.get(preset, PRESETS["argo"])()
    active = {"preset": preset, "base_url": os.environ.get("APEXA_LLM_BASE_URL") or ep.base_url,
              "model": os.environ.get("ARGO_MODEL", ""), "username": os.environ.get("ANL_USERNAME", "") if preset == "argo" else ""}
    return {"saved": saved, "active": active, "error": error,
            "environment_override": environment_pinned(),
            "restart_required": bool(saved and saved["revision"] != _applied_revision),
            "presets": [{"id": name, "base_url": cls().base_url, "wire": cls().wire,
                         "model_suggestions": list(ARGO_SETUP_MODELS) if name == "argo" else []}
                        for name, cls in PRESETS.items()
                        if cls().tool_calling and cls().wire in SUPPORTED_WIRES]}


def check_network(config: ConnectionConfig) -> None:
    from apexa_network import tier, has_web
    if tier() == "data" and not is_loopback(config.base_url):
        raise ValueError("APEXA_NETWORK=data permits verification only against a loopback model server")
    if config.preset in {"openai", "anthropic", "gemini"} and not has_web():
        raise ValueError("This provider needs APEXA_NETWORK=web and an environment that permits public access")


def verification_key(config: ConnectionConfig, supplied: str) -> str:
    if supplied:
        return supplied
    if config.preset == "argo":
        return config.username
    if config.preset == "ollama":
        return "ollama"
    saved = load_saved()
    if saved:
        previous = ConnectionConfig.model_validate(saved["config"])
        if previous.keyring_account() == config.keyring_account():
            stored = endpoints.keyring_get(config.keyring_account(saved["revision"]))
            if stored:
                return stored
    # Reuse environment/legacy credentials only for the exact active endpoint.
    # A draft URL must never inherit a key for another host.
    ep = endpoints.active_endpoint()
    if ep.name == config.preset and ep.base_url.rstrip("/") == config.base_url:
        return ep.resolve_key()
    raise ValueError("Enter an API key for this endpoint, or save one in its OS keyring entry")


async def verify(config: ConnectionConfig, api_key: str = "", *, transport=None) -> dict:
    """Two synthetic requests prove native tool-result round-trip, not science."""
    check_network(config)
    key = await asyncio.to_thread(verification_key, config, api_key)
    ep = PRESETS[config.preset]()
    nonce = secrets.token_hex(12)  # cannot be guessed from the initial prompt
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    prompt = "Call apexa_connection_probe once. Then repeat its returned receipt exactly."
    headers = {"Authorization": "Bearer " + key}
    if ep.wire == "anthropic":
        headers = {("Authorization" if ep.auth_style == "bearer" else "x-api-key"):
                   ("Bearer " + key if ep.auth_style == "bearer" else key), "anthropic-version": "2023-06-01"}
        route = "/v1/messages"
        body = {"model": config.model, "max_tokens": 256,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"name": "apexa_connection_probe", "description": "Return a connection receipt", "input_schema": schema}],
                "tool_choice": {"type": "tool", "name": "apexa_connection_probe"}}
    else:
        route = "/chat/completions"
        body = {"model": config.model, "max_completion_tokens": 256,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"type": "function", "function": {"name": "apexa_connection_probe", "description": "Return a connection receipt", "parameters": schema}}],
                "tool_choice": {"type": "function", "function": {"name": "apexa_connection_probe"}}}
    # No redirects: a gateway cannot forward the supplied credential elsewhere.
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport) as client:
        async def post():
            response = await client.post(config.base_url + route, headers=headers, json=body)
            if not response.is_success:
                raise ValueError(f"Provider returned HTTP {response.status_code}; check the endpoint, model and credential")
            return response.json()
        async with asyncio.timeout(45):
            first = await post()
            if ep.wire == "anthropic":
                calls = [c for c in first.get("content", []) if c.get("type") == "tool_use"]
                if len(calls) != 1 or calls[0].get("name") != "apexa_connection_probe" or not calls[0].get("id") or calls[0].get("input") != {}:
                    raise ValueError("Provider did not return the requested structured tool call")
                body["messages"] += [{"role": "assistant", "content": first["content"]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": calls[0]["id"], "content": nonce}]}]
                body["tool_choice"] = {"type": "auto"}
                second = await post()
                answer = "".join(c.get("text", "") for c in second.get("content", []) if c.get("type") == "text")
            else:
                msg = first["choices"][0]["message"]
                calls = msg.get("tool_calls", [])
                if len(calls) != 1 or calls[0].get("function", {}).get("name") != "apexa_connection_probe" or not calls[0].get("id") or json.loads(calls[0]["function"].get("arguments", "null")) != {}:
                    raise ValueError("Provider did not return the requested structured tool call")
                body["messages"] += [{"role": "assistant", "content": msg.get("content"), "tool_calls": calls},
                    {"role": "tool", "tool_call_id": calls[0]["id"], "content": nonce}]
                body["tool_choice"] = "none"
                second = await post()
                answer = second["choices"][0]["message"].get("content") or ""
            if nonce not in answer:
                raise ValueError("Provider did not use the structured tool result")
    return {"ok": True, "model": config.model, "checks": ["structured_tool_call", "structured_tool_result"],
            "scope": "Synthetic connection check only; scientific engines were not run"}
