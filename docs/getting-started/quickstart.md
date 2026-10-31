# APEXA - Quick Start

## Setup (2 minutes)

### 1. Run Setup Script
```bash
# From the repository root
./setup_user.sh
```

The script will ask for:
- Your ANL username
- Preferred AI model (setup script default: gpt55; model aliases can change with the Argo catalog)
- A pip MIDAS environment, detected from `midas-pipeline` or entered manually
- An optional MIDAS repo clone for forward simulation and legacy tools
- Optional Materials Project API key

### 2. Start APEXA
```bash
./start_beamline_assistant.sh
```

With the current `servers.config`, startup should report:
```
  core: 13 tools
  midas: 60 tools
  motor: 13 tools
  gsas2: 26 tools
  tomo: 2 tools

  APEXA - Advanced Photon EXperiment Assistant
  Model: gpt55 (PROD)  |  114 tools  |  Servers: core, midas, motor, gsas2, tomo
```
The connected server/tool counts depend on `servers.config`; each engine also
depends on its own runtime and host readiness.

### 3. Start Analyzing
```
APEXA> list files in /data/experiment
APEXA> calibrate the CeO2 image
APEXA> integrate the .tif file
APEXA> show me the lineout
```

## Requirements

- **Python:** 3.13+ (with `uv` package manager)
- **Network:** ANL network or VPN
- **MIDAS:** pip `midas-suite` environment, detected from `midas-pipeline` or configured with `APEXA_MIDAS_BIN`

## MIDAS setup

The setup script looks for a pip `midas-suite` environment on `PATH` and in
common APS and Conda locations. It writes `APEXA_MIDAS_BIN` to the environment's
`bin/` directory when it finds `midas-pipeline`. If no native environment is
available, set it manually or install the optional local fallback:

```bash
uv sync --extra midas
```

`MIDAS_PATH` points to a MIDAS repository clone and is only needed for forward
simulation and legacy C tools. See [MIDAS setup](../MIDAS_SETUP.md) for details.

## Manual Configuration

Edit `.env` directly:

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
Fix: Set `MIDAS_PATH` in `.env`

**Authentication Error:**
```
Error calling Argo API: 401 Unauthorized
```
Fix: Check `ANL_USERNAME` in `.env` and network connection

## Multi-User Environments

Each user should:
1. Run `./setup_user.sh` with their own ANL username
2. Keep `.env` private (`chmod 600 .env`)

---

See the [user manual](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/USER_MANUAL.md) for complete documentation.
