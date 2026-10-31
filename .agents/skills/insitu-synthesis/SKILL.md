---
name: insitu-synthesis
description: Batch analysis of in-situ thermal-synthesis XRD series — phase fractions vs temperature, refinement transparency, Avrami/JMAK kinetics and an advisory next-temperature step. Use when the user has a heating/cooling series (processed_<sample>.h5) from APS EDXRD, BNL or a lab diffractometer and asks which phases form, how fractions evolve with T, the reaction kinetics, or what temperature step to take next.
compatibility: Routes to its own Python 3.12 env via APEXA_SYNTH_PYTHON (gemmi, lmfit, numpy>=2, h5py, pandas, tables, deap). synth_fit_series also needs APEXA_SYNTH_CODE pointing at the collaborator's XRD_gemmi.py (unreleased, not shipped). Missing env → structured error. Setup: docs/SYNTHESIS_SETUP.md.
metadata:
  author: pawan-tripathi
  version: "1.0"
  tools: synth_inspect_series, synth_pca_map, synth_fit_series, synth_recommend_step, synth_fit_kinetics, synth_report
  backend: gemmi + lmfit (I. A. Shkrob's XRD_gemmi engine)
---

## In-situ synthesis XRD — a whole temperature series, transparently

Methods are I. A. Shkrob's (ANL CSE), built for in-situ solid-state synthesis of Na-ion
cathodes (Na–Fe–P–O: NFPP, Na2FeP2O7, NaFePO4). Two principles from his practice:
**work on the batch** (one call per series, never a loop over patterns), and **never
trust a fit you cannot see into** — read the transparency block and the report, not just
the fractions.

### THE ORDER
1. **`synth_inspect_series`** — scan count, T/d ranges, and the model-free quick look
   (Σ counts + peak count vs T). Counts falling with few peaks = amorphous/transforming;
   peak count rising = crystallisation. Choose the fit window (`begin`) here: fit only
   where the phases exist.
2. **`synth_pca_map`** (optional) — where the pattern changes; kinks in PC1/PC2.
3. **`synth_fit_series`** — global multiphase fit over the window. Read `transparency`:
   per-scan Rp, worst scan, `params_at_bounds`, `constraint_frozen_phases`,
   `phase_zero_scale_scans`.
4. **`synth_fit_kinetics`** on a consumed phase's `weightN` column (fix `avrami_n` if the
   mechanism is known — a single ramp cannot separate Ea from n).
5. **`synth_recommend_step`** — advisory next ΔT. **Nothing is actuated**; say so.
6. **`synth_report`** on the fit directory (pass the saved recommend / kinetics JSONs to
   include them). Present its verdict and findings, not your own summary of fractions.

### The collaborator's reference recipe (Sample16 heating)
`phases=["NFPP","Na2FeP2O7","NaFePO4"]`, `abc_scale_permil=[5,10,-10]`, `begin=28`,
**`d_min=3.6, d_max=6.0`**, `constraints="cooling"`, `d_dependent_width=True`,
`sigma=0.01`. Recommender: `d_min=2.3`, box 5, change 2 %, steps 2–20 °C, over the
**whole series** — do NOT pass the fit's `begin`; his control simulation ran on every scan
(a window starting at 28 changes the PCs and the answer: 3.5 °C instead of 7.1 °C).

### Traps (each was hit on real data)
| trap | consequence | do instead |
|---|---|---|
| Wrong d-window | Converges to a wrong basin with median Rp ≈ 0.8 in **every** scan (so no scan looks like an outlier). His saved results used **3.6–6.0 Å**, not the `dmin=0,dmax=11` line live in his script | Match the window of the analysis you compare against; the report flags median Rp > 0.6 |
| Lattice moves faster than thermal expansion | NFPP `a` spans 2–3 % over 235 K (~1e-4 /K) in his fit AND ours: the cell is weakly determined, or the phase is changing composition — the fractions inherit the ambiguity | Constrain that phase (constraints JSON) or narrow `lattice_window`; if χ² barely changes, the data don't determine the cell |
| Unconstrained fit lands elsewhere | Same input/code reached χ² 1280 vs his 871 with `a1_0` railed — the global problem has multiple basins and the path depends on library versions | Prefer the constrained recipe; treat a railed `p0` as "not converged" |
| Edge scan outlier | The first scan of a window can be a poor fit (Sample16 scan 28) | Kinetics normalises by the median of the first 3 points, not the edge scan |
| `abc_scale` units | Parts per **thousand**, not percent | `[5,10,-10]` = +0.5 %, +1 %, −1 % |
| EDXRD axis | Data are on a d-spacing axis converted from energy; the λ in the file (1.54059) is a pseudo-CuKα used only for Lorentz-polarisation | Do not "correct" λ |
| Unmodelled reflection | Sample16 low-T scans show a peak near 4.62 Å that none of the three phases produce | Check the residual plate before trusting fractions; consider a fourth phase |

### Honesty contract
- Report fractions as **modelled crystalline** fractions; amorphous content is invisible.
- Kinetics are **approximate** (his Igor form is unknown). On Sample16, Na2FeP2O7
  (n = 2) gives Ea ≈ 33 ± 6 kJ/mol vs his 30.5; NFPP does not reproduce his 25.5.
- The recommender reproduces his `SVD_control_table` exactly; it is still advisory.
