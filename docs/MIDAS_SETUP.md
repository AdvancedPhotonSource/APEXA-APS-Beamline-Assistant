# MIDAS setup & the APEXA runtime environments

This document answers two questions that regularly cause confusion:

1. **How do I install MIDAS so APEXA can use it?** (least friction, no surprises)
2. **What are all these "venvs" and which one runs what?**

---

## 0. TL;DR — the one rule

> **APEXA routes to a *native* MIDAS install. It does not bundle its own.**

You point APEXA at MIDAS; you do not install MIDAS *into* APEXA. Bundling a second
copy of `midas-suite` inside APEXA's own `.venv` is exactly what once ran
`midas-calibrate-v2` **0.5.3** while the beamline operator had gate-checked
**0.22.0** — and produced a void calibration. So `midas-suite` is an **opt-in
extra**, never a base dependency.

**Decision tree (this is the whole thing):**

```
Do you have a native MIDAS on this host?
│
├─ YES  (beamline host, shared /home/beams env, or you installed midas-suite yourself)
│        → set APEXA_MIDAS_BIN=<that env>/bin   (setup_user.sh auto-detects it)
│        → done. Reconstruction/calibration/integration all route there.
│
└─ NO   (a bare dev laptop, nothing MIDAS installed)
         → uv sync --extra midas
         → installs the pip stack into APEXA's own .venv as a fallback.
         → (leave APEXA_MIDAS_BIN unset.)

Separately: do you need FORWARD SIMULATION?
         → ForwardSimulationCompressed / simulateNF are C-only (not in pip).
         → you need a built MIDAS clone: set MIDAS_PATH or MIDAS_HOME.
         → everything else works without a clone.
```

Run `docs/setup_user.sh` once and it does the YES branch for you (probes for
`midas-pipeline`, writes `APEXA_MIDAS_BIN` into `.env`), or tells you the one
command for the NO branch.

---

## 1. The two ways MIDAS ships

| | **pip `midas-suite`** | **native C build (repo clone)** |
|---|---|---|
| Install | `pip install "midas-suite[all]"` | `git clone … && ./build.sh` |
| Language | pure-Python / PyTorch | C / C++ / CUDA |
| Provides | calibration, integration, FF/PF/NF/tomo **reconstruction** (console scripts: `midas-pipeline`, `midas-nf-pipeline`, `midas-calibrate-v2`, `midas-ring-thresh`, …) | the full C binary set **+ forward simulation** (`ForwardSimulationCompressed`, `simulateNF`) + legacy `AutoCalibrateZarr.py` |
| Needs a clone? | **No** | yes — it *is* the clone |
| APEXA points at it with | `APEXA_MIDAS_BIN` → its `bin/` | `MIDAS_PATH` (or `MIDAS_HOME`) → the clone root |

**Key fact:** pip covers ~everything that *reduces data*. The **only** things that
require a native C build are **forward simulation** and the legacy C tools. A
beamline host doing calibration/integration/reconstruction needs **only** the pip
env (`APEXA_MIDAS_BIN`); it does not need a clone.

---

## 2. The environments ("venvs") — which interpreter runs what

APEXA deliberately keeps several interpreter worlds apart, because mixing them
breaks things (e.g. the C++ build's `DYLD_LIBRARY_PATH` corrupts the PyTorch
stack's `h5py`/`libhdf5` symbols). Here is the full map:

| # | Environment | Selected by | Runs |
|---|---|---|---|
| 1 | **APEXA's own `.venv`** | `sys.executable` (created by `uv sync`) | APEXA itself: CLI / web / Gradio / desktop UI, the MCP servers, the agents. **Also** the 8 PyTorch "capability" packages (PDF, defect, DFXM, …) via `_capability_runner.py` with a *clean* env. |
| 2 | **Native MIDAS pip env** | `APEXA_MIDAS_BIN` → its `bin/` | The reconstruction/calibration/integration console scripts (`midas-pipeline`, `midas-calibrate-v2`, …). This is the operator's **gate-checked** env. |
| 3 | **Native MIDAS C build** | `MIDAS_PATH` / `MIDAS_HOME` | The C binaries: forward sim (`ForwardSimulationCompressed`, `simulateNF`), legacy C executables, legacy `AutoCalibrateZarr.py`. |
| 4 | **GSAS-II interpreter** | `APEXA_GSASII_PYTHON` | GSAS-II refinement, out of process. |

Environments 2–4 are **external** — APEXA finds them and shells out to them. Only
#1 is APEXA's own. The `--extra midas` fallback installs the pip stack **into #1**,
so a laptop with no #2 can still run reconstruction (just from APEXA's `.venv`
instead of a separate native env).

### How APEXA decides which MIDAS to run (resolution order)

Two single functions make every decision, both consulting `APEXA_MIDAS_BIN` first:

- **`get_midas_env()`** builds the subprocess environment. `PATH` =
  `APEXA_MIDAS_BIN` **first** → C build `bin/` → system `PATH`. When
  `APEXA_MIDAS_BIN` is set it also does the equivalent of `conda deactivate` for
  the child (drops conda bins, clears `CONDA_PREFIX`/`PYTHONHOME`) so an active
  `midas_env` can't shadow it. It does **not** set `DYLD_LIBRARY_PATH` — the C
  binaries carry `@rpath`, and overriding it breaks `h5py`.
  *(There is just one env function now — the old `get_midas_python_env` is gone.)*
- **`midas_search_path()`** is the matching `PATH` for `shutil.which()`, so the
  engine APEXA *picks* and the engine it *runs* always agree.
- **`_calibration_interpreter()`** prefers `APEXA_MIDAS_BIN`'s `python` and
  **probes** it (runs a real import) rather than trusting a version pin — this is
  the direct guard against the 0.22.0-vs-0.5.3 skew.
- **`midas_c_binary_dirs()`** lists where C executables are searched: the clone's
  `build/bin`, `FF_HEDM/bin`, `NF_HEDM/bin`, plus any `MIDAS_HOME`-derived dirs.
  (`APEXA_MIDAS_BIN` is deliberately **not** searched here — pip doesn't ship C
  binaries.)

**Net effect:** set `APEXA_MIDAS_BIN` and the native pip env wins everywhere. Leave
it unset and APEXA falls back to its own `.venv` (which is empty of MIDAS unless you
ran `--extra midas`).

---

## 3. Install scenarios

### A. Beamline host with a shared gate-checked env (the common case)
```bash
# setup_user.sh auto-detects this; or by hand in .env:
APEXA_MIDAS_BIN=/home/beams12/S1IDUSER/opt/envs/midas/bin
# MIDAS_PATH only if you also run forward simulation.
uv sync          # no extra — do NOT bundle a second midas-suite
```

### B. Dev laptop, no MIDAS anywhere
```bash
uv sync --extra midas      # pip reconstruction stack into APEXA's .venv
# leave APEXA_MIDAS_BIN unset; APEXA uses its own .venv.
# forward simulation still needs a built clone (MIDAS_PATH/MIDAS_HOME).
```

### C. You also need forward simulation (either host)
```bash
git clone https://github.com/marinerhemant/MIDAS && cd MIDAS && ./build.sh
# then, in APEXA's .env:
MIDAS_PATH=/path/to/MIDAS      # or MIDAS_HOME if the build lives elsewhere
```

---

## 4. Verify what APEXA sees

`validate_midas_installation` is **route-aware** — it reports each capability
independently and never tells a pip-only host to "rebuild MIDAS":

```jsonc
"modes": {
  "pip_reconstruction": { "available": true,  "clis": ["midas-pipeline", …] },
  "forward_simulation": { "available": false, "requires": "native C build …" }
}
```
- A pip-only host → `overall_status: "good"` with a *non-blocking* note that
  forward simulation is unavailable. Calibration/integration/reconstruction are
  explicitly unaffected.
- `"insufficient"` only when **neither** route is present.

---

## 5. Offline note (air-gapped beamline hosts)

The **base install stays offline-clean**: `uv sync` (no extras) pulls no
public-internet dependency. `midas-suite` lives in the `midas` extra and `mp-api`
in the `mp` extra — both opt-in — so an air-gapped host (`APEXA_NETWORK=internal`,
e.g. copland) that routes to a native env via `APEXA_MIDAS_BIN` never fetches
either. See `docs/OFFLINE_DEPLOYMENT.md`.
