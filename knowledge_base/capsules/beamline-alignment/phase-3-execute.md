# Phase 3 — Execute the alignment

## Goal

Drive the optimizer, dispatch motor moves through the safety-gated motor
server, record every step. Dry-run by default on first invocation; real
motion only on explicit operator approval.

## Procedure (per algorithm class)

### `custom:vo_center_fit` (analytical, no motor loop)

Used by `rotation_axis_from_tomo`.

1. Call `fit_rotation_axis(tomo_h5)`.
2. Read `cor_offset_px`, `vertical_drift_rows_per_360deg`, RMS.
3. Translate to motor moves via RUNBOOK.md `px_to_mm` (fail if missing — T5).
4. Present the proposal to the operator; do NOT auto-move. Rotation-axis
   corrections are large enough to warrant explicit approval.

### `scan_then_fit` (1D scan + Gaussian fit)

Used by `diode_max_1d_scan`.

1. Grid-scan the single motor over its declared range using
   `move_motor_absolute` + `caget` of the diode PV.
2. Fit `A exp(-(x - x0)^2 / (2σ^2)) + c` to the trace.
3. Check `fit_r2 >= 0.90` and `peak_width_fraction_of_range <= 0.25`.
4. If both pass: `move_motor_absolute(x0)`.
5. If either fails: `status: "partial"`, leave motor at the sample-max point
   (not the fitted peak, which is unreliable).

### `xopt.generators.bayesian.*` (BO)

Used by `saxs_beam_center_on_beamstop`, `slit_tune_for_ic_target`.

1. Build the Xopt object from the task YAML VOCS + warm-start prior.
2. Seed with `n_initial` points (biased to the warm-start mean).
3. Loop:
   - `X.step()` produces one candidate `inputs: dict`.
   - For each decision variable, call `move_motor_absolute(variable, value)`
     on `epics_motor_server`. Capture the return.
     - If any move is **rejected** by the safety gate: record this as a
       constraint violation (objective=`inf`), do NOT retry, log the reason.
   - Observe: for each `observation` in the task YAML, call the appropriate
     tool (`grab_pva_frame` + `analyze_detector_frame`, or scaler read).
   - Compute the objective + constraint values from the reducer output.
   - Feed back to Xopt via `X.evaluate(inputs, outputs)`.
   - Check convergence: `improvement_rel < 0.01` for `patience` consecutive
     steps → stop.
4. After the loop, move to the best observed point (not the GP's extrapolated
   optimum — that's a different point than any evaluated).

## Dry-run mode

If `dry_run=True`:
- Propose moves but DO NOT dispatch to the motor server.
- Simulate observations by sampling the GP prior (or returning the historical
  mean for a scan). Report every proposed move + predicted outcome.
- Final `status: "dry_run"`, never `"converged"` — even if the simulated
  trajectory looks great.

## Recording

Every evaluation goes into `alignment_runs.trajectory_json` with:
```json
{"iter": N, "inputs": {motor: value, ...},
 "outputs": {objective: val, constraint: val, ...},
 "timestamp": "...", "move_rejections": [...]}
```

The full `task_spec_json` and resolved warm-start are also saved so the run
is fully reproducible via `replay_alignment(run_id)` (future tool).

## Refusal / halt cases

- H1 (safety gate rejected): halt, report the rejection reason, do not retry.
- H2 (objective worse than start after `n_initial`): halt, `status: failed`.
- H3 (saturation during a scan): halt, `status: partial`, propose
  attenuation.
- H4 (PV reads stuck): halt, `status: partial`, ask operator to kick IOC.

## Successful phase-3 exit

```json
{"phase": 3, "status": "...", "final_point": {...},
 "trajectory_count": N, "advance": "phase-4-verify"}
```
