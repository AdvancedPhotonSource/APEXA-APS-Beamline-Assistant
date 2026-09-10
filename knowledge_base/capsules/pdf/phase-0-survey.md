# §0 — Survey: is this total scattering, and do you have what the reduction needs?

> Part of the **PDF doc set**. Spine: [`README.md`](README.md). Read this first, before
> promising anything.

The reduction needs five things. Four of them cannot be recovered later, and three of them
are silent when wrong. Establish all five **before** touching `midas_pdf`.

---

## §0a. The five inputs

| # | Input | Where it comes from | Silent if wrong? |
|---|---|---|---|
| 1 | **I(Q) to high Q**, with its Q axis | `midas-integrate-v2` (or a `.xy`/`.xye`) | no — Q_max is visible |
| 2 | **σ on I(Q)** | `integrate_*_with_variance` | **yes** — you get σ_G = 0, not an error |
| 3 | **Composition**, as number fractions | synthesis, stoichiometry, EDS — *not* the diffraction | **yes** — rescales S(Q) smoothly |
| 4 | **Wavelength** | `midas-calibrate-v2` refined geometry | **yes** — defaults to 0.1 Å if falsy (rule 4) |
| 5 | **ρ₀** (atoms Å⁻³) | density and composition | **yes** — sets the low-r anchor for phase 2 |

Write all five down, each with the file you read it from. Rule 4 exists because filenames lie.

---

## §0b. Is it total scattering at all?

**Yes if:** one integrated 1-D pattern per state, reaching high Q (roughly ≥15 Å⁻¹ for
distinct near-neighbour shells), on a powder-like or amorphous sample, and the question is
about **local** structure — bond distances, coordination, short-range order, or how those
change between states.

**No — different doc set if:**

| Instead | Doc set |
|---|---|
| the rings break into discrete spots (coarse-grained) | `pf-hedm` / `ff-hedm` |
| you want the diffraction pattern *per voxel* across a sample | `xrd-ct` |
| it is single-crystal diffuse scattering and you want 3D-ΔPDF | **out of scope** — a different measurement and reconstruction, explicitly excluded by `deltapdf.py` |
| it is a neutron TOF measurement | out of scope — the Compton/recoil treatment here is X-ray-specific (rule 5) |
| you only need Bragg-peak positions/intensities | Rietveld (`run_gsas_refinement`), not a PDF |

The dividing line for #1 is operational: **continuous rings at the working bin size, or not.**
Check it before any recipe here applies.

---

## §0c. Check the σ path end to end

This is the check that catches the pipeline's commonest silent failure (Notebook §1a).

```bash
head -3 pattern.xye        # how many numeric columns are actually there?
```

- **2 columns** → there is no σ. Re-integrate with variance, or accept that everything
  downstream is σ-free and say so. Do **not** let a CLI fabricate one (rule 2).
- **3 columns** → good, *but* APEXA's `compute_pair_distribution` will still discard it.
  If σ matters, call `i_of_q_to_Gr(..., sigma_intensity=σ)` from Python directly.

After any reduction, look at `sigma_G`. An all-zeros array means the chain was σ-free
regardless of what the input had.

---

## §0d. Already-processed check

Before reducing, look for work someone already did:

```bash
ls *.gr *.sq *.fq 2>/dev/null           # existing PDFs / structure factors
ls *_pdf*.json *_gr*.json 2>/dev/null   # APEXA capability-runner outputs
```

If a `.gr` exists, find out **which convention** it is (G? g? T? R?) and **whether its
normalization was refined** before comparing anything to it (rule 7). A `.gr` with only two
columns will make every downstream χ² arbitrary (rule 2).

---

## §0e. Fluorescence, before you blame the background

One call answers a yes/no question that otherwise becomes a long background hunt:

```python
from midas_pdf import expected_fluorescence
print(expected_fluorescence(composition, energy_keV))   # which elements fluoresce here
```

If an element fluoresces at your energy, a smooth additive baseline is *expected*, and phase 2
should fit a background (`bg_order ≥ 1`) rather than you chasing it as structure.

---

## §0f. Survey output

Record, in the campaign directory:

```
Pattern:       <ABSOLUTE PATH>   columns: <2|3>   Q range: <min>–<max> Å⁻¹, <N> points
Sigma:         present | absent   (and: does your call path keep it?)
Composition:   <El:frac, …>  source: <file/knowledge>   ionic species? <yes/no>
Wavelength:    <Å>          source: <refined geometry file>
rho_0:         <atoms Å⁻³>  source: <density + composition>
Shortest bond: <Å>          (the floor every "first peak" is checked against)
Beamline:      <station>    (record it — the capsule currently asserts no scope)
Goal:          G(r) | Δ-PDF between states | structural model | coordination number
```

The **shortest bond** line is what makes rule 10 checkable. Compute it once here and carry it.

→ Next: **[phase-1-normalize.md](phase-1-normalize.md)**
