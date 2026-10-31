# APEXA Architecture

## Overview

APEXA's default `APEXA_AGENT_MODE=single` uses one persistent reasoning loop with the full configured tool set. `APEXA_AGENT_MODE=legacy` enables the older keyword-routed specialist agents. Both modes execute registered MCP tools through `argo_mcp_client.py`.

## System Diagram

```
User (CLI / Gradio / Web UI)
         |
    Argo Gateway API
    (GPT-4o, Claude, Gemini)
         |
    OrchestratorAgent
    (single loop by default)
         |
    MCP tool registry
         |
    +----------+----------+----------+----------+----------+
    | core     | midas    | gsas2    | tomo     | motor    |
    | 13 tools | 60 tools | 13 tools | 2 tools  | 13 tools |
    +----------+----------+----------+----------+----------+
```

Counts reflect `@mcp.tool` definitions in the active `servers.config` on
2026-10-15. They do not establish that optional engine dependencies or hardware
are installed and ready.

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
| `midas_comprehensive_server.py` | MIDAS MCP server: HEDM, calibration, integration, powder, validation and materials-analysis tools |
| `gsas2_server.py` | Agentic-GSAS-II powder refinement jobs, reports and evidence tools |
| `tomo_server.py` | TomoPy stack inspection and reconstruction tools; needs a configured native environment |
| `epics_motor_server.py` | EPICS motor control tools; needs Channel Access utilities and a reachable IOC |
| `apexa_lib.py` | Reusable primitives for debug/test/analysis scripts: robust image/dark loading (`load_image` picks the detector dataset `/exchange/data`, never 1-D metadata), MIDAS param read/write + `compare_geometry`, lineout & outcome-manifest readers. Import this instead of hand-rolling I/O. |
| `servers.config` | Server configuration (`name:script_path` pairs) |
| `.agents/skills/` | Agent Skills: canonical MIDAS workflow reference |

## Agent Layer (`apexa_agents.py`)

### ArgoProvider
Single class for all Argo Gateway HTTP calls. Handles PROD vs DEV endpoint selection based on model name.

### AgentRunner
Normalizes provider responses and supports native structured `tool_calls` as
well as the textual `TOOL_CALL:` / `ARGUMENTS:` fallback. The response format
depends on the configured provider/model and gateway behavior; do not assume the
gateway always strips native calls.

**Execution-integrity guards** (both agent modes) stop the model from reporting work it did not do:
- *Anti-confabulation* -- a final answer containing broken tool-call syntax (no parsed call) is forced to retry, then fails honestly rather than confabulating results.
- *Phantom-launch / false-async* -- "launched / I'll report once it completes" with no tool call is rejected: tools are synchronous, so the model must emit the call now.
- *Refusal-to-execute* -- a false "I can't run commands / please run it yourself" is rejected and the real tool call is forced (common model-drift failure).
- *Verify-before-report* -- integration/calibration tools return only outputs found on disk and write an `APEXA_*.json` manifest; an exit-0 with no lineout is reported `incomplete`, never a fabricated path.

### OrchestratorAgent
The default single mode keeps one full-fidelity transcript and lets the model
select from the configured tool registry. Set `APEXA_AGENT_MODE=legacy` to use
the older keyword-score routes for calibration, analysis, knowledge,
visualization and motor tasks.

### _TOOL_PREAMBLE
Text-protocol instructions used when the provider needs the `TOOL_CALL:` /
`ARGUMENTS:` fallback. Native structured tool calls are also supported.

## MCP Servers

The active server list and tool schemas come from `servers.config` and each
server's `@mcp.tool` definitions. The current source inventory is summarized in
[`analysis-tool-inventory.json`](../analysis-tool-inventory.json). In brief:

| Server | Scope | Readiness boundary |
|---|---|---|
| `core` | Files, environment, commands and X-ray calculations | Remote commands depend on configured SSH access and permitted hosts |
| `midas` | HEDM, detector calibration/integration and materials analysis | MIDAS build, data formats and optional packages vary by host |
| `gsas2` | Agentic powder refinement, job control, reports and evidence | Needs the configured GSAS-II interpreter and Agentic-GSAS-II checkout |
| `tomo` | Tomography pre-flight and reconstruction | Needs `APEXA_TOMO_PYTHON` with TomoPy and dxchange |
| `motor` | EPICS motor read/write/control | Needs Channel Access utilities, IOC connectivity and safety configuration |

The optional MAUD backend is reached through MIDAS `run_gsas_refinement`; it is
not a separate active MCP server. MAUD is limited to the supported MIDAS caked
archive path and requires its installation, MILK and a JDK. See the diffraction
survey and [TOMO_SETUP.md](../TOMO_SETUP.md) for details.

## Powder engine boundary

`apexa_powder.py` calls the independently installed Agentic-GSAS-II engine in its
GSAS-II interpreter. The platform supplies model access, presents durable jobs
and renders evidence. The engine owns observation identity, numerical state,
configured checks and isolated trial decisions. This lets APEXA share a workspace
across techniques while retaining a separately usable scientific engine.

`apexa_powder_contract.py` normalizes outcomes for the status/report tools;
`frontend/src/lib/refinement.ts` applies the same presentation rules in the UI.
Execution failures are separate from completed blocked/provisional results.
Numerical charts use completed results with finite values, preserve histogram
scope for joint fractions, and do not connect unrelated observations.

Image advice retains transmitted-panel provenance and rejects citations to unseen
panels. A shadow trial remains a separate candidate even when accepted. The
[workflow guide](../powder-refinement.md) documents the exact current acceptance
policy and the benchmark boundaries. Model aliases and interface availability do
not establish reproducibility or end-to-end scientific benefit.

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
| `gsas2-agentic` | Agentic powder refinement and scientific interpretation limits |
| `tomo-reconstruct` | Tomography pre-flight and reconstruction with a native TomoPy environment |

## Server Configuration (`servers.config`)

The current file enables `core`, `midas`, `motor`, `gsas2` and `tomo`.

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

Provider URLs, model aliases, request limits and response normalization are
defined in `apexa_agents.py`. Model availability and native tool-call support are
gateway-side settings that can change independently of this repository; check
the configured gateway/catalog before treating a model as available.

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
