# APEXA Architecture

## Overview

APEXA uses a **multi-agent orchestration** architecture (Phase 2) where natural language queries are routed to specialist agents that call tools on MCP servers.

## System Diagram

```
User (CLI / Gradio / Web UI)
         |
    Argo Gateway API
    (GPT-4o, Claude, Gemini)
         |
    OrchestratorAgent
    (keyword-score routing)
         |
    +----------------+----------+-----------+----------------+
    | Calibration    | Analysis | Knowledge | Visualization  |
    | Agent (0.3)    | Agent    | Agent     | Agent (0.3)    |
    |                | (0.5)    | (0.6)     |                |
    +-------+--------+----+-----+-----+-----+-------+--------+
            |              |           |             |
            +--------------+-----------+    MIDAS viewer scripts
                           |                (launched via run_command)
                +----------+----------+
                |   core server       |
                |   (9 tools)         |
                +---------------------+
                |   midas server      |
                |   (37 tools)        |
                +---------------------+
```

## Entry Points

| Interface | File | Start Command |
|---|---|---|
| CLI | `argo_mcp_client.py` | `./start_beamline_assistant.sh` |
| Gradio UI | `gradio_ui.py` | `./start_gradio_ui.sh` |
| Web server | `web_server.py` | `python web_server.py` |

All three call `run_query()` as the single entry point.

## Core Files

| File | Purpose |
|---|---|
| `apexa_agents.py` | Agent definitions, ArgoProvider, AgentRunner, OrchestratorAgent |
| `argo_mcp_client.py` | MCP client, tool registry, CLI session loop |
| `beamline_core_server.py` | Core MCP server: file ops, shell commands, X-ray calculations |
| `midas_comprehensive_server.py` | MIDAS MCP server: 38 tools for HEDM workflows, validation, stress analysis, PyTorch v11 pipeline |
| `apexa_lib.py` | Reusable primitives for debug/test/analysis scripts: robust image/dark loading (`load_image` picks the detector dataset `/exchange/data`, never 1-D metadata), MIDAS param read/write + `compare_geometry`, lineout & outcome-manifest readers. Import this instead of hand-rolling I/O. |
| `servers.config` | Server configuration (`name:script_path` pairs) |
| `.agents/skills/` | Agent Skills: canonical MIDAS workflow reference |

## Agent Layer (`apexa_agents.py`)

### ArgoProvider
Single class for all Argo Gateway HTTP calls. Handles PROD vs DEV endpoint selection based on model name.

### AgentRunner
Dual-mode tool calling:
1. **Text-based parsing** (primary) -- model outputs `TOOL_CALL: name` / `ARGUMENTS: {json}`, regex extracts and executes
2. **Native API tool_calls** (fallback, rarely used)

Text-based mode is primary because the Argo Gateway returns `{"response": "text"}` -- it strips native tool_calls from model responses.

**Execution-integrity guards** (both agent modes) stop the model from reporting work it did not do:
- *Anti-confabulation* -- a final answer containing broken tool-call syntax (no parsed call) is forced to retry, then fails honestly rather than confabulating results.
- *Phantom-launch / false-async* -- "launched / I'll report once it completes" with no tool call is rejected: tools are synchronous, so the model must emit the call now.
- *Refusal-to-execute* -- a false "I can't run commands / please run it yourself" is rejected and the real tool call is forced (common model-drift failure).
- *Verify-before-report* -- integration/calibration tools return only outputs found on disk and write an `APEXA_*.json` manifest; an exit-0 with no lineout is reported `incomplete`, never a fabricated path.

### OrchestratorAgent
Keyword-score routing to specialist agents:

| Agent | Keywords | Temperature |
|---|---|---|
| CalibrationAgent | calibrat, ceo2, beam center, lsd, validate param, diagnose | 0.3 |
| AnalysisAgent | integrat, hedm, grain, gsas, stress, von mises, schmid, d0 (default fallback) | 0.5 |
| KnowledgeAgent | explain, what is, literature, best practice | 0.6 |
| VisualizationAgent | plot, visualiz, view, show, lineout, caked, heatmap | 0.3 |
| MotorAgent | motor, move, position, caget, caput, epics | 0.2 |

### _TOOL_PREAMBLE
System prompt injected into every agent call. Instructs the model to use `TOOL_CALL:` / `ARGUMENTS:` text format. Without this, the model generates text instead of calling tools.

## MCP Servers

### Core Server (`beamline_core_server.py`) -- 9 tools
`list_directory`, `read_file`, `write_file`, `get_file_info`, `run_command`,
`check_environment`, `xray_calculate`, `validate_beamline_parameters`, `list_common_calibrants`

### MIDAS Server (`midas_comprehensive_server.py`) -- 38 tools

**Workflows:** `midas_auto_calibrate` (supports `seed_from_params=` — seed BC/Lsd
from a trusted neighbour's params for robust low-SNR calibration),
`midas_integrate_2d_to_1d`, `midas_integrate_series` (integrate MANY separate
files in ONE call, per-file verified + one manifest — use instead of looping the
single-file tool), `midas_batch_integrate`,
`run_gsas_refinement`, `run_live_analysis`, `fetch_cif_from_mp`,
`run_ff_hedm_full_workflow`, `run_nf_hedm_reconstruction`, `run_pf_hedm_workflow`,
`run_ff_calibration`, `match_grains`, `calculate_misorientation`,
`run_forward_simulation`, `extract_grain_centroids`, `convert_nf_to_dream3d`,
`overlay_ff_nf_results`, `batch_convert_ge_to_tiff`,
`create_midas_parameter_file`, `validate_midas_installation`, `get_midas_workflow_status`,
`run_midas_viewer`,
`query_hedm_knowledge`, `get_material_properties`, `get_typical_hedm_parameters`,
`estimate_parameters_from_image`

**Parameter validation (midas-params):** `validate_parameter_file`, `diagnose_parameter_file`,
`inspect_dataset_file`, `enumerate_bragg_rings`

**Stress analysis (midas-stress):** `compute_grain_stress`, `get_material_stiffness`,
`correct_d0_equilibrium`, `analyze_slip_systems`, `read_grains_summary`

## Tool Registry

Built once at connection time in `connect_to_multiple_servers()`:
- `_tool_registry: Dict[str, str]` -- bare tool name to server name (O(1) lookup)
- `_available_tools: List[Dict]` -- OpenAI-format tool definitions passed to agents

`execute_tool_call(tool_name, args)` does O(1) dict lookup: `server = _tool_registry[tool_name]`.

## Agent Skills (`.agents/skills/`)

Canonical MIDAS workflow reference files (agentskills.io format):

| Skill | Content |
|---|---|
| `midas-calibrate` | AutoCalibrateZarr.py flags, formats, convergence guide |
| `midas-integrate` | integrator.py (CPU) + integrator_batch_process.py (GPU), v11 flags |
| `midas-hedm` | FF/NF/PF-HEDM pipeline, GPU flags, resume/checkpoint |
| `midas-gsasii` | GSAS-II refinement, live analysis pipeline, CIF fetcher |
| `midas-visualize` | MIDAS viewer scripts table, when to use each |

## Server Configuration (`servers.config`)

```bash
core:beamline_core_server.py
midas:midas_comprehensive_server.py
```

The startup script reads this file, validates server existence, and passes them to `argo_mcp_client.py`.

## MIDAS Environment

`get_midas_env()` sets up PATH, MIDAS_PATH, and PYTHONPATH for all MIDAS operations (C++ and Python). C++ binaries have @rpath set at build time so DYLD_LIBRARY_PATH is not needed.

MIDAS v11 Python scripts use the `midas_env` conda environment (`~/miniconda3/envs/midas_env/bin/python`). APEXA uses `uv` -- the two environments are kept separate. All MIDAS tools invoke scripts via subprocess using `find_midas_python()`.

### MIDAS Python Packages (Optional)

Two optional packages in `$MIDAS_PATH/packages/` extend APEXA's capabilities:

| Package | Install | What It Does |
|---|---|---|
| `midas-params` | `pip install -e $MIDAS_PATH/packages/midas_params` | Parameter file validation, LLM diagnosis, dataset inspection, Bragg ring enumeration |
| `midas-stress` | `pip install -e $MIDAS_PATH/packages/midas_stress` | Stress/strain analysis from Grains.csv: Hooke's law, d0 correction, slip-system analysis |

Install in the **midas_env** conda environment (not the APEXA uv venv):
```bash
conda activate midas_env
pip install -e $MIDAS_PATH/packages/midas_params
pip install -e $MIDAS_PATH/packages/midas_stress
```

Run `validate_midas_installation` in APEXA to check if both packages are detected.

## Argo API

- **PROD:** `https://apps.inside.anl.gov/argoapi/api/v1/resource/chat/` (all models, March 2026)
- **DEV:** `https://apps-dev.inside.anl.gov/argoapi/api/v1/resource/chat/` (future beta models)
- `DEV_ONLY_MODELS` set in `apexa_agents.py` -- currently empty, add beta model names here
- Response: `{"response": "text"}` -- no native tool_calls support
- Do NOT pass `tools` in API payload
- Model-specific params: o-series/GPT-5 use `max_completion_tokens` (no temperature); Haiku 4.5/Sonnet 4.5/4.6 reject both temperature+top_p

### Available Models (all on PROD)
- **OpenAI:** gpt4o (default, fastest ~0.8s), gpt4olatest, gpt41/mini/nano, gpto3mini, gpto4mini, gpt5/mini/nano, gpt51, gpt52, gpt54
- **Anthropic:** claudeopus47/46/45/41/4, claudesonnet46/45/4/37, claudehaiku45
- **Google:** gemini25pro, gemini25flash

## CLI Features

- `timing` command toggles API response time display (or `APEXA_SHOW_TIMING=1`)
- `model <name>` switches models at runtime
- `clean_markdown()` strips bold/italic/headers from LLM responses for clean terminal output
- `plot ...` with unrecognized subcommand falls through to APEXA (not intercepted by CLI)

## Dependencies

- `uv sync` installs the locked dependency set in ~1 second
- `uv run` auto-creates `.venv/` -- users never activate manually
- Optional extras: `uv sync --extra extra` (pyfai, vtk, seaborn, lmfit, etc.)
- Core deps: httpx, mcp, fastapi, gradio, numpy, scipy, matplotlib, fabio, h5py, xrayutilities
- MIDAS Python scripts use separate `midas_env` conda environment (not the uv venv)

## Adding a New MCP Server

1. Create `my_server.py` with `@mcp.tool()` decorated functions
2. Add `myserver:my_server.py` to `servers.config`
3. Restart APEXA

See [adding-servers.md](adding-servers.md) for the full template.
