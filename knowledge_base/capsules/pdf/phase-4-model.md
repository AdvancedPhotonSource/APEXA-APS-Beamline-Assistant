# §4 — Model: Δ-PDF, small-box, multi-phase, core-shell, joint SAXS, RMC

> Part of the **PDF doc set**. Spine: [`README.md`](README.md).
>
> **Gate:** this phase is only meaningful if phase 2 converged. A model fitted to an
> unnormalized G(r) absorbs the scale error into its `scale` and inflated `u_iso` and reports
> a comfortable χ² (DIAGNOSIS, "converges with a comfortable χ² but a wrong lattice").

---

## §4a. Δ-PDF — the difference PDF, and why σ is the whole point

For two states (time points, loads, temperatures):

```
ΔG(r) = G_b(r) − G_a(r)
σ²(ΔG) = σ²(G_a) + σ²(G_b)          [independent states]
```

That second line is why this belongs in an error-propagating pipeline: each ΔG(r) feature can
be **tested against noise**. Without propagated σ the difference map is decorative.

```python
from midas_pdf.deltapdf import (delta_pdf, significant_mask,
                                sequence_delta_pdf, significant_features,
                                cluster_significant_regions)
dG, sig = delta_pdf(G_a, G_b, sigma_a, sigma_b)
mask = significant_mask(dG, sig, n_sigma=3.0)
```

**Preconditions that make the test valid** (halt row in the spine): both states reduced with
the *same* composition, scale, background, Q_max, window and r-grid. Anything else and ΔG
contains the normalization difference.

**Four behaviours to know:**

| Behaviour | Consequence |
|---|---|
| a missing σ is treated as **0**; σ_Δ is all-zeros if neither is given | the test silently degrades |
| points with σ = 0 are reported **not significant** (rather than dividing by zero) | a "significant" flag proves σ was non-zero *there* — check it is non-zero everywhere you read |
| `sequence_delta_pdf(baseline="mean")` puts frame *t* inside its own baseline | σ² does **not** simply add for that mode |
| `cluster_significant_regions(min_width_points=2)` drops single-point islands | genuine PDF features span ≥2 r bins |

**This is not 3D-ΔPDF.** That is single-crystal diffuse scattering with a different
reconstruction, explicitly out of scope (`deltapdf.py` docstring, spine scope gate).

---

## §4b. Small-box refinement (PDFfit-style)

`structure.py` computes a model reduced PDF as a sum of Gaussian-broadened interatomic-distance
contributions — the same real-space model PDFgui uses — but as a **torch graph differentiable
in** the lattice, atomic coordinates, displacement parameters, `scale`, correlated-motion
δ1/δ2 and instrument `Qdamp`/`Qbroad`.

```bash
midas-pdf-refine --cif Ni.cif --gr Ni.gr --r-min 1.5 --r-max 20 \
                 --u-iso 0.005 --scale 1.0 --steps 200 --bg-order 0 --json out.json
```

Outputs refined `(a, u_iso, scale)`, **Hessian-based** uncertainties and χ²/ndof.
`--posterior-samples` adds posterior sampling (`bayesian_refine.py`).

> **The σ trap at the CLI (rule 2).** A two-column `.gr` makes `cli/_common.fallback_sigma`
> fabricate σ = **5 % of |G|max** and print a banner to **stderr** saying the χ² is arbitrary.
> Capture stderr. A χ²/ndof computed on a fabricated σ is not a goodness of fit.

**Read `u_iso`.** Implausibly large displacement parameters are the tell that the model is
absorbing a normalization error rather than describing thermal motion.

---

## §4c. Multi-phase and core-shell

```bash
midas-pdf-multiphase --cif Ni.cif Cu.cif --gr mix.gr --weights 0.5 0.5 \
                     --diameter 0 0 --u-iso 0.005 --r-min 1.5 --r-max 20 --steps 200
midas-pdf-coreshell  --core-cif Ni.cif --shell-cif NiO.cif --gr particle.gr \
                     --r-core 25 --shell-thickness 5 --pin-geometry --steps 200
```

Multi-phase fits `G_total(r) = Σ w_i · G_i(r) · γ_i(r)`; weights **auto-renormalise**, and
`--diameter` applies per-phase finite-size damping γ_i. Core-shell fits two nested-sphere
phases weighted by volume fraction.

Weights are correlated with per-phase scale, `u_iso` and damping (ENVELOPE §3), so:
report weights **with uncertainties**, and compare against the single-phase model (§4e).

---

## §4d. Joint SAXS + PDF (+ SANS)

```bash
midas-pdf-joint --cif Ni.cif --gr Ni.gr --saxs Ni_saxs.dat [--sans Ni_sans.dat] \
                --shape sphere --polydispersity 0.1 --n-poly-nodes 21 \
                --init-a 3.52 --init-u-iso 0.005 --init-diameter 50 --steps 300
```

Fits real-space local structure and small-angle morphology **simultaneously**, so particle
size is constrained by both the SAXS form factor and the PDF's finite-size damping instead of
being fitted twice and reconciled by hand. Emits per-channel χ², which is the thing to read:
one channel dominating means the weighting, not the physics, chose the answer.

---

## §4e. RMC — and the discipline it requires

```bash
midas-pdf-rmc --cif seed.cif --gr target.gr --size 6 --moves 200000 \
              --move-types displace+cluster --sigma-A 0.05 --min-distance 2.0 \
              --temperature 0.01 --seed 0 --output final.cif --first-shell-window 2.0 3.2
```

Move mixtures: `displace` (default) · `displace+cluster` (50/50 single + block) · `all`
(+ rigid rotation) · `gc` (grand-canonical, adds insert/remove with `--chemical-potential`).

**RMC will fit noise** (rule 12, ENVELOPE §3). It is underdetermined: many configurations
reproduce one G(r). Report, always:

- initial vs final χ², and the acceptance ratio
- first-shell coordination number (`--first-shell-window`) and its spread
- the parameter count relative to the number of independent data points
- a comparison against a simpler model (§4f)

Present RMC as **a generator of candidate local motifs**, not as *the* structure.

---

## §4f. Model comparison — required before presenting any model

`model_comparison.py` implements WAIC and a LOO estimator over the posterior-predictive
stacks (`G_samples`, `I_saxs_samples`, `I_sans_samples`):

```
lppd   = Σ_i log( (1/S) Σ_s p(y_i | θ_s) )
p_waic = Σ_i Var_s( log p(y_i | θ_s) )
WAIC   = −2 (lppd − p_waic)                      lower is better
```

`compare_models` anchors on the best model and reports every other relative to it.

**Caveat that propagates:** these are likelihood-based, so they inherit the σ. If σ was
fabricated (§4b), the model comparison is as arbitrary as the χ² it is built on.

→ Next: **[phase-5-report.md](phase-5-report.md)**
