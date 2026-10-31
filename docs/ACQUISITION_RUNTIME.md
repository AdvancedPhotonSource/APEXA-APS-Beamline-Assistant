# Acquisition completion and durable local runs

Automatic acquisition execution now requires an explicit completion manifest.
Quiet-file discovery remains useful for review plans, but `--execute` alone no
longer treats quiet files as authoritative detector closure.

The watcher uses the existing `APEXAPlan` and a common SQLite run store. The
store provides atomic admission, stable run IDs, claim leases, persisted events,
cancellation requests, and conservative restart reconciliation. It does **not**
claim exactly-once execution of external engines. No model or hardware action is
needed to ingest and validate a manifest.

## Producer contract

The beamline writer/data mover closes its files, obtains frame identities, hashes
the closed bytes, and appends a newline-terminated JSON object to an operator
trusted JSONL log. The configured `--producer` must match every event. This is
an operational trust boundary, not a signature or network authentication scheme.
Protect the log and run directory using the beamline service account's filesystem
permissions. Keep the SQLite database on local storage with working locking;
this implementation is not qualified for shared NFS/Box/OneDrive databases.

Example producer code, executed after detector closure:

```python
import json
from apexa_dataset import file_identity

manifest = {
    "schema_version": 1,
    "acquisition_id": "2026-10-28-scan-0042",
    "revision": 1,
    "producer": "detector-writer",
    "closed": True,
    "expected_frames": ["0042:0", "0042:1"],
    "files": [file_identity("/data/scan0042.h5",
                            frame_ids=["0042:0", "0042:1"],
                            data_location="exchange/data")],
    "required_corrections": ["calibration", "dark"],
    "corrections": {
        "calibration": {"revision": 1, "files": [file_identity("/data/params.txt")]},
        "dark": {"revision": 1, "files": [file_identity("/data/dark0042.h5")]},
    },
}
with open("/data/completion.jsonl", "a") as stream:
    stream.write(json.dumps({"event_id": "scan0042-r1", "manifest": manifest}) + "\n")
    stream.flush()
    # The production writer should fsync and publish with its normal durable
    # acquisition protocol before claiming delivery to downstream consumers.
```

Every revision is a **full snapshot**. An open or partial snapshot can be logged,
but cannot execute. `expected_frames` is the authoritative frame order, including
frames inside an HDF5 container. Each detector file has absolute path, byte size,
SHA-256, ordered `frame_ids`, and `data_location` (required for HDF5). Completeness
requires closure, exact frame coverage, representable order and every named
required correction. HDF5 and TIFF frame counts are checked against their file
contents. Plain-text powder observations represent one frame per file; other
detector formats require a qualified adapter.

Late darks require another dataset revision containing their correction identity.
Changed correction bytes require a new correction revision as well. Reusing an
event ID or dataset/correction revision for different content is refused. Events
may arrive out of revision order; only the highest delivered revision can start.
A newer snapshot cancels unstarted jobs from older revisions. Previously running
or completed versions retain their outcomes and separate output directories.

## Running the watcher

```sh
export APEXA_RUN_DIR="$HOME/.apexa/runs"
python apexa_acquisition_watcher.py /data \
  --events /data/completion.jsonl --producer detector-writer \
  --dark-source file --dark-location exchange/data --execute
```

`--run-dir` overrides `APEXA_RUN_DIR`; the default is `~/.apexa/runs`. The web
service and watcher must use the same directory to share run status. Review plans
are written to `<run-dir>/plans/<dataset-version>.yaml`. Results are placed in a
dataset-version-specific directory under `--result-folder` or `APEXA_results`.

The supported execution slice is WAXS/SAXS/integration and powder submission.
HEDM/calibration still require their reviewed technique workflows. The integration
adapter currently supports whole files in lexical path order, one common HDF5
data location, and one explicitly identified shared dark. HDF5 darks require an
explicit `--dark-location` that is checked in the file. Embedded HDF5 darks also
require that explicit location and are bound by the whole detector-file hash.
Unknown correction kinds and detector filenames the adapter would silently
exclude remain pending rather than producing an incomplete execution. Arbitrary frame
subsets, per-frame dark maps and mixed container layouts remain pending with an
explanation. Powder mode requires identified `cif` correction files and preserves
the acquisition frame IDs. A manifest must identify the calibration file; CLI
parameters cannot silently replace it.

The cursor records byte offset, file identity and a digest of consumed bytes.
Incomplete trailing lines wait for the next poll. Cursor updates and ingested
snapshots commit together; snapshot-to-run admission is another atomic transaction
that is reconsidered after restart. Duplicate event delivery creates no new run.
Log truncation, replacement and modification of consumed bytes are refused. For
intentional log rotation, pass a new `--event-source`; event identities continue
to deduplicate across logs. The current local implementation checks the consumed
prefix on each poll, so rotate logs to bound this I/O cost.

Input bytes are verified at readiness, again before execution, and after the
adapter returns. This adds sequential hashing I/O and should run beside the data.
Producers must retain immutable snapshots through the run: hash verification
cannot atomically protect a file another process is concurrently overwriting.
Post-execution changes invalidate the receipt. A future immutable object-store
adapter could avoid redundant scans using its trusted version identity.

## Job API and recovery

`apexa_jobs.default_job_store()` uses `APEXA_RUN_DIR`. Its methods are:

| Method | Result |
| --- | --- |
| `submit(payload, idempotency_key=None, trace_id=None)` | Stable `run-<sha256>` ID; different payload under the same key is refused |
| `get(run_id)` / `status(run_id)` | Persisted execution record, payload, engine receipt and cancellation state |
| `list(limit=100, states=None)` | Most recent persisted records |
| `events(run_id, after=0, limit=500)` | Ordered durable events; use last `seq` as cursor |
| `cancel(run_id)` | Updated record; request is distinct from confirmed stop |
| `reconcile(now=None)` | Expired unstarted claims become queued; expired running claims become uncertain |
| `resolve(run_id, state=..., evidence=..., result=None)` | Record an externally verified terminal outcome without rerunning anything |

`run_one(executor, prepare=None)` is the local worker seam. Preparation has no
external execution effects. A token and renewable lease fence publication from
stale workers. The transition to `running` is persisted **before** invoking an
engine; a crash between these steps can conservatively produce uncertainty even
if the engine never started. Executor exceptions, lost/malformed receipts and
unconfirmed cancellation also become `uncertain`. Failed, refused and uncertain
runs never automatically retry. Recovery requires inspecting engine receipts,
artifacts and process state, then recording evidence with `resolve`. There is no
generic force/retry endpoint that silently duplicates a possibly completed job.

Cancellation status is independent: `not_requested`, `requested`, `acknowledged`,
or `stopped`. Queued/claimed work can be cancelled before execution. Running work
receives a request; the watcher propagates task cancellation and records the
acknowledgement, but does not claim that stopping a local task proves a detached
or remote engine stopped. Explicitly verified stopped receipts can terminate a
run as `cancelled`. External powder submission remains `submitted`, preserving
Agentic-GSAS-II job IDs; it is not scientific completion. The existing powder
status API remains authoritative for the engine, and its result must be verified
before resolving the wrapper run. An automatic powder-to-wrapper reconciliation
adapter is not included in this slice.

`GET /api/runs`, `GET /api/runs/{run_id}` and
`GET /api/runs/{run_id}/events?after=<seq>` can expose these read methods; cancellation
uses the normal authenticated mutation boundary. The store omits private worker
claim tokens. No HTTP endpoint should accept arbitrary executor payloads.
Optional trace IDs link durable events to operational diagnostics. They confer
no authority and the job event log remains the recovery source of truth.

This is a local durable wrapper, not a facility scheduler. It covers the watcher
execution path; migrating other interactive tool calls needs registered adapters,
qualified external recovery probes and matching process ownership contracts.

## Validation

```sh
python -m pytest -q tests/test_acquisition_runtime.py tests/test_plan_and_watcher.py
```

Tests use tiny local synthetic files and injected executors. They cover concurrent
admission/claims, restart before and after possible effects, stale-worker fencing,
duplicate/reordered events, partial writes/data, closure, late dark revisions,
changed contents, HDF5 frame counts, supersession and cancellation distinctions.
They do not execute scientific engines or contact beamline hardware.
