# §3 — Transform: S(Q) → G(r) with a propagated σ

> Part of the **PDF doc set**. Spine: [`README.md`](README.md).

The sine transform and its variance propagation are **reused verbatim** from
`midas_integrate_v2.pdf.fourier_sine_transform` (re-exported as `midas_pdf.gr`). `midas-pdf`
adds nothing here — which means the only ways to get this wrong are the ones you choose:
Q_max, the window, and whether σ made it this far.

---

## §3a. The transform

```
F(Q) = Q [S(Q) − 1]
G(r) = (2/π) ∫ Q [S(Q) − 1] sin(Qr) W(Q) dQ
```

One call for the whole chain (normalize + transform):

```python
from midas_pdf import i_of_q_to_Gr
G, sigma_G, S = i_of_q_to_Gr(
    q, I, comp, r_grid,
    wavelength_A=0.1665,
    scale=res.scale,             # FROM PHASE 2 — not the 1.0 default
    background=res.background,   # FROM PHASE 2
    sigma_intensity=sigma_I,     # or you get no σ (rule 2)
    compton=True,
    q_max=22.0,                  # deliberate
    window="lorch",
    return_S=True,               # keep S and look at it
)
```

`return_S=True` costs nothing and gives you the phase-1 check for free. Keep it.

---

## §3b. Q_max — a real choice, not a default

`q_max=None` (and APEXA's `q_max=0.0`) means **no truncation**: the entire measured range,
including the noisy tail, is transformed. That is rarely what you want.

| Q_max | Buys | Costs |
|---|---|---|
| lower | less noise folded into G(r) | real-space resolution — shells merge *(generic)* |
| higher | resolution: features closer in r are separable | termination ripple, and detector noise amplified by the `Q` weighting in F(Q) |

Real-space resolution is bounded by roughly 2π/Q_max *(generic)*, and **no window and no
r-grid oversampling recovers detail below it** (ENVELOPE §1). Choosing a fine r-grid does not
add information; it only interpolates.

**Practical rule:** truncate where S(Q) − 1 stops carrying visible oscillation above the
noise. Then report the value you used.

---

## §3c. The window

`window="lorch"` is the default. The Lorch window damps termination ripple at a cost in
r-resolution — a deliberate trade, and the right default for a first look. Turning it off
sharpens features **and** sharpens ripple; the two look alike at low r.

Report which window you used. A G(r) compared against another group's without pinning the
window is a comparison of two different convolutions.

---

## §3d. Termination ripple, and the first-peak check

Ripple is periodic in r with period ~2π/Q_max, decays slowly, and is **largest where the true
G(r) is smallest** — which is exactly the low-r region.

**The discriminating test:** re-run with a different Q_max. A ripple **moves and changes
amplitude**; a real peak does not.

**Then apply rule 10.** Compare the first peak against the shortest chemically possible bond
for your composition (computed in §0f). APEXA's `first_peak_r_A` field is `argmax(G(r))` over
`r > 0.5 Å` with **no physical floor** — measured returning **0.72 Å** on synthetic Ni data
where the real bond is 2.49 Å (Notebook §1c). Treat that field as a hint, never as a result.

---

## §3e. σ, one last time

After the transform, look at `sigma_G`:

- **Non-zero, smooth, larger where counts were low** → the chain propagated σ. The analytic
  band is validated against a Monte-Carlo bootstrap to **<1 %**
  (`dev/demo_sigma_validation.py`).
- **All zeros** → σ never entered. On APEXA's `compute_pair_distribution` this happens even
  with a 3-column input, because the loader discards trailing error columns (Notebook §1a).
  Report *no uncertainty*; do not report zero uncertainty.

---

## §3f. Which function to report

`conventions.py` (Keen, *J. Appl. Cryst.* **34**, 172 (2001)) gives the whole family from
G(r) and ρ₀:

| Function | Definition | σ | Use |
|---|---|---|---|
| `F(Q)` | `Q[S(Q) − 1]` | `σ_F = Q σ_S` | reciprocal-space comparison |
| `G(r)` | `(2/π)∫F sin(Qr)dQ` — Keen's D(r), Egami-Billinge's G(r) | propagated | the default reported PDF |
| `g(r)` | `1 + G/(4πrρ₀)` → 1 at large r, 0 below the first bond | scales with 1/(4πrρ₀) | intuitive; shows the low-r zero clearly |
| `T(r)` | `G + 4πrρ₀` | `σ_T = σ_G` (pure additive shift) | |
| `R(r)` | `rG + 4πr²ρ₀` | | **∫R dr over a peak = coordination number** |

The upstream package states the FZ-vs-Keen convention choice is **still to be settled with
the experimental collaborators**. Until it is, name the convention every time (rule 7).

→ Next: **[phase-4-model.md](phase-4-model.md)** if you are modelling, otherwise
**[phase-5-report.md](phase-5-report.md)**.
