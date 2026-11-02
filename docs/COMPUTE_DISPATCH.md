# Compute planning and dispatch

APEXA chooses concurrency and a device from the task profile, available host
resources, existing reservations and the executor's supported paths. Data must
be accessible on the execution host. A placement recommendation is separate
from a submitted job and its completion receipt.

## Inspect a recorded decision

Expand a tool result in chat to see **Recorded compute plan** when the tool
returns a `compute` block. The same explanation appears when that result is
opened in Canvas. It shows reported workers, threads per worker, device, host,
estimated memory and the planner's reason. **Inventory and reservation scope**
expands the recorded inventory and coordination boundary.

Series integration returns an initial placement decision plus an `allocation`;
the display uses the allocation for workers, device and memory. A result marked
`nothing_was_run` shows **No processing started**. Missing fields stay absent.
These are recorded planning values, not a live utilization monitor or evidence
of scientific acceptance. The complete tool result remains available for review.

## Concurrency and admission

`apexa_compute.plan()` considers the current resource inventory and the profile
for the selected task. Independent work items fan out within CPU, host RAM and,
when used, GPU VRAM limits. `APEXA_COMPUTE_RESERVE` (default 0.25) retains headroom;
existing reservations reduce the remaining capacity. Requested worker counts
are upper bounds. A plan with zero workers cannot admit work.

CPU planning uses affinity/scheduler/container constraints where supported and
the one-minute load average. Memory telemetry and per-worker estimates constrain
fan-out separately. Internally parallel profiles such as FF/NF reconstruction
receive one worker with a bounded thread budget. The planner's `reason` records
the limiting resource. Actual admission rechecks shared capacity atomically.

[Compute reservations](COMPUTE_RESERVATIONS.md) describe process ownership,
cleanup, uncertain reservations and the distinction between planning and
reservation. This registry coordinates cooperating APEXA processes using the
same local database. Remote plans use controller-side accounting; a host-owned
worker or facility scheduler is needed to coordinate remote resources.

## Device selection and supported executors

Automatic GPU selection requires a task profile whose `gpu_speedup` reaches
`APEXA_GPU_MIN_SPEEDUP` (default 1.25), adequate profiled VRAM, a visible GPU and
an executor that supports it. Unmeasured tasks default to a speedup of 1.0.
Explicit CUDA requests still require admission and engine support. MPS admission
is currently refused by the shared planner.

The built-in calibration profile records historical copland measurements:
221.1 seconds with four CPU threads, 230.0 seconds with 48 CPU threads and
224.7 seconds on an RTX A6000 for the measured fit. Its default therefore favors
CPU fan-out. These measurements are specific to that workload and host. A
reviewed `midas_calibrate_series(..., benchmark=True)` run can profile a staged
representative dataset and save `~/.apexa/task_profiles.json`. It performs real
calibration work; compare scientific outputs as well as timing before adopting
new profiles. No new engine benchmark was run for the UI change.

Current adapter boundaries:

| Adapter | Compute behavior |
| --- | --- |
| `midas_calibrate_series` | Plans independent calibrations from resources/profile and passes bounded worker environments to the calibration executor. |
| `midas_integrate_series` | Uses the CPU `integrator.py` backend. Accepted targets are `auto`, `local`, `cpu` and `remote`. CUDA/MPS requests are refused by this adapter. |
| Integration with `compute_target="remote"` | Returns a recommendation and `nothing_was_run=true`; this call does not submit remote work. |
| FF/PF pipeline adapters | Forward supported `machine`, `n_nodes` and `shard_gpus` settings to the installed MIDAS pipeline. Engine, scheduler, data access and job receipts remain host-specific requirements. |

`_pick_compute_target()` keeps automatic placement near accessible data. A cost
estimate by itself does not establish useful GPU acceleration or remote data
access. The former `APEXA_GPU_AUTO_DISPATCH`/`APEXA_COST_LOCAL_CPU` dispatch rule
is no longer the active series-integration placement policy.

## Configuration

| Variable | Purpose |
| --- | --- |
| `APEXA_COMPUTE_RESERVE` | Fraction of host capacity held back before admission planning. |
| `APEXA_GPU_MIN_SPEEDUP` | Minimum profile speedup for automatic GPU selection. |
| `APEXA_RESOURCE_DB` | Shared local SQLite reservation registry for cooperating processes. |
| `APEXA_MIDAS_DEVICE` | Calibration device preference; executor/admission checks still apply. |
| `APEXA_GPU_MACHINE`, `APEXA_GPU_ENDPOINT` | Remote placement metadata; configuring them alone does not submit a job. |

Choose an execution host that already sees the complete input dataset, geometry,
dark/mask and output locations. Facility systems such as Polaris may require
explicit staging and a reviewed scheduler configuration. Keep the returned
execution receipt and scientific outcome with the compute decision.
