---
name: midas-visualize
description: Visualize MIDAS diffraction data, integrated lineouts, calibration results, and HEDM grain maps. Use when the user asks to plot, view, show, display, visualize, inspect a lineout, caked pattern, diffraction image, grain map, FF-HEDM spots, or NF microstructure.
compatibility: Requires midas_env conda environment (PyQt5, pyqtgraph, dash, plotly, zarr==2.18.3). All scripts in ~/Git/MIDAS/gui/ and ~/Git/MIDAS/gui/viewers/.
metadata:
  author: pawan-tripathi
  version: "2.0"
  midas-version: "11.0"
  manual: MIDAS/manuals/GUIs_and_Visualization.md
---

## Visualization Workflows (MIDAS v11)

### APEXA Agent Usage

For the Web UI right pane, use `open_science_view(path=..., kind="structure")`
for an existing local CIF, or `kind="sequence"` for local raw detector frames
(HDF5/TIFF/Fabio formats). It validates the file and emits a native canvas
artifact. Use `dataset` for ambiguous HDF5 contents and `pattern` for sample-only
directory sequences. Files do not need to live in the project directory.
Do not generate HTML, invoke a desktop `open`, or copy a CIF merely to display it.
These native views take precedence over the raw-image desktop viewer below when
the user wants the Web UI canvas. MP/COD fetch results also produce CIF artifacts.

When running through APEXA, use the `run_midas_viewer` MCP tool instead of constructing
manual Python commands for the MIDAS analysis viewers below. The tool handles MIDAS path resolution and Python environment
automatically.

**All 17 viewer names** (`run_midas_viewer(viewer=...)`):

| Viewer | Kind | Input |
|---|---|---|
| `ff_asym_qt` | Qt GUI | raw 2D image |
| `nf_qt` | Qt GUI | NF `.mic` |
| `plot_lineout_results` | Qt GUI | `*_lineout.xy` (dir) |
| `plot_lineout_comparison` | CLI | lineout + `--paramFN` |
| `plot_integrator_peaks` | CLI | `*.zarr.zip` |
| `plot_caked_peaks` | Qt GUI | caked zarr / dir |
| `viz_caking` | Dash web | caked `*.zarr.zip` |
| `plot_calibrant_results` | Qt GUI | `*corr.csv` |
| `plot_phase_id_results` | Qt GUI | phase-id dir |
| `interactiveFFplotting` | Dash web | FF result dir + zarr |
| `PlotFFNF` | Qt GUI | FF+NF dir |
| `live_viewer` | Qt/CLI | `lineout.bin` |
| `plotGrains3d` | Plotly HTML | FF/PF result dir (`Grains.csv`) |
| `plotFFSpots3d` | Plotly HTML | result dir (`InputAll.csv`) |
| `plotFFSpots3dGrains` | Plotly HTML | result dir (`SpotMatrix.csv`+`Grains.csv`) |
| `pfIntensityViewer` | Dash web | PF param file |
| `peak_sigma_statistics` | CLI | results dir (`LayerNr_*`) |

Example:
```
TOOL_CALL: run_midas_viewer
ARGUMENTS: {"viewer": "plot_lineout_results", "data_file": "/path/to/lineout.xy", "param_file": "/path/to/params.txt"}
```

The Plotly viewers (`plotGrains3d`, `plotFFSpots3d`, `plotFFSpots3dGrains`) run to
completion, write standalone `.html` into the result folder, and the tool returns
the generated file paths in `html_files`. The Dash viewers (`viz_caking`,
`pfIntensityViewer`, `interactiveFFplotting`) launch detached and print a localhost
URL. Qt GUIs launch detached.

---

## Input file → correct viewer

| You have | Use | Where |
|---|---|---|
| Raw 2D diffraction image (`.tif`, `.ge`, `.h5`, `.zip`) | `ff_asym_qt.py` | `gui/` |
| `*_lineout.xy` (integrated 1D) | `plot_lineout_results.py` or `plot_lineout_comparison.py` | `gui/viewers/` |
| `*_lineout.bin` (GPU streaming, live) | `live_viewer.py` | `gui/viewers/` |
| `*_caked.hdf.zarr.zip` (2D caked output) | `plot_integrator_peaks.py` or `viz_caking.py` | `gui/viewers/` |
| `*_caked_peaks.h5` (fitted peaks) | `plot_caked_peaks.py` | `gui/viewers/` |
| `*corr.csv` (calibration residuals) | `plot_calibrant_results.py` | `gui/viewers/` |
| `Grains.csv` (FF/PF result dir) | `plotGrains3d.py` (3D grain map → HTML) | `gui/viewers/` |
| `InputAll.csv` (FF spots) | `plotFFSpots3d.py` (→ HTML) | `gui/viewers/` |
| `SpotMatrix.csv` + `Grains.csv` | `plotFFSpots3dGrains.py` (spots by grain → HTML) | `gui/viewers/` |
| `Grains.csv` + `SpotMatrix.csv` + `.zarr` | `interactiveFFplotting.py` (Dash browser) | `gui/viewers/` |
| `LayerNr_*` result tree | `peak_sigma_statistics.py` (peak-width stats) | `gui/viewers/` |
| NF `.mic` / `.map` microstructure | `nf_qt.py` | `gui/` |
| FF + NF results (overlay) | `PlotFFNF.py` | `gui/viewers/` |
| PF-HEDM sinograms / intensity | `pfIntensityViewer.py` | `gui/viewers/` |

---

## 1. FF-HEDM Image Viewer — `ff_asym_qt.py` (recommended)

**PyQtGraph-based. Auto-detects data files from current directory.**

```bash
cd <data_directory>
python ~/Git/MIDAS/gui/ff_asym_qt.py &
```

**Key features:**
- Auto-detects `.tif`, `.ge`, `.h5`, `.zip` from CWD
- Loading a MIDAS `.zip` auto-populates Lsd, BC, Wavelength, SpaceGroup from metadata
- **Ring overlays**: click "Rings Material" → ideal ring positions live-updated on BC/Lsd change
- Nearest ring + hkl shown in status bar at cursor
- P2–P98 auto-scaling, log scale, HFlip/VFlip/Transpose
- Frame navigation (← → or Ctrl+scroll), Max/Sum over frames
- Movie mode (Play/Pause/Stop, 1–30 FPS), drag-and-drop, session save (Ctrl+S)
- Export PNG

**Keyboard shortcuts:**

| Key | Action |
|---|---|
| ← / → | Previous / Next frame |
| L | Toggle log scale |
| R | Toggle ring overlay |
| Q | Quit |
| Ctrl+S | Save session |

> **Note:** Mouse-wheel zoom is disabled (SSH/remote display optimization). Use the Pan/Zoom toolbar buttons. Double-click resets to full view.

**Legacy Tkinter version:** `gui/ff_asym.py` — same features, slower rendering.

---

## 2. Lineout Viewers

### Plot single lineout — `plot_lineout_results.py`

```bash
cd <integration_directory>
python ~/Git/MIDAS/gui/viewers/plot_lineout_results.py \
    CeO2_000001.tif.analysis.MIDAS_lineout.xy \
    --params ../refined_MIDAS_params_CeO2.txt
```

`--params` enables 2θ and Q axes. Without it, x-axis is R (pixels).

### Lineout + ideal ring overlay — `plot_lineout_comparison.py`

```bash
python ~/Git/MIDAS/gui/viewers/plot_lineout_comparison.py \
    --paramFN refined_MIDAS_params_CeO2.txt \
    CeO2_000001.tif.analysis.MIDAS_lineout.xy
```

Overlays ideal CeO2 (or other calibrant) ring positions. Good for verifying calibration quality.

---

## 3. Caked Output Viewers

### Peak fitting from zarr.zip — `plot_integrator_peaks.py`

```bash
python ~/Git/MIDAS/gui/viewers/plot_integrator_peaks.py \
    --zarr scan_01.caked.hdf.zarr.zip \
    --peaks 245.3 347.1 425.8 \
    --frame -1
```

Fits Pseudo-Voigt peaks along 2θ for each η slice. Output: 2D scatter of fitted 2θ vs η with ring assignment.

### Caked peak viewer — `plot_caked_peaks.py`

For `_caked_peaks.h5` output (from `IntegratorZarrOMP` or `fit_caked_peaks.py`):

```bash
python ~/Git/MIDAS/gui/viewers/plot_caked_peaks.py /path/to/results/

# With lattice parameter analysis:
python ~/Git/MIDAS/gui/viewers/plot_caked_peaks.py /path/to/results/ \
    -paramFN refined_MIDAS_params_CeO2.txt
```

**Four-panel display:**
1. Caked heatmap (2θ × η), interactive crosshair
2. 1D profile at selected η bin with fitted peak overlays
3. Peak data table: center, area, FWHM, sig, gam, η_mix, d-spacing, χ²
4. (with `--paramFN`) Lattice parameter *a* vs η + relative strain Δa/a₀ vs η

First run `fit_caked_peaks.py` to generate `_caked_peaks.h5` if it doesn't exist:
```bash
python ~/Git/MIDAS/utils/fit_caked_peaks.py \
    scan_01.caked.hdf.zarr.zip \
    -paramFN refined_MIDAS_params_CeO2.txt
```

### 2D caking visualization — `viz_caking.py`

```bash
python ~/Git/MIDAS/gui/viewers/viz_caking.py \
    scan_01.caked.hdf.zarr.zip
```

---

## 4. Calibration QC — `plot_calibrant_results.py`

Quick scatter plot of lattice parameter vs η from `*corr.csv`. In v11 the corr.csv
is produced by the native `midas_calibrate` engine / `CalibrantIntegratorOMP` (the
archived `CalibrantPanelShiftsOMP` is no longer used):

```bash
python ~/Git/MIDAS/gui/viewers/plot_calibrant_results.py \
    CeO2_000001..tif.corr.csv \
    --paramFN refined_MIDAS_params_CeO2.txt
```

Via APEXA: `run_midas_viewer(viewer="plot_calibrant_results", data_file="<...corr.csv>")`.

---

## 5. Live Integration Dashboard — `live_viewer.py`

For real-time monitoring **during GPU streaming experiments** only. Tails `lineout.bin` and `fit.bin`.

```bash
cd <integration_directory>
python ~/Git/MIDAS/gui/viewers/live_viewer.py \
    --lineout lineout.bin \
    --nRBins 500 \
    --params refined_MIDAS_params_CeO2.txt
```

With peak fitting output:
```bash
python ~/Git/MIDAS/gui/viewers/live_viewer.py \
    --lineout lineout.bin --nRBins 500 \
    --fit fit.bin --nPeaks 3 \
    --params setup.txt
```

**Critical:** flag is `--nRBins` (capital B, capital R) — case-sensitive.
`--params` enables triple x-axes (R, 2θ, Q).

**Three-panel display:** 1D Lineout | Heatmap (R × frame waterfall) | Peak evolution vs frame

---

## 6. FF-HEDM Interactive Browser — `interactiveFFplotting.py`

Browser-based (Plotly Dash) for exploring grain maps, spot matrices, and reciprocal space after FF-HEDM reconstruction.

**Required inputs:**
- `Grains.csv` — reconstructed grain centroids, Euler angles, completeness, strain
- `SpotMatrix.csv` — all matched diffraction spots per grain
- `.zarr` file — raw detector data (for volume rendering of individual spots)

```bash
python ~/Git/MIDAS/gui/viewers/interactiveFFplotting.py \
    -resultFolder /path/to/ff_results \
    -dataFileName /path/to/data.zarr
```

Then open `http://localhost:8050` in browser.

| Argument | Description | Default |
|---|---|---|
| `-resultFolder` | Folder with `Grains.csv`, `SpotMatrix.csv` | **required** |
| `-dataFileName` | Path to `.zarr` raw data file | **required** |
| `-HostName` | IP to host on (`0.0.0.0` for remote access) | `0.0.0.0` |
| `-portNr` | Port number | `8050` |

**Workflow:**
1. **Global views** (top): All Spots 3D + G-vectors reciprocal space — color by ringNr/strain
2. **Filter grains** (middle): Eta/2θ/Omega range sliders + Grain ID search
3. **Grain selection** (bottom-left): click a grain → see its spots in 3D and 2D
4. **Volume rendering** (bottom-right): click a spot in 2D → raw detector volume from Zarr
5. **Spot details table**: sortable/filterable table of all spots for selected grain

---

## 6b. 3D grain / spot maps (Plotly HTML) — FF & PF results

These take an FF/PF **result folder** (`-resultFolder`), write standalone
interactive `.html`, and exit. Via APEXA the tool returns the generated files in
`html_files`. Best first look at a finished reconstruction — no GUI/Qt needed.

```
run_midas_viewer(viewer="plotGrains3d",        data_file="/path/to/result_dir")
run_midas_viewer(viewer="plotFFSpots3d",       data_file="/path/to/result_dir")
run_midas_viewer(viewer="plotFFSpots3dGrains", data_file="/path/to/result_dir")
```

| Viewer | Reads | Produces |
|---|---|---|
| `plotGrains3d` | `Grains.csv` | `grains3DConfidence.html`, `grains3DID.html` — 3D grain centroids sized by GrainSize, colored by confidence / grain ID |
| `plotFFSpots3d` | `InputAll.csv` | `spots3D.html`, `spots3D_polar.html` — all FF spots colored by ring number |
| `plotFFSpots3dGrains` | `SpotMatrix.csv` + `Grains.csv` | `spots3Dgrains.html`, `spots3DgrainsSelector.html` — spots colored by grain, per-grain visibility toggles |

> Requires `plotly` in `midas_env` (present by default). For PF V-map voxel
> orientation/strain maps, render `v_map.h5` directly or use `pfIntensityViewer`.

## 6b′. FF reconstruction report (HTML) — `generate_ff_reconstruction_report`

A dedicated **whole-run report** (FF Handbook §9), separate from the 3D viewers.
Wraps `utils/midas_ff_report.py`: reads a `LayerNr_<N>/Grains.csv` (+ optional
`processgrains_diagnostics.h5`) and emits ONE self-contained `report.html` with all
figures base64-embedded — grain/completeness stats, orientation & position maps,
per-grain strain, and (with the diagnostics H5) spot-residual plates + auto-findings.

```
generate_ff_reconstruction_report(run_dir="/path/to/result/LayerNr_1")
# optional: material="Au", c11=..., c12=... (stress), beam_height=... (µm)
```

Needs `Grains.csv` — produce it with `run_ff_hedm_full_workflow(refine_backend="c-omp",
process_grains=True)`. Without `processgrains_diagnostics.h5` the report still renders
(grain/orientation/strain panels), just without the residual plates. Use this for a
publishable summary; use `plotGrains3d`/`plotFFSpots3dGrains` for interactive inspection.

## 6c. PF-HEDM sinogram / intensity — `pfIntensityViewer.py` (Dash)

```
run_midas_viewer(viewer="pfIntensityViewer",
                 data_file="<PF params.txt>",
                 param_file="<result dir>")     # optional -resultDir
```
Launches a Dash web app (default port 8051) for browsing PF sinograms and
per-voxel intensity. Detached — open the printed localhost URL.

## 6d. Peak-width statistics — `peak_sigma_statistics.py`

```
run_midas_viewer(viewer="peak_sigma_statistics",
                 data_file="<results dir with LayerNr_*>",
                 param_file="<params.txt>")      # optional --paramFN
```
Aggregates fitted peak sigma (width) across `LayerNr_*` folders — a quick strain/
microstructure-broadening QC after FF reconstruction.

## 7. NF-HEDM Viewer — `nf_qt.py`

```bash
cd <nf_data_directory>
python ~/Git/MIDAS/gui/nf_qt.py &
```

All FF viewer features plus:
- **Microstructure overlay**: load `.mic`/`.map`, color by Confidence, GrainID, Euler, KAM, GROD, Phase
- **Spot simulation**: GrainDialog for simulating expected diffraction spots
- **Select Spots workflow**: right-click to pick spots (left-click = zoom); "Compute Distances" auto-triangulates → Lsd per distance
- **Box H/V ROI**: rectangular region with live Sum/Mean/Min/Max statistics
- **Median subtraction**: per-frame background removal

---

## 8. Requirements

All viewers require `midas_env`:
```bash
conda activate midas_env
```

| Viewer type | Dependencies |
|---|---|
| PyQtGraph GUIs (`ff_asym_qt.py`, `nf_qt.py`, `live_viewer.py`, `plot_caked_peaks.py`) | PyQt5, pyqtgraph |
| Dash browser (`interactiveFFplotting.py`, `pfIntensityViewer.py`) | dash, dash-bootstrap-components, plotly, pandas |
| Matplotlib utilities (`plot_lineout_results.py`, etc.) | matplotlib, numpy |

If PyQt5 is missing: `pip install PyQt5 pyqtgraph`
If Dash is missing: `pip install dash dash-bootstrap-components plotly`
