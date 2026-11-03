"""Scheduler fault cases on isolated local files; no SSH or Slurm service."""
import getpass
import json
from pathlib import Path

import pytest

import apexa_slurm as S


@pytest.fixture
def supervisor(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "params.txt").write_text("Lsd 100\n")
    (data / "frame.h5").write_bytes(b"closed fixture")
    engine = tmp_path / "MIDAS/FF_HEDM/workflows/integrator.py"
    engine.parent.mkdir(parents=True)
    engine.write_text("# fixture")
    policy = {"run_root": str(tmp_path / "runs"), "data_roots": [str(data)],
              "apexa_root": str(tmp_path), "python": str(tmp_path / "python"), "midas_path": str(tmp_path / "MIDAS"),
              "engine_sha256": S.digest_file(engine), "cpus": 4, "memory_gb": 8, "minutes": 10}
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy))
    calls = []
    def run(argv):
        calls.append(argv)
        return "1234\n" if argv[0] == "sbatch" else ""
    runtime = S.SlurmSupervisor(S.load_policy(policy_path), policy_path, run=run)
    args = {"parameter_file": str(data / "params.txt"), "images": [str(data / "frame.h5")],
            "dark_source": "none", "compute_target": "remote", "n_cpus": 8, "n_parallel": 10}
    return runtime, args, calls


def test_duplicate_submission_is_owned_once_and_budgeted(supervisor):
    runtime, args, calls = supervisor
    first = runtime.submit(args)
    assert first == runtime.submit(args)
    assert len(calls) == 1 and "--cpus-per-task=4" in calls[0]
    record = json.loads((runtime.path(first["job_id"]) / "job.json").read_text())
    assert record["frozen"]["arguments"]["n_cpus"] == 4
    assert record["frozen"]["arguments"]["n_parallel"] == 1
    assert first["scientific_verdict"] == "unassessed"


def test_lost_sbatch_reply_is_recovered_by_identity_without_resubmission(supervisor):
    runtime, args, calls = supervisor
    def lost(argv):
        calls.append(argv)
        raise TimeoutError("reply lost")
    runtime.run = lost
    first = runtime.submit(args)
    assert first["status"] == "unknown"
    runtime.submit(args)
    assert len(calls) == 1
    runtime.run = lambda argv: f"4321|apexa-{first['job_id']}|{getpass.getuser()}|RUNNING\n"
    recovered = runtime.status(first["job_id"])
    assert recovered["scheduler_id"] == "4321" and recovered["status"] == "running"


def test_scheduler_completion_needs_intact_worker_artifacts(supervisor):
    runtime, args, _ = supervisor
    job = runtime.submit(args)["job_id"]
    runtime.run = lambda argv: f"1234|apexa-{job}|{getpass.getuser()}|COMPLETED|0:0\n"
    assert runtime.status(job)["status"] == "unknown"
    folder = runtime.path(job)
    output = folder / "outputs/result.xy"
    output.parent.mkdir()
    output.write_text("1 2\n")
    S.atomic_json(folder / "result.json", {"job_id": job, "state": "completed", "result": {"status": "success"},
                                         "outputs": {"result.xy": S.digest_file(output)}})
    assert runtime.status(job)["status"] == "success"
    output.write_text("1 9\n")
    assert runtime.status(job)["status"] == "unknown"


def test_cancel_is_a_request_until_scheduler_confirms(supervisor):
    runtime, args, calls = supervisor
    job = runtime.submit(args)["job_id"]
    def running(argv):
        calls.append(argv)
        return f"1234|apexa-{job}|{getpass.getuser()}|RUNNING\n" if argv[0] != "scancel" else ""
    runtime.run = running
    receipt = runtime.status(job, cancel=True)
    assert receipt["cancellation_status"] == "requested" and receipt["status"] == "running"
    assert ["scancel", "1234"] in calls
    runtime.run = lambda argv: f"1234|apexa-{job}|{getpass.getuser()}|CANCELLED|0:15\n"
    assert runtime.status(job)["cancellation_status"] == "stopped"


def test_foreign_scheduler_identity_never_authorizes_job_adoption(supervisor):
    runtime, args, _ = supervisor
    runtime.run = lambda argv: (_ for _ in ()).throw(TimeoutError())
    job = runtime.submit(args)["job_id"]
    runtime.run = lambda argv: f"4321|apexa-{job}|different-user|RUNNING\n"
    result = runtime.status(job)
    assert result["scheduler_id"] is None and result["status"] == "unknown"


def test_outside_proposal_inputs_and_engine_drift_refuse_before_submit(supervisor, tmp_path):
    runtime, args, calls = supervisor
    outside = tmp_path / "private.h5"
    outside.write_text("private")
    with pytest.raises(ValueError, match="proposal"):
        runtime.submit({**args, "images": [str(outside)]})
    Path(runtime.policy["midas_path"], "FF_HEDM/workflows/integrator.py").write_text("changed")
    with pytest.raises(ValueError, match="engine"):
        runtime.submit(args)
    assert not calls


@pytest.mark.asyncio
async def test_verified_external_receipt_reconciles_common_job(supervisor, tmp_path, monkeypatch):
    from apexa_jobs import JobStore
    store = JobStore(tmp_path / "jobs.sqlite3")
    receipt = supervisor[0].submit(supervisor[1])
    job = store.submit({"tool": "midas_integrate_series"})
    store.run_one(lambda payload, context: receipt, run_id=job)
    monkeypatch.setattr("apexa_jobs.default_job_store", lambda: store)
    async def status(*args, **kwargs):
        return {**receipt, "status": "success", "execution": "completed"}
    monkeypatch.setattr(S, "request_remote", status)
    await S.reconcile_jobs()
    assert store.get(job)["state"] == "completed"


@pytest.mark.asyncio
async def test_remote_integration_dispatches_before_local_path_checks(monkeypatch):
    import midas_comprehensive_server as midas
    monkeypatch.setenv("APEXA_SLURM_CONFIG", "/fixture/target.json")
    seen = []
    async def submit(action, **kwargs):
        seen.append((action, kwargs))
        return {"status": "submitted", "job_id": "a" * 64, "supervisor": "slurm"}
    monkeypatch.setattr(S, "request_remote", submit)
    result = json.loads(await midas.midas_integrate_series("/data/only-on-remote/params.txt",
        images=["/data/only-on-remote/frame.h5"], compute_target="remote"))
    assert result["status"] == "submitted" and seen[0][0] == "submit"


@pytest.mark.parametrize('change', [
    {'unexpected': 'field'}, {'n_cpus': True}, {'n_parallel': -1},
    {'dark_source': 'guess'}, {'r_bin_size': float('inf')},
    {'parameter_file': 'relative/params.txt'}, {'dark_file': 'relative/dark.h5'},
])
def test_invalid_remote_arguments_never_submit(supervisor, change):
    runtime, args, calls = supervisor
    with pytest.raises(ValueError):
        runtime.submit({**args, **change})
    assert not calls


def test_admission_id_cannot_repeat_after_inputs_or_policy_change(supervisor):
    runtime, args, calls = supervisor
    job = 'b' * 64
    first = runtime.submit(args, job_id=job)
    Path(args['images'][0]).write_bytes(b'changed after admission')
    runtime.policy['minutes'] += 1
    assert runtime.submit(args, job_id=job) == first
    assert len(calls) == 1
    with pytest.raises(ValueError, match='different arguments'):
        runtime.submit({**args, 'n_cpus': 2}, job_id=job)


@pytest.mark.asyncio
async def test_lost_ssh_response_keeps_durable_job_identity(supervisor, tmp_path, monkeypatch):
    from types import SimpleNamespace
    runtime, args, scheduler_calls = supervisor
    monkeypatch.setenv('APEXA_HOME', str(tmp_path / 'coordinator'))
    target = {'ssh_alias': 'approved-host', 'python': '/usr/bin/python',
              'worker_script': '/opt/apexa/scripts/slurm_worker.py', 'config_file': '/etc/apexa/slurm.json'}
    monkeypatch.setattr(S, 'load_target', lambda: target)
    actions = []
    async def transport(argv, *, input, timeout):
        req = json.loads(input)
        actions.append(req['action'])
        if req['action'] == 'submit':
            runtime.submit(req['arguments'], job_id=req['job_id'])
            raise TimeoutError('SSH disconnected after acceptance')
        return SimpleNamespace(returncode=0, stdout=json.dumps(runtime.status(req['job_id'])))
    monkeypatch.setattr('apexa_subprocess.run', transport)
    first = await S.request_remote('submit', arguments=args)
    assert first['status'] == 'unknown' and len(first['job_id']) == 64
    assert (S.coordinator_root() / (first['job_id'] + '.json')).exists()
    second = await S.request_remote('submit', arguments=args)
    assert second['job_id'] == first['job_id'] and second['scheduler_id'] == '1234'
    assert actions == ['submit', 'status']
    assert sum(c[0] == 'sbatch' for c in scheduler_calls) == 1
