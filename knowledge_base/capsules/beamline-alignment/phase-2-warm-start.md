# Phase 2 — Warm-start from history

## Goal

Use the alignment history DB to initialize the task with a prior that
reflects what has actually worked at this (beamline, technique, detector)
tuple. Reduces BO sample budget 40–70% versus a cold start (literature:
Xopt-UCB on LCLS beamlines, Blop/NSLS-II).

## Procedure

1. **Call `recommend_initial(beamline, technique, detector, role)`** with the
   active context. The `role` depends on the task:
   - `rotation_axis_from_tomo` → `role="motor"` (returns typical samX, samY)
   - `saxs_beam_center_on_beamstop` → `role="motor"` + separately `role="ic"`
     for the saturation sanity bound
   - `slit_tune_for_ic_target` → `role="ic"` to resolve the IC target
   - `diode_max_1d_scan` → `role="diode"` to resolve the expected max

2. **Check `n_good_runs_matched`** in the response.
   - **≥ 3**: proceed. Use `typical_mean` as the BO initial point, `typical_std`
     as the BO prior kernel scale, `span` as a safe-region hint.
   - **< 3** AND `allow_cold_start=True`: proceed with random-start, log that
     the prior is from <3 runs.
   - **< 3** AND `allow_cold_start=False` (default for first-run of a new
     task): return:
     ```json
     {"status": "nothing_was_run",
      "reason": "insufficient_history",
      "n_good_runs": 0,
      "suggestion": "Ingest at least 3 past good runs via "
                    "`ingest_experiment_archive` before this task will warm-start. "
                    "To proceed from a random start, call again with "
                    "allow_cold_start=true."}
     ```

3. **Report the prior to the operator** before advancing:
   - Historical sample size
   - Date range (oldest → newest) — if newest is > 60 days old, surface T4
   - Each variable: historical mean ± std; current motor RBV; delta
   - If any variable's current RBV is more than 3σ outside the historical span,
     say so (operator may be in a genuinely new regime)

## Refusal cases

- `nothing_was_run` on insufficient history (see above).
- Historical prior spans a motor range outside the current soft limits →
  refuse; the soft limits may have been tightened for a reason.
- `recommend_initial` returned recs but none for the task's declared variables
  (e.g. task needs samX, DB only has IC values) → refuse with
  `missing_prior_for_variable`.

## Successful phase-2 exit

```json
{"phase": 2, "warm_start": {...}, "advance": "phase-3-execute"}
```
