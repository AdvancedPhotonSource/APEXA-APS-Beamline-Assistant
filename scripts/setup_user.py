#!/usr/bin/env python3
"""Interactive setup shared by shell and PowerShell. Makes no provider calls."""
from __future__ import annotations

import argparse
import getpass
import io
import json
import os
from pathlib import Path
import shutil
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values, load_dotenv
from dotenv.parser import parse_stream

import apexa_connection as connection
from apexa_llm_endpoints import PRESETS, EndpointRejected

CHOICES = (
    ("argo", "ANL Argo (ANL username, internal network/VPN)"),
    ("openai", "OpenAI API (separately billed API key)"),
    ("anthropic", "Anthropic API (separately billed API key)"),
    ("gemini", "Gemini API (API key)"),
    ("custom", "University/facility gateway (OpenAI-compatible HTTPS endpoint)"),
    ("ollama", "Local Ollama (tool-capable installed model, no API key)"),
    ("alcf-sophia", "ALCF Sophia (existing authorized Globus login/helper)"),
    ("alcf-minerva", "ALCF Minerva (existing authorized Globus login/helper)"),
    ("mag", "AmSC Model Access Gateway (project token)"),
)
# Old selections and unscoped credentials must not override a newly saved endpoint.
OVERRIDES = (*connection._PINNED, "APEXA_LLM_KEYRING_ACCOUNT",
             "APEXA_LLM_API_KEY", "APEXA_LLM_TOKEN_CMD")

# External clients receive analysis tools, not the motor/alignment servers.
CLIENT_SERVERS = ("core", "midas", "gsas2", "tomo", "synthesis")
CLIENT_ENV = {"APEXA_KB_PREWARM": "0", "APEXA_ALLOW_UNSANDBOXED_COMMANDS": "0"}


def client_servers(repo: Path) -> dict[str, Path]:
    """Use enabled analysis entries from servers.config; never scan arbitrary code."""
    root = repo.resolve()
    servers = {}
    for line in (root / "servers.config").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, source = (part.strip() for part in line.split(":", 1))
        if name not in CLIENT_SERVERS:
            continue
        path = (root / source).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix != ".py":
            raise ValueError(f"Invalid configured analysis server: {name}")
        if name in servers:
            raise ValueError(f"Duplicate configured analysis server: {name}")
        servers[name] = path
    if not servers:
        raise ValueError("No enabled analysis servers in servers.config")
    return servers


def client_config(client: str, repo: Path = ROOT) -> str:
    """Print-only export: no credentials, client configuration writes or login."""
    if client not in {"codex", "claude"}:
        raise ValueError("MCP client must be codex or claude")
    # Keep the venv executable path: resolving its symlink loses venv packages.
    command = sys.executable
    launcher = str(repo.resolve() / "scripts" / "setup_user.py")
    servers = {"apexa-" + name: {"command": command,
               "args": [launcher, "--serve-mcp", name], "env": dict(CLIENT_ENV)}
               for name in client_servers(repo)}
    if client == "claude":
        return json.dumps({"mcpServers": {name: {"type": "stdio", **entry}
                           for name, entry in servers.items()}}, indent=2, ensure_ascii=False)
    blocks = []
    for name, entry in servers.items():
        blocks.append(f"[mcp_servers.{name}]\ncommand = {json.dumps(command, ensure_ascii=False)}\n"
                      f"args = {json.dumps(entry['args'], ensure_ascii=False)}\n"
                      "startup_timeout_sec = 60\n"
                      f"[mcp_servers.{name}.env]\n" + "\n".join(
                          f"{key} = {json.dumps(value)}" for key, value in CLIENT_ENV.items()))
    return "\n\n".join(blocks) + "\n"


def show_options() -> None:
    print("APEXA WebUI / CLI: provider -> model ID -> API key or facility identity")
    for name, label in CHOICES:
        print(f"  {name}: {label}")
    print("\nSubscription clients: official client login -> APEXA tools over local MCP")
    print("  codex: ChatGPT subscription; log in later with codex login")
    print("  claude: Claude subscription; log in later inside Claude Code with /login")
    print("  Export configuration with --mcp-config codex or --mcp-config claude.")
    print("  These clients have their own chat, approval and session UI; this does not")
    print("  select a subscription model in the APEXA WebUI.")
    print("No account needed to view/export options. See docs/CONNECTION_SETUP.md.")


def subscription_instructions(client: str, repo: Path) -> None:
    print(f"\n{client}: APEXA analysis tools in the official subscription client")
    print("Configuration preview only. Nothing was saved and no login was started.")
    if client == "codex":
        print("Later: install Codex, run codex login (headless: codex login --device-auth),")
        print("then merge this TOML into your Codex config.toml without replacing other settings.")
    else:
        print("Later: install Claude Code, sign in with /login, and save this JSON")
        print("outside the repository, e.g. ~/.apexa/integrations/claude-mcp.json.")
        print("Launch with: claude --mcp-config ~/.apexa/integrations/claude-mcp.json")
    print("Use /mcp in that client to check tool registration. No APEXA WebUI/session integration.")
    print("Engine readiness and client approvals still apply. See docs/CONNECTION_SETUP.md.\n")
    print(client_config(client, repo))


def serve_mcp(name: str, repo: Path = ROOT) -> None:
    """Start one configured analysis MCP server in its own process and repo cwd."""
    servers = client_servers(repo)
    if name not in servers:
        raise ValueError("Choose an enabled analysis server; hardware servers are not exported")
    os.chdir(repo)
    load_dotenv(repo / ".env")
    # These apply to this exported analysis process, including when invoked by hand.
    os.environ.update(CLIENT_ENV)
    sys.argv = [str(servers[name])]
    runpy.run_path(str(servers[name]), run_name="__main__")


def env_text(original: str, updates: dict[str, str], removed=()) -> str:
    """Preserve unrelated settings/comments, including quoted multiline values."""
    replaced = set(updates) | set(removed)
    kept = "".join(item.original.string for item in parse_stream(io.StringIO(original))
                   if item.key not in replaced)
    if kept and not kept.endswith("\n"):
        kept += "\n"
    for key, value in updates.items():
        quoted = value.replace("\\", "\\\\").replace("'", "\\'")
        kept += f"{key}='{quoted}'\n"
    return kept


def prepare_env(path: Path, text: str) -> Path:
    """Stage a private replacement before saving the connection; no partial .env."""
    fd, name = tempfile.mkstemp(prefix=".apexa-setup-", suffix=".tmp", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def run_setup(repo: Path = ROOT, *, connection_only=False, ask=None, secret=None) -> int:
    ask = ask or input
    secret = secret or getpass.getpass

    def prompt(label, default=""):
        value = ask(f"  {label}" + (f" [{default}]" if default else "") + ": ").strip()
        return value or default

    print("\nAPEXA setup - LLM connection and optional analysis runtime")
    print("API keys and chat subscriptions are different credentials.")
    print("Subscription options export APEXA tools for Codex or Claude Code.")
    print("Their login/chat stays in the official client; APEXA WebUI uses API connections.")
    print("See docs/CONNECTION_SETUP.md. Setup makes no model requests.\n")

    # Explicit process settings are deployment policy, not wizard-owned files.
    pinned = [key for key in OVERRIDES if os.environ.get(key, "").strip()]
    if pinned:
        print("The shell already sets: " + ", ".join(pinned))
        print("Unset these overrides in this shell before using the setup wizard.")
        print("No settings changed; site-managed environments may keep using their overrides.")
        return 2

    path = repo / ".env"
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    existing = dotenv_values(stream=io.StringIO(original))
    # Match the state directory the server will use after loading its .env.
    if existing.get("APEXA_HOME") and not os.environ.get("APEXA_HOME"):
        os.environ["APEXA_HOME"] = existing["APEXA_HOME"]
    saved = connection.load_saved()
    previous = saved["config"] if saved else {}
    current_preset = existing.get("APEXA_LLM_PRESET") or previous.get("preset") or "argo"
    for index, (_, label) in enumerate(CHOICES, 1):
        print(f"  {index}) {label}")
    print("  10) ChatGPT subscription via Codex (MCP configuration preview)")
    print("  11) Claude subscription via Claude Code (MCP configuration preview)")
    selected = prompt("Provider number or preset name", current_preset)
    external = {"10": "codex", "11": "claude", "codex": "codex", "claude": "claude"}.get(selected)
    if external:
        subscription_instructions(external, repo)
        return 0
    if selected.isdigit() and 1 <= int(selected) <= len(CHOICES):
        selected = CHOICES[int(selected) - 1][0]
    if selected not in dict(CHOICES):
        raise ValueError("Choose a listed API/gateway/local preset, or codex/claude for external subscription clients.")
    endpoint = PRESETS[selected]()
    same = selected == current_preset
    base_url = endpoint.base_url
    if selected == "custom":
        default_url = (existing.get("APEXA_LLM_BASE_URL") or previous.get("base_url", "")) if same else ""
        base_url = prompt("Gateway base URL (including /v1 if required)", default_url)
    username = ""
    if selected == "argo":
        username = prompt("ANL username", existing.get("ANL_USERNAME") or previous.get("username", ""))
    old_model = (existing.get("ARGO_MODEL", "") if existing.get("APEXA_LLM_PRESET")
                 else previous.get("model") or existing.get("ARGO_MODEL", ""))
    default_model = old_model if same else ""
    if selected == "argo" and not default_model:
        default_model = "claudeopus5"
    model = prompt("Exact model/deployment ID (must support tool calls)", default_model)
    config = connection.ConnectionConfig(preset=selected, base_url=base_url, model=model, username=username)

    api_key = ""
    provider_env_key = endpoint.env_key and (os.environ.get(endpoint.env_key) or existing.get(endpoint.env_key))
    if provider_env_key:
        print(f"  Existing {endpoint.env_key} supplies this provider's credential and overrides the keyring.")
    elif endpoint.uses_keyring:
        print("  Key is stored in the OS keyring, never in .env or connection.json.")
        print("  On a headless host without a keyring, leave blank and supply")
        print("  APEXA_LLM_API_KEY in the server environment for this endpoint.")
        api_key = secret("  API key/token (hidden; blank keeps a matching saved key): ").strip()
    elif selected.startswith("alcf-"):
        print("  ALCF requires its Globus helper and prior login; see docs/ALCF_MULTIUSER.md.")

    updates = {}
    removed_runtime = []
    if not connection_only:
        print("\nOptional runtime settings: Enter preserves an existing value; '-' clears it.")
        fields = [("APEXA_NETWORK", "Network tier: web, internal, data")]
        if os.name == "nt":
            fields += [("APEXA_ANALYSIS_HOST", "Linux analysis SSH host (or use WSL2)"),
                       ("APEXA_REMOTE_DATA_ROOTS", "Remote data roots, ':' separated")]
        else:
            fields += [("APEXA_MIDAS_BIN", "Native MIDAS environment bin directory"),
                       ("MIDAS_PATH", "Optional MIDAS repository clone (legacy/forward simulation)")]
        for key, label in fields:
            network_default = "data" if selected == "ollama" else ("web" if selected in {"openai", "anthropic", "gemini", "mag"} else "internal")
            default = existing.get(key) or (network_default if key == "APEXA_NETWORK" else "")
            if key == "APEXA_MIDAS_BIN" and not default:
                executable = shutil.which("midas-pipeline")
                if executable:
                    default = str(Path(executable).resolve().parent)
            value = prompt(label, default)
            if value == "-":
                removed_runtime.append(key)
            elif value:
                if key == "APEXA_NETWORK" and value not in {"web", "internal", "data"}:
                    raise ValueError("Network tier must be web, internal or data")
                updates[key] = str(Path(value).expanduser()) if key in {"APEXA_MIDAS_BIN", "MIDAS_PATH"} else value
                if key in {"APEXA_MIDAS_BIN", "MIDAS_PATH"} and not Path(updates[key]).is_dir():
                    print(f"  Directory does not exist yet: {updates[key]}")

    print(f"\nSelected: {config.preset} / {config.model} at {config.base_url}")
    print(f"Connection settings: {connection.settings_path()}")
    overrides = [key for key in OVERRIDES if key in existing]
    if overrides:
        print("Saving will remove these old .env overrides: " + ", ".join(overrides))
        print("The new connection will use its own credential; old unscoped keys/helpers are not transferred.")
    print("Other .env settings, including existing MIDAS routing, are preserved.")
    if prompt("Save this configuration? y/N", "N").lower() != "y":
        print("Cancelled. No configuration files changed.")
        return 0

    removed = (*OVERRIDES, "ANL_USERNAME", "ARGO_MODEL", *removed_runtime)
    staged = prepare_env(path, env_text(original, updates, removed))
    try:
        connection.save_config(config, api_key, saved["revision"] if saved else None)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    print("Saved. Restart APEXA to use this connection.")
    if endpoint.uses_keyring and not api_key and not provider_env_key:
        print("No new API key was supplied. A matching saved key or server environment credential is required.")
    print("Web UI: .\\start_web_viewer.bat" if os.name == "nt" else "Web UI: ./start_web_viewer.sh")
    print("Use gear > LLM connection > Verify connection for an optional billed tool-call check.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--connection-only", action="store_true", help="Preserve analysis and network settings without prompting")
    mode.add_argument("--options", action="store_true", help="List mechanisms without login or configuration changes")
    mode.add_argument("--mcp-config", choices=("codex", "claude"), help="Print credential-free MCP config for an official subscription client")
    mode.add_argument("--serve-mcp", choices=CLIENT_SERVERS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.options:
            show_options()
            return 0
        if args.mcp_config:
            print(client_config(args.mcp_config))
            return 0
        if args.serve_mcp:
            serve_mcp(args.serve_mcp)
            return 0
        return run_setup(connection_only=args.connection_only)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup interrupted.")
        return 130
    except (ValueError, OSError, EndpointRejected) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
