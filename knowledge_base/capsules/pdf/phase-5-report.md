# §5 — Report: what to state, what to label provisional

> Part of the **PDF doc set**. Spine: [`README.md`](README.md).

A PDF result is only interpretable alongside the choices that produced it. Four of those
choices (convention, Q_max, window, whether normalization was refined) change the curve
without changing how plausible it looks — so they are not appendix material.

---

## §5a. The minimum report

Every G(r) that leaves the room carries:

```
Function reported:   G(r) [Keen's D(r) / Egami-Billinge G(r)]   ← name it (rule 7)
Composition:         Ce:1, O:2        source: <file>            ionic f(Q): yes/no
Wavelength:          0.1665 Å         source: <refined geometry file>
rho_0:               <atoms Å⁻³>      fixed | refined (value ± σ)
Normalization:       REFINED (scale=…, bg_order=…, coeffs=…) | NOT REFINED
<S(Q)> over top 25%: <value>          ← the audit number for the line above
Q_max used:          22.0 Å⁻¹  (data reached <…>)
Window:              lorch
r-grid:              0–20 Å, 500 points
sigma_G:             propagated from sigma_I | ABSENT (not zero)
First peak:          <r> Å            shortest possible bond: <r_min> Å
Package:             midas-pdf, import-probe verified (NOT __version__ — see RUNBOOK)
```

`⟨S⟩` is the single number that lets a reader check the normalization claim without re-running
anything. Include it.

---

## §5b. Say which claims are provisional, and why

This capsule has **no real-data campaign behind it** (spine, "Honesty about depth"). Until one
exists, default to labelling:

| Claim | Status |
|---|---|
| Peak **positions** | the most robust output — insensitive to a multiplicative scale error |
| Peak **amplitudes** | provisional unless phase 2 converged and ⟨S⟩ ≈ 1 |
| **Coordination numbers** | provisional — carry both the amplitude scale and ρ₀ (ENVELOPE §4) |
| **Δ-PDF significance** | valid only with real σ on both states, on a common grid and normalization |
| **Structural models** | report parameter count + a model comparison, or do not present them |
| **RMC configurations** | candidate motifs, never "the structure" |

Do not use hedging language as a substitute for the audit fields in §5a. "Approximately" is
not a normalization statement; `⟨S⟩ = 2.75, not refined` is.

---

## §5c. Provenance — every number names a file and a command

Same discipline as the other doc sets. For each figure and each number:

- the pattern file, with its column count
- the command or the Python call, with the arguments that were not defaults
- the output file it was read from
- for anything from APEXA: the tool name, so the wrapper's behaviours (PARAMETERS §12) are
  attributable

A number without this cannot be re-derived by the next session, and this capsule's whole
premise is that the next session will be the first one with real data.

---

## §5d. Comparing against another tool

Before saying two PDFs disagree, pin **both** of:

1. **Convention** — G / g / T / R / F differ by additive and multiplicative functions of r.
2. **Q_max and window** — different truncations are different convolutions.

Only after both match does a residual difference mean anything. If positions agree and
amplitudes do not, the answer is almost always normalization scale (DIAGNOSIS).

---

## §5e. Feed the ledger

Anything measured goes into [`LAB_NOTEBOOK.md`](LAB_NOTEBOOK.md) with:

- what was measured, and the number
- how it was measured, reproducibly
- **what result would have refuted it**

And update the two open items the ledger is waiting on (§2 there): the **beamline** this ran
at, and whether a specialist's review changed anything. The spine currently asserts no
beamline scope precisely because nobody has filled that in.

An entry that cannot fail does not belong in the ledger.
