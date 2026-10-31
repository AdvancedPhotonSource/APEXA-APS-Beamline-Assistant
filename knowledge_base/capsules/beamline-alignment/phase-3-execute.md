# Phase 3 — Durable simulation, replay and shadow

Only `diode_max_1d_scan` has a runtime. Use the JSON plan schema and full examples
in `docs/ALIGNMENT_RUNTIME.md`; task YAMLs are not executable motor programs.

1. Select `simulation`, `replay`, or `shadow`. Declare source, motor/monitor IDs,
   units, configuration revision/details, finite scan/device/signal bounds,
   initial position, max step/travel, grid, settle/freshness and budgets.
2. Call `preflight_alignment(plan)`. Inspect the canonical plan/hash and the
   declared software preflight scope. No live hardware state is verified.
3. Call `run_alignment(plan, max_steps=21)`. It stores proposals before evaluating
   observations and commits sample/event pairs atomically. Simulation is seeded;
   replay uses a frozen copied observation list and its content hash.
4. To continue after a partial call/restart, refresh `get_alignment_status` and
   pass its run ID, configuration hash and current revision to `resume_alignment`.
   An active lease refuses concurrent ownership; an expired lease can be recovered.
   Recipe/configuration changes require a reviewed new run.
5. In shadow mode, `waiting_observation` contains one advisory `pending` request.
   The independently operated scan/monitor supplies a matching fresh measurement.
   Call `submit_alignment_observation(run_id, observation, expected_revision)`;
   then resume using the returned revision to request the next candidate.
6. To halt recommendations, call `stop_alignment`. There is no return-to-start or
   move-to-optimum command. Use the independent beamline stop for any hardware
   controlled outside this runtime.

Never substitute `move_motor_absolute`/`caget` calls, raw PV writes, or manually
constructed Xopt loops for the unavailable broker. Hardware mode refuses and
cannot be enabled by operator confirmation or a `dry_run=False` switch.

`fit_rotation_axis` and `analyze_detector_frame` are analytical helpers. Their
results may inform a reviewed plan; they do not dispatch or authorize correction
moves. SAXS/slit Bayesian optimization remains specification-only.

Proceed to [phase-4-verify.md](phase-4-verify.md) with the persisted result.
