# APEXA - Advanced Photon EXperiment Assistant

AI-powered framework for synchrotron diffraction analysis, tomography workflows, and instrument control at Argonne National Laboratory's Advanced Photon Source.

---

## 🚀 Quick Start

### Command-Line Interface (CLI)
```bash
./setup_user.sh                  # One-time setup
./start_beamline_assistant.sh    # Start APEXA CLI
```

### Web User Interface (Web UI)
```bash
./start_web_viewer.sh            # Start Web UI
```
Then open: **http://localhost:8001**

**That's it!** Natural language interface ready for:
- ✅ Detector calibration (CeO2, LaB6, Si)
- ✅ 2D→1D integration with dark subtraction
- ✅ Series/batch integration of a whole scan in one call (per-frame darks)
- ✅ FF-HEDM grain reconstruction
- ✅ NF-HEDM microstructure mapping
- ✅ Powder refinement with Agentic-GSAS-II and a dedicated refinement canvas
- ✅ Phase-transformation series contours, peak trajectories, and scan-axis-aware sequential refinement
- ✅ Tomography pre-flight and reconstruction when a native TomoPy environment is configured
- ✅ Phase identification
- ✅ Tiered CPU/GPU compute dispatch — offload supported jobs to a configured ANL GPU endpoint
  (see [Compute Dispatch](COMPUTE_DISPATCH.md))
- ✅ Server and analysis-job status where the selected workflow exposes it

---

## 📖 Documentation

### 🌐 Interactive Documentation Site
For the best experience, view the **searchable documentation site** for the pages in `docs/`:
```bash
./serve_docs.sh  # Opens at http://localhost:8000; first use needs package-index access or a cached docs environment
```

Features:
- 🔍 Full-text search across the published documentation pages
- 📱 Mobile-friendly responsive design
- 🌓 Dark/light mode toggle
- 📑 Organized navigation and table of contents

### For Users
- **[User manual](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/USER_MANUAL.md)** - Complete guide with examples and tutorials
- **[Web UI guide](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/WEB_UI_GUIDE.md)** - Browser-based interface for demos and collaboration
- **[Quick reference](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/QUICK_REFERENCE.md)** - Command cheat sheet for demos
- **[Diffraction software survey](diffraction-software-survey.md)** - Integrated engines, optional paths and external-tool boundaries
- **[GSAS-II canvas workbench](gsas2-workbench.md)** - Tutorial operations, refinement sessions, policy comparisons and coverage limits
- **[Phase-transformation dynamics](phase-transformation-dynamics.md)** - Reduce and visualize ordered diffraction series, map scan axes, and review phase trends
- **[Shared sessions](SESSIONS.md)** - Resume conversations and canvas state between WebUI and CLI
- **[Tomography setup](TOMO_SETUP.md)** - Configure the optional native TomoPy runtime
- **[Tool inventory](analysis-tool-inventory.json)** - Active server registrations, not readiness guarantees

### For Developers & Advanced Users
- **[Architecture](development/architecture.md)** - System architecture (agents, MCP servers, tool registry)
- **[Agent Skills](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/tree/main/.agents/skills)** - MIDAS workflow reference (calibration, integration, HEDM, visualization)

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
  -> run_midas_viewer (plot_lineout_results)
  [viewer window opens]

APEXA> run FF-HEDM workflow on /data/experiment
  -> run_ff_hedm_full_workflow
  Found 2,347 grains
```

---

## 🎯 Key Features

### Smart & Conversational
No need to memorize commands - just describe what you want:
- "Calibrate using the ceria file"
- "Integrate with dark subtraction"
- "Run FF-HEDM with 32 CPUs"

### Context-Aware
Shared sessions can preserve conversation and canvas state between the Web UI and CLI.
The session store is local to the APEXA host; see [Shared sessions](SESSIONS.md).

### Workflow Guidance
Workflow recommendations can suggest follow-up steps and identify missing inputs.
They are planning aids; confirm assumptions, tool results, and host readiness before
starting an analysis.

See the [user manual](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/USER_MANUAL.md) for examples.

### Extensible
Add MCP integrations through the [server development guide](development/adding-servers.md)
and `servers.config`. Engine dependencies and scientific workflows still need
host-specific readiness checks.

---

## 🛠️ System Architecture

```
User → Argo Gateway → OrchestratorAgent (single-loop default)
                         ↓
                    MCP tool registry
                         ↓
       core (13) · midas (60) · gsas2 (13) · tomo (2) · motor (13)
```

Counts are source-declared tools from active `servers.config`; they do not
guarantee that optional engines or hardware are available. Set
`APEXA_AGENT_MODE=legacy` for the older keyword-routed specialist agents. See
[the current architecture](development/architecture.md).

---

## ⚙️ Configuration

**User Settings** (`.env`):
```bash
ANL_USERNAME=your_username
ARGO_MODEL=gpt55               # check `models` for the current gateway aliases
APEXA_MIDAS_BIN=/path/to/midas-suite/bin  # optional native MIDAS environment
# MIDAS_PATH=~/Git/MIDAS       # repo clone only for forward simulation / legacy tools
```

**Server Configuration** (`servers.config`):
```bash
core:beamline_core_server.py
midas:midas_comprehensive_server.py
motor:epics_motor_server.py
gsas2:gsas2_server.py
tomo:tomo_server.py
```

---

## 📋 Requirements

- **Python:** 3.13+
- **Package Manager:** [uv](https://github.com/astral-sh/uv)
- **Network:** ANL access for Argo Gateway
- **MIDAS:** `midas-suite` (pip) — set `APEXA_MIDAS_BIN`; a repo clone is needed only for the legacy C++ / zarr paths
- **Memory:** 16+ GB RAM (64+ GB recommended for FF-HEDM)

---

## 🔧 Troubleshooting

**MIDAS not detected?**

For calibration, integration, and reconstruction, the primary route is the pip
`midas-suite` environment. Set `APEXA_MIDAS_BIN` to its `bin/` directory, or use
`uv sync --extra midas` for a local fallback. `MIDAS_PATH` identifies a
repository clone for native C and legacy tools; see [MIDAS setup](MIDAS_SETUP.md).

**Tool or session routes return 404?**
Stop the running Web UI server, restart it from the current checkout with
`./start_web_viewer.sh`, then reload the page. An existing Python process keeps
its original route table after files on disk change.

**Need help?**
```
APEXA> help
APEXA> what can you do?
APEXA> how do I calibrate?
```

See the [user manual troubleshooting guide](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/USER_MANUAL.md#troubleshooting) for other issues.

---

## 🎓 Credits

**Development:**
- Pawan Tripathi - Lead Developer
- Advanced Photon Source, Argonne National Laboratory

**Core Dependencies:**
- [MIDAS](https://github.com/marinerhemant/MIDAS) - Hemant Sharma
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - `mcp.server.fastmcp.FastMCP` (official SDK)
- [uv](https://github.com/astral-sh/uv) - Astral
- Argo Gateway - Argonne National Laboratory

---

## 📄 License

Copyright (c) 2024-2026 UChicago Argonne, LLC  
See the [LICENSE](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/LICENSE) for details.

---

**Ready to analyze? Start with:**
```bash
./start_beamline_assistant.sh
```

**Documentation Map:**
- **New user?** → [Quick Start](getting-started/quickstart.md)
- **Demo/presentation?** → [Quick Reference](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/QUICK_REFERENCE.md)
- **Developer?** → [Architecture](development/architecture.md)
- **MIDAS workflows?** → [Agent Skills](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/tree/main/.agents/skills)
