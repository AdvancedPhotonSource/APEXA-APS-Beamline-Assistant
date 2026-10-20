# Beamline Alignment — operating card

> **Source of truth** for every beamline alignment task driven by APEXA's
> `AlignmentAgent`. Loaded JIT into the agent context on the first alignment
> tool call per turn (same mechanism as the FF/NF capsules).

## Scope

- **Covers:** rotation-axis alignment, SAXS beam-on-beamstop centering, slit
  tuning to historical IC targets, diode-max 1D scans, generic BO over any
  (motor × sensor) tuple at an EPICS beamline.
- **Does NOT cover:** optics alignment (DCM/HRM feedback), magnet steering,
  closed-orbit correction, insertion-device gap tuning. Those belong to the
  accelerator team, not APEXA.

## THE ORDER

Every alignment turn follows this phase sequence. Do NOT skip phases.

| Phase | Doc | Purpose |
|---|---|---|
| 1 | [phase-1-select-task.md](phase-1-select-task.md) | Pick a task from the library OR refuse |
| 2 | [phase-2-warm-start.md](phase-2-warm-start.md) | Pull historical prior; refuse if sparse |
| 3 | [phase-3-execute.md](phase-3-execute.md) | Run through xopt/scan/custom; gated motion |
| 4 | [phase-4-verify.md](phase-4-verify.md) | Check convergence + safety record |

## HARD RULES

Numbered so an operator can cite them in a log. Rules 1–3 are **never-violate**.

1. **No alignment tool ever issues a raw `caput`.** Motion goes through
   `epics_motor_server` tools (`move_motor_absolute`, `jog_motor`, etc.) so
   the five safety checks (soft-limit, limit-switch, large-move guard,
   velocity bound, jog-duration cap) run before every move.

2. **Honest status.** An alignment run reports exactly one of:
   `converged` | `partial` | `failed` | `dry_run`. Never report "aligned"
   without the convergence block (final objective below tolerance, residual
   RMS below threshold, no safety constraint violated). A fit that didn't
   converge is `partial`, not `success-with-warning`.

3. **Warm-start requires evidence.** `recommend_initial` is only used as a
   prior when `across_n_runs >= 3` for the (beamline, technique, detector)
   tuple. With fewer good runs, the task either (a) runs purely random-start
   or (b) refuses with `nothing_was_run`, operator's choice via
   `allow_cold_start`.

4. **Saturation is a safety constraint, not a cost.** Detectors that saturate
   lose calibration (e.g. unattenuated CeO2 at UINT32_MAX, see
   project_calibrant_saturation memory). Any BO evaluation with
   `saturation_fraction > 1%` returns a constraint violation (score=inf),
   not a bad score the optimizer can trade off against.

5. **One task per invocation.** Do NOT fuse "find hotspot AND align beamstop
   AND tune slits" into one BO run — they have different objectives and
   different safe regions. Chain them in THE ORDER as separate tasks.

6. **Historical data has decay.** If the newest ingested good run for the
   target (beamline, technique) tuple is older than **60 days**, surface the
   age and recommend re-ingestion before using as a warm-start prior.

7. **Dry-run mode is first-run default.** New tasks run with `dry_run=True`
   on first invocation: BO proposes moves, agent reports them, no motor
   command is issued. Operator approves → `dry_run=False` for the real run.

8. **PVA stream reads are rate-capped.** `grab_pva_frame` enforces a maximum
   call frequency (default 2 Hz) to avoid starving the real detector
   consumers (ImageJ, DAQ pipeline, downstream analysis). BO configures its
   evaluation cadence under this cap.

## HALT CONDITIONS

Stop the alignment run (not just surface a warning) when any of these fire:

- **H1 — Motor rejected by safety gate.** If `move_motor_absolute` returns a
  rejection (soft-limit, large-move, velocity), stop and report. Do NOT
  retry with a reduced step until the operator approves — the gate exists
  because a repeat is often worse than the first attempt.

- **H2 — Objective worse than start.** After `n_initial` samples, if the
  best observed objective is worse than the starting-position value, halt
  and report `status: failed, reason: baseline_unachievable`. Something
  about the task spec, PV mapping, or motor direction is wrong.

- **H3 — Saturation during a scan.** If any BO evaluation saturates (even
  briefly), halt. Saturated detectors mislead the optimizer by clipping the
  high end of the response surface.

- **H4 — PV reads stuck.** If three consecutive sensor reads return
  identical values (byte-equal) over >5 s, halt. The IOC may be stale or the
  camera driver stalled.

## TRAPS

Short, cited silent-failure patterns that have bitten alignment at APS.

| # | Trap | Section | Mitigation |
|---|---|---|---|
| T1 | PVA channel name mismatch (plugin alias vs base) | LAB_NOTEBOOK §1 | Validate channel exists before BO; `pvapy.Channel(name).get()` with a 5-s timeout, refuse task on fail |
| T2 | Beamstop centroid picks up a dust speck | DIAGNOSIS §2 | Beamstop ROI = cold region INSIDE the beam bbox (not globally); re-check if `distance > beam_radius` |
| T3 | Slit backlash — move the slit the "long way around" | LAB_NOTEBOOK §3 | For large-move corrections, pre-move 50 µm past target, then back; add to task algorithm hint |
| T4 | IC target drifted (ring current, source refill) | LAB_NOTEBOOK §4 | `recommend_initial` reports `age_days` of newest run; agent surfaces if >14 days |
| T5 | Rotation-axis offset reported in detector-x PX but motor is sample-X MM — need the per-beamline px→mm scale from RUNBOOK.md | ENVELOPE §4 | Hard-fail if `px_to_mm` is absent from RUNBOOK for the active beamline |

Full trap table and worked examples live in [DIAGNOSIS.md](DIAGNOSIS.md).

## Where to look next

- [ENVELOPE.md](ENVELOPE.md) — Fixed/Configured/Intrinsic/Derived parameter tiers for alignment tasks (what's safe to vary, what isn't, what's inferred)
- [RUNBOOK.md](RUNBOOK.md) — **Per-beamline** PV names and motor-calibration scales (px→mm, mm→degrees, IC-channel assignments). Volatile; re-verify at start of each beamtime.
- [DIAGNOSIS.md](DIAGNOSIS.md) — Full trap table + diagnostic recipes
- [LAB_NOTEBOOK.md](LAB_NOTEBOOK.md) — Historical ground-truth: cases where the framework ran, what it found, what the operator decided
