# Tomography (TomoPy) setup

APEXA's tomography tools (`survey_tomo_stack`, `run_tomo_reconstruction`, served by the
`tomo` MCP server) use the **TomoPy** stack — the APS-standard parallel-beam
reconstruction framework — for 1-ID / 20-ID.

## Route to a native tomo env (same model as MIDAS and GSAS-II)

`tomopy` and `astra-toolbox` are a **conda-forge** stack with no reliable pip wheels, so
APEXA does **not** bundle them. It routes to a native tomo environment, exactly as it does
for MIDAS (`APEXA_MIDAS_BIN`) and GSAS-II (`APEXA_GSASII_PYTHON`):

```bash
# in APEXA's .env
APEXA_TOMO_PYTHON=/path/to/conda/envs/tomopy/bin/python   # an env that can `import tomopy, dxchange`
```

The runner (`_tomo_runner.py`) is executed by that interpreter in a subprocess, so the heavy
tomo stack never enters APEXA's own `.venv` and the **base install stays offline-clean**.

### If you have no tomo env

Create one with conda (TomoPy's supported channel):

```bash
conda create -n tomopy -c conda-forge tomopy dxchange tifffile
# optional GPU/iterative reconstructors:
conda install -n tomopy -c astra-toolbox -c conda-forge astra-toolbox   # (+ cupy for GPU)
```

Then set `APEXA_TOMO_PYTHON=~/miniconda3/envs/tomopy/bin/python`.

**There is no `tomo` pip extra.** The whole stack (`tomopy`, `dxchange`, `astra`) is
conda-forge only — there is no working pip backend to install into APEXA's `.venv`, so
APEXA's base `uv sync` carries nothing for tomo and stays offline-clean. Everything tomo
runs in the native conda env above, reached through `APEXA_TOMO_PYTHON`.

## Behaviour without a tomo env

If `APEXA_TOMO_PYTHON` is unset/missing or cannot `import tomopy, dxchange`, the tomo tools
return a **structured error** telling you to set it — never a crash and never a fabricated
reconstruction.

## What the tools do

- **`survey_tomo_stack`** — cheap, read-only pre-flight: stack dims/dtype, flat/dark layout
  (from measured frame means), transmission over the illuminated region, μ·D, truncation.
  Returns `status:"halt"` with the specific capsule hard rule when something would fail
  silently downstream. **Run this first.**
- **`run_tomo_reconstruction`** — normalize → (Paganin phase retrieval) → find/sweep the
  rotation center → reconstruct (`gridrec`/`fbp`/`astra`) → write the volume (+ optional
  threshold mask). Refuses to default pixel size / rotation axis / handedness (capsule hard
  rule 3). Honest status.

Methodology source of truth: `knowledge_base/capsules/tomo/` and the `tomo-reconstruct`
skill. Registration of the reconstructed shape into the MIDAS lab frame (the HEDM-envelope
use) is a tracked follow-up, not part of this first version.
