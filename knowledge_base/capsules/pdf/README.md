# PDF / total scattering — I(Q) → S(Q) → G(r) with a propagated 1σ, and a model fit that earns it

Total scattering and the pair distribution function: from an integrated 1-D pattern (or
detector frames) to the reduced PDF *G(r)*, its 1σ band, and — where the data support it —
a small-box, multi-phase, core-shell, joint SAXS+PDF or RMC structural model. Driven by
**`midas-pdf`**, a deliberately thin Faber-Ziman layer over `midas-calibrate-v2`,
`midas-integrate-v2` and `midas-hkls`.

This is the **spine** of the PDF doc set — the one file meant to stay loaded. It carries the
scope gate, the install gate, the order of operations, the hard rules and the halt
conditions. The procedure lives in the phase files; the index below says which holds what.

**Path conventions.** `$MIDAS` is the root of whichever MIDAS checkout you are working in
(on a beamline host, `~s1iduser/opt/MIDAS_canonical`). `$ANALYSIS` is a campaign working
directory that is **not** in this repo — a `$ANALYSIS/...` path is provenance, not a link.

> **Honesty about depth — read this before you trust any number below.**
> The far-field and near-field doc sets encode years of beamtime. **This one encodes none.**
> There is **no APS PDF campaign behind this capsule**: every rule here is traceable to the
> `midas-pdf` source, its README, its module docstrings, or to a **measured probe of APEXA's
> own tool path** run on synthetic data (`LAB_NOTEBOOK.md` §1, 2026-09-10). Nothing here has
> been checked against a real total-scattering dataset reduced by a PDF specialist, and the
> convention question (Faber-Ziman vs Keen; which of S/F/G/g/T/R to report) is explicitly
> flagged upstream as *unsettled, to be agreed with the experimental collaborators*
> (`packages/midas_pdf/dev/PLAN.md`, cited in the package README §Conventions).
> **Treat every G(r) amplitude this capsule helps produce as provisional until a specialist
> has seen it.** Peak *positions* are far more robust than peak *amplitudes* — say which you
> are quoting. This file is meant to grow as real PDF beamtimes are run.

---

### The doc set — what to read when

| File | Holds | Read it |
|---|---|---|
| **`README.md`** (this) | scope + install gate, the order, hard rules, halt conditions, traps | always, and keep loaded |
| `phase-0-survey.md` | is it total scattering? Q range, composition, the σ column, the calibration | first, before promising anything |
| `phase-1-normalize.md` | Faber-Ziman S(Q): composition, ⟨f⟩²/⟨f²⟩, Compton, ions, corrections | before any transform |
| `phase-2-refine.md` | fitting scale / background / ρ₀ against model-free physics — **not optional** | always, between normalize and transform |
| `phase-3-transform.md` | windowed sine FT → G(r) + σ; Q_max, window, termination ripple, r-grid | to produce the PDF |
| `phase-4-model.md` | small-box, multi-phase, core-shell, joint SAXS/SANS, RMC, model comparison | only if phase 2 converged |
| `phase-5-report.md` | which function you are reporting, σ provenance, what to label provisional | at the end |
| `PARAMETERS.md` | every knob: `midas-pdf` API defaults and the CLI surface | when configuring |
| `ENVELOPE.md` | what a PDF **can** determine and what it cannot | **before promising an answer** |
| `DIAGNOSIS.md` | symptom → discriminating test → cause → lever | **when something looks wrong** |
| `RUNBOOK.md` | **VOLATILE** — where it runs, installed versions, current pick-up point | at session start, re-verify live |
| `LAB_NOTEBOOK.md` | the evidence ledger — thin, and honest about being thin | before re-opening any question |

`Q` is in Å⁻¹, `r` in Å, wavelength in Å throughout. `ρ₀` is the atomic number density
(atoms Å⁻³). Contract: `~/opt/beamreport/DOCS_SPEC.md` §6 (separate repo, not under `$MIDAS`).

---

## STOP — read this before touching anything

**Scope.** X-ray **total scattering** on a powder-like or amorphous sample: one integrated
one-dimensional pattern per state, taken to high Q, normalized to a Faber-Ziman S(Q) and
sine-transformed to a reduced PDF G(r). Handled through `midas_pdf` on top of `midas_integrate_v2`. This doc
set does **not** cover 3D-ΔPDF of single-crystal diffuse scattering — a different measurement
and a different reconstruction, explicitly excluded by the package (`deltapdf.py` module
docstring). It also does not cover neutron total scattering: the Compton/recoil treatment
here is X-ray-specific (rule 5). **No beamline scope is asserted.** This capsule has no
campaign behind it, so it names no station and will never block a tool on a beamline
mismatch — record the station you run it at in `RUNBOOK.md` and `LAB_NOTEBOOK.md` so the
next session inherits it.

### When to stop and come back with a question

**"Get back to me if you get stuck" does not fire here.** PDF's failure modes finish and
look right: an unnormalized pattern still transforms into a smooth, plausible G(r) with peaks
in believable places; a dropped σ column still yields a `sigma_G` field, full of zeros; a
Fourier termination ripple at low r is still reported as a "first peak". In every case the
run succeeds and returns numbers.

So the trigger is not confusion. **Halt on these named conditions, whether or not anything
seems wrong:**

| Condition | Why you cannot decide it yourself |
|---|---|
| you are about to report a **G(r) amplitude, coordination number, or ρ₀** from a chain whose S(Q) was never refined to ⟨S⟩→1 | the amplitude is wrong by whatever the scale error is — measured at **2.75×** on APEXA's default path (Notebook §1b); positions survive, amplitudes do not (§2) |
| the tool returned a **`sigma_G` that is all zeros** and you are about to quote a σ, a χ², or a "significant" Δ-PDF feature | no uncertainty was propagated; a zeros array is not a measurement of zero (§0, Notebook §1a) |
| the **composition is not known independently** of the diffraction data | ⟨f⟩² and ⟨f²⟩ are built from it; a guessed composition silently rescales S(Q) and puts a spurious slope in G(r) (§1) |
| the sample contains **ionic species whose form factors matter** (low-Q, light+heavy mix, oxides) | `midas_hkls` **silently maps `Ni2+`→neutral `Ni`**; the error is 5–20 % at low Q and it does not raise (§1) |
| you are asked for a **first-shell coordination number** | it is an integral under a peak, so it inherits the full amplitude-scale error *and* the ρ₀ you did not independently measure (ENVELOPE) |
| you are about to call a **Δ-PDF feature "significant"** and the two states were not measured under the same normalization, Q_max, window and r-grid | σ²(ΔG)=σ²_a+σ²_b assumes independent states on a common grid; anything else makes the n-σ test meaningless (§4) |
| you want a **Placzek correction** because you have used neutron total-scattering software | for X-rays it is subsumed by the Breit-Dirac recoil factor already applied; the residual is ~10⁻¹² (rule 5). Applying one anyway is a real bias, not a no-op |
| the pattern's **Q_max is below ~15 Å⁻¹** and you are asked to resolve distinct near-neighbour shells | real-space resolution is set by Q_max; below that, shells merge and no window choice recovers them (ENVELOPE) |
| you are about to **compare a G(r) against a published one** (PDFgetX3 / PDFgetN / GudrunX / PDFgui) without pinning both conventions | the S/F/G/g/T/R family differ by additive and multiplicative r-terms; the same data plots four different ways (rule 7, `conventions.py`) |
| an **RMC or a many-parameter small-box fit converged** and you are about to present the structure | RMC fits noise readily; without a model-comparison criterion (WAIC/LOO) and a stated parameter count, "it fits" is not evidence (§4) |
| this is the **first real PDF dataset** run through APEXA at a beamline | this capsule is package-grounded only. Say so, keep the reduction, and get a PDF specialist to look before it leaves the room |
| this document and the tree **disagree** | report it; do not work around it |

When you halt, say which row fired, what you measured, and what you would need to proceed.
Finish everything not blocked by it first.

### Hard rules

1. **Normalization is a fit, not a guess — run phase 2 before you believe any amplitude.**
   `refine_normalization` fits `scale`, a polynomial background `b(Q)=Σ c_j (Q/Q_max)^j` and
   optionally ρ₀ by L-BFGS against two **model-free** constraints: ⟨S(Q)⟩→1 over the high-Q
   tail, and G(r) = −4πρ₀r below the nearest-neighbour distance (where g(r)=0, so the reduced
   PDF is exactly that straight line). This is the step PDFgetX3 and Gudrun leave to hand
   twiddling, and it is the one `midas-pdf` exists to make an optimization. Skipping it is
   the single largest error source in this pipeline: measured **S(Q) mean 2.75, min 1.23**
   on an unrefined run that produced a perfectly smooth-looking G(r) (Notebook §1b).

2. **σ is carried or it is absent — never inferred.** Every arrow in this chain is a torch
   op carrying a 1σ, and the analytic band is validated against a Monte-Carlo bootstrap to
   **<1 %** (`dev/demo_sigma_validation.py`). That guarantee holds only if you actually pass
   `sigma_intensity`. Two ways it silently evaporates: (a) APEXA's `compute_pair_distribution`
   loader **discards trailing error columns by design** (`_capability_runner._load_1d`
   docstring), so σ_G comes back as an array of zeros — measured (Notebook §1a); (b) the CLI
   commands **fabricate σ = 5 % of |G|max** when the `.gr` has only two columns and print a
   stderr banner saying the reported χ² is arbitrary (`cli/_common.fallback_sigma`). Read the
   banner. A χ² computed on a fabricated σ means nothing.

3. **Polyatomic means Faber-Ziman — the monoatomic path is a different quantity.**
   `midas_integrate_v2.pdf.normalize_to_S` divides by a single ⟨f²⟩ and is correct only for
   one element. A polyatomic sample needs both ⟨f⟩²(Q) and ⟨f²⟩(Q) and the Laue term:
   `S(Q) = [I_coh − (⟨f²⟩ − ⟨f⟩²)] / ⟨f⟩²`, with `I_coh = scale·[I_meas − background] −
   I_Compton`. That bridge is the whole reason `midas-pdf` exists; use `faber_ziman_S` (it
   reduces exactly to the monoatomic form when there is one element — pinned by a regression
   test).

4. **Never take the composition, the wavelength or ρ₀ from a filename or a template.**
   Composition sets ⟨f⟩²/⟨f²⟩; wavelength sets the Compton angular factor; ρ₀ sets the low-r
   line the refinement is anchored on. All three are silent when wrong. Worse, APEXA's runner
   **substitutes λ = 0.1 Å when the wavelength argument is empty or zero**
   (`_capability_runner.cmd_pdf`: `wavelength_A=float(args.wavelength or 0.1)`) — a falsy `0`
   does not raise, it defaults. State each value and the file you read it from.

5. **Do not apply a Placzek correction to X-ray data.** It is a *neutron* correction. For
   X-rays the equivalent is already carried by the Breit-Dirac recoil factor applied to the
   Hubbell Compton term (`Composition.compton(breit_dirac=True)`, the default); the residual
   beyond it is O((hν/Mc²)²) ≈ 10⁻¹² at 63 keV for Ni. `placzek.py` exists to say exactly
   this and ships **no** X-ray Placzek function on purpose.

6. **Ionic species need ionic form factors, and nothing will tell you they were ignored.**
   `midas_hkls` ships neutral-atom Cromer-Mann coefficients and **silently maps** `"Ni2+"`,
   `"O2-"`, `"Ce4+"` to the neutral atom — 5–20 % wrong at low Q, no warning.
   `midas_pdf.ionic_form_factors` supplies the 4-Gaussian ionic coefficients (each satisfying
   the electron-count sum rule `Σa + c ≈ Z − charge`); register or `publish_to_midas_hkls()`
   them before normalizing an oxide or any mixed light/heavy ionic sample.

7. **Name the function and the convention, every time.** F(Q)=Q[S−1]; G(r) is Keen's D(r) and
   Egami–Billinge's G(r); g(r)=1+G/(4πrρ₀); T(r)=G+4πrρ₀; R(r)=rG+4πr²ρ₀ (Keen, *J. Appl.
   Cryst.* **34**, 172 (2001), implemented in `conventions.py`). These differ by additive and
   multiplicative functions of r, so "the PDF" names four different curves. The upstream
   package states the FZ-vs-Keen choice is **still to be settled with the experimental
   collaborators** — so report the convention alongside the number, and do not silently pick.

8. **Choose Q_max deliberately and report it.** Truncating the transform at Q_max convolves
   G(r) with the window's transform: too low and shells merge, too high and you fold detector
   noise into termination ripple. The default window is **Lorch** (damps ripple at a cost in
   resolution); `q_max=None` means *no truncation at all* — the whole measured range,
   including its noisy tail, goes into the FT. That is rarely what you want.

These rules distrust the data and the physics. The rest distrust your own run:

9. **Suspect success.** Every failure above returns a smooth curve. "It ran" and "G(r) looks
   like a PDF" are not evidence. Ask what the step would look like if it had silently done the
   wrong thing — kept scale=1, dropped σ, used neutral form factors for an oxide, transformed
   an untruncated noisy tail — and check that specific thing.

10. **A peak below the shortest chemically possible bond is an artefact, not a discovery.**
    APEXA's tool reports `first_peak_r_A` as the argmax of G(r) for **r > 0.5 Å**, with no
    physical floor. On an unrefined synthetic Ni pattern it returned **0.72 Å** — a low-r
    termination ripple, presented as the nearest-neighbour distance (Notebook §1c). Compare
    every "first peak" against the shortest bond the composition allows before quoting it.

11. **Do not reimplement what a `midas_*` package already does.** Geometry and wavelength →
    `midas-calibrate-v2`; pixels → I(Q) with σ → `midas-integrate-v2` (polygon-exact, with
    polarization / solid-angle / dark); form factors and anomalous f′,f″ → `midas-hkls`;
    Compton → `midas_integrate_v2.corrections.compton`; the sine FT and its variance →
    `midas_integrate_v2.pdf.fourier_sine_transform`. `midas-pdf` adds the polyatomic FZ
    normalization and Δ-PDF, and re-exports the rest — matching that split keeps you inside
    the tested path.

12. **A converged fit is not a validated model.** The package ships WAIC and a LOO estimator
    (`model_comparison.py`) precisely because posterior samples alone do not say which model
    is better. Before presenting a structure, state the parameter count, and compare against
    at least one simpler alternative. This binds hardest on RMC, which will fit noise.

### Traps that silently corrupt results

| Trap | Symptom if missed | Where |
|---|---|---|
| σ column present in the file but dropped by the loader | `sigma_G` returned as an array of **zeros**, read as "uncertainty is negligible" | §0, Notebook §1a |
| `.gr` with only two columns fed to a CLI refinement | σ **fabricated** at 5 % of \|G\|max; χ²/ndof is arbitrary (stderr banner says so) | §4 |
| transform run without phase-2 refinement | ⟨S⟩ sits at 2.75 instead of 1; G(r) smooth and entirely wrong in amplitude | §2, Notebook §1b |
| wavelength argument left empty or `0` | silently defaults to **λ = 0.1 Å**; Compton angular factor wrong; biased S(Q) | §1 |
| ionic sample normalized with neutral form factors | 5–20 % low-Q error in ⟨f⟩²; slope in G(r) read as structure | §1 |
| monoatomic `normalize_to_S` used on a polyatomic sample | Laue term missing; S(Q) is a different quantity, not a rescaled one | §1 |
| `q_max=None` on a pattern with a noisy tail | detector noise folded into termination ripple across the whole r range | §3 |
| a low-r ripple reported as the first coordination shell | "nearest-neighbour distance" of 0.72 Å from an argmax with no physical floor | §3, Notebook §1c |
| Placzek correction applied to X-ray data | a real bias introduced to fix something Breit-Dirac already handles | §1 |
| G/g/T/R/F compared across tools without pinning the convention | disagreements that are pure definition, chased as physics | §5 |
| Δ-PDF between states on different r-grids, Q_max or normalization | σ²(ΔG) invalid; the n-σ mask is decorative | §4 |
| Δ-PDF of powder data described as 3D-ΔPDF | a different measurement entirely (single-crystal diffuse); the package says so | §4 |
| `baseline="mean"` in `sequence_delta_pdf` treated as an independent reference | frame *t* is inside its own baseline, so σ² does not simply add | §4 |
| single-point Δ-PDF "features" | genuine features span ≥2 r bins; `cluster_significant_regions` drops singletons by default | §4 |
| multiple scattering left in a thick or strongly-scattering sample | smooth background absorbed into `scale`/`offset`, distorting the low-r line | §1 |
| Monte-Carlo MS reference used inside a gradient chain | it is the one **non-differentiable** piece in the package, by design | §1 |
| `__version__` used to decide whether a feature exists | measured: reports **0.1.1** on an install that ships the 0.2.0 CLI + modules | RUNBOOK, Notebook §1d |
| RMC convergence presented as a determined structure | RMC fits noise; without WAIC/LOO and a parameter count it is not evidence | §4 |
| coordination number quoted from an unrefined ρ₀ | the integral scales with both the amplitude error and ρ₀ | ENVELOPE |

---

## 0. Environment and install gate — before anything else

`midas_pdf` is a **Python library plus seven CLI entry points**. Most of the pipeline is
library calls; the modelling steps have commands.

```bash
pip install "midas-pdf>=0.2.0"     # torch>=2.1, numpy>=1.22, midas-params>=0.9.0
```

Then run the gate and **read its output** — `pip install` exiting 0 tells you nothing, and on
this package the version string is not a capability gate (see the trap table):

```bash
KMP_DUPLICATE_LIB_OK=TRUE python - <<'PY'
import importlib, shutil, midas_pdf
print("reported version:", getattr(midas_pdf, "__version__", "?"), "(do NOT gate on this)")
for m in ("normalize", "pipeline", "refine", "deltapdf", "conventions",
          "composition", "ionic_form_factors", "corrections", "multiple_scattering",
          "structure", "cif", "rmc", "saxs", "model_comparison"):
    try:
        importlib.import_module(f"midas_pdf.{m}"); print(f"  {m}: OK")
    except Exception as e:
        print(f"  {m}: MISSING ({type(e).__name__})")
print("CLIs:", [c for c in ("midas-pdf-refine","midas-pdf-cif","midas-pdf-joint",
                            "midas-pdf-multiphase","midas-pdf-coreshell","midas-pdf-rmc",
                            "midas-integrate-v2-pdf") if shutil.which(c)])
PY
```

Gate on the **imports and the CLI list**, not the version. Outputs go in a project/gdata
directory you own — **never `/tmp`**. On an APS beamline host use the shared env by full path
(`/home/beams12/S1IDUSER/opt/envs/midas/bin/python`); see `RUNBOOK.md`.

---

## 0a. THE ORDER — do these in this sequence

Two steps cannot be checked after the fact, so they come first.

```
phase 0   survey       is it total scattering? Q range, composition, σ column, calibration
phase 1   normalize    Faber-Ziman S(Q): real composition, ionic f(Q) if ionic, Compton, corrections
          ---- look at S(Q). It must be heading for 1. Do not skip. ----
phase 2   refine       fit scale/background/rho0 against <S>->1 and G=-4*pi*rho0*r. NOT optional.
phase 3   transform    windowed sine FT to G(r) with sigma; choose Q_max deliberately
          ---- check the first peak against the shortest chemically possible bond ----
phase 4   model        small-box / multiphase / core-shell / joint SAXS / RMC, with model comparison
phase 5   report       name the convention, state sigma provenance, keep provisional labels
```

The modelling phase is optional and **gated on phase 2 converging**. A structural model
fitted to an unnormalized G(r) will absorb the scale error into its displacement parameters
and scale, and report a comfortable χ².

Phases 1–3 are also available as a single call — `i_of_q_to_Gr(q, I, comp, r, ...)` — which is
what APEXA's `compute_pair_distribution` invokes. **That single call has `scale=1.0` and no
refinement**, so using it as the whole pipeline skips phase 2 by construction (rule 1).

---

## 1. Where things live

| Thing | Where |
|---|---|
| package source | `$MIDAS/packages/midas_pdf/` |
| runnable one-per-capability examples | `$MIDAS/packages/midas_pdf/examples/` (01–13, see `examples/README.md`) |
| σ validation vs MC bootstrap | `$MIDAS/packages/midas_pdf/dev/demo_sigma_validation.py` |
| open items, conventions decision | `$MIDAS/packages/midas_pdf/dev/PLAN.md` |
| APEXA tool | `compute_pair_distribution` → `_capability_runner.py` `pdf` subcommand |
| upstream I(Q) with σ | `midas-integrate-v2` (`integrate_*_with_variance`) |

---

## 2. Done means

- S(Q) refined to ⟨S⟩→1 over the high-Q tail, with the fitted `scale`, background
  coefficients and ρ₀ reported (phase 2), **or** an explicit statement that normalization
  was not refined and every amplitude is therefore provisional.
- G(r) with a σ band whose provenance is stated: propagated from a real σ_I, or absent —
  never a zeros array quoted as a measurement.
- Q_max, window, r-grid and convention named in the report.
- The first peak checked against the shortest chemically possible bond for the composition.
- Any structural model reported with its parameter count and at least one comparison
  (WAIC/LOO or a simpler alternative).

---

## Phases

Open each as you reach it:

- **[phase-0-survey.md](phase-0-survey.md)** — §0 what you have, what is missing
- **[phase-1-normalize.md](phase-1-normalize.md)** — §1 composition, form factors, Compton, corrections, MS
- **[phase-2-refine.md](phase-2-refine.md)** — §2 the differentiable normalization fit
- **[phase-3-transform.md](phase-3-transform.md)** — §3 Q_max, window, the sine FT, σ, ripple
- **[phase-4-model.md](phase-4-model.md)** — §4 small-box, multi-phase, core-shell, joint, RMC, Δ-PDF
- **[phase-5-report.md](phase-5-report.md)** — §5 conventions, provenance, provisional labels

When something looks wrong: **[DIAGNOSIS.md](DIAGNOSIS.md)**. Before promising that this
measurement can answer the question: **[ENVELOPE.md](ENVELOPE.md)**. Every knob:
**[PARAMETERS.md](PARAMETERS.md)**. Host and version state: **[RUNBOOK.md](RUNBOOK.md)**.

## Sibling doc sets

`calibrate-integrate` (produces the I(Q) this consumes) · `xrd-ct` (spatially resolved
diffraction) · `ff-hedm` / `nf-hedm` / `pf-hedm` (grain-resolved) · `dfxm` · `dct-tt` · `tomo`.
