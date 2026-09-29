---
name: midas-calibrate
description: Run MIDAS detector geometry calibration using a CeO2 or LaB6 calibrant image. Use when the user asks to calibrate, run calibration, find beam center, refine detector distance (Lsd), or mentions CeO2/LaB6/ceria calibrant files.
compatibility: Requires midas-suite. The preferred path (canonical v2) needs midas-calibrate-v2 + scikit-image; the legacy path needs a MIDAS repo clone + diplib. Neither requires compiled C binaries for the primary path.
metadata:
  author: pawan-tripathi
  version: "3.0"
  midas-version: "11.0"
  package: "midas_calibrate_v2"
  manual: knowledge_base/capsules/calibrate-integrate/phase-4-calibrate.md
---

## Calibration in APEXA

**One tool: `midas_auto_calibrate`.** It selects an engine, runs it, enforces the
handbook's acceptance gate, and writes a v1-format parameter file that integration
and FF-HEDM pick up automatically.

The authoritative procedure is the vendored capsule
[`knowledge_base/capsules/calibrate-integrate/`](../../../knowledge_base/capsules/calibrate-integrate/) —
`phase-4-calibrate.md` for the recipe, `HARD_RULES.md` for the rules, `DIAGNOSIS.md`
when something looks wrong. **This skill describes how APEXA invokes it; the capsule
is the source of truth for the physics.**

### Step 1 — Find the files

`list_directory` to locate:
- **Calibrant image** — CeO2 or LaB6 (`.tif`, `.ge*`, `.h5`, `.zip`)
- **Dark frame** — usually `dark_*`. Pass it. A missing dark is the second most
  common cause of "no fitted points"; a *wrong-exposure* dark is worse, because it
  does not fail — it moved a fitted Lsd from 1052 mm to 578 mm in the archive.
- **Parameter file** (optional) — `ps_*.txt`, `Parameters.txt`, `*params*.txt`.
  Used as the template: detector size, pixel size, lattice, thresholds.

### Step 2 — Call the tool

```
midas_auto_calibrate(
    image_file          = "<absolute path>",
    parameters_file     = "<absolute path>",   # optional — auto-detected/synthesized
    dark_file           = "<absolute path>",   # pass it whenever one exists
    output_dir          = "<absolute path>",
    calibration_engine  = "auto",              # default
)
```

Every argument is optional except `image_file`. Other real parameters:
`template_param_file`, `detector`, `strain_gate_ue`, `ignore_calibration_gate`,
`image_transform`, `data_loc`, `energy_kev`, `wavelength_angstrom`, `lsd_guess`,
`bc_x_guess`/`bc_y_guess`, `n_iterations`, `seed_from_params`, `host`.

> There is **no** `param_file`, `bad_px_intensity` or `gap_intensity` argument.
> (Earlier versions of this skill documented all three; a literal copy failed.)

### The engines — four paths, one decision

`calibration_engine` picks; the choice and its reason are returned as `engine` and
`engine_reason`, and every rejected alternative is listed under `fallbacks`.

| value | behaviour |
|---|---|
| **`"auto"`** (default) | canonical v2 when it can run → native → pip console → legacy. Each fallback records **why**. |
| `"v2"` | canonical v2, or **refuse**. Never downgrades silently. |
| `"v1"` | the old cascade: in-process native `midas_calibrate` → `midas-autocalibrate` console → `AutoCalibrateZarr.py` |
| `"legacy"` | force `AutoCalibrateZarr.py` |

| engine | what it is | needs |
|---|---|---|
| **canonical v2** | the handbook four-stage recipe via `_calibrate_runner.py` | `midas_calibrate_v2` **+ `scikit-image`** |
| native v1 | `midas_calibrate` in-process | a torch accelerator (raises on CPU-only) |
| pip console v1 | `midas-autocalibrate` (from the **`midas-calibrate`** package — v1, not v2) | cannot read HDF5 |
| legacy | `AutoCalibrateZarr.py` → `CalibrantIntegratorOMP` | a **MIDAS repo clone** + `diplib` |

**Zarr (`.zip`/`.zarr`) only works on the legacy path.** `midas_calibrate_v2` has no
zarr reader, so `auto` routes zarr to legacy and `"v2"` refuses it.

**On a pip-only host (the blessed MIDAS env, no clone) the legacy path does not
exist.** Set `APEXA_MIDAS_BIN` to that environment's `bin/` and `auto` will find
the canonical engine there. If `midas_auto_calibrate` reports
`canonical-v2 skipped: ... No module named 'skimage'`, that is the whole problem:
`midas-calibrate-v2` only declared scikit-image as a hard requirement from 0.22.0.

### What the canonical recipe does

Read (sentinels zeroed, mask returned) → clean → **seed after cleaning** →
load the template and overwrite BC/Lsd from the seed, zero the tilts and p0–p14 →
build the spec → *(tiled only)* panel terms + no-expansion gauge + layout →
`autocalibrate_four_stage` → write a v1-format parameter file.

Three things APEXA handles that a hand-written script gets wrong:

- **`ImTransOpt` is applied exactly once**, at read time. On newer builds
  `spec_from_v1_params` also lifts it from the template and the pipeline re-applies
  it — flipping the frame back. APEXA clears the spec copy.
- **`RhoD` is written in µm**, computed as `MaxRingRad_px × px_um`. The upstream
  derivation is version-dependent (0.5.3 leaves it in *pixels*), and a pixel-valued
  `RhoD` rescales every distortion coefficient with no error and a good strain number.
- **`use_diplib=False`** — upstream flipped this default after diplib segfaulted on
  one platform and hung a Windows kernel at import.

### The acceptance gate

```
stage4_strain_uE_test  <  100 µε        # held-out, not the full set
|held-out − full| small                 # a large gap means overfitting
```

A calibrant refining worse than the gate **is not a calibration**, and APEXA
returns `status: "error"` rather than reporting the numbers as a result.

**But read it against the geometry, not as a universal bar.** The cap is a
fraction, `|1 − R_obs/R_pred|`, so `strain ≈ Δpixel / R_ring`: a short
sample-to-detector distance structurally reads a higher microstrain for the same
real precision. A real ~350 mm setup, confirmed correct by ring overlay, read
199 µε. The failure payload carries `ring_radius_window_px` and `lsd_um` so you can
tell which case you are in. Halt **H3** is *stop and LOOK*, not *fail*: open the
ring overlay before concluding the calibration is bad. Then either
`strain_gate_ue=<n>` for that geometry, or `ignore_calibration_gate=True`
deliberately.

Reference numbers from the handbook dataset: **66.1 µε held-out, 67.2 full.**

### Preconditions APEXA checks before spending a fit

Each returns `nothing_was_run: true` rather than a wrong number.

| check | why it exists |
|---|---|
| `detector_scope_gate` | the only gate a converged fit cannot fool — the fitter does not fail when the rings fall off the detector, it *succeeds*. Halted 42 of 252 archive exposures; **26 had already produced a plausible-looking calibration.** |
| dark is finite | one NaN in the dark poisons the whole frame |
| dark shape matches | — |
| `seed.threshold_rung == 0` | a relaxed threshold means the strict one found nothing; relaxed far enough, the arc finder fits noise. Reported always, warned when > 0. |
| seed method is `make_seed` | hard rule 14 — `first_time_calibrate`'s own seeder matched 3 of 37 arcs on a masked frame |

### ImTransOpt

Detector-mount specific — there is no extension-based rule (a Pilatus TIFF may need
`2`, a GE file `0`). Resolution order:

1. the `image_transform` argument
2. `ImTransOpt` in the supplied `parameters_file`
3. a sibling `parameters.txt` / `Parameters.txt` / `params.txt`
4. fallback `0` **with a warning** — surface that to the user

| code | effect |
|---|---|
| `0` | none |
| `1` | flip left/right |
| `2` | flip top/bottom |
| `3` | transpose |

Multiple codes are space-separated and applied in order: `image_transform="1 3"`.
It **cannot be checked after the fact** — the refiner absorbs a mirror and reports a
good strain. Test it directly: the right value gave 0.039 px RMS about ideal on a
2880² frame; the wrong one, 1.374 px, with ring contrast collapsing from 101 to 6.9.

### Outputs

| file | from |
|---|---|
| **`refined_MIDAS_params_v2.txt`** | canonical v2 — **the primary output**; feed this to integration and FF-HEDM |
| `paramstest_v2.txt` | canonical v2, pre-merge (missing MaskFile and the ring/binning keys) |
| `<...>_panelshifts.txt` | canonical v2, tiled detectors only |
| `refined_MIDAS_params_<material>.txt` | native / pip console / legacy |
| `autocal.log`, `*corr.csv` | legacy only |
| `APEXA_calibration.json` | every path — the per-run outcome manifest |

**Always cite `calibrated_parameters_file` from the result**, not a constructed
name. Integration auto-discovers `refined_MIDAS_params*.txt` beside the image.

### Wavelength — derive it, never hand-compute

Use `xray_calculate("energy_to_wavelength", energy_kev=…)`, which goes through
`apexa_units` (xrayutilities, CODATA-2018 fallback). Do not compute λ in your head:
a hand-rounded value bakes an energy error into the geometry.

And note **hard rule 9**: λ is *not* determined by a single-distance powder pattern.
Wavelength and Lsd are degenerate — a 1 % energy error becomes a 1 % distance error
and **the strain gate still passes**. Take λ from the beamline, cross-check it
against the filename and metadata, and never try to recover it by calibrating at
candidate energies and picking the lowest residual.

### Auto-detection from the filename

`ceo2`/`ceria` → CeO2 (SG 225) · `lab6` → LaB6 (SG 221) ·
`61p332keV` → λ · `650mm` → Lsd guess 650000 µm. `lsd_guess` is in **µm**.

### When it goes wrong

`DIAGNOSIS.md` in the capsule is organised symptom → discriminating test → cause →
lever. The entries worth knowing by name: `pixel.sentinel_unmasked` (a whole
detector reading ~4.29e9), `geometry.rings_unreachable`, `tilt.collapsed_to_seed`
(a near-zero tilt on a detector you know is tilted — **open, and the default path
most people take**), `degeneracy.lambda_lsd`, and `fit failed: no ring points`.

For a low-SNR frame that landed in a false basin, seed from a trusted neighbour:
`midas_auto_calibrate(..., seed_from_params="<good refined_MIDAS_params*.txt>")`.
