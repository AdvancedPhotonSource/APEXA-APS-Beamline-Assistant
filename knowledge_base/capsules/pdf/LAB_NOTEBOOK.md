# PDF lab notebook — the evidence ledger

> Part of the **PDF doc set**. Spine: [`README.md`](README.md).
>
> Read this before re-opening any question. Every claim carries how it was measured and what
> would refute it. **A claim not in this file is not established** — the spine and DIAGNOSIS
> may still cite the package source, but that is a *reading of code*, not a measurement.

---

## §0. State of this ledger

**Thin, and honest about it.** There has been **no APS total-scattering campaign** behind
this doc set. The ledger contains exactly one entry group: a synthetic probe of APEXA's own
tool path, run to establish whether the pipeline's headline guarantees survive the wrapper.
They do not, in three specific ways — which is why the probe was worth running and why the
spine's hard rules are shaped the way they are.

Everything else in this doc set is sourced from the `midas-pdf` implementation and labelled
`[source]` in DIAGNOSIS, or is standard total-scattering physics labelled `[generic]`.
**Nothing here has been checked against a real dataset reduced by a PDF specialist.**

What this ledger needs next, in order:

1. One real pattern, with σ, known composition, known wavelength, known ρ₀ — reduced end to
   end **with phase 2 on** — and the resulting ⟨S⟩, low-r line and first peak recorded here.
2. The same pattern reduced independently in PDFgetX3 or GudrunX, with both conventions
   pinned, as an external cross-check (rule 7).
3. The beamline recorded, so the spine's scope can stop saying "none asserted".

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

Recorded so they are not silently answered by assumption.

| Question | Status | Who decides |
|---|---|---|
| Faber-Ziman vs Keen; which of S/F/G/g/T/R to report as "the PDF" | **open upstream** — `packages/midas_pdf/dev/PLAN.md` states it is to be settled with the experimental collaborators | the collaborators, not this doc set |
| Which APS station(s) this capsule is scoped to | **unset** — no campaign, so the spine asserts no beamline and the scope gate will never fire | first real run; record it |
| Whether the `_load_1d` σ-dropping is a bug or an intentional simplification | **unresolved** — the docstring states it as designed behaviour, but it defeats the package's headline feature | APEXA maintainer |
| Whether `first_peak_r_A` should be floored at a composition-derived minimum bond | **unresolved** — currently `r > 0.5 Å` | APEXA maintainer |
| Multiple-scattering tier appropriate for typical APS capillary geometry | **untested here** — Tier 1/2/3 all implemented, none exercised on real data | first real run |

---

## §3. Refuted / retracted

*(empty — nothing has been claimed here long enough to be refuted. When something is, it
belongs in this section with the measurement that killed it, not deleted.)*
