"""Read-only canvas access to durable refinement jobs and decision evidence.

No JobStore construction, broker startup, GPX loading or scientific calculation
is performed by these HTTP reads.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path

from apexa_powder_contract import outcome_summary

ENGINE_ID = re.compile(r"[0-9a-f]{24}")
GROUP_ID = re.compile(r"gsas2-[0-9a-f]{10}")


def _json(path, limit=64 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise ValueError("Refinement artifact exceeds the viewer size limit")
    return json.loads(path.read_text())


def _safe(value):
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _within(path, root):
    path, root = path.resolve(), root.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Refinement artifact is outside its job store")
    return path


def job_rows(root, ids, *, include_decision_events=False):
    root = Path(root).expanduser().resolve()
    if not ids or len(ids) > 1000 or any(not ENGINE_ID.fullmatch(i) for i in ids):
        raise ValueError("Invalid engine job identifiers")
    database = root / "jobs.sqlite3"
    if not database.is_file():
        raise FileNotFoundError("Refinement job store is unavailable")
    jobs = []
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        for job_id in ids:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError(f"Unknown refinement job {job_id}")
            job = dict(row)
            job["payload"] = json.loads(job["payload"])
            if job.get("result_path"):
                path = _within(Path(job["result_path"]), root)
                if path.is_file():
                    job["result"] = _json(path)
            if job.get("attempt"):
                attempt = _within(root / job_id / job["attempt"], root)
                for name, key in (("progress.json", "progress"), ("decision_stream/state.json", "decision")):
                    path = _within(attempt / name, root)
                    if path.is_file():
                        try:
                            job[key] = _json(path)
                            if key == "decision" and not include_decision_events:
                                job[key].pop("events", None)
                        except (ValueError, OSError) as exc:
                            job[f"{key}_error"] = str(exc)
                inspection = _within(root / "viewer_evidence" / job_id / job["attempt"] / "final.json", root)
                if inspection.is_file() and job["state"] == "completed":
                    job["inspection"] = {"snapshot": "inspection-final", "scope": "Generated after completion; not recorded decision evidence"}
            job["heartbeat_age_seconds"] = max(0, time.time() - job["heartbeat"]) if job.get("heartbeat") else None
            jobs.append(job)
    return _safe({"jobs": jobs})


def _resolve(job_id):
    root = Path(os.environ.get("APEXA_GSAS2_JOBS", Path.home() / ".apexa" / "gsas2_jobs")).expanduser().resolve()
    if ENGINE_ID.fullmatch(job_id):
        return root / "engine", [job_id]
    if not GROUP_ID.fullmatch(job_id):
        raise ValueError("Invalid refinement job identifier")
    manifest = _within(root / job_id / "engine.json", root)
    if not manifest.is_file():
        raise FileNotFoundError("No durable engine manifest for this job")
    info = _json(manifest, 2 * 1024 * 1024)
    return Path(info["store"]).expanduser().resolve(), info["job_ids"]


def publication_request(job_id, jobs_dir=None):
    """Find the submitted output directory for either a group or engine ID."""
    root = Path(jobs_dir or os.environ.get('APEXA_GSAS2_JOBS', Path.home() / '.apexa' / 'gsas2_jobs')).expanduser().resolve()
    if GROUP_ID.fullmatch(job_id):
        path = root / job_id / 'export-request.json'
        return (path, _json(path)) if path.is_file() else None
    if not ENGINE_ID.fullmatch(job_id):
        raise ValueError('Invalid refinement job identifier')
    for path in sorted(root.glob('gsas2-*/export-request.json'), reverse=True):
        try:
            request = _json(path)
            if job_id in request.get('job_ids', []):
                return path, request
        except (OSError, ValueError):
            continue
    return None


def publication_status(job_id, jobs_dir=None):
    found = publication_request(job_id, jobs_dir)
    if found is None:
        return None
    path, request = found
    status = path.with_name('export-status.json')
    return _json(status) if status.is_file() else {'state': 'waiting', 'output_dir': request['output_dir']}


def refinement_snapshot(job_id):
    root, ids = _resolve(job_id)
    data = job_rows(root, ids, include_decision_events=True)
    counts = outcome_summary(data)
    terminal = bool(data["jobs"]) and counts["pending"] == 0 and counts["unknown"] == 0
    failed = counts["failed"] + counts["cancelled"]
    state = ("error" if failed == len(ids) else "partial" if failed else "complete") if terminal else "running"
    publication = publication_status(job_id)
    return {**data, "tool": "refinement_status", "job_id": job_id, "status": state,
            "publication": publication,
            "terminal": terminal, "done": counts["completed"], "total": len(ids),
            "outcome_summary": counts, "fetched_at": time.time(), "location": "local"}


def decision_snapshot(job_id, snapshot):
    if not ENGINE_ID.fullmatch(job_id) or not re.fullmatch(r"snapshot-[0-9]{6}|inspection-final", snapshot):
        raise ValueError("Invalid decision snapshot identifier")
    root, ids = _resolve(job_id)
    job = job_rows(root, ids)["jobs"][0]
    if not job.get("attempt"):
        raise FileNotFoundError("This job has no recorded attempt")
    path = (_within(root / "viewer_evidence" / job_id / job["attempt"] / "final.json", root)
            if snapshot == "inspection-final" else
            _within(root / job_id / job["attempt"] / "decision_stream" / f"{snapshot}.json", root))
    value = _json(path)
    if value.get("schema") != "gsas2-decision-snapshot-1":
        raise ValueError("Unsupported decision evidence schema")
    return _safe({**value, "job_id": job_id, "snapshot": snapshot})
