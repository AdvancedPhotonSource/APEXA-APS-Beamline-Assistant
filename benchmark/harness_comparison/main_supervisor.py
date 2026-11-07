"""Run the admitted baseline and main cohort once the FF numerical work clears.

This scheduler does not change study sources, budgets or outcomes. Restarting it
uses the study's durable terminal records; it never selectively repeats a trial.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--wait-for', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    state = {'supervisor_pid': os.getpid(), 'started_unix': time.time(),
             'supervisor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'wait_for': str(args.wait_for)}
    receipt = root / 'setup/main-launch.json'

    def update(**changes):
        state.update(changes, updated_unix=time.time())
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2))
        temporary.replace(receipt)

    def run(phase, command):
        update(status='running', phase=phase, command=command)
        log_path = root / 'setup' / f'{phase}-{time.time_ns()}.log'
        with log_path.open('x') as log:
            process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
            update(worker_pid=process.pid, log=str(log_path))
            while True:
                try:
                    status = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    update()
        if status:
            update(status='failed', exit_code=status, finished_unix=time.time())
            raise SystemExit(status)

    code = root / 'source/benchmark/harness_comparison'
    python = os.sys.executable
    run('verify', [python, str(root / 'setup/run-study.py'), 'verify'])
    if not (root / 'setup/main_admission.json').is_file():
        raise RuntimeError('Main admission is missing')
    if not (root / 'setup/baseline/plan.json').is_file():
        run('baseline-prepare', [python, str(code / 'baseline.py'), 'prepare', '--root', str(root)])
    update(status='queued', phase='waiting_for_ff_numerical_work', worker_pid=None)
    while True:
        if args.wait_for.is_file():
            ready = json.loads(args.wait_for.read_text())
            if ready.get('ready_for_comparison') is True:
                break
        time.sleep(30)
        update()
    run('baseline-main', [python, str(code / 'baseline.py'), 'run', '--root', str(root), '--cohort', 'main'])
    run('comparison-main', [python, str(root / 'setup/run-study.py'), 'run', '--cohort', 'main'])
    update(status='completed', phase='completed', exit_code=0, worker_pid=None, finished_unix=time.time())


if __name__ == '__main__':
    main()
