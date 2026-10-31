---
name: gsas2-agentic
description: Powder diffraction refinement through Agentic-GSAS-II, including multiphase batches, compatible joint instruments, sequential/live observations, candidate mixtures and optional image-guided trials. Reports execution separately from scientific qualification.
compatibility: Requires a GSAS-II interpreter and the Agentic-GSAS-II repository; configure APEXA_GSASII_PYTHON and APEXA_AGENTIC_GSAS2 as needed.
metadata:
  author: pawan-tripathi
  version: "3.0"
  server: gsas2_server.py
  upstream: https://github.com/pawantr/Agentic-GSAS-II
---

## Inputs and workflow

Bind each pattern to measured uncertainties, axis/units, an instrument file and
candidate CIFs. Retrieve missing candidates with `propose_structures`, then
compare plausible mixtures with search mode. Candidate ranking is a heuristic;
do not turn M20 into a calibrated probability or assume the top candidate is correct.

| Tool | Use |
|---|---|
| `refine_pattern` | One pattern with supplied CIFs and calibration |
| `propose_structures` | Retrieve and rank COD candidates |
| `powder_refine_submit` | Structured independent, sequential, joint, search or optimize jobs |
| `refine_series_submit` | Compatibility wrapper for an ordered series |
| `refinement_status` | Poll a batch group or individual live job |
| `powder_job_control` | Cancel, retry failed jobs, or resume the broker |
| `powder_live_ingest` | Submit a completed acquisition frame for tracking/diagnosis |
| `powder_report` | Shared APEXA report of results and evidence coverage |
| `powder_evidence` | Full fits, residual windows and phase markers |
| `powder_advise` | Configured image-capable provider returns typed proposals |
| `powder_trial` | Isolated numerical test of an evidence-bound proposal |
| `powder_session_create` | Start a versioned session from a completed result JSON |
| `powder_session_status` | Active result, revision history and improvement progress |
| `powder_session_evidence` | Fresh evidence tied to the active session revision |
| `powder_adopt` | Adopt an accepted trial after revision/hash comparison |
| `powder_improve` | Bounded background diagnosis/trial/adoption/reassessment loop |
| `powder_session_control` | Stop an improvement loop or restore a saved revision |
| `powder_strategy_sweep_submit` | Parallel independent policy alternatives on one observation |
| `powder_compare` | Compare alternatives only on identical observations and fit domains |
| `gsas2_tutorials` | Search/read all official tutorials, including coverage gaps |
| `gsas2_capabilities` | Installed signatures and the paper's capability inventory |
| `gsas2_workflow_submit` | Structured native GSAS-II operations in a new GPX copy |
| `gsas2_workflow_status` | Workbench operation progress, exports and numerical plots |
| `gsas2_workflow_cancel` | Stop a native workbench workflow |
| `assess_refinement` | Inspect a result already on disk |

For MIDAS detector integration products, `run_gsas_refinement` remains available
through the MIDAS server. Keep reduction provenance and uncertainty semantics
when converting detector output to powder observations.

## Batch and live rules

- Prefer durable submission for work that can take minutes. Poll, cancel or resume
  without blocking acquisition on the GSAS-II interpreter.
- Each independent observation can have its own instrument and candidate phases.
- Joint mode requires matching sample, conditions and phase identity.
- Sequential mode loads new observed data before transferring compatible state.
  Unsupported constraints and incompatible calibration cause a recorded cold start.
- Prefer acquisition completion markers. File stability is a readiness heuristic.
- Duplicate events must retain their identity; reprocessed frames need a revised
  series if their content changes. Old results cannot replace newer series state.

Queued and series submissions expose `refine_atoms`, `bkg_terms`,
`max_wall_seconds` and `run_lebail_companion`. Set `refine_atoms=True` when atomic
refinement is requested; the default leaves new atomic releases disabled.
Search mode also exposes bounded `phase_search` settings. Echo the requested
policy and inspect actual releases in the result trace.
Optional `phase_search.group_equivalent` requires pymatgen; record the
representatives, grouping tolerances and retained parse failures.
Optimize mode exposes `optimization` for bounded numerical Gaussian-process
expected-improvement search. This requires no learned checkpoint. Inspect every
trial and exclusion; optimizer uncertainty is not a phase-presence probability.

## Tutorial workbench and canvas

For tutorial-like requests, search `gsas2_tutorials`, read the relevant source
page (follow `next_offset` when necessary), and inspect `gsas2_capabilities` for
the installed signatures. The linked CW-neutron tutorial is YIG with constraints;
the PbSO4 dataset is a separate combined-refinement example.

Use `gsas2_workflow_submit` for native constraints, parameter limits, explicit
refinement stages, single crystals, simulation, peak fitting/calibration,
backgrounds, image calibration/integration, PDF calculation and prepared-model
scientific operations. Pass absolute input paths and a fresh output directory.
Recipes are structured operations, never Python source. Inspect method-specific
docs before constructing arguments. Scientific functions can reference live
project data and bind outputs for subsequent steps. Supply prepared models when
the required scientific setup is not derivable from the supplied inputs.

Poll the workflow and present its canvas artifacts. Full numerical plots load
from `figures_path`; do not copy large arrays into chat. New project versions,
parameters, progress and failures remain available in the workbench. Show
coverage gaps: native operation access does not establish complete tutorial
reproduction. Advanced `analysis` operations cover arithmetic pseudo variables,
parametric fits, pattern clustering/outliers, NIST fundamental-parameters peaks,
satellite k-vector search and ISODISTORT. `external_model` prepares native model
files and can execute a discovered PDFfit/fullrmc/RMCProfile runtime. Require
prepared model settings; inspect runtime availability before requesting execution.
Bilbao calls need the user's configured API key; never put credentials in tool
arguments or chat. Structure submission to Bilbao/ISODISTORT must be part of the
user's requested workflow. External fitted structures are not automatically
imported or adopted, and sequential PDFfit template expansion is not implemented.

The user has deferred learned phase/initialization models for this work. Keep
those entries explicitly deferred; do not invent checkpoints or claim learned
capabilities from a generic model-provider connection.

## Scientific interpretation

Execution completion does not establish correct structure or composition.
`scientific_status`, `evidence_coverage`, `qualification`, and conditional
uncertainties must accompany residuals. A `provisional` or `blocked` result is
not qualified, even when its Rwp is small. Missing evidence is unavailable.

Inspect all phases and histograms, effective cells after HStrain, covariance
singularity and solver convergence. Total movement during a refinement call is
not an inner-cycle convergence test. Step and time caps are budget outcomes.

Fractions normalize modeled crystalline material. Missing phases, amorphous
content, absorption/texture and calibration can bias them. Fixed-parameter zero
uncertainty is not measured precision. RRUFF remapped intensities do not qualify
ordinary QPA or displacement parameters. Reference cells supplied at initialization
do not demonstrate cell recovery.

## Image advice

Use an explicitly configured image-capable model and structured image transport.
The legacy string-only provider cannot carry panels. Keep observation, snapshot
and panel hashes in every proposal. Trials must reject stale evidence, singular
covariance, unconverged fits, invalid physical state and unsupported actions.
Numerical acceptance leaves a separate candidate. To continue autonomously,
create a session from the completed result JSON, preserving its input identity
and scientific limitations. Enable atomic proposals only when requested.
`powder_improve(auto_adopt=True)` regenerates evidence each round, evaluates up to
three proposals and adopts the accepted candidate with the smallest global Rwp.
It stops on abstention, no accepted candidate, a budget, cancellation or error.
`auto_adopt=False` leaves one round for review. Numerical batches need no image
model; visual diagnosis requires a configured image-capable provider.

Manual adoption uses `decision_path` and the evidence's `session_revision`.
An old revision, changed snapshot, changed trial or changed engine is rejected.
Every adoption creates a new saved version; source jobs remain intact. Poll
`powder_session_status` and distinguish the loop stop from solver convergence.
Restore an earlier revision after stopping an active loop. A blocked baseline's
external evidence limitations must remain visible after numerical improvement.

No vision advantage, external-engine parity, byte-identical replay or deployment
throughput is inferred from interface availability. Consult the powder-refinement
capsule and the current versioned benchmark manifest for tested scope.

## Current engine contract (15 October 2026)

The engine emits `blocked` when any configured check fails and `provisional`
otherwise, including when checks are unavailable. There is no `qualified` output
path. A blocked status is a policy outcome, not an expert incorrectness label.
An instrument file marked `PROVISIONAL` fails calibration; other calibration
validation can remain unavailable. `idle` is scheduler stagnation.

For batches, use `outcome_summary` and retain every record. Do not present the
first result as an aggregate. Keep cancellation and execution failure separate;
exclude failed executions and missing/nonfinite values from numerical plots.
Do not connect unrelated observations into a time series. Joint phase fractions
retain their histogram scope.

Trial acceptance requires configured structural checks, passed covariance and
convergence, global Rwp improvement >0.001 percentage points, per-histogram
worsening <=0.05 points, and lower combined loss over the union of cited windows.
Individual windows may worsen. Baseline active parameters remain released. The
legacy `uiso` action releases the existing atomic displacement representation;
cell actions can couple to HStrain. Accepted artifacts remain provisional and
need a current-snapshot comparison before adoption, performed by `powder_adopt`
or the bounded session loop. `atom_coordinates` is also available as an explicit
phase-scoped coordinate release when session policy permits it.

The advisor must cite only images actually transmitted and retain their hashes.
Model aliases are not immutable model versions; missing version metadata stays
unavailable. Follow `docs/powder-refinement.md` and its source-hashed audit snapshot:
1,676 numerical records (1,514 completed blocked, 157 provisional, five errors).
No end-to-end vision advantage or sustained live throughput is established.
The complete surveyed-source inventory lives in the manuscript supplement.
