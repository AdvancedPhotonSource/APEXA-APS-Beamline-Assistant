# Slurm execution beside the data

Slurm is the first scheduler adapter, initially for CPU series integration.
Direct SSH tools retain their existing policy. PBS and automatic reconciliation
with other external supervisors are not implemented by this adapter.

Copy `deploy/slurm-target.example.json` into private coordinator configuration
and set `APEXA_SLURM_CONFIG` to its absolute path. Configure an approved SSH
alias, remote APEXA Python, `scripts/slurm_worker.py`, and remote policy file.
Use preconfigured keys and verified SSH host keys.

On the data host, fill `deploy/slurm-policy.example.json` with the actual
account/partition, proposal roots, owned run directory, CPU/RAM/time budget,
APEXA/MIDAS paths, and reviewed integration script SHA-256. The run directory
must be visible to login and compute nodes and support qualified locking and
atomic writes. This example is not a commissioned cluster configuration.

`midas_integrate_series(..., compute_target="remote")` submits before trying to
read data-host paths locally. Inputs must be staged and closed. Use an explicit
shared `dark_file`, embedded dark, or `dark_source="none"`. Omit `result_folder`:
outputs belong in `<run_root>/<job_id>/outputs`. The worker hashes inputs, caps
concurrency to scheduler resources and rechecks bytes around execution.

The coordinator records a stable request ID under `APEXA_HOME/remote_jobs` before
opening SSH; the worker records intent before `sbatch`. Identical arguments and
target configuration find the same admission, even after an input or engine
changes. A lost SSH reply still returns an addressable ID. Status reconciliation
uses `squeue`/`sacct` with full job identity and Unix owner; uncertain work is never
blindly resubmitted. A connection lost before remote admission remains unknown
and requires investigation; it is not automatically retried. For a deliberate
new analysis, stage new immutable input paths. Requeue cannot repeat
a worker that has already recorded its start. Node failures and uncertain
execution require operator investigation.

List durable admissions with `GET /api/remote-jobs`, inspect with
`GET /api/remote-jobs/{job_id}`, and request cancellation with
`POST /api/remote-jobs/{job_id}/cancel`. `scancel` is a request; scheduler state
confirms cancellation. Completion requires the worker receipt and intact output
hashes. Remote host/location remain in the receipt; configure the remote science
viewer separately for those files.

The web process polls coordinator admission records and known shared-workflow
Slurm receipts, waiting 30 seconds between polling cycles, and reconciles
terminal results into JobStore. Review gates remain; successful
execution does not establish independent scientific acceptance. Direct tool
submissions are included in polling; CLI-only deployments use the remote-job API
or worker status command and do not run this periodic web monitor.

Site acceptance: trusted small data, lost SSH reply, coordinator restart, queued
and running cancellation, worker failure and changed outputs. Offline tests
simulate Slurm and verify ownership/deduplication/recovery contracts. They do
not certify a real scheduler, cgroup policy or compute host.
