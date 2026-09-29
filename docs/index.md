 # APEXA - Advanced Photon EXperiment Assistant

AI-powered beamline scientist for real-time HEDM data analysis at Argonne National Laboratory's Advanced Photon Source.

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
- ✅ Phase identification
- ✅ Tiered CPU/GPU compute dispatch — offload big jobs to an ANL GPU endpoint
  (see [Compute Dispatch](COMPUTE_DISPATCH.md))
- ✅ Real-time monitoring

---

## 📖 Documentation

### 🌐 Interactive Documentation Site
For the best experience, view our **searchable documentation website**:
```bash
./serve_docs.sh  # Opens at http://localhost:8000
```

Features:
- 🔍 Full-text search across all docs
- 📱 Mobile-friendly responsive design
- 🌓 Dark/light mode toggle
- 📑 Organized navigation and table of contents

### For Users
- **[USER_MANUAL.md](USER_MANUAL.md)** - Complete guide with examples and tutorials
- **[WEB_UI_GUIDE.md](WEB_UI_GUIDE.md)** - Browser-based interface for demos and collaboration
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Command cheat sheet for demos

### For Developers & Advanced Users
- **[Architecture](development/architecture.md)** - System architecture (agents, MCP servers, tool registry)
- **[Agent Skills](.agents/skills/)** - MIDAS workflow reference (calibration, integration, HEDM, visualization)

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
Remembers your session:
- Previous files and directories
- Analysis history
- Conversation context

### Proactive
Suggests next steps after each analysis:
- "📊 Suggested next steps: Integrate rings to 1D pattern"
- Auto-validates parameters before execution
- Real-time alerts during beamtime

See [USER_MANUAL.md](USER_MANUAL.md) for details on all features.

### Extensible
Add new analysis tools in 5 minutes - see USER_MANUAL.md for details.

---

## 🛠️ System Architecture

```
User → Argo Gateway (GPT-4o/Claude/Gemini)
         ↓
    OrchestratorAgent (apexa_agents.py)
         ↓
   ┌──────────────┬──────────────┬────────────────┬───────────────────┐
   │ Calibration  │  Analysis   │  Knowledge     │  Visualization    │
   │    Agent     │    Agent    │    Agent       │     Agent         │
   └──────┬───────┴──────┬──────┴───────┬────────┴────────┬──────────┘
          └──────────────┴──────────────┘                 │
                         ↓                                ↓
              ┌──────────┬──────────┐           MIDAS viewer scripts
              │   core   │  midas   │
              └──────────┴──────────┘
```

---

## ⚙️ Configuration

**User Settings** (`.env`):
```bash
ANL_USERNAME=your_username
ARGO_MODEL=gpt4o              # or claudesonnet4, gemini25pro
MIDAS_PATH=~/Git/MIDAS        # Optional - auto-detected
```

**Server Configuration** (`servers.config`):
```bash
core:beamline_core_server.py
midas:midas_comprehensive_server.py
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

APEXA automatically searches for MIDAS in this order:
1. `$MIDAS_PATH` environment variable
2. `~/Git/MIDAS`
3. `~/opt/MIDAS`
4. `/home/beams/S*USER/opt/MIDAS` (beamline systems)
5. `~/MIDAS`
6. `/opt/MIDAS`
7. `~/.MIDAS`

To override auto-detection:
```bash
export MIDAS_PATH=/path/to/MIDAS
```

**Tool warnings?**
Restart the assistant - warnings are cosmetic.

**Need help?**
```
APEXA> help
APEXA> what can you do?
APEXA> how do I calibrate?
```

See [USER_MANUAL.md](USER_MANUAL.md#troubleshooting) for detailed troubleshooting.

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
See [LICENSE](LICENSE) for details.

---

**Ready to analyze? Start with:**
```bash
./start_beamline_assistant.sh
```

**Documentation Map:**
- **New user?** → [User Manual](USER_MANUAL.md)
- **Demo/presentation?** → [Quick Reference](QUICK_REFERENCE.md)
- **Developer?** → [Architecture](development/architecture.md)
- **MIDAS workflows?** → [Agent Skills](.agents/skills/)
