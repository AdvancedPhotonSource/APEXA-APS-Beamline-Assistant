---
name: midas-gsasii
description: GSAS-II peak fitting and Rietveld refinement on MIDAS caked output. Use when the user asks to refine, run GSAS-II, fit peaks with Rietveld, get Rwp, extract lattice parameters from integrated data, or run combined integration + refinement pipeline.
compatibility: Requires MIDAS v11 (utils/gsas_ii_refine.py, FF_HEDM/workflows/integrate_and_refine.py), GSAS-II conda env (conda install gsas2full -c briantoby), zarr in GSASII or midas_env
metadata:
  author: pawan-tripathi
  version: "2.0"
  midas-version: "11.0"
  manual: MIDAS/manuals/GSAS-II_Integration.md
---

## GSAS-II Refinement Workflows (MIDAS v11)

Two MCP tools depending on the use case:

| | **Tool A — Standalone Refinement** | **Tool B — Combined Pipeline** |
|---|---|---|
| MCP tool | `run_gsas_refinement` | `run_live_analysis` |
| Input | MIDAS `.zarr.zip` / `.MIDAS.zip` archive, or `.xy` / `.xye` pattern | Raw data + params (or an existing `.zarr.zip`) |
| Driver | APEXA robust GSAS-II adapter by default; optional legacy MIDAS driver | Stage 1: integration, Stage 2: GSAS-II refinement |
| Best for | Refining an integrated pattern or MIDAS caked archive | End-to-end: integrate → refine in one step |

> **CIF files are required for refinement.** Use `fetch_cif_from_mp` (KnowledgeAgent) to download from Materials Project if the user doesn't have one.

---

## How Rietveld Refinement Works

GSAS-II Rietveld refinement computes a theoretical diffraction pattern from a crystal structure
(CIF) and minimizes the difference with the observed data by refining parameters in stages:

1. **Background + Scale** — Chebyshev polynomial baseline + overall intensity scale
2. **Unit Cell** — Lattice parameters (a, b, c, α, β, γ) → peak positions
3. **Profile** — Peak shapes: Gaussian (U, V, W) + Lorentzian (X, Y, SH/L)
4. **Atoms** — Atomic coordinates + thermal parameters → peak intensities

**Rwp** is the weighted profile residual. Lower can indicate a closer fit, but
there is no universal acceptance threshold. Inspect residual shape, phase evidence,
instrument/background assumptions, parameter stability, and uncertainty before
qualifying a result.

### Data flow through MIDAS

```
Raw 2D detector image (rings)
  ↓  midas_auto_calibrate (native midas_calibrate; AutoCalibrateZarr.py fallback)
refined_MIDAS_params.txt (Lsd, BC, wavelength, tilts)
  ↓  midas_integrate_2d_to_1d (native midas_integrate; IntegratorZarrOMP fallback)
*.caked.hdf.zarr.zip (v11 default: <stem>.zarr.zip)
  ├── REtaMap[1] = 2θ array (degrees)
  ├── REtaMap[3] = pixel area (mask)
  ├── OmegaSumFrame/ = intensity per (ω, η) slice
  └── InstrumentParameters/ = Lam, U, V, W, X, Y, Z, SH_L, Polariz, Distance
  ↓  gsas_ii_refine.py
  ├── Extracts lineouts: 1 histogram per (frame × valid η-slice)
  ├── Writes temp .xye files (2θ, intensity, sigma)
  ├── Creates GSAS-II project per histogram
  └── Runs 4-stage refinement → .gpx + refinement_summary.json
```

### Key math in lineout extraction

- **2θ**: from `REtaMap[1]` — already in degrees
- **Intensity**: `raw_counts / pixel_area` (normalized)
- **Sigma**: `√(max(|raw_counts|, 1)) / max(area, 1)` — Poisson statistics

---

## Instrument Parameters — Critical Detail

`gsas_ii_refine.py` writes `.xye` files (2θ, intensity, sigma) and passes them to
GSAS-II's `add_powder_histogram()`. **GSAS-II hard-errors on .xye without an .instprm file.**

The zarr's `InstrumentParameters/` group contains the right values (Lam, U, V, W, etc.)
but `gsas_ii_refine.py` does NOT auto-read them.

**APEXA handles this automatically:** `_extract_instprm_from_zarr()` reads the zarr's
`InstrumentParameters/` group and generates a temporary `.instprm` file before calling
the refine script. No user action needed.

---

## Python Environment — Critical Detail

GSAS-II binaries (pyspg, pypowder) are compiled against a specific Python ABI.
If the GSASII conda env uses Python 3.13, running refinement with `midas_env` Python 3.12
causes binary load failures (profile calculations unavailable).

**APEXA handles this automatically:** `find_gsasii_python()` detects the GSASII conda env's
Python and uses it for refinement. Integration still uses `midas_env` (needs diplib/skimage).

---

## Tool A: `run_gsas_refinement`

Refines caked histograms from a `.zarr.zip` using multi-stage Rietveld refinement.

```
run_gsas_refinement(
    data_file        = "<path to .zarr.zip>",
    cif_files        = ["CeO2.cif"],         # one or more phases
    output_dir       = "refinement/",         # default
    bkg_terms        = 6,                     # Chebyshev background terms
    two_theta_limits = [2.0, 15.0],           # optional 2θ range (recommended!)
    no_atoms         = False,                 # skip atomic refinement
    no_export        = False,                 # skip CIF/CSV export
    n_cpus           = 4,                     # parallel histograms
    instprm_file     = "instrument.instprm",  # optional — auto-extracted from zarr if omitted
)
```

### Refinement stages

| Stage | Refines | Parameters |
|---|---|---|
| 1 | Background + Scale | Chebyshev coefficients, phase scale |
| 2 | Unit Cell | a, b, c, α, β, γ |
| 3a | Gaussian profile | U, V, W |
| 3b | Lorentzian profile | X, Y, SH/L |
| 4 | Atomic positions + thermal | Coordinates, Uiso (optional, skip with `no_atoms`) |

### Best practices

- **Always set `two_theta_limits`** to the range containing your peaks. Without limits, the
  refinement fits the entire 2θ range (often 0–50°), where most bins are empty background,
  causing high Rwp.
- Use `no_atoms=True` for quick lattice parameter extraction (skips Stage 4).
- Start with `n_cpus=1` to debug, then scale up.

### Engine selection (`engine=` parameter)

`run_gsas_refinement` accepts `engine="gsas2"` (default), `"maud"`, or `"both"`
for MIDAS `.zarr.zip` and `.MIDAS.zip` archives. Plain `.xy` and `.xye` patterns
use the GSAS-II path directly; the engine selector does not route those patterns
to MAUD. Results have engine-specific fields, so callers should inspect the
returned `engine` and `status` instead of assuming identical output schemas.

- `engine="gsas2"` — fastest, the standard path. Use unless you have a specific reason not to.
- `engine="maud"` — the limited MAUD/MILK backend for supported MIDAS caked
  archives. It currently performs cell and profile refinement; it is not a
  general MAUD texture or microstructure workflow. Missing MAUD, MILK, or JDK
  returns a structured `engine_unavailable` result.
- `engine="both"` — runs GSAS-II and then MAUD on a supported caked archive and
  returns cross-validation data. If MAUD is unavailable, the result records a
  GSAS-II fallback and its reason; check that field before treating the result as
  a two-engine comparison.

Texture and microstructure modes (`refine_texture=`, `refine_microstructure=`) are
not exposed by this adapter.

---

## Tool B: `run_live_analysis`

Combined pipeline: Integration (Stage 1) → GSAS-II Refinement (Stage 2).

APEXA runs these as **two separate subprocesses** with the correct Python for each:
- Stage 1 (Integration): `midas_env` Python (has diplib, skimage, h5py)
- Stage 2 (Refinement): GSASII conda Python (has matching binaries)

### Batch backend (CPU)

```
run_live_analysis(
    backend     = "batch",
    param_file  = "refined_MIDAS_params_CeO2.txt",
    data_file   = "data/sample_000001.h5",
    cif_files   = ["CeO2.cif"],
    output_dir  = "refinement/",
    n_cpus      = 8,
)
```

### Stream backend (GPU, folder)

```
run_live_analysis(
    backend    = "stream",
    param_file = "refined_MIDAS_params_CeO2.txt",
    folder     = "/data/experiment/scan_01/",
    cif_files  = ["CeO2.cif"],
    output_dir = "refinement/",
    n_cpus     = 8,
)
```

### Stream backend (GPU, live PVA)

```
run_live_analysis(
    backend    = "stream",
    param_file = "setup.txt",
    pva        = True,
    pva_ip     = "10.54.105.139",
    cif_files  = ["CeO2.cif"],
    output_dir = "refinement/",
    n_cpus     = 8,
)
```

### Skip integration (refine existing .zarr.zip)

```
run_live_analysis(
    backend          = "batch",
    param_file       = "params.txt",
    cif_files        = ["CeO2.cif"],
    skip_integration = True,
    zarr_file        = "output/CeO2_caked.hdf.zarr.zip",
)
```

### Integration only (no GSAS-II)

```
run_live_analysis(
    backend         = "stream",
    param_file      = "params.txt",
    folder          = "/data/scan_01/",
    cif_files       = [],
    skip_refinement = True,
)
```

---

## CIF File Fetcher: `fetch_cif_from_mp`

Downloads CIF files from the Materials Project database. Routed to KnowledgeAgent.

```
fetch_cif_from_mp(
    formula    = "CeO2",
    output_dir = ".",              # saves CeO2_mp-XXXXX.cif
    mp_api_key = None,             # from env MP_API_KEY or ~/.config/.pmgrc.yaml
)
```

Returns up to 3 structures sorted by thermodynamic stability (energy above hull).
**CIFs are auto-converted to the conventional standard cell with proper space group**
(e.g., Fm-3m for CeO2, not primitive P1). This is critical for GSAS-II — primitive P1
cells produce incorrect peak intensities and cause refinement failure (Rwp ~70-100%).

---

## Output files

| File | Tool | Description |
|---|---|---|
| `hist_NNNN.gpx` | Both | Per-histogram GSAS-II project file |
| `hist_NNNN_<phase>.cif` | Both | Refined crystal structure (unless `no_export`) |
| `hist_NNNN_data.csv` | Both | 2θ, observed, calculated, difference |
| `refinement_summary.json` | Both | Aggregated results: Rwp, lattice params, counts |
| `_midas_extracted.instprm` | Both | Auto-generated instrument parameters (temporary) |

### refinement_summary.json structure

```json
{
  "data_file": "CeO2_caked.hdf.zarr.zip",
  "total_histograms": 360,
  "succeeded": 358,
  "failed": 2,
  "skipped": 0,
  "mean_Rwp": 4.23,
  "histograms": [
    {
      "histogram_index": 0,
      "Rwp": 3.87,
      "phases": [{"name": "CeO2", "cell": {"a": 5.4116, "b": 5.4116, "c": 5.4116, "alpha": 90, "beta": 90, "gamma": 90, "volume": 158.56}}],
      "status": "success"
    }
  ]
}
```

---

## Prerequisites

1. **GSAS-II conda env**: `conda create -n GSASII && conda activate GSASII && conda install gsas2full -c briantoby -c conda-forge`
2. **zarr** (in GSASII env or midas_env): `pip install zarr` (v2 or v3 — `_open_zarr()` handles both)
3. **mp-api** (for CIF fetcher): `uv sync --extra extra` or `pip install mp-api`
4. **MP API key** (for CIF fetcher): Get from https://next-gen.materialsproject.org/api → set `MP_API_KEY` env var

---

## Typical workflow

```
1. Calibrate:    midas_auto_calibrate (CalibrationAgent)
2. Integrate:    midas_integrate_2d_to_1d (AnalysisAgent) → .zarr.zip
3. Fetch CIF:    fetch_cif_from_mp("CeO2") (KnowledgeAgent) → CeO2.cif
4. Refine:       run_gsas_refinement(zarr.zip, CeO2.cif, two_theta_limits=[2,15])
5. Visualize:    plot_integrator_peaks or plot_caked_peaks (VisualizationAgent)
```

Or combined (steps 2+4 in one call):
```
1. Calibrate:    midas_auto_calibrate
2. Fetch CIF:    fetch_cif_from_mp("CeO2")
3. Integrate+Refine: run_live_analysis(backend="batch", ...)
4. Visualize:    plot_caked_peaks
```
