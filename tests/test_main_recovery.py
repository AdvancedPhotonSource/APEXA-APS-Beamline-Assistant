"""A host-recovery guardian must never retry scientific or interrupted outcomes."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('recover_main', Path(__file__).resolve().parents[1] /
                                             'benchmark/harness_comparison/recover_main.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def stopped(tmp_path):
    tmp_path = tmp_path / 'study/revisions/r5'
    entries = [dict(schedule_index=i, arm='A1', scan=261, trial=i) for i in (1, 2, 3)]
    schedule = dict(entries=entries, run_count=3)
    for name in ('setup/main_schedule.json', 'results/main/schedule.json'):
        write(tmp_path / name, schedule)
    raw = (json.dumps(dict(entries[0], outcome='timeout')) + '\n').encode()
    (tmp_path / 'results/main/runs.jsonl').write_bytes(raw)
    write(tmp_path / 'results/main/started/0001.json', entries[0])
    write(tmp_path / 'results/main/progress.json', dict(completed=1, scheduled=3, active=None))
    log = tmp_path / 'setup/main.log'
    log.write_text('1/3: A1 261 timeout\nSTOPPING: host not fit for a trial after 900s: '
                   'load 303.5 on 96 cores. No trial was run on it; '
                   'fix the host and rerun with the same seed.\n')
    launch = dict(status='failed', phase='comparison-main', exit_code=1,
                  supervisor_pid=100, worker_pid=101, log=str(log))
    return tmp_path, launch, raw, entries


def check(stopped):
    root, launch, raw, _ = stopped
    return recovery.recovery_checkpoint(root, launch, raw, is_alive=lambda pid: False)


def test_resume_at_next_unstarted_trial_preserves_timeout(stopped):
    result = check(stopped)
    assert result['retained_trials'] == 1
    assert result['next_entry']['schedule_index'] == 2
    assert (stopped[0] / 'results/main/runs.jsonl').read_bytes() == stopped[2]


@pytest.mark.parametrize('mutation', ['started', 'active', 'partial', 'changed', 'duplicate',
                                     'different_failure', 'trailing_failure', 'wrong_phase',
                                     'completed', 'wrong_schedule', 'missing_started', 'wrong_started'])
def test_unsafe_recovery_is_rejected(stopped, mutation):
    root, launch, raw, entries = stopped
    runs = root / 'results/main/runs.jsonl'
    if mutation == 'started':
        write(root / 'results/main/started/0002.json', entries[1])
    elif mutation == 'active':
        write(root / 'results/main/progress.json', dict(completed=1, scheduled=3, active=entries[1]))
    elif mutation == 'partial':
        runs.write_bytes(raw + b'{')
    elif mutation == 'changed':
        runs.write_bytes(raw.replace(b'timeout', b'correct'))
    elif mutation == 'duplicate':
        runs.write_bytes(raw + raw)
    elif mutation == 'different_failure':
        Path(launch['log']).write_text('RuntimeError: failed fit\n')
    elif mutation == 'trailing_failure':
        p = Path(launch['log']); p.write_text(p.read_text() + 'RuntimeError: changed source\n')
    elif mutation == 'wrong_phase':
        launch['phase'] = 'verify'
    elif mutation == 'completed':
        launch['status'] = 'completed'
    elif mutation == 'wrong_schedule':
        write(root / 'results/main/schedule.json', dict(entries=entries[::-1], run_count=3))
    elif mutation == 'missing_started':
        (root / 'results/main/started/0001.json').unlink()
    elif mutation == 'wrong_started':
        write(root / 'results/main/started/0001.json', entries[1])
    with pytest.raises(RuntimeError):
        check(stopped)


@pytest.mark.parametrize('live_pid', [100, 101])
def test_alive_previous_process_prevents_recovery(stopped, live_pid):
    root, launch, raw, _ = stopped
    with pytest.raises(RuntimeError, match='still alive'):
        recovery.recovery_checkpoint(root, launch, raw, is_alive=lambda pid: pid == live_pid)


def test_guardian_resumes_once_and_attaches_reporting(stopped, monkeypatch):
    root, launch, raw, entries = stopped
    base = root.parent / 'r4'
    write(base / 'results/baseline-main/runs.jsonl', {'unchanged': True})
    for name in ('setup/frozen.json', 'setup/qualification.json', 'setup/main_admission.json'):
        write(root / name, {'unchanged': True})
    source = root / 'source/benchmark/harness_comparison/main_supervisor.py'
    source.parent.mkdir(parents=True)
    source.write_text('# frozen\n')
    write(root / 'setup/main-launch.json', launch)
    reports = root.parent.parent / 'reports'
    reports.mkdir()
    commands = []

    class Process:
        pid = 200
        def poll(self):
            return 0
        def wait(self, timeout):
            return 0

    def launch_process(command, **kwargs):
        commands.append(command)
        if command[1].endswith('main_supervisor.py'):
            # Simulate the existing frozen runner resuming its unstarted suffix.
            suffix = b''.join((json.dumps(dict(e, outcome='correct')) + '\n').encode()
                              for e in entries[1:])
            (root / 'results/main/runs.jsonl').write_bytes(raw + suffix)
            write(root / 'setup/main-launch.json', dict(status='completed', supervisor_pid=200))
        return Process()

    monkeypatch.setattr(recovery.subprocess, 'Popen', launch_process)
    monkeypatch.setattr(recovery, 'alive', lambda pid: False)
    # The function's default callable is bound at definition time.
    original = recovery.recovery_checkpoint
    monkeypatch.setattr(recovery, 'recovery_checkpoint',
                        lambda r, l, p: original(r, l, p, is_alive=lambda pid: False))
    state_dir = reports / 'recovery'
    args = SimpleNamespace(root=root, baseline_root=base, state_dir=state_dir,
                           baseline_sha256=recovery.digest(base / 'results/baseline-main/runs.jsonl'),
                           reporting_dir=reports / 'code')
    recovery.supervise(args)
    state = recovery.read(state_dir / 'status.json')
    assert state['status'] == 'completed' and state['completed'] == 3
    assert len(state['recoveries']) == 1 and len(commands) == 2
    assert commands[1][1].endswith('report_after_main.py')
    assert (root / 'results/main/runs.jsonl').read_bytes().startswith(raw)
    assert recovery.read(state_dir / 'resume-001/previous-main-launch.json') == launch
    assert recovery.read(state_dir / 'resume-001/checkpoint.json')['next_entry'] == entries[1]
