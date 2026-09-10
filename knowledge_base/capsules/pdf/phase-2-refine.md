# §2 — Refine the normalization: the step that makes amplitudes mean something

> Part of the **PDF doc set**. Spine: [`README.md`](README.md). Hard rule 1 lives here.

Traditional total-scattering software (PDFgetX3, Gudrun) leaves the overall `scale` and an
additive background to **hand twiddling**. Because this chain is differentiable end to end,
they can be **fitted** — against constraints that involve no structural model at all.

Skipping this is the single largest error source in the pipeline, and it is silent: the
transform still returns a smooth, plausible G(r) (Notebook §1b).

---

## §2a. The two model-free constraints

1. **High-Q asymptote.** ⟨S(Q)⟩ → 1 over the top of the Q range. This is the definition of
   the Faber-Ziman structure factor, not an assumption about the sample.
2. **Low-r behaviour.** Below the nearest-neighbour distance there are no pairs, so g(r) = 0
   and the reduced PDF is **exactly** the straight line `G(r) = −4π ρ₀ r`. Any deviation
   there is normalization error or termination ripple, never structure.

Both are differentiable in `(scale, background coefficients[, ρ₀])`, so a few L-BFGS steps
replace the twiddling.

---

## §2b. The call

```python
from midas_pdf.refine import refine_normalization

res = refine_normalization(
    q, I, comp, r_grid,
    wavelength_A=0.1665,
    number_density=rho0,        # atoms Å⁻³ — from §0
    sigma_intensity=sigma_I,    # keep it: the fit can then be reported with parameter σ
    q_max=22.0,                 # deliberate (rule 8)
    window="lorch",
    r_min_phys=2.2,             # JUST BELOW the nearest-neighbour distance — not the 1.0 default
    q_asymptote_frac=0.25,
    fit_background=True, bg_order=0,
    fit_number_density=False,
    steps=60, lr=0.2,
)
```

**`r_min_phys` is the parameter people leave at its default and should not.** It is the upper
bound of the unphysical low-r window where the straight line must hold. The default `1.0` Å
is below almost every real bond, so it under-uses the constraint; set it just under the
shortest bond you computed in §0f.

`bg_order=0` is a single additive constant. Raise it to absorb a fluorescence baseline or an
air/Compton-tail residual — but a high-order polynomial can eat real low-Q structure, so
raise it only with a reason from §0e or DIAGNOSIS.

`fit_offset` is a **deprecated** alias for `fit_background`; do not use it in new code.

---

## §2c. When to refine ρ₀

`fit_number_density=False` by default. Refine it when you do not know the density well —
**and then check the refined value against the physical one**. A refined ρ₀ far from the
known density is not a measurement of an unexpected density; it is usually a symptom that the
composition is wrong, because composition and density enter the same constraint (DIAGNOSIS,
"the low-r region is not the straight line").

If you fix ρ₀, say which value and where it came from. Every coordination number downstream
carries it (ENVELOPE §4).

---

## §2d. Did it work?

| Check | Healthy | If not |
|---|---|---|
| ⟨S(Q)⟩ over the top `q_asymptote_frac` | ≈ 1 | scale did not converge — check ρ₀, `r_min_phys`, and whether Q_max includes a noisy tail |
| G(r) below the shortest bond | follows −4πρ₀r | see DIAGNOSIS: offset ⇒ background; wrong slope ⇒ ρ₀ or composition; oscillatory ⇒ ripple |
| fitted `scale` | a stable number you can report | wildly different between `bg_order` settings ⇒ background and scale are trading off; fix `bg_order` at the lowest defensible value |
| convergence | ≲60 L-BFGS steps | raise `steps` once; if it still will not converge, the problem is upstream, not the optimizer |

**Report the fitted parameters.** `scale`, the background coefficients and ρ₀ are results, not
internal details — they are what makes every downstream amplitude auditable.

---

## §2e. If you cannot refine

Sometimes you have a two-column `.gr` from elsewhere and no I(Q) to go back to. Then:

- Say so explicitly, and label **every amplitude, coordination number and model parameter
  provisional** (spine §2 "Done means").
- Peak **positions** are still usable — they are insensitive to a multiplicative scale error.
  Quote positions, not amplitudes.
- Do **not** substitute a hand-chosen scale and present the result as normalized. That is the
  exact practice this step replaces.

→ Next: **[phase-3-transform.md](phase-3-transform.md)**
