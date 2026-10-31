"""Publish completed powder artifacts to the directory requested by the user.

The follower reads job state, copies artifacts and generates an HTML report.
It never submits or resumes a scientific calculation. Requests survive WebUI
restarts; copies and reports are additive.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from apexa_refinement_live import job_rows, publication_request


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, indent=2, allow_nan=False))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source, target):
    digest = _hash(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and _hash(target) != digest:
        # Preserve an earlier export or a user-edited copy.
        target = target.with_name(f"{target.stem}-{digest[:12]}{target.suffix}")
    if not target.exists():
        temp = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temp)
            if _hash(temp) != digest:
                raise RuntimeError(f"Artifact changed during export: {source.name}; retry publication")
            try:
                os.link(temp, target)  # atomic publication without overwriting
            except FileExistsError:
                if _hash(target) != digest:
                    raise RuntimeError(f"Export destination changed: {target}")
        finally:
            temp.unlink(missing_ok=True)
    elif _hash(target) != digest:
        raise RuntimeError(f"Export destination was edited: {target}")
    return {"source": str(source), "path": str(target), "sha256": digest, "bytes": target.stat().st_size}


def report_path(request):
    group = request['manifest_name'].removesuffix('.outputs.json')
    return Path(request['output_dir']).expanduser().resolve() / f'powder_report_{group}.html'


def default_report_path(job_id, jobs_dir):
    found = publication_request(job_id, jobs_dir)
    if found is None:
        raise ValueError('No submission output_dir is recorded for this job; supply output_path explicitly')
    _, request = found
    return Path(request['output_dir']).expanduser().resolve() / f'powder_report_{job_id}.html'


def write_report(manifest, path, *, source_path=''):
    """Render recorded results without changing the scientific artifacts."""
    from apexa_report import build_report, render_html
    spec = build_report(manifest, technique='powder-refinement', source_path=source_path)
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temp.write_text(render_html(spec), encoding='utf-8')
        record = _copy(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    record.pop('source', None)
    return {**record, 'state': 'published', 'verdict': spec.verdict,
            'summary': spec.verdict_note, 'generated_at': spec.generated_at}


def publish(request, previous=None):
    root = Path(request['store']).expanduser().resolve()
    output = Path(request['output_dir']).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    records = []
    cached = {r['job_id']: r for r in (previous or {}).get('jobs', [])}
    data = job_rows(root, request['job_ids'])
    for job in data['jobs']:
        prior = cached.get(job['id'], {})
        if (prior.get('state') in ('completed', 'failed', 'cancelled') and prior.get('state') == job['state']
                and prior.get('attempt') == job.get('attempt') and prior.get('files')):
            records.append(prior)
            continue
        record = {"job_id": job['id'], "state": job['state'], "attempt": job.get('attempt'), "error": job.get('error'), "files": []}
        if job['state'] in ('completed', 'failed', 'cancelled') and job.get('attempt'):
            source = (root / job['id'] / job['attempt']).resolve()
            if not source.is_relative_to(root):
                raise ValueError('Attempt is outside the job store')
            target = output / job['id']
            record['directory'] = str(target)
            for path in sorted(source.rglob('*')):
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):
                    continue
                if path.name.startswith('.') or path.suffix in ('.tmp', '.junk') or '.bak' in path.name:
                    continue
                record['files'].append(_copy(path, target / path.relative_to(source)))
            inspection = root / 'viewer_evidence' / job['id'] / job['attempt'] / 'final.json'
            if inspection.is_file():
                record['files'].append(_copy(inspection, target / 'final_fit_inspection.json'))
        records.append(record)
    terminal = bool(records) and all(r['state'] in ('completed', 'failed', 'cancelled') for r in records)
    result = {"schema": "apexa-powder-outputs-1", "state": "waiting",
              "updated_at": time.time(), "output_dir": str(output), "jobs": records,
              "report": {"state": "waiting", "path": str(report_path(request))},
              "note": "Scientific results are copied unchanged. Original paths inside result.json retain provenance; this manifest maps them to exported files."}
    if terminal:
        identity = [{k: r.get(k) for k in ('job_id', 'attempt', 'state')} for r in records]
        saved = (previous or {}).get('report') or {}
        try:
            saved_path = Path(saved['path']) if saved.get('path') else None
            if (saved.get('jobs') == identity and saved.get('state') == 'published'
                    and saved_path and saved_path.is_file() and _hash(saved_path) == saved.get('sha256')):
                result['report'] = saved
            else:
                result['report'] = {**write_report(
                    {**data, 'tool': 'refinement_status'}, report_path(request),
                    source_path=str(output / request['manifest_name'])), 'jobs': identity}
            result['state'] = 'published'
        except Exception as exc:
            # Artifact copies remain available even if report rendering fails.
            result['state'] = 'retrying'
            result['report'].update(state='retrying', error=str(exc))
    _write(output / request['manifest_name'], result)
    return result


def follow(request_path, *, wait_for_publisher=False):
    path = Path(request_path).resolve()
    request = json.loads(path.read_text())
    deadline = time.monotonic() + request.get('max_wait_seconds', 86400)
    with path.with_suffix('.lock').open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not wait_for_publisher or time.monotonic() >= deadline:
                    return
                # Allows an older artifact-only publisher to finish before this
                # version attaches report generation to an already running job.
                time.sleep(1)
        status_path = path.with_name('export-status.json')
        previous = None
        if status_path.is_file():
            try:
                previous = json.loads(status_path.read_text())
            except (OSError, ValueError):
                pass
        while time.monotonic() < deadline:
            try:
                state = publish(request, previous)
                previous = state
                _write(status_path, state)
                if state['state'] == 'published':
                    return
            except Exception as exc:
                _write(status_path, {"state": "retrying", "error": str(exc), "output_dir": request['output_dir']})
            time.sleep(5)
        _write(status_path, {"state": "waiting_timeout", "output_dir": request['output_dir'],
                             "error": "Publication wait expired; the scientific job was not changed."})


def start(request_path, *, wait_for_publisher=False):
    request_path = Path(request_path).resolve()
    command = [sys.executable, str(Path(__file__).resolve()), str(request_path)]
    if wait_for_publisher:
        command.append('--wait-for-publisher')
    with request_path.with_name('export.log').open('a') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return process.pid


def queue_export(info, folder, output_dir):
    request = {"store": info['store'], "job_ids": info['job_ids'],
               "output_dir": str(Path(output_dir).expanduser().resolve()),
               "manifest_name": Path(folder).name + '.outputs.json'}
    path = Path(folder) / 'export-request.json'
    try:
        _write(path, request)
        start(path)
        return {"state": "waiting", "output_dir": request['output_dir'],
                "report": {"state": "waiting", "path": str(report_path(request))},
                "manifest": str(Path(request['output_dir']) / request['manifest_name'])}
    except Exception as exc:
        # Submission has already succeeded; an export failure must not invite
        # the caller to resubmit the scientific calculation.
        return {"state": "error", "error": str(exc), "output_dir": request['output_dir']}


def resume_exports(jobs_dir):
    for path in Path(jobs_dir).glob('gsas2-*/export-request.json'):
        try:
            status = path.with_name('export-status.json')
            if status.is_file():
                saved = json.loads(status.read_text())
                if saved.get('state') == 'published' and saved.get('report', {}).get('state') == 'published':
                    continue
            start(path)
        except (OSError, ValueError):
            continue


if __name__ == '__main__':
    follow(sys.argv[1], wait_for_publisher='--wait-for-publisher' in sys.argv[2:])
