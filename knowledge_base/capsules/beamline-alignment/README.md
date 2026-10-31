# Beamline Alignment — operating card

This card describes the implemented runtime in `apexa_alignment_server.py`.
The full plan and observation contracts are in `docs/ALIGNMENT_RUNTIME.md`.

## Scope

Implemented: one bounded `diode_max_1d_scan` recipe in simulation, immutable
replay, and read-only shadow mode; separate history ingestion and analytical
rotation-axis/frame helpers. The slit/SAXS Bayesian-optimization YAMLs remain
specifications. No alignment runtime mode reads or commands hardware.

## THE ORDER

| Phase | Doc | Purpose |
|---|---|---|
| 1 | [phase-1-select-task.md](phase-1-select-task.md) | Select an implemented capability and declare configuration/constraints |
| 2 | [phase-2-warm-start.md](phase-2-warm-start.md) | Understand exact-context fit priors and legacy-history limits |
| 3 | [phase-3-execute.md](phase-3-execute.md) | Preflight and run simulation/replay/shadow checkpoints |
| 4 | [phase-4-verify.md](phase-4-verify.md) | Report the persisted scientific result and its evidence scope |

## HARD RULES

1. Hardware execution requires a separately qualified broker, which is not
   implemented. `hardware` mode refuses. Do not bypass that refusal by stitching
   generic motor tools, shell/Python commands, raw PV writes, or an agent-driven
   optimizer into an alignment loop. Ordinary operator motor controls are a
   separate capability, not a qualified alignment broker.
2. Software preflight validates declared finite bounds, units, configuration,
   step/travel and time/evaluation budgets. It does not verify live interlocks,
   actual device limits, beam identity or settling. Never describe it as hardware
   qualification.
3. Simulation/replay/shadow all produce recommendations only. Even `completed`
   with `fit_accepted=true` has `hardware_alignment_verified=false`,
   `motion_executed=false` and `requires_operator_review=true`. Never call the
   beamline aligned based on these results.
4. Shadow observations must come from an independent read-only monitor/operator
   scan. Use actual measured position, source identity, configuration and fresh
   acquisition time; do not substitute proposed positions or invented readings.
   Inputs remain unattested even when validation succeeds.
5. Accepted fits from the exact configuration revision/hash, recipe identity
   and same mode may initialize the Gaussian fit only. Legacy `recommend_initial`
   results are contextual historical observations, never safe limits, automatic
   runtime priors or authorization. No sample-efficiency claim has been measured.
6. Use the persistent canonical tools. Never fabricate `_record_run`,
   `replay_alignment`, `dry_run`/`allow_cold_start` arguments, Xopt execution, or
   automatic return moves. The implemented schemas are in the runtime contract.
7. `stop_alignment` revokes the software worker and prevents late commits. It
   cannot stop externally controlled hardware. Use the independent beamline stop
   if physical equipment is moving.

## HALT CONDITIONS

- Invalid/nonfinite bounds, configuration, units or budgets: preflight refuses.
- Stale/missing/mismatched/out-of-bounds observations: runtime records a rejection
  and finishes `failed`. Start a reviewed new run after resolving the problem.
- Time/evaluation budget exhausted: `partial`; do not convert exhaustion to
  convergence. A changed recipe/configuration refuses resume.
- STOP: terminal `stopped`; no subsequent observation or fit can be committed.
- A flat, boundary, broad, underresolved, uncertain or poorly fitted peak:
  `partial`, with at most a provisional sampled-maximum recommendation.

## Context and future commissioning

[RUNBOOK.md](RUNBOOK.md), [ENVELOPE.md](ENVELOPE.md), and
[DIAGNOSIS.md](DIAGNOSIS.md) contain beamline context and future commissioning
considerations. Their physical recipes are not executable alignment procedures.
Unverified PV names/scales must not be inferred as operational facts.
[LAB_NOTEBOOK.md](LAB_NOTEBOOK.md) contains historical notes, not independent
validation truth. A reviewed study knowledge manifest controls whether any of
these bodies may enter a benchmark run.
