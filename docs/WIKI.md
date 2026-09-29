# APEXA — Advanced Photon EXperiment Assistant

*An AI-powered agentic framework for autonomous HEDM data analysis and instrument
control at synchrotron beamlines (APS, Argonne National Laboratory).*

This wiki is the project overview. It suits the OSF project wiki
(https://osf.io/zxwp6) and the GitHub repository wiki.

---

## 1. What APEXA is

APEXA turns a beamline scientist's **natural-language request** into **executed,
verifiable data-reduction and instrument-control actions** — calibration, azimuthal
integration, HEDM analysis, GSAS-II refinement, visualization, and EPICS motor control —
running against the real MIDAS analysis stack and real hardware.

Its distinguishing idea is **execution integrity**: a stochastic LLM drives the workflow,
but *trust lives in deterministic tool-layer code, not in the prompt*. A guard refuses to
surface any result not backed by an actually-executed tool call, and safety checks run in
server code before any hardware command. This makes "the model said it calibrated" into
"the calibration provably ran."

## 2. Architecture (trust-boundary design)

```
Natural-language prompt
        │
   Reasoning agent (LLM, via institutional Argo gateway)   ← stochastic
        │   emits TOOL_CALL text
        ▼
╔══ DETERMINISTIC TOOL LAYER ══╗   ← trust boundary (enforced in code)
║  • format-tolerant parser     ║
║  • execution-integrity guard  ║
║  • 5 EPICS safety checks       ║
╚═══════════════════════════════╝
     │           │            │
  core(10)    MIDAS(38)     EPICS(13)     ← 3 MCP servers, 61 tools
     └──────────┴────────────┘
          Real hardware · manifest-logged results
```

- **Single reasoning loop** over all 61 tools (Claude-Code-style persistent context),
  with a legacy keyword-routed 5-specialist mode behind `APEXA_AGENT_MODE`.
- **Argo Gateway** returns plain-text responses (strips native tool calls), so APEXA
  uses a **text `TOOL_CALL:` / `ARGUMENTS:` protocol** parsed deterministically.
- **MCP servers:** `core` (file/shell/X-ray calc), `midas` (FF/NF/PF-HEDM, calibration,
  integration, GSAS-II, CIF fetch, viewers), `motor` (EPICS caget/caput).

## 3. APEXA-Bench (evaluation harness)

- **58 facility tasks** across six categories (Calibration, Integration, HEDM Analysis,
  Motor Control, GSAS-II Refinement, Domain Knowledge), each carrying a **four-class
  physical-consequence taxonomy** — the first benchmark axis to separate a wasted compute
  cycle from a damaged instrument (I / C / P_R / P_I).
- **50-scenario adversarial motor-safety suite** (out-of-range, limit-switch, large slew,
  contradictory, prompt-injection, multi-axis, invalid-velocity, runaway-jog).
- Two **deterministic** uses demonstrated: the motor-safety gate (**0/200** violations
  tool-enforced vs **15/200** prompt-only) and cross-detector CI (grading one pipeline
  across four detectors against a NIST reference **caught two latent pipeline bugs**).

## 4. Real-hardware deployment

Demonstrated end-to-end on a live APS 1-ID dataset (`ai_tune`, CeO₂/LaB₆, 63 keV,
~900 mm, Varex 2880×2880): from one natural-language prompt per frame, APEXA identifies
the calibrant, resolves the matching dark, refines the geometry, and integrates to 1D.
Two chemically distinct standards agree to ~0.03 mm in L_sd; an attenuation sweep holds
geometry stable to ±0.02 mm; nonphysical calibrations are autonomously flagged and
rejected. An engine abstraction runs the identical analysis on a Python (GPU) or compiled
(CPU) back end.

## 5. Repositories & data

| Artifact | Location |
|----------|----------|
| Framework + APEXA-Bench harness (code, task defs, safety traces) | GitHub: `AdvancedPhotonSource/APEXA-APS-Beamline-Assistant` |
| `ai_tune` raw data + per-run outputs + frozen results/figure data | OSF: https://osf.io/zxwp6 |
| Cross-detector CI frames | MIDAS bundled CeO₂ (NIST SRM 674b) examples |

Data provenance: every paper figure is generated from a committed CSV/xy file; nothing is
hardcoded. See `DATA_README.md` in the OSF archive for the figure↔data map.

## 6. Getting started

```bash
uv sync                              # install (locked dependency set)
cp .env.template .env                # set ANL_USERNAME, ARGO_MODEL
./start_beamline_assistant.sh        # CLI  (Windows: start_beamline_assistant.bat / launch.py)
./start_web_viewer.sh                # Web UI at http://localhost:8001
```

Reproduce the deterministic benchmark results (no hardware needed):
```bash
uv run python benchmark/eval_harness.py --dry-run          # validate 58 tasks
uv run python benchmark/eval_safety.py  --mock             # 0/200 tool-enforced
uv run python benchmark/eval_safety.py  --mock --prompt-only   # 15/200 baseline
```

## 7. Papers

- **TPC @ SC'26 workshop** — *Execution-Integrity Enforcement for Multi-Agent LLM
  Automation of Synchrotron Data Reduction* (this project's core paper; deterministic
  integrity + real-hardware deployment + benchmark-as-CI).
- **JSR (Detector Zoo)** — cross-detector calibration/refinement robustness.
- **NMI** — APEXA as a data-analysis + control framework (broader scope).

## 8. Key design decisions

- **Trust in code, not prompt** — the execution-integrity guard and safety checks are
  deterministic and model-independent (hold for any LLM).
- **Text tool-call protocol** — required by the Argo gateway; parsed tolerantly to absorb
  cross-model format drift.
- **Manifest-logged runs** — every calibration/integration writes an outcome manifest, the
  on-disk record the guard and the figures rely on.
- **Physical-consequence taxonomy** — grading distinguishes digital from physical failure,
  and the composite score couples correctness × safety multiplicatively (unsafe ⇒ zero).

---

*Contact / credit:* Developed at the Advanced Photon Source, Argonne National Laboratory.
The `ai_tune` calibration data was acquired at APS 1-ID.
