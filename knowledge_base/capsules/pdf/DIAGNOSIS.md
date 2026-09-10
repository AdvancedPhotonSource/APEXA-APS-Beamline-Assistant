# PDF diagnosis reference

> Part of the **PDF doc set**. Spine: [`README.md`](README.md).

Symptom → discriminating test → cause → lever, for X-ray total scattering. Indexed by
**symptom**, not by step — the step that produced a symptom is rarely the step you are on.
Every entry carries a test that **can come back the other way**; an entry that cannot
exonerate the cause it names does not belong here.

> **Provenance.** Entries marked **[measured]** come from `LAB_NOTEBOOK.md` §1 (a synthetic
> probe of APEXA's tool path, 2026-09-10). Entries marked **[source]** are read off the
> `midas-pdf` implementation. Entries marked **[generic]** are standard total-scattering
> behaviour and have **not** been verified here. There is no APS PDF campaign behind this
> file — weight the entries accordingly.

---

## S(Q) symptoms

### S(Q) does not approach 1 at high Q  **[measured]**

**Test.** Take the mean of S(Q) over the top 25 % of the Q range. If it sits at a roughly
constant value ≠ 1 with the right *shape*, it is a scale problem; if it drifts or ramps, it
is a background/Compton problem. Both can be present.

- **Constant offset from 1** → `scale` was never fitted. The one-call path leaves
  `scale=1.0` (PARAMETERS §10a). Measured ⟨S⟩ = 2.75 (min 1.23, max 3.98) on an unrefined
  run whose G(r) looked entirely normal. **Lever:** run phase 2 `refine_normalization`.
- **Monotonic ramp** → Compton not subtracted, or subtracted with the wrong wavelength.
  Check `compton=True` and that `wavelength_A` is the real wavelength, not the `0.1 Å`
  silent default (rule 4). **Lever:** pass the true λ; re-check.
- **Ramp that survives a correct λ** → fluorescence or air background. Run
  `expected_fluorescence(composition, energy)` first — it tells you whether any sample
  element fluoresces at your energy, which is a yes/no answer, not a guess. **Lever:** fit a
  background with `bg_order ≥ 1`, or subtract a measured empty-container pattern.

**Exonerating result:** ⟨S⟩ within a few percent of 1 with no ramp ⇒ normalization is not
your problem; move to the transform.

### S(Q) has a step or kink at one Q  **[generic]**

**Test.** Overlay the detector-module or panel boundaries in Q. A kink at a panel edge is an
integration artefact, not structure.

**Lever:** back to `calibrate-integrate` — this is not fixable in the PDF layer.

### S(Q) is right but G(r) is wrong  **[source]**

Then the problem is in the transform, not the normalization. Go to the transform section.

---

## G(r) symptoms

### A peak below the shortest possible bond  **[measured]**

**Test.** Compute the shortest chemically possible bond for the composition and compare.
Then re-run with a **lower** `q_max`: a termination ripple moves and changes amplitude with
Q_max; a real peak does not.

- **Moves with Q_max** → Fourier termination ripple. Measured: `first_peak_r_A = 0.72 Å` on
  synthetic Ni data (Ni–Ni is 2.49 Å), reported unconditionally by APEXA's tool because its
  argmax floor is only `r > 0.5 Å` (rule 10). **Lever:** ignore it; report the first peak
  above the physical floor, and say the tool's `first_peak_r_A` field is unreliable.
- **Does not move** → could be a genuine short contact, or a normalization error putting
  curvature into the low-r region. Check the low-r line next.

### The low-r region is not the straight line −4πρ₀r  **[source]**

**Test.** Plot G(r) below the nearest-neighbour distance against −4πρ₀r with your ρ₀.

- **Right slope, offset** → additive background residual. **Lever:** `fit_background=True`.
- **Wrong slope** → wrong ρ₀, or wrong composition (which changes ⟨f⟩² and hence the whole
  amplitude). **Lever:** refine ρ₀ (`fit_number_density=True`) and *check the result against
  the known density* — if the refined ρ₀ is far from the physical one, suspect the
  composition, not the density.
- **Oscillatory** → termination ripple leaking to low r. **Lever:** Lorch window; lower Q_max.

**Exonerating result:** a clean straight line at the right slope is strong evidence that
scale, background, ρ₀ and composition are all mutually consistent — it is the best single
check in this pipeline.

### G(r) has a slope or curvature at large r  **[generic]**

**Test.** G(r) should decay to 0 at large r for a normal sample.

- **Persistent slope** → composition or ⟨f⟩² wrong (including neutral form factors used for
  an ionic sample — 5–20 % at low Q, rule 6). **Lever:** check the composition
  independently; register ionic species.
- **Long-period oscillation** → nanoparticle finite-size damping (real, model it with
  `--diameter`) or an unsubtracted container. Distinguish by measuring the empty container.

### Amplitudes disagree with a published PDF, positions agree  **[source]**

**Test.** Ask which function the other source plots: G, g, T, R or F. They differ by additive
and multiplicative functions of r (`conventions.py`, Keen 2001).

**Lever:** convert with `conventions.py` before comparing. If they still disagree after that,
it is the normalization scale — positions surviving while amplitudes do not is exactly the
unrefined-scale signature.

---

## Uncertainty symptoms

### `sigma_G` is an array of zeros  **[measured]**

**Test.** Did you pass `sigma_intensity`? On APEXA's `compute_pair_distribution`, you cannot:
`_load_1d` discards trailing error columns by design, so even a 3-column `.xye` yields
σ_G = 0. Measured: `sigma_G_stats = {mean: 0, std: 0, min: 0, max: 0}` on an input that
**had** a σ column.

**Lever:** call `i_of_q_to_Gr(..., sigma_intensity=σ)` directly from Python, or fix the
loader. Until then, report **no uncertainty** — a zeros array is not a measurement of zero.

### χ²/ndof is off by ~100×  **[source]**

**Test.** Read **stderr**. `cli/_common.maybe_warn_fallback_sigma` prints a banner whenever
σ was fabricated at 5 % of |G|max because the `.gr` had only two columns.

**Lever:** supply a real third column. The package's own docstring calls this "a common
source of 'why is my χ² off by 100×?' confusion" — it is designed to be visible, so the
failure is only ever *not reading the banner*.

### A Δ-PDF feature is "significant" but implausible  **[source]**

**Test.** Were both states reduced with the same composition, scale, background, Q_max,
window and r-grid? Were their σ real (see above)?

- **σ was zero** → `significant_mask` reports points with σ=0 as **not** significant, so a
  "significant" flag means σ was non-zero somewhere; check it is non-zero *everywhere* you
  are reading.
- **Different normalization between states** → the difference contains the normalization
  difference. **Lever:** refine both states jointly or with identical fixed parameters.
- **Baseline = `"mean"`** → frame *t* is inside its own baseline, so σ²(ΔG) ≠ σ²_t + σ²_ref.
  **Lever:** use an explicit reference row if you want the simple variance.
- **Single-point feature** → genuine PDF features span ≥2 r bins;
  `cluster_significant_regions(min_width_points=2)` drops singletons for this reason.

---

## Modelling symptoms

### A small-box refinement converges with a comfortable χ² but a wrong lattice  **[generic]**

**Test.** Was the G(r) normalized (phase 2)? An unrefined amplitude is absorbed by the model
`scale` and inflated `u_iso`, leaving the fit comfortable and the physics wrong.

**Lever:** normalize first; then refit. Report `u_iso` — implausibly large values are the tell.

### RMC fits beautifully  **[source]**

**Test.** That is the symptom, not the result. Report acceptance ratio, initial vs final χ²,
first-shell coordination number, and compare against a simpler model with WAIC or LOO
(`model_comparison.py`).

**Lever:** if the simpler model is not clearly worse by the criterion, do not present the RMC
configuration as the structure (rule 12, ENVELOPE §3).

### Multi-phase weights are unstable between runs  **[generic]**

**Test.** Weights auto-renormalise and are correlated with per-phase scale, `u_iso` and
finite-size damping. Fix `--diameter` and `--u-iso` and see whether the weights stabilise.

**Lever:** report weights **with** uncertainties, and a model comparison against the
single-phase alternative.

---

## Environment symptoms

### A feature documented here is missing  **[measured]**

**Test.** Run the import probe from spine §0. Do **not** check `__version__`: a 0.1.1-labelled
install was measured shipping the full 0.2.0 module set and all seven CLIs, and APEXA's own
tool docstring says "0.1.0". Three numbers, none authoritative.

**Lever:** gate on imports. If a module genuinely fails to import, `pip install -U midas-pdf`
on the host that owns the data.

### A `midas-pdf` call dies with a torch/library symbol error  **[source]**

**Test.** Was `DYLD_LIBRARY_PATH` / `LD_LIBRARY_PATH` set by a MIDAS C++ environment?

**Lever:** `midas-pdf` is regime 3 — it must run under the `.venv` interpreter with a clean
env, as `_capability_runner.py` does. The pip torch stack breaks under C++ library injection.
