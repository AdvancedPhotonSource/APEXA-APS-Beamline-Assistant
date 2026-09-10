# PDF parameter reference — API surface and CLI surface

> Part of the **PDF doc set**. Spine: [`README.md`](README.md). Section numbers (§n) are
> continuous across the set.

`midas-pdf` has **no parameter file**. Everything is either a Python keyword argument or a
CLI flag. This file is the complete knob list, with the defaults as they are in the source —
defaults matter here because several of them are the trap (rule 1, rule 8).

Units throughout: **Q in Å⁻¹, r in Å, wavelength in Å, ρ₀ in atoms Å⁻³**, u_iso in Å².

---

## 10. Library API

### 10a. `i_of_q_to_Gr` — the whole chain in one call (`pipeline.py`)

`i_of_q_to_Gr(q, intensity, composition, r_grid, *, ...) -> (G, sigma_G, S)`

| Keyword | Default | Meaning | Note |
|---|---|---|---|
| `wavelength_A` | **required** | source wavelength | sets the Compton angular factor. Wrong ⇒ biased S(Q) |
| `scale` | `1.0` | multiplies I_meas onto the per-atom electron scale | **`1.0` is almost never right.** Fit it in phase 2 (rule 1) |
| `compton` | `True` | subtract the modelled Compton term | Hubbell tabulated + Breit-Dirac recoil |
| `background` | `None` | lumped smooth term subtracted from I_meas *before* normalization | Tier-1 MS + fluorescence + air; see `multiple_scattering` |
| `sigma_intensity` | `None` | σ on I(Q) | **`None` ⇒ no uncertainty is propagated** (rule 2) |
| `q_max` | `None` | truncate the FT | `None` = no truncation, noisy tail included (rule 8) |
| `window` | `"lorch"` | FT window | Lorch damps ripple, costs r-resolution |
| `fractions` | `None` | override the composition's mole fractions with a tensor | makes fractions differentiable |
| `return_S` | `True` | also return S(Q) | **always keep it and look at it** — it is the phase-1 check |

All inputs may carry `requires_grad`; gradients flow to `scale`, `fractions`, `background`
and, through `q`, to the upstream geometry and wavelength.

### 10b. `faber_ziman_S` — the normalization (`normalize.py`)

`S(Q) = [I_coh(Q) − (⟨f²⟩ − ⟨f⟩²)] / ⟨f⟩²`, with
`I_coh(Q) = scale·[I_meas(Q) − background(Q)] − I_Compton(Q)`.

Same keywords as above (`composition`, `wavelength_A`, `scale`, `compton`, `background`,
`sigma_intensity`, `fractions`). σ propagation treats form factors and the Compton model as
noiseless, so `σ_S(Q) = scale · σ_I(Q) / ⟨f⟩²`.

Reduces **exactly** to the monoatomic `midas_integrate_v2.pdf.normalize_to_S` for a
single-element composition — pinned by a regression test.

### 10c. `refine_normalization` — the fit (`refine.py`)

`refine_normalization(q, intensity, composition, r_grid, *, wavelength_A, number_density, ...) -> RefineResult`

| Keyword | Default | Meaning |
|---|---|---|
| `number_density` | **required** | ρ₀; anchors the low-r line `G = −4πρ₀r` |
| `r_min_phys` | `1.0` | upper bound (Å) of the unphysical low-r region where that line must hold — set it **just below the nearest-neighbour distance** for your material, not at the default |
| `q_asymptote_frac` | `0.25` | fraction of the high-Q tail over which ⟨S⟩→1 is enforced |
| `fit_background` | `True` | refine `b(Q) = Σ_j c_j (Q/Q_max)^j` |
| `bg_order` | `0` | polynomial degree; `0` = single additive constant |
| `fit_offset` | `None` | **deprecated** alias for `fit_background` |
| `fit_number_density` | `False` | also refine ρ₀ |
| `init_scale` | `1.0` | starting scale |
| `steps` / `lr` | `60` / `0.2` | L-BFGS budget |
| `w_lowr` / `w_highq` | `1.0` / `1.0` | relative weight of the two constraints |
| `compton`, `q_max`, `window`, `fractions`, `sigma_intensity` | as §10a | |

### 10d. Δ-PDF (`deltapdf.py`)

| Function | Signature | Note |
|---|---|---|
| `delta_pdf` | `(G_a, G_b, sigma_a=None, sigma_b=None) -> (ΔG, σ_Δ)` | ΔG = G_b − G_a; σ² adds (assumes independence). A missing σ is treated as **0**, and σ_Δ is all-zeros if neither is given |
| `significant_mask` | `(ΔG, σ_Δ, n_sigma)` | \|ΔG\| > n·σ. Points with σ=0 are reported **not** significant rather than dividing by zero |
| `sequence_delta_pdf` | `(G_stack (T,R), sigma_stack=None, baseline=0)` | `baseline` is a row index or `"mean"`. For `"mean"`, frame *t* is inside its own baseline, so σ² does **not** simply add |
| `significant_features` | `(ΔG, σ_Δ, n_sigma)` | per-frame per-r mask |
| `cluster_significant_regions` | `(mask, r, min_width_points=2)` | contiguous (r_lo, r_hi) intervals; single-point islands dropped by default |

### 10e. Corrections and backgrounds

| Function | Module | Note |
|---|---|---|
| `detector_efficiency`, `apply_detector_efficiency` | `corrections` | η(Q) = 1 − exp(−µt/cos ψ). A real high-Q tilt at large angle — divide it out |
| `flat_plate_transmission` | `corrections` | symmetric transmission slab |
| `paalman_pings_cylinder_in_cylinder`, `paalman_pings_cell_only` | `corrections` | capillary + container |
| `linear_attenuation_um` | `corrections` | µ from NIST MACs shipped in `midas_hkls` — no new data |
| `lumped_background`, `polynomial_basis` | `multiple_scattering` | Tier-1 smooth MS + fluorescence + air, `b(Q)=Σ c_j (Q/Q_max)^j` |
| `differential_cross_section` | `cross_section` | per-atom dσ/dΩ(Q), the MS engine |
| `multiple_scattering_mc`, `multiple_scattering_mc_cylinder` | `ms` | Tier-3 Monte-Carlo reference — **non-differentiable, by design** |
| `ms_background_on_grid` | `ms_transport` | all-orders MS by differentiable discrete-ordinates transport (slab) |
| `expected_fluorescence` | `fluorescence` | which sample elements fluoresce at a given energy — run it before blaming the background |

### 10f. Form factors and composition

`Composition({"Ce": 1, "O": 2})` — **number (mole) fractions**, not mass fractions. Provides
⟨f⟩(Q), ⟨f²⟩(Q), the Laue term, and `.compton(..., breit_dirac=True)`.

`ionic_form_factors`: `ionic_form_factor(species, q)`, `register_ion`, `is_ionic_species`,
`available_ions`, `publish_to_midas_hkls()`. Coefficients are 4-Gaussian + constant,
`f(Q) = c + Σ a_i exp(−b_i s²)`, `s = Q/4π`, each satisfying `Σa + c ≈ Z − charge`.
Needed because `midas_hkls` **silently** maps ionic strings to the neutral atom (rule 6).

### 10g. Conventions (`conventions.py`, Keen 2001)

| Function | Returns | σ |
|---|---|---|
| `structure_function_F` | `F(Q) = Q[S(Q) − 1]` | `σ_F = Q σ_S` |
| `pair_distribution_g` | `g(r) = 1 + G/(4πrρ₀)` | scales with 1/(4πrρ₀) |
| `total_correlation_T` | `T(r) = G + 4πrρ₀` | `σ_T = σ_G` (pure additive shift) |
| `radial_distribution_R` | `R(r) = rG + 4πr²ρ₀` | ∫R dr over a peak = coordination number |

---

## 11. CLI surface

Seven console scripts. All read 2- or 3-column whitespace ASCII (`x y [sigma]`, comments
`#`, `//`, `@`) and emit JSON on stdout.

> **The σ trap, at the CLI.** If the `.gr` has only two columns, `cli/_common.fallback_sigma`
> **fabricates σ = 5 % of \|G\|max** and `maybe_warn_fallback_sigma` prints a banner to
> **stderr**. The run succeeds and reports a `chi2_reduced` that is arbitrary. Capture stderr.

| Command | Purpose | Key flags |
|---|---|---|
| `midas-pdf-refine` | small-box refinement against a `.gr` | `--cif --gr --r-min --r-max --u-iso --scale --steps --bg-order --posterior-samples --json` |
| `midas-pdf-multiphase` | `G_total = Σ w_i·G_i·γ_i`, weights auto-renormalised | `--cif A.cif B.cif --gr --weights --diameter --u-iso --scale --r-min --r-max --steps --lr` |
| `midas-pdf-coreshell` | two nested-sphere phases, volume-fraction weighted | `--core-cif --shell-cif --gr --r-core --shell-thickness --u-iso-core --u-iso-shell --pin-geometry --steps --lr` |
| `midas-pdf-joint` | joint SAXS+PDF (`--sans` for three-way) | `--cif --gr --saxs [--sans] --shape --polydispersity --n-poly-nodes --init-a --init-u-iso --init-diameter --init-scale-saxs --init-scale-sans --steps --lr` |
| `midas-pdf-rmc` | reverse Monte Carlo on an (N,N,N) supercell | `--cif --gr --size --moves --move-types --sigma-A --cluster-radius --temperature --chemical-potential --min-distance --u-iso --seed --output --first-shell-window` |
| `midas-pdf-cif` | inspect / round-trip a CIF through `midas-hkls` | `info <path>` \| `convert <src> <dst>` |
| `midas-integrate-v2-pdf` | integration-side PDF entry point | see `midas-integrate-v2` |

`--move-types` for RMC: `displace` (default) · `displace+cluster` (50/50) · `all`
(+ rigid rotation) · `gc` (grand-canonical: insert/remove).

Outputs are JSON summaries of the refined parameters, their **Hessian-based** uncertainties,
and χ²/ndof — read the caveat above before quoting the χ².

---

## 12. APEXA's own path

`compute_pair_distribution(pattern_file, composition, wavelength, x_is_two_theta=False,
q_max=0.0, window="lorch", r_min=0.0, r_max=20.0, n_r=500, output_file="")`
→ `_capability_runner.py pdf` → `i_of_q_to_Gr`.

Three behaviours of that wrapper you must know (all measured, Notebook §1):

| Behaviour | Consequence |
|---|---|
| `_load_1d` **discards trailing error columns** by design | `sigma_intensity=None` ⇒ `sigma_G` returns as an array of **zeros**, not `null` |
| no refinement step; `scale` stays at its `1.0` default | ⟨S⟩ does not approach 1 (measured 2.75); amplitudes are wrong (rule 1) |
| `wavelength_A = float(args.wavelength or 0.1)` | a falsy `0` silently becomes **λ = 0.1 Å** (rule 4) |
| `first_peak_r_A` = argmax of G(r) for `r > 0.5 Å` | no physical floor; a low-r ripple is reported as the nearest-neighbour distance (rule 10) |
| `composition` parsed as `"El:frac,El:frac"`, bare element ⇒ 1.0 | mass fractions or a formula string give the wrong Laue term, silently |
| `q_max=0.0` means *no truncation* | the whole measured range, noisy tail included (rule 8) |

For anything where amplitude matters, drive `refine_normalization` explicitly rather than
using the one-call path.
