---
name: tomo-reconstruct
description: Survey and reconstruct parallel-beam tomography (TomoPy) at APS 1-ID / 20-ID — projections → registered sample shape/mask. Use when the user asks to reconstruct a tomo/CT scan, find the rotation center, correct absorption/phase contrast, or build a sample envelope/mask from projection images.
compatibility: Routes to a native TomoPy conda env via APEXA_TOMO_PYTHON (tomopy + dxchange; astra optional). NOT the APEXA .venv. With no such env the tools return a structured "set APEXA_TOMO_PYTHON" error. Source of truth: knowledge_base/capsules/tomo/.
metadata:
  author: pawan-tripathi
  version: "1.0"
  tools: survey_tomo_stack, run_tomo_reconstruction
  backend: tomopy
---

## Tomography (TomoPy) — projections to a trustworthy sample shape

APS-standard parallel-beam absorption / propagation-contrast tomography for **1-ID and
20-ID**, reconstructed with **TomoPy** (`gridrec`/`fbp`/`astra`). The goal is not a pretty
picture — it is a **sample shape** (and optional mask) you can trust as the illuminated
volume for a diffraction experiment. MIDAS's own `midas-tomo` is a different backend; this
is the TomoPy path. Capsule: `knowledge_base/capsules/tomo/` (THE ORDER, hard rules).

### THE ORDER (do not skip; two steps cannot be checked afterwards)
1. **`survey_tomo_stack` first** — dims/dtype, flat/dark layout, transmission, μ·D,
   truncation. Resolve every `halt` before reconstructing.
2. **Geometry from metadata** — pixel size, angles (ω start/step), energy. Not recoverable
   later.
3. **`run_tomo_reconstruction`** — normalize → (phase) → center → recon → (threshold→mask).

### The failures that finish and look right
Three defects reconstruct perfectly and pass every quality metric — so the trigger to stop
is a **named condition**, not confusion:
- **Mirrored** reconstruction (wrong in-plane handedness) — microstructure handed the wrong
  way. → `handedness` is **required**, never defaulted.
- **Wrong pixel size** — a sharp recon of an object of the wrong size; silently rescales
  every downstream length/volume. → `pixel_size_um` is **required**, read from metadata.
- **Truncated** scan — bowl-shaped cupping that looks like a density gradient. → survey's
  truncation check halts when edge transmission does not return to ~1.

### Hard rules (enforced in the runner as runtime data checks)
1. **Flat/dark layout from measured frame means, never assumed.** Layouts differ between
   beamtimes (`dark, white1, white2, proj` vs `flats, proj, flats, darks`). A negative
   transmission anywhere ⇒ the assignment is wrong ⇒ **halt** (do not tune around it).
2. **Every transmission statistic is restricted to the illuminated region.** Outside the
   beam `flat − dark ≈ 0` and the ratio is noise that reads as a smooth plateau of partial
   attenuation.
3. **Never default pixel size, rotation axis, or in-plane handedness.** `n/2` is a guess,
   not an axis. The tool refuses rather than guess.
4. **Registration into the MIDAS lab frame is a read, not a fit** — stage vertical
   (APS y = MIDAS z). **Out of v1 scope** here (follow-up via `midas_stress`).
5. **Check μ·D before promising an absorption correction.** Below ~0.1 the correction is at
   or under per-spot noise — the honest answer is "no detectable effect". Measured from the
   projections, never assumed from the element.

### Rotation center is an OUTPUT
Pass `rotation_center` if known; else `center_sweep=[lo,hi,step]` writes one slice per center
to `<out>/center_sweep` for visual pick (then pass the chosen value), else it auto-finds via
`find_center_vo`. The chosen shift is recorded in the payload — it must travel with the result.

### Phase contrast
If the scan has a non-zero propagation distance and a **mask** is wanted, run Paganin phase
retrieval first (`phase_retrieval=True` + `propagation_mm` + `energy_kev`). Thresholding a
propagation recon without it gives a hollow shell from edge enhancement, not a filled sample.

### Honest status
`success` only when a fresh, non-degenerate volume was written; otherwise `warning`/`error`
with the reason. A tomo env that cannot `import tomopy, dxchange` returns a structured error
telling you to set `APEXA_TOMO_PYTHON` — it never fabricates a reconstruction.
