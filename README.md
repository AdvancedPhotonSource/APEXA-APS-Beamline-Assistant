<img src="frontend/public/apexa-mark.svg" width="64" height="64" alt="APEXA photon beam and diffraction mark">

# APEXA - Advanced Photon EXperiment Assistant

AI-powered agentic framework for synchrotron diffraction analysis, tomography workflows, and instrument control at Argonne National Laboratory's Advanced Photon Source.

---

## Quick Start

### Command-Line Interface (CLI)
```bash
./setup_user.sh                  # One-time setup
./start_beamline_assistant.sh    # Start APEXA CLI
```

### Gradio UI (experimental alternative)
```bash
./start_gradio_ui.sh             # Opens at http://localhost:7860
```

### Web UI (recommended interactive workspace)
```bash
./start_web_viewer.sh            # serves the React workspace at http://localhost:8001
```

The maintained interactive workspace uses **Chat / Split / Canvas**, with shared
sessions, explicit workflow approvals and results that open automatically.
**Classic light** restores the original white, cobalt and cyan palette; the
header theme button preserves optional dark modes. See the [Canvas guide](docs/CANVAS_WORKSPACE.md)
for tab reuse, comparison, favorites and the exact rebuild/restart steps.

### Windows

**Install the prerequisites once** (PowerShell; `winget` ships with Win 10/11). npm comes
bundled with Node.js, which is only needed for the Web UI:
```powershell
winget install --id=astral-sh.uv -e          # uv (Python package/venv manager)
winget install --id=OpenJS.NodeJS.LTS -e     # Node.js + npm (Web UI only)
winget install --id=Python.Python.3.13 -e    # Python 3.13 (skip if already installed)
```
Close and reopen the terminal, then verify: `uv --version`, `node --version`, `npm --version`.
No winget? Use the installers: https://astral.sh/uv, https://nodejs.org (LTS), https://python.org
(uv alt: `powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"`).

The `.sh` scripts need bash. On Windows use the `.bat`/`.ps1` launchers (or run the
Python directly) — everything else is the same:
```bat
copy .env.template .env          :: then edit ANL_USERNAME / ARGO_MODEL
uv sync                          :: install deps
start_beamline_assistant.bat     :: CLI  (or: uv run python launch.py)
start_gradio_ui.bat              :: Gradio UI
start_web_viewer.bat             :: Web UI (needs Node.js/npm; http://localhost:8001)
```
PowerShell: `.\start_beamline_assistant.ps1` (add `-ExecutionPolicy Bypass` if blocked).
`launch.py` is a cross-platform launcher that works everywhere via `uv run python launch.py`.

> **If `uv sync` fails to build `midas-index`** (`cmake_minimum_required < 3.5 removed`):
> it compiles native code, so it needs **CMake** + a **C/C++ compiler**
> (`winget install Kitware.CMake Microsoft.VisualStudio.2022.BuildTools`, pick "Desktop
> development with C++"), and `set CMAKE_POLICY_VERSION_MINIMUM=3.5` before `uv sync`
> (the `.bat`/`.ps1` launchers already set this). If you don't need MIDAS HEDM tools on
> this box, use the base `uv sync` install without the optional `midas` extra, or run MIDAS
> under WSL2/Linux (below).

> **Note:** the **motor (EPICS)** server needs `caget`/`caput` (Unix) and **MIDAS** needs a
> platform build, so native Windows runs the CLI, agent, and core tools; for full HEDM
> calibration/integration + motor control use **WSL2 (Ubuntu)** or Linux, where the `.sh`
> scripts run as-is. For a core-only setup on Windows, keep just
> `core:beamline_core_server.py` in `servers.config`.

---

## Architecture

```
User (natural language)
         |
    Argo Gateway (GPT-4o / Claude / Gemini)
         |
    OrchestratorAgent (single-loop default)
                             |
                  +----------+----------+
                  |   core (13 tools)   |
                  |   midas (60 tools)  |
                  |   motor (13 tools)  |
                  |   gsas2 (26 tools)  |
                  |   tomo (2 tools)    |
                  +---------------------+
```

### Legacy Specialist Routes
Set `APEXA_AGENT_MODE=legacy` to use these keyword-routed agents. The default
`single` mode uses one persistent reasoning loop with the full configured tool set.

| Agent | Routes when |
|---|---|
| CalibrationAgent | calibrate, CeO2, beam center, Lsd, detector distance |
| AnalysisAgent | integrate, HEDM, grain, GSAS-II, refine, workflow (default) |
| KnowledgeAgent | explain, what is, literature, best practice |
| VisualizationAgent | plot, visualize, lineout, caked, heatmap, show |
| MotorAgent | motor, move, position, EPICS commands |

### MCP Servers (`servers.config`)
| Server | File | Tools |
|---|---|---|
| core | `beamline_core_server.py` | 13 tools: file ops, document reading, local + remote (SSH) shell commands, X-ray calculations |
| midas | `midas_comprehensive_server.py` | 60 tools: FF/NF/PF-HEDM, calibration, integration, TIFF background combining, powder refinement, series contours and peak trajectories, visualization, validation, stress, reports and capability adapters |
| motor | `epics_motor_server.py` | 13 tools: EPICS motor control (read/move/jog/limits) |
| gsas2 | `gsas2_server.py` | 26 tools: tutorial workbench, native operations, pattern/batch refinement, policy optimization, candidate search, evidence, versioned sessions and bounded improvement loops |
| tomo | `tomo_server.py` | 2 tools: tomography stack pre-flight and TomoPy reconstruction, when a compatible native environment is configured |

These are source-declared tool counts from the active `servers.config`; they do not
mean each optional engine or external dependency is installed and ready.

### Agent Skills (`.agents/skills/`)
Workflow skills describe supported procedures and boundaries; engine readiness is host-specific:
- `midas-validate` — parameter-file / dataset validation (run first)
- `midas-calibrate` — the canonical four-stage recipe (`calibration_engine="auto"`), with the native / pip-console / legacy engines as fallbacks
- `midas-integrate` — single (`midas_integrate_2d_to_1d`), **series** (`midas_integrate_series`, many files, one call, per-frame darks), and GPU-streaming integration
- `midas-hedm` / `midas-ff-hedm` — FF/NF/PF-HEDM full pipeline
- `midas-gsasii` — GSAS-II refinement, live analysis pipeline, CIF fetcher
- `gsas2-agentic` — agentic powder refinement with independent, sequential, joint and search jobs; results distinguish execution from scientific qualification
- `tomo-reconstruct` — TomoPy stack pre-flight and reconstruction; requires a configured native tomography environment
- `midas-visualize` — MIDAS viewer scripts for lineouts, caked, grains, 3D spots/PF
- `midas-mask` — build a MIDAS `MaskFile` (uint8 TIFF, 1 = masked) from dead/hot pixels, sentinels, module gaps
- `midas-ffpipeline` — **deprecated**; use `midas-ff-hedm`

### Which GSAS-II path to use
Two powder paths serve different data/workflow needs:

| | `midas:run_gsas_refinement` | `gsas2:powder_refine_submit` |
|---|---|---|
| Input | MIDAS caked `.zarr.zip` / `.MIDAS.zip`; `.xy`/`.xye` use its GSAS-II path | powder patterns supported by the configured GSAS-II interpreter |
| Starting model | you supply CIFs | supply CIFs, **or** retrieve them from COD |
| Strategy | staged MIDAS refinement; optional MAUD backend for caked archives | independent, sequential, joint or bounded phase-search jobs |
| Returns | fit summary and cell; optional MAUD cross-validation | phase fractions, qualification, evidence and decision trace |
| Scope | one observation per call | single patterns, batches and sequential/live series |

The optional MAUD path needs MAUD, MILK and a JDK; the `gsas2` server needs the GSAS-II conda env and the Agentic-GSAS-II
repository; set `APEXA_GSASII_PYTHON` and `APEXA_AGENTIC_GSAS2` if they are not
at their defaults. It runs refinements in a subprocess, so a GSAS-II abort does
not take the agent down, and pins BLAS threads to 1 per job — an unpinned
refinement opens a thread pool per worker and will swamp a beamline host.

See the [powder refinement guide](docs/powder-refinement.md) and
[current demo](DEMO_gsas2_agentic.md) for batch/live submission, evidence and
shadow trials. The current engine emits blocked or provisional scientific
statuses; completion alone does not qualify a result. The guide includes the
manuscript's versioned benchmark scope and architecture figure.

The [GSAS-II canvas workbench](docs/gsas2-workbench.md) covers tutorial discovery,
native operations, policy sweeps and versioned refinement continuation.

### Compute dispatch (`docs/COMPUTE_DISPATCH.md`)
APEXA tiers work to **local CPU / local GPU / remote GPU endpoint** by task size and
available hardware. Large batch/HEDM on a CPU-only beamline host can offload to an
ANL GPU (ALCF Polaris via `--machine polaris`, or a lab GPU node) — set
`APEXA_GPU_MACHINE` or `APEXA_GPU_ENDPOINT`. FF/PF workflows accept
`machine`/`n_nodes`/`shard_gpus`; `midas_integrate_series` accepts `compute_target`.

---

## Example Usage

```
APEXA> calibrate the CeO2 image in test1
  -> midas_auto_calibrate
  Refined BC: (809.55, 700.52), Lsd: 641.95 mm

APEXA> integrate CeO2 in test1 using the refined params
  -> midas_integrate_2d_to_1d
  Output: CeO2_000001.tif.analysis.MIDAS_lineout.xy

APEXA> show me the lineout
  -> run_command (plot_lineout_results.py)
  [viewer window opens]

APEXA> convert 61.332 keV to wavelength
  -> xray_calculate
  Wavelength: 0.20215 Angstroms

APEXA> run FF-HEDM workflow on /data/experiment
  -> run_ff_hedm_full_workflow
  Found 2,347 grains
```

---

## In-Session Commands & Runtime Switches

Type these at the `APEXA>` prompt (CLI):

| Command | What it does |
|---|---|
| `model <name>` | Switch the LLM mid-session (e.g. `model gpt55`). `models` lists all. |
| `models` | Show available models with context/output sizes and notes |
| `session new [name]` | Archive the current conversation and start a fresh one (optionally named) |
| `session save [name]` | Save the conversation (named snapshot, or unnamed) |
| `session load <name>` \| `session switch <name>` | Restore a saved session and continue it (new turns append) |
| `session resume` | Reload the most-recent session (auto-saved after every turn) |
| `session list` \| `session summary` | List sessions / show current session info |
| `timing` | Toggle per-response API timing display |
| `ls [path]` | Quick directory listing |
| `help` | Show all commands |
| `quit` | Exit (session is auto-saved; resume with `session resume`) |

**Which model?** `claudeopus5` is the default — newest Opus, best for multi-step
planning and agentic work. `gpt56sol` / `gpt55` / `gpt54` are strong alternatives for
tool-heavy execution. Type `models` for what the gateway actually serves.

> On `APEXA_LLM_MODE=proxy` against Argo's **native** endpoint, the Anthropic path
> refuses non-streaming requests, so `claudeopus5` does not work there — use
> `gpt56sol` or `gemini35flash`, or stay on the default `argo` transport.

### Environment switches (set before launch, or in `.env`)

| Variable | Default | Effect |
|---|---|---|
| `APEXA_AGENT_MODE` | `single` | `single` = one persistent reasoning loop, full context across turns (Claude-Code style). `legacy` = keyword-routed specialists + guards. |
| `ARGO_MODEL` | `claudeopus5` | Default model at startup (overridable in-session with `model`). |
| `APEXA_LLM_MODE` | `argo` | `argo` = legacy `/chat/`. `proxy` = OpenAI-compatible `/v1` with structured tool calls. See `docs/ARGO_NATIVE_ENDPOINT.md`. |
| `APEXA_LLM_BASE_URL` | *(unset)* | Override the `/v1` base for `proxy` mode. Leave unset for Argo — the `anl` profile supplies its native endpoint. |
| `APEXA_LLM_STRICT` | on with `proxy` | Abort startup on an unreachable endpoint rather than silently downgrading. |
| `APEXA_NETWORK` | `internal` | `data` / `internal` / `web`. Beamline hosts are `internal`; only `fetch_cif_from_mp` needs `web`. |
| **`APEXA_MIDAS_BIN`** | *(unset)* | **`bin/` of the pip `midas-suite` env.** Prepended to PATH with conda stripped. The primary MIDAS mechanism — no repo clone needed. |
| `APEXA_MIDAS_DEVICE` | `cpu` | `cpu` / `cuda` / `auto` for the PyTorch MIDAS engines. |
| `APEXA_FORCE_LEGACY_MIDAS` | *(unset)* | `1` = force the legacy C++ engine. Note it is also the **only** path that can read zarr (`.zip`). |
| `APEXA_CALIB_TIMEOUT` | `1800` | Calibration subprocess timeout in seconds (7200 on the canonical v2 path). |
| `APEXA_BEAMLINE` | *(unset)* | e.g. `1-ID-E`. Enables the capsule scope gate; `APEXA_IGNORE_SCOPE_GATE=1` overrides. |
| `APEXA_STAGE_GUARDRAILS` | on | Inject each stage's skill + technique capsule as it is entered. |
| `APEXA_SHOW_TIMING` | *(unset)* | `1` = show API response times (same as the `timing` command). |
| `MIDAS_PATH` | *(auto)* | A MIDAS **repo clone**. Needed only by the legacy C++ / AutoCalibrateZarr paths. |
| `MIDAS_PYTHON` | *(auto)* | Override the interpreter used for those legacy scripts. |

**Calibration engine:** `calibration_engine="auto"` (the default) runs the canonical
four-stage recipe from the MIDAS `calibrate-integrate` handbook when it can, and falls
back to the native → pip-console → legacy cascade otherwise, recording *why* in the
result. `"v2"` pins the canonical engine and refuses rather than downgrading; `"v1"` and
`"legacy"` pin the older paths. The canonical engine needs `midas_calibrate_v2` **and
`scikit-image`** in the interpreter `APEXA_MIDAS_BIN` points at; it cannot read zarr.
Results are written as `refined_MIDAS_params_v2.txt`, and a calibration failing the
held-out strain gate is reported as an error, not as a result.

---

## Configuration

**User Settings** (`.env`):
```bash
ANL_USERNAME=your_username
ARGO_MODEL=claudeopus5       # default; or gpt56sol, gpt55, gpt54, gemini35flash
APEXA_AGENT_MODE=single      # single (default) | legacy
APEXA_MIDAS_BIN=/home/beams12/S1IDUSER/opt/envs/midas/bin   # pip midas-suite env
# MIDAS_PATH=~/Git/MIDAS     # Optional - only for the legacy C++ / zarr paths
# APEXA_FORCE_LEGACY_MIDAS=1 # Optional - force the legacy engine
```

**Server Configuration** (`servers.config`):
```bash
core:beamline_core_server.py
midas:midas_comprehensive_server.py
motor:epics_motor_server.py
gsas2:gsas2_server.py
```

---

## Requirements

- **Python:** 3.13+ (with [`uv`](https://github.com/astral-sh/uv) package manager)
- **Network:** ANL access for Argo Gateway
- **MIDAS:** `midas-suite` (pip) — set `APEXA_MIDAS_BIN`. A repo clone plus `diplib`
  is needed only for the legacy C++ / zarr paths.
- **Memory:** 16+ GB RAM (64+ GB recommended for FF-HEDM)

`uv` handles the virtual environment automatically — users never need to activate it.
`uv sync` installs the locked dependency set in ~1 second. Optional extras:
`uv sync --extra extra` (pyfai, vtk, seaborn…), `--extra mp` (Materials Project;
online-only), `--extra alcf`.

> **The lockfile is the deployment contract.** `uv.lock` is what makes an install
> reproducible on an air-gapped beamline host — `uv sync --upgrade` on a laptop is a
> production change for every host that later pulls. See `docs/OFFLINE_DEPLOYMENT.md`.

---

## MIDAS Auto-Detection

**Preferred: set `APEXA_MIDAS_BIN`** to the pip `midas-suite` environment's `bin/`.
That covers every current workflow and needs no repo clone. The search below applies
to `MIDAS_PATH` — a clone, required only by the legacy C++ / AutoCalibrateZarr paths:

1. `$MIDAS_PATH` environment variable
2. `~/Git/MIDAS`
3. `~/opt/MIDAS`
4. `/home/beams/S*USER/opt/MIDAS` (beamline systems)
5. `~/MIDAS`
6. `/opt/MIDAS`
7. `~/.MIDAS`

---

## Documentation

- **[Canvas workspace](docs/CANVAS_WORKSPACE.md)** — Tabs, comparison, Classic light, and how to load updates
- **[Repository layout](docs/REPOSITORY_LAYOUT.md)** — Source locations and preserved local outputs

- **[USER_MANUAL.md](USER_MANUAL.md)** — Complete guide with examples
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** — Command cheat sheet
- **[WEB_UI_GUIDE.md](WEB_UI_GUIDE.md)** — Browser-based interface
- **[GRADIO_UI_GUIDE.md](GRADIO_UI_GUIDE.md)** — Gradio chat interface
- **[Diffraction software survey](docs/diffraction-software-survey.md)** — Native engines, optional paths, and external-tool boundaries
- **[Shared sessions](docs/SESSIONS.md)** — Resume conversations and canvas state between the Web UI and CLI
- **[Tomography setup](docs/TOMO_SETUP.md)** — Configure the optional native TomoPy runtime
- **[Active tool inventory](docs/analysis-tool-inventory.json)** — Source registrations in `servers.config`; not a runtime-readiness report
- **[Phase-transformation dynamics workflow](docs/phase-transformation-dynamics.md)** — Integrate and inspect ordered diffraction series, map scan axes, track features and review phase refinements
- **[docs/development/architecture.md](docs/development/architecture.md)** — Developer architecture
- **[.agents/skills/](.agents/skills/)** — MIDAS workflow reference (Agent Skills)

To browse the searchable documentation site, run `./serve_docs.sh`.

---

## Credits

**Development:**
- Pawan Tripathi - Lead Developer
- Advanced Photon Source, Argonne National Laboratory

**Core Dependencies:**
- [MIDAS](https://github.com/marinerhemant/MIDAS) v11 - Hemant Sharma
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - `mcp.server.fastmcp.FastMCP` (the official SDK; not the third-party `jlowin/fastmcp` package)
- [uv](https://github.com/astral-sh/uv) - Package manager
- Argo Gateway - Argonne National Laboratory

---

## License

Copyright (c) 2024-2026 UChicago Argonne, LLC
See [LICENSE](LICENSE) for details.
