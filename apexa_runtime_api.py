"""Single-operator run and trace inspection; no arbitrary worker submission."""
from fastapi import APIRouter, HTTPException
from apexa_jobs import default_job_store
from apexa_observability import trace_store

router = APIRouter(prefix="/api")


@router.get('/remote-jobs')
def remote_jobs():
    import json
    from apexa_slurm import coordinator_root
    rows = []
    for path in coordinator_root().glob('*.json'):
        try:
            record = json.loads(path.read_text())
            rows.append({"job_id": record["job_id"], "host": record["target"]["ssh_alias"],
                         "created": record["created"], "cancellation_status": record["cancellation"],
                         "result": record.get("result"), "status": record.get("result", {}).get("status", "unknown")})
        except (ValueError, OSError, KeyError):
            continue
    return {"jobs": sorted(rows, key=lambda row: row["created"], reverse=True)}


@router.get('/remote-jobs/{job_id}')
async def remote_job(job_id: str):
    from apexa_slurm import request_remote
    try:
        return await request_remote("status", job_id=job_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(502, "Remote job status is unavailable; no work was retried") from exc


@router.post('/remote-jobs/{job_id}/cancel')
async def cancel_remote_job(job_id: str):
    from apexa_slurm import request_remote
    try:
        return await request_remote("cancel", job_id=job_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(502, "Remote cancellation was not confirmed; inspect scheduler state") from exc


@router.get('/resources/reservations')
def resource_reservations():
    from apexa_compute import resource_registry
    return {"reservations": resource_registry().list(),
            "scope": "cooperating_processes_on_this_controller"}


def _call(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, "Run not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/runs')
def runs(limit: int = 100):
    return _call(lambda: {"runs": default_job_store().list(limit=limit)})


@router.get('/runs/{run_id}')
def run_status(run_id: str):
    return _call(lambda: default_job_store().get(run_id))


@router.get('/runs/{run_id}/events')
def run_events(run_id: str, after: int = 0, limit: int = 200):
    def read():
        events = default_job_store().events(run_id, after=after, limit=limit)
        return {"events": events, "next_cursor": events[-1]['seq'] if events else after}
    return _call(read)


@router.post('/runs/{run_id}/cancel')
def cancel_run(run_id: str):
    return _call(lambda: default_job_store().cancel(run_id))


@router.get('/traces/{trace_id}')
def trace_events(trace_id: str, after: int = 0, limit: int = 200):
    def read():
        events = trace_store().events(trace_id, after=after, limit=limit)
        return {"events": events, "next_cursor": events[-1]['seq'] if events else after,
                "telemetry": trace_store().status()}
    return _call(read)
