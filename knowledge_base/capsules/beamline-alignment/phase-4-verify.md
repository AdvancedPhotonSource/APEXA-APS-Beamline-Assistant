# Phase 4 — Verify, record, honest status

## Goal

Decide the final `status` of the alignment run and write the record to the
history DB. This is the step that makes the next run smarter — a `converged`
run becomes warm-start fuel; a `failed` run becomes an anti-prior.

## Verdict rules

`converged` ← ALL of:
- Final objective below the task's `success_criteria`.
- Residual RMS below the task's convergence tolerance (if applicable).
- Zero safety-gate rejections across the whole trajectory.
- Zero constraint violations at the FINAL evaluated point (constraint
  violations earlier in the trajectory are fine — that's BO learning).

`partial` ← one of:
- Final objective above the success threshold but the fit is well-formed
  (e.g. Gaussian R² ≥ 0.90 but peak is at a boundary; CoR fit succeeded but
  vertical drift exceeded 3 rows).
- Convergence tolerance missed but BO ran its full iteration budget.
- Analytical path completed but RUNBOOK scale missing (T5), so the proposed
  move can't be dispatched.

`failed` ← one of:
- H1/H2/H3/H4 halted the run.
- Objective at the best observed point is WORSE than at the starting point
  (something is wrong with the task spec or the hardware).

`dry_run` ← ran in dry-run mode. **Never report `converged` on a dry run**,
regardless of simulated outcome.

## Record

Call `_record_run(run_id, task_name, status, trajectory_json, task_spec_json)`
to insert into `alignment_runs`. The record MUST include:
- The resolved VOCS (post-warm-start)
- Every evaluation with inputs, outputs, timestamp
- The final decided motor positions
- The verdict rationale (which rule triggered which status)

If the run included new experiment data (a tomo-H5 input), optionally ingest
it with `outcome` matching the verdict:
- `converged` → `ingest_experiment_h5(..., outcome="good")` so future runs
  warm-start from it.
- `failed` → `ingest_experiment_h5(..., outcome="bad")` so future runs
  avoid that region.
- `partial` / `dry_run` → `outcome="unknown"` (don't bias future priors).

## Report to the operator

A one-paragraph summary + three bullet points:

```
Status: <converged|partial|failed|dry_run>

- Final <objective_name>: <value> (<Δ from start>, <target>)
- Final motor positions: samX = X.XX mm, samY = Y.YY mm
- Trajectory: N evaluations over T seconds; <K> safety-gate rejections

Why this verdict: <rule that triggered status>

Next step: <explicit action — "approve dry-run moves", "re-ingest archive to
refresh prior", "ingest result as good", etc.>
```

Everything else goes in the DB. Keep the chat terse.

## Successful phase-4 exit (= end of task)

```json
{"phase": 4, "run_id": "...", "status": "...", "recorded": true}
```
