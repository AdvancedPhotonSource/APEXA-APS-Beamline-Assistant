# §1 — Normalize: I(Q) → Faber-Ziman S(Q)

> Part of the **PDF doc set**. Spine: [`README.md`](README.md). Do §0 first.

This is the step `midas-pdf` exists for. Everything else in the chain is reused from
`midas-integrate-v2` and `midas-hkls`.

---

## §1a. The equation

```
S(Q) = [ I_coh(Q) − (⟨f²⟩(Q) − ⟨f⟩²(Q)) ] / ⟨f⟩²(Q)
I_coh(Q) = scale · [ I_meas(Q) − background(Q) ] − I_Compton(Q)
```

with `S(Q) → 1` as `Q → ∞`. The `(⟨f²⟩ − ⟨f⟩²)` term is the **Laue** (disorder) term; it
vanishes for a single element, which is why the monoatomic
`midas_integrate_v2.pdf.normalize_to_S` is not merely a rescaling of this — it is a different
quantity for any polyatomic sample (rule 3).

`background` is on the **measured** scale; the Compton term is in **per-atom** units. Getting
those two the wrong way round produces a smooth, plausible, wrong S(Q).

```python
from midas_pdf import Composition, faber_ziman_S
comp = Composition({"Ce": 1, "O": 2})          # NUMBER fractions
S, sigma_S = faber_ziman_S(I, q, comp, wavelength_A=0.1665,
                           sigma_intensity=sigma_I, compton=True)
```

σ propagation treats form factors and the Compton model as noiseless:
`σ_S(Q) = scale · σ_I(Q) / ⟨f⟩²(Q)`.

---

## §1b. Composition — the input that is silent when wrong

`Composition` takes **number (mole) fractions**. `{"Ce": 1, "O": 2}` is CeO₂. Mass fractions,
or a formula string parsed the wrong way, give the wrong ⟨f⟩² and ⟨f²⟩ — and therefore the
wrong Laue term and the wrong amplitude — with no error.

APEXA's tool parses `"Ce:1,O:2"`; a bare element defaults to fraction `1.0`.

**Ionic species (rule 6).** `midas_hkls` ships neutral-atom Cromer-Mann coefficients and
**silently maps** `"Ni2+"`, `"O2-"`, `"Ce4+"` to the neutral atom. The error is **5–20 % at
low Q** and it does not raise. For oxides and mixed light/heavy ionic samples:

```python
from midas_pdf.ionic_form_factors import available_ions, register_ion, publish_to_midas_hkls
publish_to_midas_hkls()          # push verified ions into the canonical registry
```

Each coefficient set is 4-Gaussian + constant, `f(Q) = c + Σ a_i exp(−b_i s²)`, `s = Q/4π`,
and must satisfy the electron-count sum rule `Σa + c ≈ Z − charge`. If your ion is not in
`available_ions()`, register it and check the sum rule before trusting it.

---

## §1c. Compton, and why there is no Placzek

`compton=True` (default) subtracts the Hubbell tabulated incoherent scattering with the
**Breit-Dirac recoil factor** applied. The wavelength sets the angular factor, so a wrong λ
biases the subtraction — and APEXA's runner silently substitutes **0.1 Å** for a falsy
wavelength argument (rule 4).

**Do not add a Placzek correction (rule 5).** It is a neutron correction. For X-rays the
equivalent is subsumed by Breit-Dirac; the residual is O((hν/Mc²)²) ≈ 10⁻¹² at 63 keV for Ni.
`placzek.py` exists to document exactly this and ships no X-ray Placzek function. Applying one
because you are used to the neutron TOF world introduces a real bias.

---

## §1d. Corrections that belong before normalization

All are differentiable and backed by the NIST mass-attenuation tables already in
`midas_hkls` — no new data (`corrections.py`).

| Correction | When it matters | Call |
|---|---|---|
| **Detector efficiency η(Q)** | high-energy total scattering reaching large 2θ: a flat sensor absorbs a Q-dependent fraction, so there is a real high-Q **tilt** to divide out | `apply_detector_efficiency(I, sigma, q, ...)` |
| **Flat-plate self-absorption** | slab samples in symmetric transmission | `flat_plate_transmission(q, ...)` |
| **Paalman-Pings** | capillary: sample-in-container, and container alone | `paalman_pings_cylinder_in_cylinder`, `paalman_pings_cell_only` |

Skip them and their smooth Q-dependence gets absorbed into `scale` and the fitted background
in phase 2 — which *looks* fine, and puts a slope into G(r).

---

## §1e. Multiple scattering — three tiers, one of them non-differentiable

MS adds a smooth, slowly-Q-varying background carrying no sharp structural signal. Unlike the
other corrections it has **no closed form**: it is a volume integral over intermediate
directions, depends on sample *shape*, and is self-referential.

| Tier | What | Module | Differentiable |
|---|---|---|---|
| 1 | lumped smooth polynomial `b(Q) = Σ c_j (Q/Q_max)^j`, absorbing MS + fluorescence + air | `multiple_scattering.lumped_background` | yes — and it is what phase 2 fits |
| 2 | analytic single + double scattering | `ms.py` | yes |
| 3 | Monte-Carlo reference (slab, cylinder); all-orders discrete-ordinates transport (slab) | `ms.multiple_scattering_mc*`, `ms_transport.ms_background_on_grid` | **MC: NO** (by design); transport: yes |

Default practice: Tier 1, fitted in phase 2. Reach for Tier 2/3 only for thick or strongly
scattering samples — and never put the **MC reference inside a gradient chain**.

---

## §1f. The phase-1 check — look at S(Q) before you transform

Plot it. This is the "do not skip" line in THE ORDER.

- Does it **oscillate about a level** and decay? Good — the shape is right.
- Is that level heading for **1**? If not, you have a scale problem, and phase 2 fixes it.
  (Measured on APEXA's default path: ⟨S⟩ = 2.75. Notebook §1b.)
- Is there a **monotonic ramp**? Compton wavelength, or fluorescence/air background — see
  DIAGNOSIS "S(Q) does not approach 1".
- Is there a **step at one Q**? Panel boundary — go back to `calibrate-integrate`.

Do not transform a S(Q) you have not looked at.

→ Next: **[phase-2-refine.md](phase-2-refine.md)** — and it is not optional.
