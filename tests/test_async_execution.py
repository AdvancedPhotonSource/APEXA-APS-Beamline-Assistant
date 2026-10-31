"""Process lifetime, resource admission and series concurrency regression tests.

Only tiny local Python children and stubbed scientific engines are executed.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

import apexa_compute as C
from apexa_subprocess import run


@pytest.mark.asyncio
async def test_async_children_overlap_and_keep_event_loop_responsive(tmp_path):
    code = "import json,time; start=time.monotonic(); time.sleep(.15); print(json.dumps([start,time.monotonic()]))"
    ticks = 0
    done = False

    async def heartbeat():
        nonlocal ticks
        while not done:
            ticks += 1
            await asyncio.sleep(0.01)

    beat = asyncio.create_task(heartbeat())
    results = await asyncio.gather(*(run([sys.executable, "-c", code]) for _ in range(4)))
    done = True
    await beat
    intervals = [json.loads(result.stdout) for result in results]
    assert max(start for start, end in intervals) < min(end for start, end in intervals)
    assert ticks >= 5


@pytest.mark.asyncio
async def test_child_environment_and_cwd_are_private(tmp_path, monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "99")
    env = C.worker_env(C.ExecutionPlan(threads_per_worker=2))
    result = await run([sys.executable, "-c",
                        "import os,json; print(json.dumps([os.getcwd(),os.environ['OMP_NUM_THREADS']]))"],
                       cwd=tmp_path, env=env)
    assert json.loads(result.stdout) == [str(tmp_path), "2"]
    assert os.environ["OMP_NUM_THREADS"] == "99"


def _alive(pid):
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group assertion")
@pytest.mark.asyncio
async def test_timeout_reaps_parent_and_term_ignoring_descendant(tmp_path):
    pids = tmp_path / "pids.json"
    descendant = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
    parent = ("import os,sys,json,subprocess,time,pathlib; "
              f"child=subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
              f"pathlib.Path({str(pids)!r}).write_text(json.dumps([os.getpid(),child.pid])); time.sleep(60)")
    with pytest.raises(subprocess.TimeoutExpired):
        await run([sys.executable, "-c", parent], timeout=0.25)
    assert pids.exists()
    assert not any(_alive(pid) for pid in json.loads(pids.read_text()))


@pytest.mark.asyncio
async def test_cancellation_waits_until_child_has_stopped(tmp_path):
    pid_file = tmp_path / "pid"
    child = ("import os,time,pathlib; "
             f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)")
    task = asyncio.create_task(run([sys.executable, "-c", child]))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not _alive(int(pid_file.read_text()))


@pytest.mark.asyncio
async def test_cancel_during_spawn_drains_full_pipes_and_records_ownership(monkeypatch):
    import apexa_subprocess as runner
    created = asyncio.Event()
    release = asyncio.Event()
    handles = []
    attached = []
    real_spawn = asyncio.create_subprocess_exec
    async def delayed_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        handles.append(proc)
        created.set()
        await release.wait()
        return proc
    monkeypatch.setattr(runner.asyncio, 'create_subprocess_exec', delayed_spawn)
    monkeypatch.setattr(C, 'attach_current_child', lambda pid, process_group=None: attached.append(pid))
    task = asyncio.create_task(run([sys.executable, '-c',
        'import os,time; os.write(1,b"x"*10000000); time.sleep(60)'], input=b'pending input'))
    await asyncio.wait_for(created.wait(), 3)
    await asyncio.sleep(.05)  # let the child's pipe fill before returning its handle
    task.cancel()
    release.set()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert attached == [handles[0].pid]
        assert not _alive(handles[0].pid)
    finally:
        if _alive(handles[0].pid):
            handles[0].kill()
            await handles[0].communicate()


@pytest.mark.asyncio
async def test_zero_admission_never_invokes_sync_or_async_workers():
    calls = []

    def sync(item):
        calls.append(item)

    async def asynchronous(item):
        calls.append(item)

    plan = C.ExecutionPlan(n_workers=0, reason="insufficient RAM")
    first = C.run_batch([1, 2], sync, plan, progress=False)
    second = await C.run_batch_async([1, 2], asynchronous, plan, progress=False)
    assert calls == []
    assert first.n_fail == second.n_fail == 2
    assert all(r["nothing_was_run"] for r in first.records + second.records)


@pytest.mark.asyncio
async def test_batch_cancellation_drains_all_inflight_workers():
    started = []
    stopped = []
    barrier = asyncio.Event()

    async def worker(item):
        started.append(item)
        if len(started) == 2:
            barrier.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            stopped.append(item)

    task = asyncio.create_task(C.run_batch_async(range(5), worker, C.ExecutionPlan(n_workers=2)))
    await asyncio.wait_for(barrier.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(started) == sorted(stopped) == [0, 1]


@pytest.fixture
def scientific_stubs(monkeypatch, tmp_path):
    import midas_comprehensive_server as M
    monkeypatch.setenv("APEXA_RESOURCE_DB", str(tmp_path / "resources.sqlite3"))
    monkeypatch.setenv("APEXA_IDEMPOTENCY", "0")
    monkeypatch.setenv("APEXA_USE_NATIVE_MIDAS", "0")
    monkeypatch.delenv("APEXA_MIDAS_DEVICE", raising=False)
    monkeypatch.setattr(C, "profile_for", lambda task: C.TaskProfile(task, threads_per_worker=2, rss_gb=1))
    monkeypatch.setattr(C, "inventory", lambda *a, **k: C.Resources(cores=8, mem_total_gb=32, mem_free_gb=24))
    monkeypatch.setattr(M, "decide_exec_host", lambda *a, **k: {"is_remote": False, "reason": "local fixture"})
    monkeypatch.setattr(M, "_calibration_interpreter", lambda: ("/fixture/python", "test", True, "fixture"))
    monkeypatch.setattr(M, "_detector_shape_and_px", lambda *a: (8, 8, 150))
    monkeypatch.setattr(M, "_find_dark_for_image", lambda *a: None)
    monkeypatch.setattr(M, "_choose_calibration_engine", lambda *a: {
        "engine": "canonical-v2", "interpreter": "/fixture/python", "v2_version": "test",
        "reason": "fixture", "fallbacks": []})
    template = tmp_path / "params.txt"
    template.write_text("SpaceGroup 225\nLatticeParameter 5.411\nWavelength 0.2\npx 150\nNrPixelsY 8\nNrPixelsZ 8\nLsd 900000\nBC 4 4\nImTransOpt 0\n")
    return M, template


@pytest.mark.parametrize("one_failure", [False, True])
@pytest.mark.asyncio
async def test_real_calibration_series_overlaps_canonical_workers_with_thread_limits(scientific_stubs, tmp_path, monkeypatch, one_failure):
    M, template = scientific_stubs
    frames = tmp_path / "frames"
    frames.mkdir()
    originals = []
    for index in range(3):
        image = frames / f"CeO2_{index + 101}.tif"
        image.write_bytes(b"stub image")
        originals.append(str(image))
    active = peak = 0
    seen_images, environments = [], []

    async def fake_engine(command, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        seen_images.append(command[command.index("--image") + 1])
        environments.append(kwargs["env"])
        await asyncio.sleep(0.03)
        if one_failure and command[command.index("--image") + 1].endswith("CeO2_102.tif"):
            active -= 1
            return subprocess.CompletedProcess(command, 1, json.dumps({"status": "error", "error": "fixture failure"}), "")
        out = Path(command[command.index("--output-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        result = out / "paramstest.txt"
        result.write_text("Lsd 900001\nBC 4 4\ntx 0\nty 0\ntz 1\npx 150\nWavelength 0.2\n")
        active -= 1
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "status": "success", "paramstest_file": str(result),
            "gate": {"passed": True, "threshold_uE": 100, "stage4_strain_uE_test": 5}}), "")

    monkeypatch.setattr(M, "run_subprocess", fake_engine)
    response = json.loads(await M.midas_calibrate_series(
        images=str(frames), output_root=str(tmp_path / "out"),
        template_param_file=str(template), energy_kev=63.314, n_parallel=3))
    assert response["n_ok"] == (2 if one_failure else 3), response
    assert response["status"] == ("partial" if one_failure else "success")
    assert response["success"] is (not one_failure)
    assert peak == 3
    successful = [row for row in response["per_frame"] if row["status"] == "success"]
    assert all(row["tz_deg"] == 1 and row["lsd_um"] == 900001 for row in successful)
    assert all(row["heldout_uE"] == 5 and row["manifest"] for row in successful)
    assert sorted(seen_images) == originals
    assert all(env["OMP_NUM_THREADS"] == env["OPENBLAS_NUM_THREADS"] == "2" for env in environments)
    assert not any(path.is_symlink() for path in frames.iterdir())


@pytest.mark.parametrize("export_failure", [False, True])
@pytest.mark.asyncio
async def test_integration_series_uses_async_workers_and_cpu_allocation(scientific_stubs, tmp_path, monkeypatch, export_failure):
    M, template = scientific_stubs
    engine = tmp_path / "MIDAS/FF_HEDM/workflows/integrator.py"
    engine.parent.mkdir(parents=True)
    engine.write_text("# fake")
    monkeypatch.setattr(M, "MIDAS_ROOT", tmp_path / "MIDAS")
    monkeypatch.setattr(M, "find_midas_python", lambda: "/fixture/python")
    monkeypatch.setattr(M, "get_midas_env", lambda: {})
    if export_failure:
        def failed_export(*args, **kwargs):
            raise OSError("fixture full output disk")
        monkeypatch.setattr(M, "_write_fxye", failed_export)
    images = []
    for index in range(3):
        image = tmp_path / f"sample_{index + 100:06d}.h5"
        image.write_bytes(b"stub")
        images.append(str(image))
    active = peak = 0
    budgets = []

    async def fake_engine(command, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        budgets.append((command[command.index("-nCPUsLocal") + 1], kwargs["env"]["OMP_NUM_THREADS"]))
        await asyncio.sleep(0.03)
        output = Path(command[command.index("-resultFolder") + 1])
        (output / "sample_lineout.xy").write_text("1 10\n2 20\n3 30\n")
        active -= 1
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(M, "run_subprocess", fake_engine)
    response = json.loads(await M.midas_integrate_series(
        str(template), images=images, dark_source="none", result_folder=str(tmp_path / "out"),
        n_cpus=2, n_parallel=3))
    assert response["succeeded"] == (0 if export_failure else 3), response
    assert response["status"] == ("error" if export_failure else "success")
    assert peak == 3
    assert budgets == [("2", "2")] * 3
    assert response["compute"]["device"] == "cpu"


@pytest.mark.asyncio
async def test_calibration_series_refuses_low_memory_once(scientific_stubs, tmp_path, monkeypatch):
    M, template = scientific_stubs
    frame = tmp_path / "CeO2.h5"
    frame.write_bytes(b"stub")
    monkeypatch.setattr(C, "inventory", lambda *a, **k: C.Resources(cores=32, mem_total_gb=64, mem_free_gb=0.1))

    async def never(*a, **k):
        pytest.fail("no worker may run without an admitted resource budget")

    monkeypatch.setattr(M, "midas_auto_calibrate", never)
    response = json.loads(await M.midas_calibrate_series(str(frame), template_param_file=str(template)))
    assert response["status"] == "resource_unavailable"
    assert response["nothing_was_run"] is True


@pytest.mark.asyncio
async def test_calibration_benchmark_uses_explicit_devices_and_isolated_trials(tmp_path, monkeypatch):
    import midas_comprehensive_server as M
    calls = []
    monkeypatch.setattr(C, "inventory", lambda *a, **k: C.Resources(gpus=[C.GPU(1, "fixture", 48, 40)]))
    monkeypatch.setattr(C, "save_profile", lambda p: True)
    monkeypatch.setattr(C, "plan", lambda *a, **k: C.ExecutionPlan(device="cpu"))
    monkeypatch.setenv("APEXA_MIDAS_DEVICE", "cpu")

    async def one(image, **kwargs):
        calls.append((image.name, kwargs, os.environ["APEXA_MIDAS_DEVICE"]))
        return {"status": "success"}

    await M._benchmark_calibration([Path("a.h5"), Path("b.h5")],
                                   C.ExecutionPlan(n_workers=2, threads_per_worker=2), C, one, "cpu")
    assert calls[0][1] == {"device_override": "cpu", "trial_id": "benchmark_cpu"}
    assert calls[1][1] == {"device_override": "cuda:1", "trial_id": "benchmark_gpu"}
    assert all(call[2] == "cpu" for call in calls)
    assert all(call[1]["trial_id"] == "benchmark_sweep" for call in calls[2:])
