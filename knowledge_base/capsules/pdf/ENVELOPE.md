# PDF / total scattering — measurement envelope

> Part of the **PDF doc set**. Spine: [`README.md`](README.md). Contract:
> `~/opt/beamreport/DOCS_SPEC.md` §6 (separate repo, not under `$MIDAS`).

What a total-scattering measurement can determine, what it cannot, and which of those is a
choice you can change next cycle. Read this **before promising an answer**, and before
proposing a different measurement.

> **Provenance caveat.** Rows here are derived from the `midas-pdf` implementation and from
> standard total-scattering physics, **not** from an APS PDF campaign — this capsule has none
> (spine, "Honesty about depth"). Ranges marked *(generic)* are textbook, not measured here.

---

## 1. Fixed — cannot change this cycle

| Property | Value | Provenance | What it makes unobtainable | Substitute |
|---|---|---|---|---|
| **Q_max of the acquired pattern** | set by energy + detector reach | the data | Real-space resolution: features closer than ~2π/Q_max in r cannot be separated, and no window recovers them *(generic)* | Nothing after the fact. Re-measure at higher energy / larger 2θ reach |
| **Q_min** | set by beamstop + geometry | the data | The longest-range correlations; the low-Q part of S(Q) that anchors the FT | `image_to_Gr`'s `q_min` floor drops the unreliable low-Q rather than trusting it |
| **Counting statistics per Q bin** | in the frames | `midas-integrate-v2` variance | The σ floor on every downstream G(r) point, hence which Δ-PDF features can ever be significant | Longer exposure / more frames; nothing in reduction adds information |
| **Whether a σ column exists at all** | per pipeline | upstream integration | If I(Q) arrived with no σ, the "propagated 1σ" chain has nothing to propagate | Re-integrate with `integrate_*_with_variance`. Do **not** fabricate σ |
| **Sample composition** | the sample | independent knowledge (synthesis, EDS, stoichiometry) | ⟨f⟩², ⟨f²⟩, the Laue term, and hence S(Q) itself | None from the diffraction data alone — this is an input, not an output |
| **X-ray, not neutron** | the source | this pipeline | Neutron contrast, isotopic substitution, light-element sensitivity | A neutron measurement. The Compton/Breit-Dirac treatment here is X-ray-specific |
| **Powder / amorphous averaging** | the measurement | scope gate | Any directional information; single-crystal diffuse 3D-ΔPDF is a different experiment | Single-crystal diffuse scattering, out of scope for this doc set |

---

## 2. Configured — set per run, changeable next time

| Parameter | Used | Achievable range | Limited by | What changing it would buy |
|---|---|---|---|---|
| **Normalization refinement** | per reduction | on / off | choice, not hardware | **The single largest error source.** Off ⇒ measured ⟨S⟩ ≈ 2.75 instead of 1 and every amplitude is wrong (Notebook §1b). On costs ~60 L-BFGS steps |
| **Q_max used in the transform** | per reduction | 0 … Q_max of data | the data, and the noise floor | Real-space resolution against termination ripple. `None` = use everything, including the noisy tail |
| **Window** | per reduction | `lorch` (default) / none | choice | Lorch damps ripple at a cost in r-resolution. Report which |
| **Background model order** | per refinement | `bg_order=0` (constant, default) upward | overfitting | Absorbs fluorescence baseline and air/Compton-tail residual. Higher order can eat real low-Q structure |
| **ρ₀ refined or fixed** | per refinement | `fit_number_density=False` (default) | whether you know it | A refined ρ₀ is an output with a stated uncertainty; a fixed wrong one biases the low-r anchor |
| **r-grid range and sampling** | per reduction | any | nothing physical | Only presentation — oversampling r does **not** add real-space information beyond Q_max |
| **Ionic vs neutral form factors** | per sample | registered ions | `ionic_form_factors` coverage | 5–20 % at low Q for ionic samples. Free to fix, silent if not (rule 6) |
| **Absorption / detector-efficiency corrections** | per geometry | flat-plate, Paalman-Pings cylinder-in-cylinder, cell-only, detector η(Q) | `corrections.py` + NIST MACs in `midas_hkls` | Removes real high-Q tilt and container contribution that otherwise land in `scale`/background |
| **Multiple-scattering tier** | per sample | Tier 1 lumped polynomial / Tier 2 analytic single+double / Tier 3 MC or discrete-ordinates transport | `multiple_scattering.py`, `ms.py`, `ms_transport.py` | Thick or strongly-scattering samples. **The MC reference is non-differentiable by design** |
| **Structural model class** | per analysis | small-box (PDFfit-style), multi-phase, core-shell, joint SAXS(+SANS), RMC | `structure.py`, `multi_phase.py`, `saxs/`, `rmc/` | What question the model can answer — and how easily it fits noise |

---

## 3. Intrinsic — the physics or the sample forbids it

| Question | Why it is not answerable | Distinguish from |
|---|---|---|
| "What is the composition?" | Composition is an **input** to normalization, not an output. Getting it wrong rescales S(Q) smoothly, so the result still looks like a PDF | A *consistency check*: a badly wrong composition leaves a slope in G(r) at high r — suggestive, not a measurement |
| "Where is each atom?" | A powder PDF is a 1-D distribution of interatomic **distances**, orientationally averaged. Many structures share a G(r) | A small-box refinement, which tests whether *a proposed* structure is consistent — a much weaker claim |
| "Is this phase fraction exact?" | Multi-phase weights are correlated with per-phase scale, displacement parameters and finite-size damping | A weight **with** its uncertainty and a model comparison against the one-phase alternative |
| "Is the RMC configuration the structure?" | RMC is underdetermined: many configurations fit one G(r), and it will fit noise | RMC as a *generator of candidate local motifs*, reported with acceptance ratio, χ² change and coordination statistics |
| Absolute coordination number without an independent ρ₀ | N = ∫R(r)dr over the peak, and R(r) carries both the amplitude scale and 4πr²ρ₀ | A coordination number quoted **with** the refined ρ₀ and its σ, after phase 2 converged |
| Sub-Q_max structural detail | Real-space resolution is bounded by 2π/Q_max *(generic)* | Apparent detail from an unwindowed transform — that is termination ripple |
| Whether a Δ-PDF change is "real" without σ | The n-σ test is the whole mechanism; with σ=0 the mask is decorative | A Δ-PDF with genuinely propagated σ on a common grid, where `significant_mask` means something |

---

## 4. Derived — computed, and only as good as its inputs

| Quantity | Computed from | Inherits |
|---|---|---|
| S(Q) | I(Q), composition, Compton, scale, background | every error in all five |
| G(r) | S(Q), Q_max, window | the S(Q) amplitude error, plus termination ripple |
| σ_G | σ_I through the FT (validated <1 % vs MC bootstrap) | **zero if σ_I was never supplied** — the commonest case on APEXA's path |
| g(r), T(r), R(r) | G(r) and ρ₀ | the ρ₀ error, additively/multiplicatively in r |
| Coordination number | ∫R(r)dr over a shell | amplitude scale **and** ρ₀ |
| ΔG, significance mask | two G(r) and their σ | both σ, and the assumption of independence and a common grid |
| χ²/ndof from a CLI refinement | the `.gr` σ column | **arbitrary** if σ was fabricated at 5 % of \|G\|max (rule 2) |
| WAIC / LOO | posterior-predictive stacks | the likelihood's σ, so the same caveat propagates into model selection |
