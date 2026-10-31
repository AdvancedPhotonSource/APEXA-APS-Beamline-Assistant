---
name: midas-integrate
description: Integrate 2D diffraction images to 1D patterns using MIDAS. Use when the user asks to integrate, cake, produce a lineout, convert 2D to 1D, run azimuthal integration, process diffraction frames for phase identification, GSAS-II, or live/real-time streaming integration.
compatibility: Requires MIDAS v11 (FF_HEDM/workflows/), midas_env conda environment (zarr==2.18.3, numpy, scipy), calibrated params file from midas_auto_calibrate
metadata:
  author: pawan-tripathi
  version: "3.1"
  midas-version: "11.0"
  package: "midas_integrate"
  manual: MIDAS/manuals/FF_Radial_Integration.md
---

## Integration Workflows (MIDAS v11)

In v11, single-frame integration is the **native `midas_integrate` package**, run
in-process by APEXA (detector-map → integrate → 1D profile). The
`integrator.py` script is the subprocess fallback when the package isn't
importable; `midas_batch_integrate` and the GPU streaming path still use the
scripts because they orchestrate multi-frame / multi-panel / auto-mapping work the
single-frame package CLI doesn't cover.

| | **Native (single frame)** | **Workflow A — GPU Streaming** | **Workflow B — CPU Batch** |
|---|---|---|---|
| Path | `midas_integrate` in-process | `FF_HEDM/workflows/integrator_batch_process.py` | `FF_HEDM/workflows/integrator.py` |
| Best for | One frame, laptop/beamline quick-check | Real-time / high-throughput | Post-experiment, many files |
| Engine | PyTorch CSR integrator | `IntegratorFitPeaksGPUStream` (CUDA) | `IntegratorZarrOMP` (OpenMP) |
| MCP tool | `midas_integrate_2d_to_1d` | *(script via `run_command`)* | `midas_batch_integrate` |

Underlying package CLIs (the v11 substrate; called automatically — listed for
`run_command` use): `midas-detector-mapper` (build `Map.bin`/`nMap.bin`),
`midas-integrate` (integrate one frame), `midas-integrate-export-csv` (lineouts +
REtaMap → CSV). `midas_integrate_2d_to_1d` runs the equivalent in-process.

> **The old `Integrator` C binary is gone — never use it.** Set
> `APEXA_USE_NATIVE_MIDAS=0` to force the `integrator.py` subprocess path.

### Many SEPARATE files (a scan/series) → `midas_integrate_series` — never loop

`midas_batch_integrate` handles a **frame range inside one file**. For a directory
of many **separate** files (e.g. an overnight echem scan `JL_0Nb_*.vrx.h5`), call
`midas_integrate_series` **once** — do **NOT** call `midas_integrate_2d_to_1d` once
per file. Looping the single-file tool burns the agent iteration budget, hits the
iteration cap, and forces a finalize where results get summarised from memory
(fabricated file lists — a real failure we have observed). One call = one manifest,
per-file verified outputs.

- **Subset first:** `max_files=3` (evenly-spaced start/middle/end) to smoke-test the
  params + dark matching, *then* re-run with `max_files` omitted for the full scan.
- Per-file darks are matched by nearest file number (`dark_after_<n+1>`); pass a
  single `dark_file=` to override.
- Writes `APEXA_integration_series.json` (real paths + per-file `success`/`error`).

```python
midas_integrate_series(
    parameter_file=".../JLi_params_calib_guess.txt",
    image_dir=".../overnight_scan_JLi",
    pattern="JL_0Nb_*.h5",
    max_files=3,                    # subset first; omit for the full scan
    result_folder=".../APEXA_benchmark/integration/series",
)
```

For a directory of separate TIFF frames, set the input `pattern` explicitly to
`"*.tif"` or `"*.tiff"`; the tool default is `"*.h5"`. Confirm the selected MIDAS
runtime can read that TIFF layout before processing the full series. An explicitly
supplied `images` list is processed in the order supplied; directory discovery is
sorted by numeric filename suffix with a stable filename tie-break.

Do not infer dark pairing from a directory called `Background` or from the nearest
frame number alone. Verify that the candidate is a detector dark/background for
the same exposure and attenuation, and that the facility workflow intends pixelwise
subtraction. This MCP tool accepts one `dark_file` for all patterns or matches
individual files by naming convention. It does not combine a background stack;
when a homogeneous TIFF background group is scientifically appropriate, use
combine_detector_backgrounds with method="mean" or "median" and an explicit
output_file first. The combiner records its inputs and output but does not assess
correction suitability. Verify that the selected MIDAS runtime reads its float32
TIFF before passing it as dark_file. If no valid correction is established, make
the no-dark choice explicit and record it in the pilot manifest.

### Report ONLY verified outputs (all integration tools)

Every integration tool returns the **real** output paths it found on disk and
writes an `APEXA_integration*.json` manifest. When reporting: cite **only** those
paths / manifest entries. Never construct an expected `*_lineout.xy` name yourself,
and never call a file integrated unless its result/manifest marks it `success` —
`incomplete`/`error` means **no lineout was produced**. If a run was interrupted
(iteration cap, gateway error), say so explicitly and report only what the
manifests confirm.

---

## Agent prompting protocol (run BEFORE calling either MCP tool)

Integration is **not silent-defaults safe** — the 2θ window and azimuthal slice
depend on operator intent, and for batch runs the output location matters.

### Always (single image AND batch): confirm R/Eta ranges

1. **Read the calibrated params file** (`refined_MIDAS_params*.txt`) and extract:
   `RMin`, `RMax`, `RBinSize`, `EtaMin`, `EtaMax`, `EtaBinSize`.
2. **Show those values back to the user** verbatim, then ask:
   "Use these ranges or override (e.g. zoom into a single ring, restrict eta to a
   sector, change radial bin size for higher resolution)?"
3. Forward via `r_min` / `r_max` / `r_bin_size` / `eta_min` / `eta_max` /
   `eta_bin_size` kwargs.

### Confirm `result_folder` ONLY for batch (`midas_batch_integrate`)

- **Single image (`midas_integrate_2d_to_1d`)** → don't prompt. The default
  `<image_dir>/integration` is derived from the image the user just named, lands
  next to the data, and is almost always what they want. Asking every time is
  noise.
- **Batch (`midas_batch_integrate`)** → **prompt.** The default is
  `./integration_results` relative to the agent's CWD, which the user can't
  predict and which scatters outputs in the wrong place. Suggest a sensible
  default like `<data_file_dir>/integration_results` and ask "use this or
  specify another?"

> Skip both prompts if the user already specified ranges/folder in the same
> turn (e.g. "integrate the first 5 rings, eta -90 to 90, output to /tmp/run1").
> Re-confirming twice is noise.

---

## Workflow B (CPU): MCP Tools

### Single image → `midas_integrate_2d_to_1d`

`calibration_file` is **optional** — auto-detected from image directory if omitted.

```
midas_integrate_2d_to_1d(
    image_file       = "<absolute path to image>",
    calibration_file = "<refined_MIDAS_params*.txt>",  # optional, auto-found
    dark_file        = "<dark image if available>",
    result_folder    = "<output dir>",                  # default: <image_dir>/integration  (don't prompt — default is fine)
    n_cpus           = 4,
    convert_files    = True,

    # R/Eta overrides — pass when user wanted different ranges than the params file.
    # Defaults: from refined_MIDAS_params*.txt (RMin/RMax/RBinSize/EtaMin/EtaMax/EtaBinSize).
    r_min            = None,   # e.g. 200  (zoom past the beamstop)
    r_max            = None,   # e.g. 800  (limit to first N rings)
    r_bin_size       = None,   # e.g. 0.25 (finer Δ2θ for sharper peaks)
    eta_min          = None,   # e.g. -90  (restrict to half the detector)
    eta_max          = None,   # e.g.  90
    eta_bin_size     = None,   # e.g. 1    (finer azimuthal slices)
)
```

### Batch → `midas_batch_integrate`

```
midas_batch_integrate(
    data_file      = "<path to HDF5 or first image>",
    dark_file      = "<dark file>",
    parameter_file = "<refined_MIDAS_params*.txt>",
    start_frame    = <int>,
    end_frame      = <int>,
    result_folder  = "./integration_results",   # CONFIRM with user
    num_cpus       = 10,
    map_detector   = True,
    convert_files  = True,

    # Same R/Eta overrides as the single-image tool — None = use params-file values.
    r_min          = None, r_max        = None, r_bin_size   = None,
    eta_min        = None, eta_max      = None, eta_bin_size = None,
)
```

---

## Workflow A (GPU): integrator_batch_process.py

Use `run_command` to invoke directly. Requires MIDAS compiled with CUDA.

**Process a folder of TIFFs:**
```bash
python ~/Git/MIDAS/FF_HEDM/workflows/integrator_batch_process.py \
    --param-file refined_MIDAS_params_CeO2.txt \
    --folder /data/experiment/scan_01 \
    --dark /data/experiment/dark.bin \
    --output-h5 scan_01_integrated.h5
```

**Live streaming from EPICS PVA detector:**
```bash
python ~/Git/MIDAS/FF_HEDM/workflows/integrator_batch_process.py \
    --param-file setup.txt \
    --pva --pva-ip 10.54.105.139 \
    --output-h5 live_analysis.h5
```

### integrator_batch_process.py key flags

| Flag | Description |
|---|---|
| `--param-file` | **Required.** Parameter file path |
| `--folder` | Source folder (`.tif`, `.ge`, etc.) — mutually exclusive with `--file`, `--pva` |
| `--file` | Single image file |
| `--pva` | Listen to EPICS PVA stream |
| `--pva-ip` | PVA detector IP address |
| `--dark` | Dark field file |
| `--output-h5` | Final consolidated HDF5 output filename |
| `--output-dir` | Output directory (default: `analysis_YYYYMMDD_HHMMSS`) |
| `--zarr-output` | Custom zarr.zip filename (default: auto from `--output-h5`) |
| `--no-zarr` | Skip zarr.zip creation (HDF5 only) |

---

## integrator.py (Workflow B) — CLI reference

### How integrator.py uses files (critical)

- `-paramFN` and `-dataFN` are **both required**
- `-dataFN` must contain a **6-digit zero-padded number** (e.g. `CeO2_000001.tif` or `scan_004018.tif`)
- File number extracted with regex `\d{6}` — determines which frame(s) to process
- Use the MIDAS-friendly symlink (e.g. `CeO2_000001.tif`) as `-dataFN` when the original filename has a complex name
- `integrator.py` self-patches its own PYTHONPATH — no external PYTHONPATH needed
- Script location: `MIDAS/FF_HEDM/workflows/integrator.py` (NOT `utils/`)

### integrator.py CLI flags

| Flag | Default | Notes |
|---|---|---|
| `-paramFN` | required | Parameter file path |
| `-dataFN` | required | First data file (6-digit number in name) |
| `-resultFolder` | `.` | Output directory |
| `-darkFN` | `''` | Dark field image |
| `-nCPUs` | `1` | Simultaneous files (parallel) |
| `-nCPUsLocal` | `4` | OMP threads per file |
| `-mapDetector` | `1` | Run DetectorMapper if Map.bin missing |
| `-convertFiles` | `1` | Convert input to Zarr before integrating |
| `-startFileNr` | `-1` | Start frame (-1 = read from dataFN) |
| `-endFileNr` | `-1` | End frame (-1 = single file) |
| `-writeMat` | `0` | Write .mat output |
| `-skipExisting` | flag | Skip already-processed files |
| `-dataLoc` | `exchange/data` | HDF5 dataset path |
| `-darkLoc` | `exchange/dark` | Dark HDF5 dataset path |
| `-shortNames` | `1` | **NEW v11.** Output as `<stem>.zarr.zip` (default). Set `0` for legacy `<stem>.h5.analysis.MIDAS.zip.caked.hdf.zarr.zip` suffix-stacking. |
| `-outName` | `''` | **NEW v11.** Override the zarr.zip stem entirely (single-file runs only — errors on multi-file). |
| `-brightFN` | `''` | **NEW v11.** Bright/flat-field image; 1D + 2D profiles embedded under `processed/bright/` in each output zarr for downstream normalization. |
| `-csvOutput` | `0` | **NEW v11.** Also export per-frame lineouts + REtaMap as CSVs alongside each zarr.zip via `midas-integrate-export-csv`. |

**Parameter overrides** (append to end of command):
```bash
integrator.py -paramFN setup.txt -dataFN scan_001.tif MinRad 10 MaxRad 1000 RBinSize 0.5
```

---

## Executable chain

```
Workflow B (CPU):
  integrator.py
    ├─ DetectorMapper      → Map.bin, nMap.bin, maskMap.bin  (first run only)
    └─ IntegratorZarrOMP   → _lineout.xy + <stem>.zarr.zip per file
                             (legacy: <stem>.h5.analysis.MIDAS.zip.caked.hdf.zarr.zip
                              when -shortNames=0)

Workflow A (GPU):
  integrator_batch_process.py
    ├─ integrator_server.py  (TCP socket server)
    ├─ DetectorMapper        → Map.bin
    └─ IntegratorFitPeaksGPUStream → HDF5 + zarr.zip + fit.bin (peak fits)
```

> **DetectorMapper** is now a unified binary — `DetectorMapperZarr` is retired. Handles both text and Zarr inputs. Pass `-nCPUs N` to parallelize mapping.

---

## Output files

| File | Workflow | Use |
|---|---|---|
| `*_lineout.xy` | B (CPU) | 2θ (degrees) vs intensity text — load in GSAS-II, plot directly |
| `*_lineout.bin` | B (CPU) | Binary lineout (backward compatible) |
| `<stem>.zarr.zip` | Both | Full caked data (default v11 with `-shortNames=1`) — GSAS-II compatible (`zarr==2.18.3`) |
| `<stem>.h5.analysis.MIDAS.zip.caked.hdf.zarr.zip` | Both | Legacy long-form (only when `-shortNames=0`) |
| `*_lineouts.csv`, `*_REtaMap.csv` | B (CPU) | Optional CSVs when `-csvOutput=1` (uses `midas-integrate-export-csv`) |
| `Map.bin`, `nMap.bin`, `maskMap.bin` | Both | Geometry maps (generated once per detector/params) |
| `scan_01_integrated.h5` | A (GPU) | Consolidated HDF5 with lineouts + fit results |
| `fit.bin` | Both (if peak fit) | Binary stream: 7 doubles/peak/frame (Area, Center, sig, gam, FWHM, η, χ²) |
| `_caked_peaks.h5` | Both (if peak fit) | HDF5 with per-η fitted peaks for `plot_caked_peaks.py` |

---

## Parameter file keys

From `refined_MIDAS_params*.txt` (auto-written by `midas_auto_calibrate`):

```
# Geometry (required)
Lsd        <µm>       sample-to-detector distance
BC         <Y> <Z>    beam center (pixels)
Wavelength <Å>
px         <µm>       pixel size
tx ty tz   <deg>      detector tilts

# Integration range (required)
RMin       <px>       integration range start
RMax       <px>       integration range end
RBinSize   <px>       radial bin size
EtaMin     <deg>
EtaMax     <deg>
EtaBinSize <deg>

# File info (required)
DataType   6          (6=TIFF, 8=HDF5)
NrPixelsY  <int>
NrPixelsZ  <int>
FileStem   <stem>
Folder     <path>

# Q-spacing mode (optional — replaces RMin/RMax/RBinSize when all present)
QBinSize   <Å⁻¹>
QMin       <Å⁻¹>
QMax       <Å⁻¹>

# Corrections (optional)
ImTransOpt 0          (0=none, 1=FlipH, 2=FlipV, 3=Transpose)
MaskFile   <path>     uint8 TIFF, 0=valid, 1=masked
BadPxIntensity -2
GapIntensity   -1
p0..p5         distortion coefficients

# Peak fitting (optional)
DoPeakFit      1      enable 1D Pseudo-Voigt peak fitting
MultiplePeaks  1      allow multiple peaks
PeakLocation   <px>   expected peak radius — repeatable, one per line
DoSmoothing    1      Savitzky-Golay smoothing before auto peak detection
FitROIPadding  20     half-width of fitting ROI (radial bins)
FitROIAuto     0      1 = auto-size ROI from FWHM
```

> **`midas_auto_calibrate` writes all geometry + range keys automatically.** No manual editing needed for standard integration.

> **⚠ Empty-value lines (e.g. `Dark ` with no value) crash `ffGenerateZipRefactor.py`.** The server strips these automatically after calibration, but check manually if editing the params file.

---

## Peak fitting (both engines)

Both `IntegratorZarrOMP` (CPU) and `IntegratorFitPeaksGPUStream` (GPU) support **1D Pseudo-Voigt peak fitting** (GSAS-II area-normalized, TCH mixing).

Two modes:
- **User-specified:** Add `PeakLocation <px>` lines to params file (one per ring). Implicitly enables `DoPeakFit 1` and `MultiplePeaks 1`.
- **Auto-discovery:** Set `DoPeakFit 1` + `MultiplePeaks 1` without `PeakLocation`. Engine finds peaks via SNIP baseline + local maxima.

`fit.bin` format: 7 doubles per peak per frame — `[Area, Center, sig, gam, FWHM, η, χ²]`

```python
import numpy as np
n_peaks = 3
data = np.fromfile('fit.bin', dtype=np.float64).reshape(-1, n_peaks, 7)
area, center, fwhm = data[:,:,0], data[:,:,1], data[:,:,4]
```

---

## Post-processing tools

| Tool | Use |
|---|---|
| `gui/viewers/live_viewer.py` | Real-time PyQtGraph dashboard — tails `lineout.bin` + `fit.bin` during GPU streaming |
| `gui/viewers/plot_caked_peaks.py` | Interactive Qt viewer for `_caked_peaks.h5` — 4-panel: heatmap, 1D profile, peak table, lattice plots |
| `gui/viewers/plot_integrator_peaks.py` | Offline Pseudo-Voigt fitting from `.caked.hdf.zarr.zip` |
| `utils/fit_caked_peaks.py` | Standalone peak fitter — produces `_caked_peaks.h5` |
| `utils/extract_lineouts.py` | Batch `_lineout.xy` extraction for a file series |
| `gui/viewers/plot_lineout_comparison.py` | Overlay calibrant + integrator lineouts vs ideal ring positions |

---

## Supported input formats

`.tif/.tiff`, `.ge/.ge1-.ge5`, `.h5/.hdf5/.nxs`, `.zip` (Zarr)
