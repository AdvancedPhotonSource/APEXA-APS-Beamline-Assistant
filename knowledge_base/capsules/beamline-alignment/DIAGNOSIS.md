# Alignment diagnosis — traps and what to do

> Short, cited silent-failure patterns. If you add a trap, cite the real
> case that produced it, same as the FF/NF capsules.

## Trap table (quick index)

| # | Pattern | Where it fires | Mitigation |
|---|---|---|---|
| T1 | PVA channel name mismatch | start of saxs_beam_center_on_beamstop | pre-flight channel probe |
| T2 | Beamstop centroid is dust/noise, not the stop | SAXS reducer | ROI inside beam bbox; sanity-check `distance <= beam_radius` |
| T3 | Slit backlash | large-move corrections | pre-move 50 µm past, then back |
| T4 | IC target drifted since last ingested run | slit_tune_for_ic_target | surface `age_days` of newest good run |
| T5 | px scale not registered for the active beamline | rotation axis / SAXS centering | refuse task if `px_to_mm` missing in RUNBOOK |
| T6 | PV reads stuck (stale IOC) | any observation-polling loop | halt condition H4 |
| T7 | BO stuck at saturation ceiling | SAXS centering with attenuator too low | halt H3; apply attenuation; resume |
| T8 | Rotation-axis correction overshoots | after fit_rotation_axis + move | sign flip in RUNBOOK (sample-X vs detector-X) |

---

## T1 — PVA channel name mismatch

**Symptom.** `grab_pva_frame` times out with no error context. `recommend_initial`
returned a prior OK; task config looks fine; motors are responsive.

**Cause.** The PVA channel used by the areaDetector `NDPluginPva` is often
*not* the same as the "image" channel ImageJ sees. The ImageJ plugin typically
subscribes to `beamline:detector:image` while the raw camera IOC publishes on
`beamline:cam1:image` with a plugin-aliasing layer in between.

**Fix.** Add a pre-flight check: `pvapy.Channel(pv_name).get(..., timeout=5.0)`
in a sanity-only path before entering the BO loop. If it fails, refuse the
task with `nothing_was_run` and ask the operator for the correct channel.
Record the working channel in RUNBOOK.md so the next task picks it up.

---

## T2 — Beamstop centroid picks up a dust speck

**Symptom.** `distance_from_beamstop_px` reports ~10 px when the beam is
visually centered on the beamstop. BO wanders.

**Cause.** The reducer finds the darkest region of the *whole frame* and
labels it "beamstop". A dust mote on the sensor or a dead-pixel cluster is
darker than the beamstop shadow.

**Fix.** The reducer in `analyze_detector_frame` with `geometry=circ_with_beamstop`
restricts the beamstop search to the *bounding box of the beam* — a dark
region OUTSIDE the beam cannot be the beamstop. Also sanity-check:
`distance_from_beamstop_px <= beam_radius_px * 1.2`. If not, refuse the
measurement with `confidence=low` and ask for a wider ROI hint.

---

## T3 — Slit backlash: motor goes "the long way around"

**Symptom.** Motor command accepted, final RBV matches target, but the beam
position jumps by far more than the slit change suggests.

**Cause.** Mechanical backlash in the slit assembly. The motor controller
takes up the slack on direction reversal, which can equate to several hundred
microns of actual beam movement for a nominally 10 µm commanded change.

**Fix.** For any slit-cen move larger than one gear-tooth (varies per
beamline; typical 20 µm), pre-move 50 µm past the target in the NEW direction,
then return. Encode this as an algorithm hint on `slit_tune_for_ic_target`:
`algorithm.pre_move_backlash_um: 50`.

---

## T4 — IC target drifted since last ingested run

**Symptom.** `slit_tune_for_ic_target` reports `converged`, but IC2 is still
30% off the operator's expectation. Historical target was 1.2e5; current
best is 1.5e5.

**Cause.** The historical prior is from a beamtime 90+ days ago. Ring current,
front-end attenuation, mirror reflectivity have all drifted. The "typical IC2"
is a moving target.

**Fix.** `recommend_initial` already reports `across_n_runs`. Extend it to
report `latest_run_date` (schema change — low priority but worth doing).
Agent surfaces the staleness and either (a) runs anyway with the stale prior
+ warning, or (b) ingests the last beamtime's data first.

---

## T5 — px scale not registered

**Symptom.** `fit_rotation_axis` returns `cor_offset_px = 2.5`. Agent proposes
a 2.5-unit samX move but has no scale → asks the user "mm or px?".

**Cause.** The per-beamline px → mm scale lives in RUNBOOK.md and has not
been filled in yet.

**Fix.** `_rotation_axis_correction_moves` currently emits raw px offsets
with the action string "× px_to_mm per RUNBOOK". A follow-up should resolve
`px_to_mm` from RUNBOOK and emit real mm deltas, failing hard if missing.
This is the main gap between the current code and a one-call-and-done
rotation-axis-correction tool.

---

## T6 — PV reads stuck

**Symptom.** BO's "evaluation function" returns the same objective value for
multiple consecutive iterations even though motors have moved.

**Cause.** IOC stale, camera driver stalled, or the PVA plugin's internal
queue backed up. The PV read succeeds but returns a cached value.

**Fix.** Halt condition H4 — three consecutive byte-equal reads over >5 s
halts the task. The agent reports and asks the operator to kick the IOC.

---

## T7 — BO stuck at saturation ceiling

**Symptom.** `distance_from_beamstop_px` plateaus; several evaluations in a
row report `saturation_fraction > 1%`; constraint violation flagged but BO
keeps proposing moves in the same region.

**Cause.** The attenuator setting is too low; the sweet spot (centered beam)
is in a saturated region of the detector response. BO cannot distinguish two
saturated frames.

**Fix.** Halt H3. Surface a two-step recovery: insert the next attenuator
foil, re-check saturation at the current position, resume the task.

---

## T8 — Rotation-axis correction overshoots

**Symptom.** `fit_rotation_axis` reported `cor_offset_px = +0.5`. Agent moved
samX by -0.5 × px_to_mm. Follow-up fit reports `cor_offset_px = +1.0` — the
move DOUBLED the offset.

**Cause.** Sign convention: samX positive = sample moves outboard. The
rotation-axis offset is reported in detector-x space, not sample-x space. For
this beamline, a +0.5 px CoR offset means the sample should move in the
POSITIVE samX direction, not negative.

**Fix.** The sign flip is in RUNBOOK.md ("Shared conventions"). The task's
`_rotation_axis_correction_moves` function should read the sign from RUNBOOK
rather than hard-coding "negative offset". This is beamline-specific and
cannot be guessed from first principles.
