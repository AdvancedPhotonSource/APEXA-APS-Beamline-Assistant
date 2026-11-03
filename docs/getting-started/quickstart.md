# APEXA - Quick Start

## Setup (2 minutes)

### 1. Run Setup Script
```bash
# From the repository root
./setup_user.sh
```

The script asks for an access type (facility, personal API key, or local/self-hosted),
then its provider/host, model and credential. ALCF asks for the cluster and model;
Ollama asks for the model family and installed tag. Only Argo requires an ANL
username. API keys use the OS keyring;
selected connection settings use `~/.apexa/connection.json`. Use `--options` to
review mechanisms without an account, or `--mcp-config codex` / `--mcp-config claude`
to prepare subscription-client tool access. Subscriptions run in those official
clients; APEXA WebUI subscription login remains unimplemented. See
[connection setup](../CONNECTION_SETUP.md).

Optional prompts cover the network tier and native MIDAS paths; existing values
are preserved. Use `./setup_user.sh --connection-only` to skip runtime prompts.
Windows uses `.\docs\setup_user.ps1` with the same wizard.

### 2. Start APEXA
```bash
./start_beamline_assistant.sh
```

The standard configuration declares seven MCP servers: core, MIDAS, motor,
alignment, GSAS-II, tomography and synthesis. The connected tool counts depend
on the configured servers; each scientific engine needs its own runtime and
host qualification. See the [source inventory](../analysis-tool-inventory.json).

### 3. Start Analyzing
```
APEXA> list files in /data/experiment
APEXA> calibrate the CeO2 image
APEXA> integrate the .tif file
APEXA> show me the lineout
```

## Requirements

- **Python:** 3.13+ (with `uv` package manager)
- **Network:** Reachability to the selected provider; ANL/VPN is required only for Argo
- **MIDAS:** pip `midas-suite` environment, detected from `midas-pipeline` or configured with `APEXA_MIDAS_BIN`

## MIDAS setup

The setup script preserves an existing native MIDAS path, or detects
`midas-pipeline` on `PATH` for a new setup. It writes `APEXA_MIDAS_BIN` to the environment's
`bin/` directory when it finds `midas-pipeline`. If no native environment is
available, set it manually or install the optional local fallback:

```bash
uv sync --extra midas
```

`MIDAS_PATH` points to a MIDAS repository clone and is only needed for forward
simulation and legacy C tools. See [MIDAS setup](../MIDAS_SETUP.md) for details.

## Manual Configuration

The wizard and WebUI share saved connection settings. For an explicit
environment-managed Argo deployment, edit `.env` directly:

```bash
cp .env.template .env
nano .env
```

```bash
ANL_USERNAME=your_anl_username
ARGO_MODEL=gpt55                     # check `models` for current gateway aliases
APEXA_MIDAS_BIN=/path/to/midas-suite/bin
# MIDAS_PATH=~/Git/MIDAS             # only for forward simulation / legacy tools
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `models` | Show available AI models |
| `model <name>` | Switch AI model |
| `timing` | Toggle API response time display |
| `tools` | List all analysis tools |
| `servers` | Show connected servers |
| `session new [name]` | Archive current conversation, start a fresh one |
| `session save [name]` | Save a named (or unnamed) snapshot |
| `session load <name>` / `session switch <name>` | Restore a session and continue it (new turns append) |
| `session resume` | Reload the most-recent session (auto-saved each turn) |
| `session list` / `session summary` | List sessions / current session info |
| `ls <path>` | List directory |
| `clear` | Clear conversation history |
| `help` | Show help |
| `quit` | Exit (auto-saves; `session resume` to return) |

## Natural Language Examples

Just ask in plain English:

```
APEXA> calibrate the CeO2 image in test1
APEXA> integrate CeO2_000001.tif using refined parameters
APEXA> show me the lineout for CeO2
APEXA> convert 61.332 keV to wavelength
APEXA> what is the d-spacing for Fe (110)?
APEXA> run FF-HEDM workflow on /data/experiment
APEXA> explain what beam center calibration does
APEXA> validate my parameter file for ff-hedm
APEXA> compute stress from Grains.csv for copper sample
APEXA> what are the elastic constants for titanium?
```

## Switching AI Models

```
APEXA> models
[Shows available models]

APEXA> model claudesonnet55
Switched to: claudesonnet55
```

## Troubleshooting

**MIDAS Not Found:**
```
WARNING: MIDAS not found
```
Fix: Set `APEXA_MIDAS_BIN` to the qualified native MIDAS environment; see
[MIDAS setup](../MIDAS_SETUP.md). `MIDAS_PATH` is an optional repository clone.

**Authentication Error:**
```
Error calling Argo API: 401 Unauthorized
```
Fix: Check the selected Argo username in connection settings (or the explicit
`ANL_USERNAME` environment override) and ANL/VPN reachability.

## Multi-User Environments

Each user should:
1. Run setup under their own OS identity, with their own provider credential.
2. Use a separate APEXA instance and private state directory; see
   [shared deployment](../SHARED_DEPLOYMENT.md).

---

See the [user manual](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/USER_MANUAL.md) for complete documentation.
