#!/usr/bin/env python3
"""Resume only an identified between-trial host-preflight stop.

Run this outside the frozen experiment. All scientific outcomes remain terminal;
arbitrary failures, interrupted trials and missing supervisors require review.
The frozen supervisor rechecks admission, source/input hashes and the unchanged
host limits. A fresh reporting watcher accompanies each recovered supervisor.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


HOST_STOP = re.compile(
    r"STOPPING: host not fit for a trial after 900s: .+\. No trial was run "
    r"on it; fix the host and rerun with the same seed\."
)


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Detached Linux children can briefly remain as zombies before init reaps them.
    status = Path(f'/proc/{pid}/status')
    if status.exists() and re.search(r'^State:\s+Z', status.read_text(), re.M):
        return False
    return True


def terminal_records(root, prefix=b''):
    path = root / 'results/main/runs.jsonl'
    raw = path.read_bytes() if path.exists() else b''
    if not raw.startswith(prefix) or (raw and not raw.endswith(b'\n')):
        raise RuntimeError('Previously retained outcomes changed or record is partial')
    rows = [json.loads(line) for line in raw.splitlines()]
    schedule = read(root / 'setup/main_schedule.json')
    if read(root / 'results/main/schedule.json') != schedule:
        raise RuntimeError('Saved and frozen schedules differ')
    entries = schedule['entries']
    if len(rows) > len(entries):
        raise RuntimeError('Too many terminal records')
    for row, entry in zip(rows, entries):
        if any(row[key] != entry[key] for key in ('schedule_index', 'arm', 'scan', 'trial')):
            raise RuntimeError('Terminal records are not the unchanged schedule prefix')
    return raw, rows, entries


def recovery_checkpoint(root, launch, prefix=b'', is_alive=alive):
    if launch.get('status') != 'failed' or launch.get('phase') != 'comparison-main':
        raise RuntimeError('Only a failed main host preflight can be resumed')
    if not launch.get('exit_code'):
        raise RuntimeError('Missing nonzero worker exit status')
    if any(is_alive(launch.get(key)) for key in ('supervisor_pid', 'worker_pid')):
        raise RuntimeError('Previous supervisor or worker is still alive')
    log = Path(launch['log']).resolve()
    if not log.is_relative_to((root / 'setup').resolve()):
        raise RuntimeError('Unexpected main log location')
    lines = log.read_text().rstrip().splitlines()
    if not lines or not HOST_STOP.fullmatch(lines[-1]):
        raise RuntimeError('Failure is not the exact between-trial host-preflight stop')
    raw, rows, entries = terminal_records(root, prefix)
    progress = read(root / 'results/main/progress.json')
    if (progress.get('active') is not None or progress.get('completed') != len(rows)
            or progress.get('scheduled') != len(entries) or len(rows) == len(entries)):
        raise RuntimeError('Not an incomplete cohort at a confirmed trial boundary')
    started = sorted((root / 'results/main/started').glob('*.json'))
    if len(started) != len(rows):
        raise RuntimeError('A started trial lacks a terminal record')
    for path, entry in zip(started, entries):
        record = read(path)
        if path.stem != f"{entry['schedule_index']:04d}" or any(
                record[key] != entry[key] for key in ('schedule_index', 'arm', 'scan', 'trial')):
            raise RuntimeError('Started records disagree with the schedule')
    return {'retained_trials': len(rows), 'retained_sha256': hashlib.sha256(raw).hexdigest(),
            'next_entry': entries[len(rows)], 'reason': lines[-1]}


def supervise(args):
    root, baseline = args.root.resolve(), args.baseline_root.resolve()
    state_dir = args.state_dir.resolve()
    if any(state_dir.is_relative_to(p) for p in (root, baseline)):
        raise RuntimeError('Recovery state must be outside frozen studies')
    state_dir.mkdir(parents=True, exist_ok=False)
    # One guardian per frozen revision, even if a second invocation uses a new state directory.
    lock = (root.parent.parent / 'reports' / f'{root.name}-recovery.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = {'pid': os.getpid(), 'started_unix': time.time(), 'recoveries': [],
             'code_sha256': digest(Path(__file__)), 'root': str(root)}
    receipt = state_dir / 'status.json'

    def update(**changes):
        state.update(changes, updated_unix=time.time())
        atomic(receipt, state)

    try:
        pins = {str(root / name): digest(root / name) for name in (
            'setup/frozen.json', 'setup/qualification.json', 'setup/main_admission.json',
            'setup/main_schedule.json', 'source/benchmark/harness_comparison/main_supervisor.py')}
        baseline_file = baseline / 'results/baseline-main/runs.jsonl'
        pins[str(baseline_file)] = args.baseline_sha256
        atomic(state_dir / 'pins.json', pins)
        prefix, _, _ = terminal_records(root)
        atomic(state_dir / 'initial-records.json', {'bytes': len(prefix),
               'sha256': hashlib.sha256(prefix).hexdigest()})
        while True:
            if any(digest(Path(path)) != expected for path, expected in pins.items()):
                raise RuntimeError('Pinned study/admission/baseline bytes changed')
            launch = read(root / 'setup/main-launch.json')
            if alive(launch.get('supervisor_pid')):
                update(status='watching', supervisor_pid=launch['supervisor_pid'],
                       progress=read(root / 'results/main/progress.json'))
                time.sleep(30)
                continue
            if launch.get('status') == 'completed':
                raw, rows, entries = terminal_records(root, prefix)
                if len(rows) != len(entries):
                    raise RuntimeError('Supervisor claims completion for an incomplete cohort')
                update(status='completed', completed=len(rows),
                       terminal_sha256=hashlib.sha256(raw).hexdigest(),
                       scientific_review_required=True)
                return
            checkpoint = recovery_checkpoint(root, launch, prefix)
            prefix = (root / 'results/main/runs.jsonl').read_bytes()
            attempt = state_dir / f"resume-{len(state['recoveries']) + 1:03d}"
            attempt.mkdir()
            atomic(attempt / 'previous-main-launch.json', launch)
            atomic(attempt / 'previous-progress.json', read(root / 'results/main/progress.json'))
            atomic(attempt / 'checkpoint.json', checkpoint)
            # Re-read before mutation, in case another operator already resumed the study.
            if read(root / 'setup/main-launch.json') != launch:
                raise RuntimeError('Main launch changed during recovery inspection')
            command = [sys.executable, str(root / 'source/benchmark/harness_comparison/main_supervisor.py'),
                       '--root', str(root), '--baseline-root', str(baseline),
                       '--wait-for', str(root / 'setup/scientific-ready.json')]
            with (attempt / 'supervisor.log').open('x') as stream:
                child = subprocess.Popen(command, cwd=root, stdout=stream,
                                         stderr=subprocess.STDOUT, start_new_session=True)
            recovery = dict(checkpoint, supervisor_pid=child.pid, started_unix=time.time(),
                            directory=str(attempt))
            state['recoveries'].append(recovery)
            update(status='starting_recovery')
            for _ in range(30):
                if read(root / 'setup/main-launch.json').get('supervisor_pid') == child.pid:
                    break
                if child.poll() is not None:
                    raise RuntimeError('Recovered supervisor exited before writing its receipt')
                time.sleep(1)
            else:
                raise RuntimeError('Recovered supervisor has not written its receipt')
            report = [sys.executable, str(args.reporting_dir / 'report_after_main.py'),
                      '--root', str(root), '--baseline-root', str(baseline),
                      '--output', str(attempt / 'analysis'), '--state', str(attempt / 'analysis-status.json')]
            with (attempt / 'reporting.log').open('x') as stream:
                reporter = subprocess.Popen(report, stdout=stream, stderr=subprocess.STDOUT,
                                            start_new_session=True)
            recovery['reporter_pid'] = reporter.pid
            update(status='watching')
            # Avoid unreaped owned children. Non-owned initial supervisors are monitored above.
            while child.poll() is None:
                update(status='watching', supervisor_pid=child.pid,
                       progress=read(root / 'results/main/progress.json'))
                time.sleep(30)
            if reporter.wait(timeout=300):
                raise RuntimeError('Recovery reporting watcher failed; inspect its receipt')
    except Exception as exc:
        update(status='manual_review_required', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--baseline-root', type=Path, required=True)
    parser.add_argument('--baseline-sha256', required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--reporting-dir', type=Path, required=True)
    supervise(parser.parse_args())


if __name__ == '__main__':
    main()
