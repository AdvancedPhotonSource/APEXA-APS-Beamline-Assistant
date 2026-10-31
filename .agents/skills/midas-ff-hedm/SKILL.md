---
name: midas-ff-hedm
description: Run the MIDAS v11 FF-HEDM grain reconstruction pipeline via APEXA. Use when the user asks to run far-field HEDM, reconstruct grain orientations, index grains, or analyze diffraction data from a polycrystalline sample. Wraps midas-pipeline run --scan-mode ff.
compatibility: Requires MIDAS v11 + midas-suite ≥0.4.0 (midas-pipeline ≥0.6.1, midas-nf-pipeline ≥0.1.1). Install via pip install midas-suite.
metadata:
  author: pawan-tripathi
  version: "1.2"
  midas-version: "11.0"
  package: "midas-pipeline"
  tool: "run_ff_hedm_full_workflow"
  verified-against: "midas-pipeline 0.6.1 / midas-process-grains 0.6.0 (end-to-end smoke run)"
---

## When to use

The APEXA tool is `run_ff_hedm_full_workflow`. It wraps `midas-pipeline run --scan-mode ff`.
Use it for:
- Reconstructing grain orientations from FF-HEDM data
- Indexing a polycrystalline diffraction dataset
- Generating Grains.csv / IndexBest.bin from a MIDAS zarr archive
- Synthetic dataset testing via `midas-pipeline simulate`

**Do NOT use this skill for:** NF-HEDM reconstruction (`run_nf_hedm_reconstruction`), PF-HEDM (`run_pf_hedm_workflow`), or calibration (`midas_auto_calibrate`).

---

## Required inputs

Two inputs are always required:

| Parameter | Description |
|---|---|
| `param_file` | Path to `Parameters.txt` — geometry + scan config |
| `data_file` | Path to `.analysis.MIDAS.zip` zarr archive (single-detector FF scan) |

Optional but common:

| Parameter | Default | Notes |
|---|---|---|
| `result_folder` | `/tmp/ff_hedm_output` | Where LayerNr_N/ directories are created |
| `n_cpus` | 4 | CPU threads for non-GPU stages |
| `layers` | `"1-1"` | Layer range, e.g. `"1-3"` for 3 layers |
| `indexer_backend` | `"python"` | `"python"` (in-process, GPU-compatible) or `"c-omp"` (fast CPU) |
| `refine_backend` | `"python"` | `"python"` (PyTorch) or `"c-omp"` (fast, writes Grains.csv) |
| `detectors` | `None` | Path to `detectors.json` for multi-detector runs |

---

## Quick start

```
run FF-HEDM reconstruction,
  param_file=/path/to/Parameters.txt,
  data_file=/path/to/scan.analysis.MIDAS.zip,
  result_folder=/path/to/output,
  n_cpus=8
```

APEXA will fire the **pre-action reasoning gate** — write SITUATION / GAP / PLAN before calling the tool.

---

## What midas-pipeline does internally

FF-HEDM stages that actually **execute** for a single-scan run (verified via
`midas-pipeline status --json` on a real smoke run):

```
zip_convert → hkl → peakfit → transforms → cross_det_merge →
global_powder → binning → indexing → refinement → process_grains
```

Conditionally **skipped** in a single-scan FF config (they are valid stage names —
see the checkpoint enum below — but do not run here): `merge_overlaps`,
`calc_radius` (superseded by `calc_radius_v` on the V-map path), `consolidation`
(only with `generate_h5=True`), and the V-map/multi-scan stages `merge_scans`,
`seeding`, `find_grains`, `grain_geometry`, `calc_radius_v`, `refine_vmap`.

Key stages:
- **peakfit** — detect diffraction spots from zarr frames
- **indexing** — match spots to HKL predictions for each candidate grain orientation
- **refinement** — fit grain position + orientation to matched spots
- **process_grains** — consolidate into Grains.csv (c-omp refiner only; see bug note)
- **consolidation** — write consolidated HDF5 (only when `generate_h5=True`)

Resume from any stage with `--resume auto` or `--resume from --from <stage>`.

---

## Parameters.txt — minimum required keys

```
# Geometry (from calibration)
Lsd              <µm>           sample-to-detector distance
BC               <Y> <Z>        beam center (pixels)
Wavelength       <Å>
px               <µm>           pixel size
tx ty tz         <rad>          detector tilts
p0 p1 p2 p3 p4 p5               distortion coefficients

# Detector
NrPixels         <int>          detector pixels (square) or NrPixelsY / NrPixelsZ
BadPxIntensity   -2             APS convention
GapIntensity     -1             APS convention

# Crystal
SpaceGroup       <int>          e.g. 225 for FCC/Au
LatticeParameter <a b c α β γ>  e.g. 4.08 4.08 4.08 90 90 90

# Scan
OmegaRange       <start stop>   e.g. -180 180
OmegaStep        <deg>          e.g. 0.25
```

**Note:** When providing a zarr archive via `data_file`, you do NOT need `RawFolder`, `FileStem`, or `Ext` — midas-pipeline uses `--skip-validation` automatically. Do NOT call `validate_parameter_file` when a zarr is provided.

---

## Output files

All outputs land in `result_folder/LayerNr_1/`:

| File | Description | Always present? |
|---|---|---|
| `IndexBest.bin` | Per-seed indexing solutions (15 float64 cols, col 14 = nMatches) | ✅ Yes |
| `IndexBestFull.bin` | Matched spot pairs for each solution | ✅ Yes |
| `OrientPosFit.bin` | Refined grain orientations + positions | ✅ Yes |
| `FitBest.bin` | Refinement residuals per grain | ✅ Yes |
| `InputAll.csv` | All detected diffraction spots | ✅ Yes |
| `paramstest.txt` | Geometry used for this run | ✅ Yes |
| `hkls.csv` | HKL list used for indexing | ✅ Yes |
| `Grains.csv` | Consolidated grain list | ⚠️ c-omp refiner only |
| `consolidated_Output.h5` | Full consolidated HDF5 | Optional (`generate_h5=True`) |

---

## Reading IndexBest.bin (grain count without Grains.csv)

```python
import numpy as np
data = np.fromfile("IndexBest.bin", dtype=np.float64).reshape(-1, 15)
n_matches = data[:, 14]           # column 14 = nMatches
solved = data[n_matches > 0]      # seeds that found a grain orientation
n_grains = len(solved)
best_match = int(n_matches.max()) # max rings matched
print(f"{n_grains} grains solved, best nMatches={best_match}")
```

---

## Backend selection

| Combination | Grains.csv? | Speed | Notes |
|---|---|---|---|
| `indexer=python, refiner=python` | ❌ No | Moderate | Default; GPU-compatible; results in IndexBest.bin |
| `indexer=c-omp, refiner=c-omp` | ✅ Yes | Fast CPU | Requires compiled `midas_indexer` + `midas_fitgrain` binaries |
| `indexer=python, refiner=c-omp` | ❌ No | — | Mixed — c-omp refiner cannot read python indexer output format |

**Known bug (first seen midas-process-grains 0.4.6; CONFIRMED still present on 0.6.0):**
the **python refiner does not write `FitBest.bin`** (the per-spot fit output that
`process_grains` spot-aware mode needs). Without it, `process_grains` falls back to
its legacy path and exits non-zero (`CalledProcessError`, exit 1) — no `Grains.csv`.
Verified with a raw `midas-pipeline run --refine-backend python` (no auto-skip):
`process_grains(FF): FitBest.bin absent (c-omp refiner) → using legacy mode` →
failure. Therefore APEXA **auto-skips `process_grains` + `consolidation` whenever
`refine_backend="python"`** and reads `IndexBest.bin` directly for the grain count.
Do not remove the auto-skip on the 0.6.0 pin — the bug is still live (deferred to
Hemant). The earlier `OrientPosFit.bin`-in-`Results/` theory was superseded by this
0.6.0 finding: the real blocker is the missing `FitBest.bin`.

To get `Grains.csv`, set both `indexer_backend="c-omp"` and `refine_backend="c-omp"`. This requires compiled MIDAS binaries — check with `validate_midas_installation`.

---

## Synthetic dataset (simulate → reconstruct)

Generate a synthetic FF-HEDM dataset to test the pipeline without real data:

```bash
midas-pipeline simulate \
  --out /path/to/sim/ \
  --params Parameters.txt \
  --n-grains 50 \
  --seed 42
```

Outputs (all five, verified on disk):
- `sim/midas_ff_pipeline_synth_det1.analysis.MIDAS.zip` — synthetic zarr archive
  (single-detector stem is `midas_ff_pipeline_synth_det1`)
- `sim/GrainsSim.csv` — ground-truth grain orientations + positions (for comparison)
- `sim/Parameters.txt` — the parameter file to feed straight back into reconstruction
- `sim/detectors.json` — detector layout
- `sim/setup_truth.json` — full ground-truth setup (geometry + grain seeds) for provenance

Then reconstruct:
```
run FF-HEDM reconstruction,
  param_file=/path/to/sim/Parameters.txt,
  data_file=/path/to/sim/<stem>.analysis.MIDAS.zip,
  result_folder=/path/to/output,
  n_cpus=4
```

Expected performance (50-grain synthetic, single small detector, verified on a
laptop-class CPU, `n_cpus=4`):

| Backend pair | Wall time | Result | Grain count |
|---|---|---|---|
| `indexer=python, refiner=python` | ~90 s (9 stages) | `IndexBest.bin` | **233 / 275 seeds solved (85%)**, best nMatches **116** |
| `indexer=c-omp, refiner=c-omp` | ~60 s (10 stages) | `Grains.csv` | **41 grains** recovered / 50 ground truth (82%) |

These are the actual numbers from a real smoke run on this machine — not estimates.
Runtime scales with detector size, frame count, and grain density; a real beamline
scan (large detector, thousands of frames) takes minutes-to-hours, not seconds.
The python path reports *seeds solved* (grain count needs `IndexBest.bin` col 14);
the c-omp path writes an explicit `%NumGrains` header in `Grains.csv`.

---

## Resume an interrupted run

```
run_command("midas-pipeline resume /path/to/output --from indexing --scan-mode ff --params Parameters.txt")
```

Or via the tool with args: the tool auto-detects completed stages and resumes if the result directory exists.

Check status of a previous run:
```
run_command("midas-pipeline status /path/to/output")
```

---

## Multi-detector runs

For multi-panel detectors (e.g. 4-panel Hydra at 1-ID):
```
run_ff_hedm_full_workflow(
    param_file    = "Parameters.txt",
    detectors     = "detectors.json",   # multi-detector layout
    result_folder = "/path/to/output",
    n_cpus        = 16
)
```

Generate a detectors.json from the MIDAS detector calibration output, or auto-create with `midas-pipeline simulate --n-detectors 4`.

---

## Checkpoint / resume stages

Valid stage names for `--from` / `--only` / `--skip`:
```
zip_convert, hkl, peakfit, merge_overlaps, calc_radius, transforms,
cross_det_merge, global_powder, merge_scans, seeding, binning,
indexing, refinement, find_grains, voxel_cleanup, sinogen, reconstruct,
fuse, potts, em_refine, process_grains, grain_geometry, consolidation,
calc_radius_v, refine_vmap
```

---

## Pre-action reasoning gate

Before calling `run_ff_hedm_full_workflow`, ALWAYS write:

```
SITUATION: <what data is available, how many frames, detector type, calibration status>
GAP: <what is missing or uncertain — parameter file? zarr file? calibration done?>
PLAN: <exact tool call you will make and why>
```

Do NOT skip this. The gate is enforced by APEXA's Analysis agent (`use_planning=True`).

---

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `KeyError: RawFolder` during validation | validate_parameter_file called with zarr input | Skip validate_parameter_file when data_file is a zarr; midas-pipeline uses --skip-validation |
| `Cannot access local variable 're'` on import | Import/runtime error in the server code loaded by the running process; a stale bytecode cache is not established by this message alone | Keep the traceback, verify which checkout and Python process are active, fix the reported source issue, then restart the top-level APEXA process with its normal launcher. Do not blanket-delete `.pyc` files or kill a child server by process name. |
| `process_grains` fails (`CalledProcessError` exit 1) with python refiner | python refiner writes no `FitBest.bin` → legacy fallback fails (still present 0.6.0) | Expected — APEXA auto-skips it; read IndexBest.bin, or use the c-omp backend pair for Grains.csv |
| Model hallucinates `~/opt/MIDAS` path | Model uses training-data path not actual MIDAS_PATH | Anti-hallucination guard in run_ff_hedm_full_workflow blocks this; actual path from MIDAS_PATH env var |
| `Grains.csv not found` after run | python refiner used | Normal — use IndexBest.bin for grain count |

---

## Parameter pitfalls — silent-corruption traps

These write no error and often exit 0, but silently corrupt the run. The
deterministic linter (`diagnose_parameter_file` → `handbook_traps`, and the
pre-dispatch gate inside `run_ff_hedm_full_workflow`) enforces the unambiguous
ones — a `⛔ error` trap BLOCKS the run until fixed. Know them anyway; set the
values right the first time.

| Parameter | Trap | Correct |
|---|---|---|
| **Width** | Ring half-width is in **µm** (default **1500**). Entering pixels (e.g. `Width 7.5`) makes a ~0.04 px band that rejects nearly every peak → empty `InputAll.csv` → aborted run. **⛔ blocks.** | ~1500 µm (7.5 px × 200 µm/px). *FF Parameters Reference* |
| **MarginRadius** | Also **µm** (default **500**). Same px-for-µm mistake. **⛔ blocks.** | ~500 µm. *FF Parameters Reference* |
| **BoxSize** | `Ymin Ymax Zmin Zmax` in **µm**. A collapsed span (e.g. `BoxSize -10000 0 0 0` → zero Z-span) makes the fit_setup keep-box reject **every** spot → all-zero `InputAll.csv` → the indexer dies two stages later on an empty `Data.bin` (`mmap Data.bin failed`). An **absent** BoxSize is safe (filter skipped). **⛔ blocks** when present-but-collapsed. | Permissive box, e.g. `BoxSize -1000000 1000000 -1000000 1000000`, or omit it. *FF Parameters Reference* |
| **Min/MaxOmeSpotIDsToIndex** | Seed ω-window (degrees). Both default to **0.0** with no auto-derivation → a degenerate single-point window at ω=0° keeps no spots → empty `SpotsToIndex.csv` → **0 indexing seeds → 0 grains** (a 4-byte `IndexBest_all.bin`), independent of RingToIndex. Looks like the indexer "ran" (loads all spots) but seeds = 0. **⛔ blocks** when both missing or Min ≥ Max; one-sided is a ⚠ warning. | Open to the full ω sweep, e.g. `MinOmeSpotIDsToIndex -180` / `MaxOmeSpotIDsToIndex 180`. *FF Parameters Reference* |
| **LatticeConstant + SpaceGroup** | Left as the **calibrant's** on a sample run — "the single most common copy-paste error." CeO₂ 5.4116 → the indexer looks for the wrong rings. | Replace with the **sample's**: e.g. gold 4.0782 (both SG 225). *FF §6* |
| **ImTransOpt** | Must **match the calibration**. A mismatch flips/transposes the detector frame → peaks land on the wrong rings. | Copy ImTransOpt from the calibration param file verbatim. *FF §6* |
| **RingThresh** | Copied from a **template** instead of the data → 0 peaks. | Run `calibrate_ring_thresholds` on the zarr, paste the per-ring values. *FF §6b* |
| **darkLoc / darkDataset** | A standalone dark `.h5` stores its frame at **`exchange/data`**; the integrator default `exchange/dark` reads nothing → all-zero dark → threshold-invariant 0-peak output. | Set BOTH to `exchange/data`. *FF §3d* |
| **OmegaStart / ω-sign** | Wrong sign silently mirrors orientations. | `-1` for an aero stage; confirm against the scan direction. *FF §2* |

---

## Workflow integration

Typical APEXA session for FF-HEDM (FF Handbook order):

1. **Calibrate** — `midas_auto_calibrate` → produces `refined_MIDAS_params_CeO2.txt`
2. **Integrate** — `midas_integrate_2d_to_1d` → produces `*_lineout.xy`, `*_caked.hdf.zarr.zip`
3. **Ring thresholds** — `calibrate_ring_thresholds(zarr_file=<...>.MIDAS.zip)` → paste-ready
   `RingThresh` lines from the data (FF §6b). Set these in the parameter file BEFORE
   reconstructing — never copy RingThresh from a template (→ 0 peaks). Also run
   `diagnose_parameter_file` and read its `handbook_traps` list (SkipFrame, dark loc,
   MinPeakSNR, Rsample-as-bound, calibrant lattice, ω-sign).
4. **Reconstruct** — `run_ff_hedm_full_workflow` (default `process_grains=True`). Use
   `refine_backend="c-omp"` to produce `LayerNr_<N>/Grains.csv` +
   `processgrains_diagnostics.h5` (FF §8); the python refiner only yields
   `IndexBest.bin` (see backend bug note above). Check `report_ready` in the result.
5. **Report** — `generate_ff_reconstruction_report(run_dir="<result>/LayerNr_1")` →
   self-contained `report.html` (FF §9). Needs `Grains.csv`; the diagnostics H5 adds
   spot-residual plates.
6. **Compare / visualize** — `read_grains_summary`; then
   `run_midas_viewer(viewer="plotGrains3d", data_file="<result_dir>")` (3D grain map →
   HTML), or `plotFFSpots3dGrains` for spots-by-grain. See [[midas-visualize]].

---

## Graph mode — deterministic setup with human-in-the-loop gates

Set `APEXA_WORKFLOW_MODE=graph` to run the **calibrate → in-plane tx → reconstruct**
setup as a checkpointed LangGraph state machine (`apexa_ffhedm_graph.py`) instead of
the open-ended agent loop. The graph makes the FF-HEDM handbook's decision points into
explicit control flow, so the ordering can't drift and each step runs exactly once:

- **which-calibrant** — if `cali/` holds more than one calibrant, it asks which to use
  (or `all`) instead of guessing; a single calibrant auto-selects.
- **propose-folders** — proposes separate output dirs (`APEXA_calib`, `APEXA_inplane_tx`,
  `APEXA_ff_recon`) and waits for confirmation before any write.
- **ω-sign** — auto-resolves `-1` for an aero stage, otherwise asks.
- **verify-calibration** — pauses if status is not ok or residual is missing / > 1.0 px.
- **mandatory ring overlay** — always confirms the calibrant rings sit on the data
  before reconstruction is allowed to start.
- **verify-grains** — writes the citable `APEXA_ffhedm_workflow.json` summary.

Gates surface as ordinary assistant questions and resume on your next turn — no special
UI, and the same in the CLI, web, and desktop apps. Combined with the **idempotency
guard** on the heavy tools (a content hash in `<output_dir>/.apexa_done.json` replays a
prior result when inputs and on-disk outputs are unchanged), this closes the
"3× duplicate calibration" failure mode by construction. Bypass the guard for a forced
re-run with `force=true` or `APEXA_IDEMPOTENCY=0`.

**Durable across restart.** The graph checkpoints to `~/.apexa/ffhedm_graph.sqlite`
(override `APEXA_FFHEDM_DB`; `APEXA_FFHEDM_DURABLE=0` for in-memory only). A run paused
on a gate survives a full APEXA restart — start APEXA again and your next message
resumes it mid-procedure. The checkpoint thread is the active session name, so
`session switch` swaps between in-flight workflows.

Design + rationale: `docs/LANGGRAPH_FF_HEDM_SPEC.md`. Default mode (unset) keeps the
single-loop agent behavior described above.

---

## MIDAS v11 key additions (vs v10)

| Feature | Command |
|---|---|
| GPU indexing | `--device cuda` |
| Checkpoint resume | `--resume auto` |
| Python differentiable refiner | `--refine-backend python` |
| Consolidated HDF5 | `--generate-h5` |
| Synthetic forward model | `midas-pipeline simulate` |
| V-map (volumetric) | `--vmap-run` |
| NF→FF seeded indexing | `--nf-result-dir <nf_output>` |
| Multi-GPU sharding | `--shard-gpus auto` |
