# Powder refinement

**Scope.** Laboratory and synchrotron powder observations, compatible CW/TOF
joint projects, independent batches and sequential/live tracking through the
separately installed Agentic-GSAS-II engine. A completed fit is not a validated
structure or quantitative composition.

## Order of operations

1. Bind each measured pattern to axis units, uncertainty, instrument file and
   candidate CIFs. Use `propose_structures` when candidate retrieval is needed.
2. Submit a single structured batch through `powder_refine_submit`. Use
   independent, sequential, joint or search mode explicitly.
3. Poll `refinement_status`; inspect failures, budget stops and evidence coverage.
   Use `powder_report` for the shared APEXA report and `powder_job_control` to
   cancel, retry or resume. These tools accept live job IDs as well as batch IDs.
4. Review per-histogram phase fractions and conditional uncertainties, plus
   reference and effective cells. Report the normalization basis.
5. For diagnosis, use `powder_evidence`, optionally `powder_advise`, and
   `powder_trial`. Image proposals are experiments; the numerical engine decides
   whether the isolated result passes its checks.
6. Ready acquisition observations can enter through `powder_live_ingest`.
   An explicit completion marker is preferred to a file-stability heuristic.

## Traps

- Never infer trustworthy composition from a low Rwp.
- Never treat absent diagnostics or fixed-parameter zero esds as passed checks.
- Joint mode needs matching sample and phase identity. Preserve each histogram's
  actual temperature and conditions. Differing conditions require a recorded
  `recipe.shared_structure_justification` for shared coordinates/ADPs; HStrain
  only models lattice offsets. Use independent/sequential fitting otherwise.
- Legacy string-only model transport does not carry images. A configured
  image-capable structured provider is required for `powder_advise`.
- QPA excludes unmodeled/amorphous material; RRUFF remapped intensities are
  unsuitable for ordinary QPA/ADP interpretation.
- GSAS-II uses the existing scientific interpreter, not APEXA's web interpreter.
- Live input rate and external-engine parity require deployment qualification.

## Current evidence contract

See [the current workflow guide](../../../docs/powder-refinement.md) and
[source-hashed benchmark snapshot](../../../docs/data/powder-benchmark-20261015.json).
The engine has no `qualified` output path. `blocked` means a configured check
failed; `provisional` can include unavailable checks. Approximate instrument files
marked `PROVISIONAL` fail calibration. Scheduler `idle` is not solver convergence.

Report all batch records with execution and scientific status separately. Do not
use the first fit as the batch headline, connect unrelated observations into a
trend, or plot values from execution failures as completed fits. Keep joint
fractions associated with their histograms.

Image proposals cite only transmitted panels. Trials retain the baseline active
parameter set, require passed covariance/convergence, global Rwp improvement
>0.001 percentage points, at most 0.05 points deterioration per histogram and
lower combined loss over the cited window union. Individual windows may worsen.
`uiso` releases the existing atomic displacement representation, including
anisotropic terms when present. Accepted shadow candidates are not adopted.

The audited 1,676 records describe the numerical engine: 1,514 completed blocked,
157 provisional and five execution errors. DARA's 20 supplied-phase mixtures
are all blocked (pooled median absolute error 24.97 percentage points); all 17
temperature-series frames are blocked. The joint TOF suite timed out. Small
separate transfer/trial checks do not establish QPA accuracy, a vision benefit
or live throughput. Brian H. Toby originated the concept and is a coauthor;
interpretation follows the published derivative-ranking method and his
Powder Crystallography edition 0.9.3 without treating quotations as a classifier.

## Executable scientific recipe

The default engine recipe is `toby-0.9.3/1`. `gsas2_capabilities` exposes all
30 chapters with automatic controls, expert branches and validation boundaries.
`plan_refinement` returns `submission_metadata` and `submission_recipe`; carry
these into `powder_refine_submit`. Planning alone does not configure a job.

Never infer geometry from `.XRA`, a source name, or a small wavelength. Radiation
and CW/TOF type are checked against the imported instrument. Carry a known radius
in `observation.experiment.goniometer_radius_mm`; temperature goes in
`conditions.temperature_K`. Do not rewrite factual metadata to satisfy a joint gate.

Z and Zero are withheld. Babinet is a solvent-scattering correction, distinct
from extinction. It requires an explicit physical justification. Calibrated
profiles are held; sample broadening needs matched calibration and justification.
Occupancy constraints, anisotropic broadening alternatives, restraint targets/
weights, rigid bodies and symmetry/indexing decisions require explicit expert
workflows. A native tool's availability does not establish automatic selection.

The loop records withheld candidates, actual added/removed independent variables,
per-bank residual evidence and acceptance checks. Failed trials preserve their
GPX and diagnostic evidence. The fallback and cleanup paths use the same checks.
The Le Bail comparison reports all-histogram and per-bank residuals with matching
input scope. Report `comparable` and its checks; do not call an unqualified value
a floor. No model-based or learned initialization is added by this recipe.
