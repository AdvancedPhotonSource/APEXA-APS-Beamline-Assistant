> Runtime boundary: only `diode_max_1d_scan` simulation/replay/read-only shadow is implemented. Hardware and Bayesian-optimization guidance below is future commissioning context, not an executable procedure. Do not dispatch motor commands or bypass a broker refusal. See [README.md](README.md) and `docs/ALIGNMENT_RUNTIME.md`.

# Alignment task parameter envelope

> Which parameters are **Fixed** (never vary), **Configured** (set per
> beamtime), **Intrinsic** (per task, from the YAML spec), **Derived** (computed
> from the data at run time). This is the hierarchy that keeps a BO search
> from wandering out of the physically meaningful region.

## Fixed (hard, same across every beamline and every task)

| Parameter | Value | Source |
|---|---|---|
| Minimum IC reading (not-shuttered threshold) | 100 cps | Any lower = beam is physically off; running an optimizer here is noise |
| Maximum saturation fraction | 1% | Above this, detector response is nonlinear |
| Minimum good-run count for history warm-start | 3 | Fewer runs = anecdote, not prior |
| Historical-prior staleness threshold | 60 days | Beyond this, ring current / optics drift makes the prior unreliable |
| Dry-run default on first invocation of a new task | `True` | Operator must opt into live motion |
| PVA frame cap | 2 Hz | Prevent starving real detector consumers |

## Configured (per beamtime, written into RUNBOOK.md)

| Parameter | Example (1-ID-E) | Where it goes wrong |
|---|---|---|
| EPICS motor PV prefix | `1ide:` | Prefix mismatch → every PV read 404s |
| Default area detector PVA channel | `1ide:pixirad:image` | ImageJ plugin uses the same channel; conflict possible |
| IC2 PV | `1ide:scaler1.S3` | Beamline rewires scaler channels between beamtimes |
| Pixel→mm scale (detector x) | 0.075 mm/px (pixirad) | Wrong scale = CoR correction overshoots by Nx |
| Px→deg scale (rotation axis) | 1.0 deg / px / (sample distance) | geometry-dependent |
| Soft limits per axis | samX: [-5, +5] mm | motor_server enforces, task clamps proposals to this |

## Intrinsic (per task, locked by the YAML spec)

| Parameter | Where it lives |
|---|---|
| Decision variables (motors) | `vocs.variables` |
| Objective function | `vocs.objectives` + `observations[].reducer` |
| Safety constraints | `vocs.constraints` |
| Algorithm choice (BO/scan/custom) | `algorithm.class` |
| Convergence tolerance | `algorithm.convergence` |
| Warm-start source | `prior_source` |

Changing an Intrinsic requires editing the YAML; the agent should NOT infer
these at run time.

## Derived (computed from the data at run time)

| Parameter | How |
|---|---|
| Beam centroid (px) | `analyze_detector_frame` reducer |
| Beamstop centroid (px) | `analyze_detector_frame` reducer with `geometry=circ_with_beamstop` |
| Target IC value | `recommend_initial(..., role='ic')` from history DB |
| Rotation-axis offset (px) | `fit_rotation_axis` (Vo CoR) |
| Vertical beam drift (rows / 360deg) | `fit_rotation_axis` (sinusoid+drift fit) |
| Current motor positions / soft limits | `get_motor_position` + `get_motor_limits` on epics_motor_server |
| Historical sample size + date range | `query_alignment_history` filter |

Derived values are freshly computed every run — never cached, never reused
from a prior run (that's what the history DB warm-start is for, and that's
labeled explicitly).
