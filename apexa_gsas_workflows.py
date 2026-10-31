"""Durable process supervision for the GSAS-II canvas workbench."""
from pathlib import Path
import argparse
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import time

from apexa_powder import write_json


def submit(python, repo, operations, output_dir, input_gpx=None, max_wall_seconds=900):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100 or any(not isinstance(o, dict) for o in operations):
        raise ValueError("supply 1..100 structured GSAS-II operations")
    if type(max_wall_seconds) not in (int, float) or not math.isfinite(max_wall_seconds) or not 1 <= max_wall_seconds <= 86400:
        raise ValueError("max_wall_seconds must be from 1 to 86400")
    root = Path(output_dir).expanduser().resolve()
    source = str(Path(input_gpx).expanduser().resolve(strict=True)) if input_gpx else None
    root.mkdir(parents=True, exist_ok=False)
    request = dict(python=str(python), repo=str(repo), operations=operations, input_gpx=source,
                   max_wall_seconds=max_wall_seconds, workflow_dir=str(root))
    write_json(root / 'request.json', request)
    write_json(root / 'status.json', dict(status='submitted', execution_status='queued', workflow_dir=str(root), operations=[]))
    try:
        with (root / 'supervisor.log').open('w') as log, (root / 'workflow.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(root / 'request.json')],
                stdout=log, stderr=subprocess.STDOUT, cwd=str(Path(__file__).resolve().parent),
                pass_fds=(lock.fileno(),), start_new_session=True)
        write_json(root / 'supervisor.json', dict(pid=process.pid, started=time.time()))
    except Exception as exc:
        write_json(root / 'status.json', dict(status='error', execution_status='failed', error=str(exc), workflow_dir=str(root)))
        raise
    return dict(status='submitted', workflow_dir=str(root), poll_with='gsas2_workflow_status')


def status(workflow_dir):
    root = Path(workflow_dir).expanduser().resolve(strict=True)
    result = json.loads((root / 'status.json').read_text())
    # Files are the authoritative outcomes. Process existence alone is never
    # treated as successful execution.
    if result.get('execution_status') in ('queued', 'running'):
        with (root / 'workflow.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                result.update(status='interrupted', execution_status='failed', error='supervisor exited without a final result')
    return result


def cancel(workflow_dir):
    root = Path(workflow_dir).expanduser().resolve(strict=True)
    result = status(root)
    if result.get('execution_status') in ('queued', 'running'):
        write_json(root / 'cancel.json', {'requested': time.time()})
        result['stop_requested'] = True
    return result


def stop_process(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The parent can exit while an external runtime still has descendants.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise(request_path):
    root = Path(request_path).resolve().parent
    request = json.loads(Path(request_path).read_text())
    env = dict(os.environ, PYTHONPATH=request['repo'], PYTHONNOUSERSITE='1', OMP_NUM_THREADS='1',
               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', MPLBACKEND='Agg')
    started = time.monotonic()
    process = None
    reason = None
    try:
        with (root / 'worker.log').open('w') as log:
            process = subprocess.Popen([request['python'], '-m', 'agentic_gsas2.workflows', str(request_path)],
                stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(root), start_new_session=True)
            while process.poll() is None:
                if (root / 'cancel.json').exists():
                    reason = 'cancelled'
                    break
                if time.monotonic()-started >= request['max_wall_seconds']:
                    reason = 'wall_clock'
                    break
                time.sleep(.25)
            if reason:
                stop_process(process)
            result = json.loads((root / 'status.json').read_text())
            if reason or result.get('execution_status') in ('running', 'queued'):
                result.update(status='cancelled' if reason == 'cancelled' else 'error',
                    execution_status='cancelled' if reason == 'cancelled' else 'failed',
                    stop_reason=reason or 'worker_exited', error=None if reason == 'cancelled' else reason or f'worker exit {process.returncode}')
                write_json(root / 'status.json', result)
    except Exception as exc:
        stop_process(process)
        result = json.loads((root / 'status.json').read_text())
        write_json(root / 'status.json', {**result, 'status': 'error', 'execution_status': 'failed', 'error': str(exc)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request')
    supervise(parser.parse_args().request)
