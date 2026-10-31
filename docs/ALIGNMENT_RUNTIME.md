# Bounded alignment runtime

APEXA implements one complete recipe, `diode_max_1d_scan`: a bounded one-dimensional grid, Gaussian plus offset fit, and quality-gated peak recommendation. Its supported modes are deterministic **simulation**, frozen-observation **replay**, and **read-only shadow**. No mode reads or commands a device, and no result verifies hardware alignment. `hardware`, `live`, and other execution modes fail with `broker_unavailable`.

The other task YAML files remain specifications or analytical helpers. In particular, installing Xopt does not enable Bayesian optimization or commission motor motion. The existing single-frame PVA and rotation-axis analysis tools are separate from this runtime.

## Tools and persistence

| Canonical MCP tool | Behavior |
| --- | --- |
| `preflight_alignment(plan)` | Validate software constraints; return the canonical immutable plan, configuration hash, plan hash, and recipe identity. No device access or database writes. |
| `run_alignment(plan, max_steps=21)` | Create a run and advance at most `max_steps` observations. Shadow persists one advisory request and waits. |
| `get_alignment_status(run_id, include_trajectory=False)` | Compact state, revision, pending request, budget deadline, and scientific result. Opt in to the frozen plan, observations and event log. |
| `resume_alignment(run_id, configuration_hash, expected_revision, max_steps=21)` | Check configuration, recipe, revision, deadline and ownership; continue the same run. |
| `submit_alignment_observation(run_id, observation, expected_revision)` | Validate and commit one shadow observation. Invalid data fails the run with a persistent rejection reason. Advance separately to request the next point. |
| `stop_alignment(run_id, expected_revision=None)` | Revoke the worker lease and stop recommendations. A worker cannot commit a late observation or fit afterward. |

State is stored in `$APEXA_HOME/alignment/runtime.db` (default `~/.apexa/alignment/runtime.db`), separately from legacy ingested history. Each proposal is committed before observation; each accepted observation and its event are atomic. A process must hold the run's renewable 30-second SQLite lease. Another worker receives `run_busy`; after expiry, resume records a recovery event and reuses the pending deterministic simulation/replay request. An expired worker cannot commit. Shadow requests remain pending across restart. Clients must refresh the revision after a conflict.

Stopping is terminal. A new plan creates a new run; there is no automatic return move. **`stop_alignment` stops this software's recommendations only. It cannot stop hardware controlled by another system.** Use the beamline's independent physical stop when needed. Deadline checks occur when advancing/submitting, with `deadline_expired` visible in status; this process is not a hardware watchdog.

## Explicit plan

This is a complete offline simulation plan. IDs marked `SIM` are synthetic identities, not PVs to connect to.

```json
{
  "task": "diode_max_1d_scan",
  "mode": "simulation",
  "source_id": "offline-diode-series-1",
  "instrument": {
    "beamline": "SIM",
    "motor": "SIM:m1",
    "monitor": "SIM:diode",
    "position_units": "mm",
    "signal_units": "A",
    "configuration_revision": "optics-1",
    "configuration": {"energy_kev": 65, "optics": "synthetic"}
  },
  "scan": {
    "bounds": [-5, 5],
    "device_limits": [-10, 10],
    "initial_position": -5,
    "max_step": 0.5,
    "max_total_travel": 10,
    "grid_points": 21
  },
  "budget": {"max_evaluations": 21, "max_seconds": 300},
  "observation": {
    "signal_bounds": [-1, 200],
    "settle_seconds": 0.5,
    "max_age_seconds": 2,
    "position_tolerance": 0.001
  },
  "fit": {
    "min_r2": 0.90,
    "max_fwhm_fraction": 0.25,
    "max_center_std_fraction": 0.02
  },
  "simulation": {
    "peak": 0.7,
    "sigma": 0.6,
    "amplitude": 100,
    "baseline": 2,
    "noise_std": 0,
    "seed": 42,
    "faults": {}
  }
}
```

All numeric inputs must be finite. Bounds must be increasing and inside declared device limits; the initial position must be inside those limits. The plan scans monotonically from low to high. Both initial-to-low travel and grid spacing must obey `max_step`, and the entire planned path must obey `max_total_travel`. There are 5–201 grid points; evaluation limits are 1–201. A lower evaluation budget produces a partial run. Position tolerance cannot exceed a quarter of grid spacing. Units are explicit and matched exactly; no implicit conversion occurs.

Preflight checks **caller-declared** configuration and limits. It does not verify physical soft limits, interlocks, authorization, optics state, device identity, positioning accuracy, or settle behavior. The configuration fingerprint covers the entire instrument mapping, including the explicit configuration revision. Supply enough configuration detail to distinguish optics/energy/detector changes; changing only the beamline name is insufficient. A new revision requires a new plan.

The runtime binds both its Python source hash and the task YAML hash. Resuming after either changes fails with `recipe_changed`; an operator must review and start a new run. This is intentional for reproducible replay. Plan and replay data are copied into the database so later caller-side edits cannot change them.

## Simulation and replay

Save the JSON above to a temporary `plan.json`. This example uses a temporary database and has no device/network side effects:

```python
import copy
import json
import tempfile
from pathlib import Path
from apexa_alignment_runtime import AlignmentRuntime, preflight

plan = json.loads(Path("plan.json").read_text())
assert preflight(plan)["status"] == "ready"
with tempfile.TemporaryDirectory() as directory:
    runtime = AlignmentRuntime(Path(directory) / "runtime.db")
    started = runtime.create(plan)
    completed = runtime.advance(started["run_id"], started["configuration_hash"])
    trajectory = runtime.status(completed["run_id"], include_trajectory=True)
    print(completed["status"], completed["result"])

    replay_plan = copy.deepcopy(plan)
    replay_plan["mode"] = "replay"
    replay_plan.pop("simulation")
    replay_plan["replay"] = trajectory["observations"]
    replay = runtime.create(replay_plan)
    replay_result = runtime.advance(replay["run_id"], replay["configuration_hash"])
    print(replay_result["status"], replay_result["replay_sha256"])
```

Simulation uses a Gaussian plus offset and seeded noise independent of run ID/restart. Each step's logical acquisition occurs after the configured settle interval plus 1 ms; no real waiting or simulated hardware command is issued. The budget limits both elapsed wall time and logical acquisition time. Fault injection maps zero-based indices to `missing`, `stale`, `nonfinite`, `configuration`, or `position`, for example `"faults": {"3": "stale"}`.

Replay requires the same source/configuration/units/schedule as its frozen records. It retains each original request ID and original requested/acquired/received timestamps. Freshness is evaluated in that source timeline; replaying last year's valid acquisition does not make it stale today. Source sequence must increase; each next source request must follow the previous receipt. Time gaps consume replay's logical budget. Missing records, invalid positions, inconsistent identities or stale source measurements fail explicitly. Replay has a source-content hash but no independent physical provenance attestation.

## Shadow acquisition contract

For shadow, set `mode` to `shadow` and remove `simulation`/`replay`. Run the plan; the result is `waiting_observation` with one persisted `pending` candidate. This candidate is an advisory request for an independently operated scan. It is not an authorization or motor command. An external read-only monitor/operator must supply an observation from the actual candidate position and after the request's settling interval.

A complete submitted record contains:

```python
record = {
    "request_id": pending["request_id"],
    "step_index": pending["step_index"],
    "requested_at": pending["requested_at"],
    "source_id": acquisition.source_id,
    "sequence": acquisition.sequence,
    "configuration_hash": acquisition.configuration_hash,
    "configuration_revision": acquisition.configuration_revision,
    "position": acquisition.measured_position,
    "position_units": acquisition.position_units,
    "signal": acquisition.diode_current,
    "signal_units": acquisition.signal_units,
    "acquired_at": acquisition.utc_unix_seconds
}
```

`acquisition` represents an external monitor record; no acquisition adapter or broker is supplied by this runtime. Do not substitute the proposed position for measured position, invent timestamps, or relabel configuration to make a record pass. Source/configuration/units must match the immutable plan, sequence must strictly increase, and position/signal must obey the declared bounds. `acquired_at` must be at least `requested_at + settle_seconds`, no later than the runtime's receipt clock, and at most `max_age_seconds` old. Runtime receipt time is authoritative for shadow; caller-provided `received_at` cannot override it. Clocks must be synchronized. A rejected observation ends the run as `failed`; corrections require a reviewed new run.

Use `submit_alignment_observation(run_id, record, expected_revision)` and then `resume_alignment` with the returned revision to request the next point. The first call commits the sample; the second persists the next request. Repeat through the grid or stop. The end-to-end offline test `test_complete_shadow_scan_is_advisory_and_requires_fresh_each_step` demonstrates this entire exchange, including reconstructing the runtime between steps. All shadow inputs remain caller-supplied and unattested even when checks pass.

## Scientific result and priors

A full scan is `completed` only if its Gaussian fit passes all checks: finite parameters/covariance, nonflat signal, R² threshold, interior peak, uncertainty limit, and **FWHM divided by full scan span** below the declared threshold. FWHM must also cover at least two grid intervals to reject an unresolved spike. Signal/coordinate normalization makes the fit independent of the choice of physical units. This is a single recipe, not a globally optimal search guarantee. Incomplete scans and rejected fits are `partial` and may return a provisional sampled-maximum recommendation. Invalid observations are `failed`. Budget exhaustion never becomes convergence.

Every result separates evidence scope (`simulation_only`, `replay_only`, or `advisory`) from `fit_accepted`. `hardware_alignment_verified` and `motion_executed` remain false, and `requires_operator_review` remains true. Even an accepted peak is never automatically selected on a physical motor. Gaussian covariance is a model-dependent uncertainty estimate, not a calibrated experimental confidence interval.

The application-level `outcome` remains scientifically `provisional` for completed/partial advisory runs, so a passed mathematical fit cannot turn the UI into a verified-science success. Ready/waiting runs are `pending`, active runs are `running`, and stopped runs are `cancelled` at that boundary.

Only accepted fits from this runtime with the **same mode, exact configuration fingerprint/revision, and recipe identity** can provide a prior, and only as the next fit's initial center guess within its bounds. The selected prior run is recorded. Legacy `recommend_initial` history is not used automatically, and a prior is never safety evidence. Priors currently search the latest 500 persisted runtime runs; absence of a match simply uses the current sampled maximum.

## Deployment boundary and verification

Before enabling any physical execution, a separately qualified broker must bind authorization to the exact plan/revision, read fresh device/interlock state, enforce physical limits and move budgets, issue and reconcile commands, verify settling, produce trusted observations, and own an independent stop/recovery mechanism. This runtime intentionally exposes no flag that enables motion. Beamline qualification, device adapters, real scan comparison, and hardware commissioning remain outstanding.

Run the offline contract suite with:

```sh
.venv/bin/python -m pytest tests/test_alignment_runtime.py -q
```

Tests cover known-peak recovery, rejected fits, deterministic fault injection, finite constraints, evaluation/time budgets, immutable replay, complete shadow progression, stale/configuration/unit checks, revision conflicts, concurrent ownership, proposal/observation restart recovery, STOP races, contextual priors, recipe changes, and canonical MCP registration. They do not contact hardware, models, remote machines or production datasets.
