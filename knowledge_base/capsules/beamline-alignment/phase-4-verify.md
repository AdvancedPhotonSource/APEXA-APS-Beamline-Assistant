# Phase 4 — Verify and report persisted evidence

The runtime records plans, proposals, accepted observations, rejected-data reasons,
leases/recovery, STOP and final scientific results in `alignment/runtime.db`.
Use `get_alignment_status(run_id, include_trajectory=True)` to inspect that record.
There is no `_record_run` tool and no need to manually write the legacy history DB.

Terminal states:

- `completed`: the full scan passed finite Gaussian/covariance, R², interior-peak,
  FWHM/span, sampling-resolution and center-uncertainty gates.
- `partial`: incomplete budget/scan or rejected fit; an observed sampled maximum
  may be a provisional recommendation. This is not verified alignment.
- `failed`: invalid/missing/stale/mismatched observation, with a persistent reason.
- `stopped`: operator stopped recommendations; no later samples may commit.

Always report `result.evidence_scope`: `simulation_only`, `replay_only`, or
`advisory`. Accepted shadow fits still contain caller-supplied, unattested evidence.
`hardware_alignment_verified` and `motion_executed` remain false in every mode.
A peak recommendation is never an instruction to move a motor automatically.

A concise operator report should state the run ID/revision/mode/status, number of
accepted observations, fit acceptance or rejection reason, recommendation and
units (if present), and the operator-review requirement. Do not rename the status
`converged`, invent final physical positions, or relabel simulated/replayed data
as a good measured experiment. Gaussian covariance describes this model fit;
it is not independently calibrated experimental uncertainty.

If the analysis suggests a physical correction, commissioning of a separately
qualified broker and beamline validation remain outstanding. Starting a new
simulation or reviewed shadow run does not waive that boundary.
