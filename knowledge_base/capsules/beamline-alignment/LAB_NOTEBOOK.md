# Alignment lab notebook — historical ground-truth

> Record every alignment case this framework runs on. Over time this becomes
> the single most valuable artifact in the capsule — concrete evidence of what
> worked, what didn't, and why.

## 2026-10-20 — LaB6-on-diamond difficult rotation-axis case (1-ID)

**Context.** Beamline scientist flagged a difficult sample alignment case:
LaB6 particle mounted on the tip of a diamond peak, poor diamond contrast,
beam vertical drift during the scan. 3600 frames × 90 rows × 510 cols,
360° rotation (-180° to +180°), DX-file H5.

**Data path.** `scratch/tomo_alignment/xrds2_lab6_tomo_000239.h5`
(local for the diagnostic run; archive location at
`/gdata/dm/1ID/2026/<project>/...` on `copland`).

**What the framework did.**
1. `inspect_h5` → confirmed `(3600, 90, 510)` uint16 projections + dark/flat.
2. `fit_rotation_axis` with algotom Vo's method on the 0-180° half-sinogram
   → `cor_vo = 254.50 px` (ideal 255.00) — **0.5 px off center**.
3. Vertical centroid + sinusoid+drift fit → drift = **+15.86 rows per 360°**,
   RMS = 1.48 rows. That matches the scientist's description ("beam was
   vertically drifted") within pixel noise.

**Outcome.** No ML needed for this case. Classical methods handle the
"difficult" dataset cleanly. Horizontal RMS was 27 px — large, but caused
by diamond-support contamination of the centroid, which is a feature-isolation
problem, not an alignment-math problem. ROI masking (rows 5-25 only) is the
cheap fix.

**Lesson for the framework.** The handbook's "difficult case" was difficult for
the OPERATOR, not for Vo's method. Don't reach for ML until classical fails;
instrument the Vo output + residual so the operator sees what the method
actually recovered. This case became `rotation_axis_from_tomo.yaml`.

**Added to task library.** `rotation_axis_from_tomo` ← this run.

---

<!-- Add new entries below this line, newest first. One entry per case. -->
