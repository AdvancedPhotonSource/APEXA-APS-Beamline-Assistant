# Shared host compute reservations

`apexa_compute.plan()` and `reserve_worker()` now consult a shared local SQLite
registry. Separate APEXA processes using the same database coordinate CPU thread
budgets, host RAM and physical GPU VRAM atomically. This replaces the earlier
process-private reservation dictionary.

The default database is `~/.apexa/runtime/resources.sqlite3`. Set
`APEXA_RESOURCE_DB` to a local, service-account-owned path on each execution host.
All cooperating APEXA processes on that host must use the same path. Keep the
database off network/cloud-synchronized storage; SQLite locking on those paths
has not been qualified. Separate users/databases and non-APEXA workloads are not
coordinated by this registry.

The planner still uses measured workload profiles and current inventory. CPU,
RAM and VRAM reservations are separate constraints. A reserve operation runs in
one `BEGIN IMMEDIATE` transaction and rechecks aggregate capacity, so two callers
that planned against the same free resources cannot both spend them. An active
caller's capacity ceiling cannot be enlarged by a later optimistic caller.
Uncertain reservations continue consuming capacity. Registry errors refuse new
work; there is no fallback to an independent private pool.

GPU reservations use the physical `GPU-...` UUID. CUDA ordinal `cuda:0` can name
different GPUs in different processes, so ordinal-only GPU execution is refused
at admission. The child environment is pinned to the admitted UUID and receives
the corresponding logical device. MIG partitions still require a separately
qualified inventory/admission adapter.

## Ownership and recovery

Use the existing synchronous context around the **awaited owned subprocess and
its cleanup**:

```python
with reserve_worker(execution_plan) as lease:
    result = await run_subprocess(command, env=worker_env(execution_plan))
```

The shared `apexa_subprocess.run` runner automatically uses the task-local
`attach_current_child(pid, process_group=...)` seam to record each local child
immediately after spawn. It is a no-op outside a reservation. Custom subprocess
owners must call the same seam and uphold the cleanup contract. Every child PID,
start time, boot identity and optional
process group is retained; a subsequent spawn cannot overwrite an earlier
ownership record. If attachment fails, the subprocess owner must stop and reap
the child before propagating the error. A very short child may already be reaped
when attachment occurs: its launcher-known POSIX session group is still recorded
and checked for surviving descendants.

Normal context exit releases the reservation only after the caller has finished
cleanup and attached children/groups are verified absent. An attached live or
uninspectable child/group changes the reservation to `uncertain`. A caller whose
adapter cannot establish stopped execution can explicitly call
`lease.mark_uncertain(reason)` to prevent normal release.

The registry records the owner PID **and process start/boot identity**, rather
than assuming a reused PID belongs to the original worker. On reconciliation, a
lost owner becomes `uncertain`; it does not expire or free capacity automatically.
Its local children or remote jobs may still be running. This deliberately trades
availability for avoiding duplicate resource admission after a crash.

An operator or a qualified supervisor can inspect and resolve the reservation:

```python
from apexa_compute import resource_registry

registry = resource_registry()
registry.reconcile()                 # identifies lost owners, never frees them
reservations = registry.list()
# Verify the recorded child groups, external execution receipts and artifacts.
registry.release_verified(reservation_id,
    evidence="verified process groups stopped; external job receipt confirms termination")
```

The evidence string records an operator/supervisor assertion; it is not an
automatic proof. Do not expose this method as an unauthenticated or generic
"clear busy resources" button. Releasing a reservation does not kill processes,
cancel jobs or retry scientific work. Durable job reconciliation and resource
reconciliation are separate responsibilities.

## Scope and limits

Local plans report `admission_scope="same_host_registry"`. Remote plans report
`controller_host_only`: requests from cooperating processes on one controller
are coordinated, but this does not reserve capacity against other controllers
or services on the remote machine. SSH aliases are not a remote scheduler.
Remote/facility execution still requires a host-owned worker or Slurm/PBS/other
qualified scheduler that owns allocation and process lifetime there.

The registry is admission accounting, not a cgroup/container memory limit. It
does not enforce RSS or VRAM ceilings against a misbehaving engine, and workloads
outside APEXA may consume resources after inventory sampling. Capacity estimates
remain conservative: available-memory telemetry can already include running
APEXA workers, and subtracting reservations can under-admit until those workers
finish. Profiles and scheduler quotas should be calibrated on the actual host.

The reservation contract must surround the whole process lifetime. Detached
workers must transfer ownership to a qualified supervisor instead of leaving
this context while work continues. Descendants that deliberately escape an
owned process group require a container/cgroup/scheduler boundary; a local PID
registry alone cannot prove their termination.

## Validation

```sh
python -m pytest -q tests/test_resource_registry.py tests/test_compute.py tests/test_async_execution.py
```

The tests launch only tiny local Python children. Independent processes race
for CPU, RAM and the same GPU UUID under different CUDA ordinals. Other cases
cover dead owners with surviving children, explicit stopped-execution evidence,
unavailable databases, multiple recorded children and already-reaped children.
No numerical engines, GPUs or remote machines are exercised by these tests.
