# `compute_pair_distribution` — measured wrapper behaviour

APEXA-side findings about **APEXA's own PDF tool wrapper**, not about MIDAS. They
were measured on 2026-09-10 and originally lived in a hand-written `pdf` capsule
that has since been superseded by the authoritative upstream MIDAS `pdf` manual
(`knowledge_base/capsules/pdf/`, synced from `MIDAS/manuals/pdf/`).

They are kept here because they describe `_capability_runner.py` and
`midas_comprehensive_server.compute_pair_distribution` — APEXA code. Upstream has
no reason to carry them, and the sync would otherwise have deleted them.

**Status: open.** None of the three has been fixed. Each is a silent degradation
of a guarantee the `midas-pdf` package itself provides.

---

## §1. APEXA's `compute_pair_distribution` path — synthetic probe

**Date:** 2026-09-10. **Who:** APEXA dev session (capsule authoring).
**Purpose:** establish whether the package's two headline guarantees — end-to-end σ
propagation and a physically normalized S(Q) — survive APEXA's wrapper.

**Method.** A synthetic 3-column pattern (`Q`, `I`, `σ`) on Q ∈ [0.5, 25] Å⁻¹, 2000 points:
a decaying `1000·exp(−Q/8)` background plus two Gaussian peaks at Q = 3.07 and 5.1 Å⁻¹,
`σ = √I`. Passed to `_capability_runner.py pdf --pattern pat.xye --composition Ni:1
--wavelength 0.1665 --out g.xy` — the exact path `compute_pair_distribution` takes.

**This is synthetic data on an arbitrary intensity scale.** It cannot say anything about
Ni's real PDF. It *can* say what the wrapper does to σ, to the normalization and to the
reported first peak, because those are properties of the code path, not of the sample.

### §1a. The σ column is discarded — `sigma_G` returns as zeros  ✅ ESTABLISHED

| Result | Value |
|---|---|
| input | 3 columns, real σ present |
| `sigma_G_stats` | `{mean: 0.0, std: 0.0, min: 0.0, max: 0.0, median: 0.0}` |

**Cause (read from source, consistent with the measurement):**
`_capability_runner._load_1d` documents that it "Ignores comment/header lines **and trailing
error columns**", so `i_of_q_to_Gr` is called with `sigma_intensity=None`.

**Why it matters more than a `null` would.** The field is present and populated with zeros,
not absent. A reader — human or model — sees a `sigma_G_stats` dict and concludes the
uncertainty was propagated and is negligible. The package's validated <1 % analytic σ band
never ran.

**Refutable by:** passing a σ column and getting a non-zero `sigma_G_stats` from this path.

### §1b. Normalization is never refined — ⟨S⟩ ≈ 2.75, not 1  ✅ ESTABLISHED

| Result | Value |
|---|---|
| `S_q_stats` | mean **2.751**, median 2.775, std 0.857, min **1.233**, max 3.983 |
| expected after refinement | ⟨S⟩ → 1 at high Q |

**Cause:** the wrapper calls `i_of_q_to_Gr` directly, whose `scale` default is `1.0`, and
never calls `refine_normalization`. The Faber-Ziman asymptote is a *constraint the refinement
enforces*, not something the transform imposes.

**Why it matters.** G(r) still came out smooth and PDF-shaped. Peak **positions** are
insensitive to a multiplicative scale error; **amplitudes**, coordination numbers, and any
model fitted to them are not. This is the single largest error source in the pipeline and it
produces no warning.

**Caveat on the number.** 2.75 is the value for *this synthetic pattern's* arbitrary
intensity scale — the specific factor is meaningless. What is established is that **no
mechanism in this path drives ⟨S⟩ toward 1**, so whatever the scale error is, it survives.

**Refutable by:** a path that reports a fitted `scale` and an ⟨S⟩ near 1.

### §1c. `first_peak_r_A` has no physical floor — returned 0.72 Å  ✅ ESTABLISHED

| Result | Value |
|---|---|
| `first_peak_r_A` | **0.7214 Å** |
| shortest real Ni–Ni bond | ≈ 2.49 Å |

**Cause:** the field is `argmax(G(r))` over the mask `r > 0.5 Å`
(`_capability_runner.cmd_pdf`). With an unrefined normalization and no Q truncation, the
largest excursion in that window is a low-r termination ripple.

**Why it matters.** The field is documented as the "nearest-neighbour distance". A model
reading the tool output has no signal that 0.72 Å is impossible.

**Refutable by:** the same field returning a chemically plausible distance on an unrefined
pattern — which would mean the argmax happened to land right, not that the check is safe.

### §1d. The version string is not a capability gate  ✅ ESTABLISHED

Three different version numbers are simultaneously visible:

| Source | Value |
|---|---|
| `packages/midas_pdf/pyproject.toml` | **0.2.0** |
| installed distribution metadata + `midas_pdf.__version__` | **0.1.1** |
| APEXA tool docstring | **"midas-pdf 0.1.0"** |

Yet the 0.1.1-labelled install imports `structure`, `cif`, `rmc`, `saxs`,
`model_comparison`, `ionic_form_factors`, `placzek`, `multi_phase`, `aniso_refine` and
`bayesian_refine`, and exposes **all seven** CLI entry points — i.e. the 0.2.0 content.

**Consequence:** gate on the import probe, never on `__version__` (spine §0, RUNBOOK).

---

## §2. Questions deliberately left open
