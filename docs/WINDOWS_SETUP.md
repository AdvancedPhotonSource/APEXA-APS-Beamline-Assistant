# APEXA on Windows

**Short version:** APEXA installs and runs fully on native Windows. `uv sync`
pulls everything from prebuilt wheels — **no Visual Studio / MSVC / compiler
needed**. The one thing that does not run *natively* on Windows is **local MIDAS
compute** (the C/CUDA analysis stack has no Windows wheels); you get it either via
**WSL2** or, the usual beamline pattern, by **SSH-routing MIDAS to a Linux analysis
host**. Everything else — CLI, Web UI, Desktop UI, Gradio, RAG/knowledge base, the
LLM transports, remote execution — is first-class on Windows.

This was verified against `uv.lock`: the Windows base set is 210 packages, **0** of
which lack a Windows wheel; the only sdist-only packages are pure-Python
(`asciitree`, `proxy-tools`). Every extra (`alcf`, `extra`, `mp`) is also wheel-clean
on Windows, and `--extra midas` resolves to **0 packages** on Windows (the native
stack is `sys_platform`-excluded, so it can never trigger a source build).

---

## 1. Prerequisites

| Need | Why | Install |
|---|---|---|
| **uv** | package manager / runner | `winget install --id=astral-sh.uv -e` then reopen the terminal |
| **WebView2 Runtime** | Desktop UI only (pywebview's Chromium backend) | Preinstalled on Win11 and current Win10. If missing: `winget install --id=Microsoft.EdgeWebView2Runtime -e` |
| Node.js LTS | **only** if you need to rebuild the React frontend | `winget install --id=OpenJS.NodeJS.LTS -e` — usually unnecessary: `frontend/dist` is committed, so the Web/Desktop UI run without Node |

You do **not** need a C/C++ compiler, CMake, or CUDA for the base install.

## 2. Install

```powershell
git clone <repo-url>
cd beamline-assistant-dev

# Guided config (Windows twin of setup_user.sh): writes .env, probes the network,
# and optionally configures SSH-routing to a Linux MIDAS host.
powershell -ExecutionPolicy Bypass -File .\docs\setup_user.ps1
# ...or do it by hand instead:  copy .env.template .env   (then edit ANL_USERNAME, ARGO_MODEL)

uv sync                        # base install — wheels only, ~seconds
```

> **`setup_user.sh` is bash and does not run on native Windows** — use
> `docs\setup_user.ps1` (above). It is the Windows equivalent: same `.env` output,
> same network probe, but offers SSH-routing to a Linux MIDAS host instead of
> detecting a native `APEXA_MIDAS_BIN` (there is none on Windows). If you are in
> WSL2 or Git-Bash, you can run the original `bash docs/setup_user.sh` there.

That's the whole install. `pywebview` automatically pulls `pythonnet` + `clr-loader`
on Windows (they're in the lockfile under `sys_platform == 'win32'`), so the Desktop
UI works with no extra step.

## 3. Launch (Windows launchers are included)

| Interface | Double-click / run |
|---|---|
| CLI | `start_beamline_assistant.bat` (or `.ps1`) |
| Web UI + viewer | `start_web_viewer.bat` (or `.ps1`) → http://localhost:8001 |
| Desktop UI (native window) | `start_desktop_ui.bat` |
| Gradio UI | `start_gradio_ui.bat` (or `.ps1`) → http://localhost:7860 |

Each launcher `cd`s to the repo, uses `uv run` when `uv` is on PATH (falling back to
system Python otherwise), and keeps the window open on error so you can read it.

## 4. Feature availability on Windows

| Feature | Native Windows | Notes |
|---|---|---|
| CLI / Web / Desktop / Gradio UIs | ✅ | all four launchers included |
| LLM transports (Argo `/chat/`, argo-proxy) | ✅ | needs ANL network / VPN |
| RAG knowledge base (`query_hedm_knowledge`) | ✅ | offline-clean; stage the embedder cache for `internal` tier |
| File ops, X-ray calcs, parameter lint/diagnose | ✅ | pure-Python |
| **Remote execution / SSH routing** | ✅ | Windows 10+ ships the OpenSSH client; set up key-based SSH (below) |
| EPICS motor control | ✅ | caget/caput over the network |
| **Local MIDAS compute** (calibration, integration, FF/PF/NF, tomo) | ⚠️ not native | `midas-suite` has no Windows wheels → run via **WSL2** or **SSH-route to Linux** (§5) |
| **Forward simulation** (`ForwardSimulationCompressed`, `simulateNF`) | ⚠️ not native | C binaries, built on Linux → WSL2 or a Linux clone |

So on Windows you lose **no APEXA feature** — you only move the MIDAS number-crunching
to WSL2 or a Linux host, which is exactly the normal beamline topology (APEXA on one
host, MIDAS where the data lives).

## 5. MIDAS from a Windows APEXA host — two supported paths

### Path A — SSH-route to a Linux analysis host (recommended at the beamline)
APEXA already routes data-dependent MIDAS tools to the host that owns the data over
SSH (see the Remote Execution section in `.env.template` and `CLAUDE.md`). From
Windows this is the cleanest option — the heavy compute stays on Linux where MIDAS
is gate-checked.

```powershell
# in .env
APEXA_ANALYSIS_HOST=copland
APEXA_REMOTE_DATA_ROOTS=/gdata
APEXA_REMOTE_MIDAS_ACTIVATE=conda deactivate && export PATH=/home/beams12/S1IDUSER/opt/envs/midas/bin:$PATH
```
Set up key-based SSH from Windows (OpenSSH client is built in):
```powershell
ssh-keygen -t ed25519          # if you don't have a key
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh <user>@copland "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
ssh <user>@copland "bash -lc 'which midas-pipeline'"   # must succeed
```

### Path B — WSL2 (run MIDAS locally on the same machine)
Install WSL2 (`wsl --install`), set up MIDAS inside the Linux environment (pip
`midas-suite`, or a built clone for forward sim), and either run APEXA itself inside
WSL2, or keep APEXA on Windows and point it at the WSL2 host over SSH as in Path A.

**`uv sync --extra midas` on Windows does nothing** (the native stack is excluded) —
that is intentional. Do not expect it to install MIDAS on Windows; use WSL2 or SSH.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `uv not found` | `winget install --id=astral-sh.uv -e`, then **reopen** the terminal |
| Desktop UI window is blank / won't open | install WebView2 Runtime (§1) |
| Web UI 404 / blank | `frontend\dist` missing — re-pull (dist is committed) or install Node and let the launcher rebuild |
| SSH tool returns rc=255 + `ssh-copy-id` hint | key-based SSH isn't set up to the remote host — see §5 Path A |
| A MIDAS calibration/integration tool says data is remote and refuses | expected fail-closed guard — route via `APEXA_ANALYSIS_HOST` / `remote_hosts.json` so it runs where the data lives |

See also: `docs/MIDAS_SETUP.md` (route-to-native + the interpreter-world map) and
`docs/OFFLINE_DEPLOYMENT.md` (air-gapped hosts).
