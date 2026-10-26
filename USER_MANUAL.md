# APEXA User Manual
**Advanced Photon EXperiment Assistant**

Your AI beamline scientist for real-time HEDM data analysis at APS.

---

## Quick Start

### Option 1: Gradio UI (Recommended)

```bash
./start_gradio_ui.sh
```

Opens http://localhost:7860 with conversational chat, drag-and-drop file uploads, and embedded visualizations.

### Option 2: Command Line (Power Users)

```bash
./start_beamline_assistant.sh
```

Terminal interface with command history and tab completion.

### Option 3: Web UI

```bash
python web_server.py
```

Traditional forms for calibration, integration, and visualization at http://localhost:8000.

---

## How APEXA Works

APEXA uses a multi-agent architecture. Your natural language request is routed by the **OrchestratorAgent** to the right specialist:

| Agent | Handles | Key Tools |
|---|---|---|
| **CalibrationAgent** | Calibration, beam center, detector distance | `midas_auto_calibrate`, `xray_calculate` |
| **AnalysisAgent** | Integration, GSAS-II refinement, FF/NF/PF-HEDM workflows | `midas_integrate_2d_to_1d`, `run_gsas_refinement`, `run_live_analysis` |
| **KnowledgeAgent** | Explanations, material properties, CIF files | `query_hedm_knowledge`, `get_material_properties`, `fetch_cif_from_mp` |
| **VisualizationAgent** | Plotting, viewing lineouts, caked data | `run_midas_viewer` (launches MIDAS viewer scripts) |

You don't need to know which agent handles your request -- just ask naturally.

---

## Detector Calibration

Calibrate detector geometry using calibrant powders (CeO2, LaB6, Si, Al2O3).

**Example Prompts:**
```
APEXA> calibrate the CeO2 image in test1
APEXA> calibrate using LaB6_650mm.tif with 40 iterations
APEXA> run calibration on the ceria file with initial parameters
```

**What happens:**
1. APEXA finds the calibrant image and parameter file
2. Runs MIDAS `AutoCalibrateZarr.py` (iterative ring-fitting)
3. Refines: beam center (BC), detector distance (Lsd), tilts (tx/ty/tz), distortion (p0-p5)
4. Outputs: `refined_MIDAS_params_<material>.txt`, `autocal.log`

**Supported Formats:** `.tif`, `.tiff`, `.ge`, `.ge1`-`.ge5`, `.h5`, `.hdf5`, `.nxs`

**Convergence:** Final mean strain < 0.001 is good. If strain is high, increase iterations or check initial parameters.

---

## 2D to 1D Integration

Integrate diffraction images to 1D intensity vs 2-theta patterns.

### Single Image
```
APEXA> integrate CeO2_000001.tif in test1 using the refined params
APEXA> integrate the .tif file with dark subtraction
```

The `calibration_file` is optional -- APEXA auto-detects `refined_MIDAS_params*.txt` in the image directory.

### Batch Integration
```
APEXA> batch integrate all .tif files in /data with 8 CPUs
APEXA> batch integrate frames 1 to 100
```

### What It Produces

| Output File | Description |
|---|---|
| `*_lineout.xy` | 2-theta (degrees) vs intensity -- text file, GSAS-II compatible |
| `*_lineout.bin` | Binary lineout |
| `*_caked.hdf.zarr.zip` | Full caked data -- GSAS-II zarr importer compatible |
| `Map.bin`, `nMap.bin` | Geometry maps (generated once per detector config) |

### GPU Streaming (Real-Time)

For live experiments with GPU hardware, use natural language to invoke `integrator_batch_process.py`:
```
APEXA> run GPU streaming integration on /data/scan_01 with dark file
APEXA> start live streaming integration from PVA detector
```

---

## GSAS-II Refinement

Rietveld-style peak fitting and lattice parameter refinement on integrated (caked) data.

**Example Prompts:**
```
APEXA> refine the caked output with GSAS-II using CeO2.cif
APEXA> run GSAS-II refinement on test1/integration/CeO2_caked.hdf.zarr.zip
APEXA> run integration and refinement on the scan data with CeO2 CIF
APEXA> fetch a CIF file for CeO2 from Materials Project
```

**What happens:**
1. For standalone refinement: Takes `.zarr.zip` + CIF files, runs multi-stage Rietveld refinement
2. For combined pipeline: Integrates raw data first, then refines (batch CPU or GPU streaming)
3. For CIF fetch: Downloads crystal structures from Materials Project database

**Outputs:**

| Output File | Description |
|---|---|
| `refinement_summary.json` | Aggregated Rwp, lattice parameters, success/fail counts |
| `hist_NNNN.gpx` | Per-histogram GSAS-II project file |
| `hist_NNNN_<phase>.cif` | Refined crystal structure |
| `hist_NNNN_data.csv` | 2θ, observed, calculated, difference |

**Typical workflow:**
```
APEXA> calibrate CeO2 in test1                         (CalibrationAgent)
APEXA> integrate the CeO2 image in test1                (AnalysisAgent)
APEXA> fetch CIF for CeO2                              (KnowledgeAgent)
APEXA> refine the caked output with GSAS-II using CeO2  (AnalysisAgent)
APEXA> show the caked peaks                             (VisualizationAgent)
```

---

## Autonomous Refinement (Agentic GSAS-II)

The section above refines MIDAS caked output with a fixed recipe. This one is
for any other powder pattern, and for the case where you do not have a starting
structure at all. The agent chooses its own parameter order from the engine's
chi-squared derivatives, gated by physical guardrails, and reports whether the
result should be believed.

**Example Prompts:**
```
APEXA> refine scan_042.xye with LiCoO2.cif and tell me if I can trust it
APEXA> I have a pattern but no structure -- find candidates for goethite
APEXA> refine this temperature series and track the phase fractions
APEXA> can I still act on the refinement in ref/042 ?
```

**What happens:**
1. `refine_pattern` picks parameters by derivative leverage, rolls back any step
   that makes the fit worse, and stops on convergence rather than on a recipe
   running out
2. `propose_structures` queries the Crystallography Open Database and ranks hits
   by de Wolff M20 against the observed peaks -- no reference structure involved
3. `refine_series_submit` walks an in-situ stack and returns a job id; poll it
   with `refinement_status` rather than blocking
4. Every result carries a **trust verdict**

**The trust verdict.** An automated fit always returns numbers. The verdict says
whether to act on them, and names its reason rather than scoring:

| Flag | Meaning |
|---|---|
| stopped on a time budget | a throughput bound, not convergence |
| hit the step cap | the fit was still improving when cut off |
| Rietveld exceeds the Le Bail floor | the structural model is the limiting factor |
| direct cell refinement was rolled back | the cell step made the fit worse |
| no parameter was accepted | the model never moved |

In a closed loop, treat a flagged result as a reason to **skip the point**, not
to search around it: a wrong cell that stops predicting any observed peak costs
almost nothing in Rwp, so the residual will not catch it.

**M20 as a confidence signal.** `propose_structures` returns de Wolff M20 per
candidate, computed from the pattern alone. On a 240-pattern mineral benchmark
the rate of a wrong phase fell monotonically with it: 3.2% at M20 >= 20 against
17.6% below 5. A low M20 means widen the search, not refine harder.

**Known limitations.** Lattice parameter through a series does not yet produce a
thermal-expansion trend -- that entry point predates the framework's direct
cell-refinement path. Absolute weight fractions carry roughly 17 wt% mean error
from uncorrected microabsorption; relative change between conditions is far
better behaved.

**Requirements.** GSAS-II conda env plus the Agentic-GSAS-II repository; set
`APEXA_GSASII_PYTHON` and `APEXA_AGENTIC_GSAS2` if not at their defaults.
Refinements run in a subprocess, so a GSAS-II abort does not take the agent
down.

## FF-HEDM Workflow

Complete Far-Field HEDM grain reconstruction pipeline.

```
APEXA> run FF-HEDM workflow on /data/experiment
APEXA> run FF-HEDM with 32 CPUs on layers 1-10
```

**Pipeline:** Data conversion -> HKL generation -> Peak search -> Peak fitting -> Merging -> Indexing -> Refinement

**Outputs:** `Grains.csv` (orientations, positions, strains), `*.MIDAS.zip`

---

## NF-HEDM Reconstruction

Near-Field HEDM for high-resolution 3D microstructure mapping (~1-5 um resolution).

```
APEXA> run NF-HEDM reconstruction with FF seed orientations
APEXA> process NF data using grains from FF-HEDM
```

---

## Visualization

Ask APEXA to plot results using MIDAS viewer scripts:

```
APEXA> show me the lineout for CeO2 in test1
APEXA> plot the caked output from test1/integration
APEXA> show calibration results for test1
APEXA> view the 2D diffraction image CeO2_000001.tif
APEXA> compare lineouts with ideal ring positions
```

APEXA launches the appropriate MIDAS viewer (PyQt/matplotlib) based on your data files:

| Data File | Viewer Used |
|---|---|
| `*_lineout.xy` | `plot_lineout_results.py` or `plot_lineout_comparison.py` |
| `*_caked.hdf.zarr.zip` | `plot_integrator_peaks.py` |
| `*_caked_peaks.h5` | `plot_caked_peaks.py` |
| `*_corr.csv` | `plot_calibrant_results.py` |
| Raw 2D image | `ff_asym_qt.py` |
| Grains.csv + SpotMatrix | `interactiveFFplotting.py` |

---

## X-ray Calculations

APEXA uses verified tools for crystallographic calculations -- never computes manually.

**Energy/Wavelength:**
```
APEXA> convert 61.332 keV to wavelength
APEXA> convert 0.2021 angstroms to energy
```

**d-spacing:**
```
APEXA> calculate d-spacing for 2-theta 10.5 degrees at 0.2066 angstroms
APEXA> get d-spacing for CeO2 (111)
APEXA> what's the d-spacing of Fe (110)?
```

**Bragg angle:**
```
APEXA> calculate Bragg angle for Si (111) at 61.332 keV
```

**Strain:**
```
APEXA> calculate strain for measured d=3.155 and reference d=3.124
```

**Common APS Energies:**
- 1-ID: 61.332 keV (0.2022 A)
- 1-ID: 71.676 keV (0.1730 A)

---

## Knowledge Base

Ask domain-specific questions:

```
APEXA> what is FF-HEDM?
APEXA> explain Bragg's law for 61 keV beam energy
APEXA> what are typical parameters for steel?
APEXA> get material properties for CeO2
APEXA> what are quality thresholds for calibration?
```

**Available materials:** CeO2, LaB6, Si, Al2O3, Fe, Ti, Ni, Al, Steel_316L, Ti6Al4V

---

## File Operations

```
APEXA> list files in /data/experiment
APEXA> read the Parameters.txt file
APEXA> what's in the integration folder?
```

---

## Model Selection

Switch AI models on the fly:

```
APEXA> models                    # list available
APEXA> model gpt54               # switch to GPT-5.4
APEXA> model claudesonnet45      # switch to Claude Sonnet 4.5
```

**Available:** `claudeopus5` (default), `claudeopus55` / `claudesonnet55` / `claudehaiku55` (newest Anthropic), `claudeopus48/47/46/45`, `claudesonnet5/46/45`, `claudehaiku45`, `gpt56sol/terra/luna`, `gpt55`, `gpt54`, `gpt5*`, `gpt41*`, `gpt4o`, `gemini35flash`, `gemini31flashlite`. Type `models` for the live list — it is built from the code, this line is not.

Set default in `.env`:
```bash
ARGO_MODEL=gpt4o
```

### Response Timing

Measure API response time to compare models:

```
APEXA> timing                    # toggle on
  ⏱ gpt4o responded in 0.8s
APEXA> timing                    # toggle off
```

Or set `APEXA_SHOW_TIMING=1` in your environment before launching.

---

## Troubleshooting

### MIDAS Not Detected
```
WARNING: MIDAS not found
```
Set the path: `export MIDAS_PATH=/path/to/MIDAS` in `.env`

### Calibration Fails to Converge
Common causes:
1. Bad initial guess for Lsd or BC in Parameters.txt
2. Wrong calibrant material specified
3. Low-quality image (check saturation)

Fix: Adjust starting parameters or increase `--n-iterations`.

### Integration Fails with h5py Error
```
Symbol not found: _H5T_IEEE_F16BE_g
```
This is an HDF5 version mismatch. APEXA uses `get_midas_python_env()` for Python scripts to avoid DYLD_LIBRARY_PATH conflicts with conda h5py.

### "Validation Error" on Integration
The model may not pass parameters correctly on the first try. Provide the calibration file name explicitly:
```
APEXA> integrate CeO2_000001.tif using refined_MIDAS_params_CeO2.txt
```

---

## File Formats

### Supported Image Formats
- **TIFF:** `.tif`, `.tiff`
- **GE Detectors:** `.ge`, `.ge1`-`.ge5`
- **HDF5:** `.h5`, `.hdf5`, `.nxs`
- **Zarr:** `.zip` (Zarr format)

### Parameter Files
MIDAS `Parameters.txt` / `refined_MIDAS_params*.txt`:
```
Lsd 650000         # Sample-to-detector distance (um)
BC 1024 1024       # Beam center Y Z (pixels)
tx 0 ty 0 tz 0     # Detector tilts (degrees)
px 200             # Pixel size (um)
Wavelength 0.1777  # X-ray wavelength (A)
```

### Key Output Files
| Step | Output |
|---|---|
| Calibration | `refined_MIDAS_params_<material>.txt`, `autocal.log` |
| Integration | `*_lineout.xy`, `*_caked.hdf.zarr.zip` |
| FF-HEDM | `Grains.csv`, `*.MIDAS.zip` |
| NF-HEDM | `Grains.csv`, `Microstructure.h5` |

---

## System Requirements

| | Minimum | Recommended |
|---|---|---|
| Python | 3.13+ | 3.13+ |
| Memory | 16 GB | 64 GB |
| CPU | 4 cores | 32+ cores (FF-HEDM) |
| GPU | -- | NVIDIA (streaming integration) |
| MIDAS | v11 | v11 |
| Network | ANL access | ANL access |

---

## Credits

**APEXA Development:**
- Pawan Tripathi (Lead Developer)
- Advanced Photon Source, Argonne National Laboratory

**Core Dependencies:**
- [MIDAS](https://github.com/marinerhemant/MIDAS) v11 - Hemant Sharma
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
- [uv](https://github.com/astral-sh/uv) - Package manager
- Argo Gateway - Argonne National Laboratory

---

**Version:** 2.0 | **MIDAS:** v11 | **Updated:** March 2026
