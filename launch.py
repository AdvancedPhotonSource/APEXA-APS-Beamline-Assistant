#!/usr/bin/env python3
"""Cross-platform APEXA launcher (Windows / macOS / Linux).

Does what start_beamline_assistant.sh does, but in pure Python so it runs where
bash can't (Windows cmd/PowerShell). Parses servers.config and starts the CLI.

    uv run python launch.py          # recommended (uv provides the venv)
    python launch.py                 # if deps are already installed

On Windows you can also double-click start_beamline_assistant.bat or run
start_beamline_assistant.ps1, both of which call this.
"""
import os
import re
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

cfg = ROOT / "servers.config"
if not cfg.exists():
    sys.exit("Error: servers.config not found")

if not (ROOT / ".env").exists():
    print("No .env file. Use saved Connection settings or configure an LLM profile "
          "(see docs/CONNECTION_SETUP.md). ANL_USERNAME is needed only for Argo.", file=sys.stderr)

server_args = []
for line in cfg.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    m = re.match(r"^([^:]+):(.+)$", line)
    if not m:
        continue
    name, path = m.group(1).strip(), m.group(2).strip()
    if (ROOT / path).exists():
        server_args.append(f"{name}:{path}")
    else:
        print(f"  {name}: not found ({path})", file=sys.stderr)

if not server_args:
    sys.exit("Error: no valid servers found in servers.config")

os.environ.setdefault("NUMEXPR_MAX_THREADS", "10")
os.environ.pop("VIRTUAL_ENV", None)   # avoid stale-venv warnings from another project

# Launch the client with the current interpreter (the uv venv under `uv run`);
# the client spawns each MCP server with this same interpreter.
sys.exit(subprocess.call([sys.executable, "argo_mcp_client.py", *server_args]))
