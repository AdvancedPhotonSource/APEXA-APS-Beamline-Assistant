"""Host admission tested with independent Python processes and tiny local children."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import psutil
import pytest

import apexa_compute as C
from apexa_resource_leases import ResourceRegistry, ReservationDenied


@pytest.fixture(autouse=True)
def registry_path(tmp_path, monkeypatch):
    monkeypatch.setenv("APEXA_RESOURCE_DB", str(tmp_path / "resources.sqlite3"))


def execution_plan(**overrides):
    fields = {"n_workers": 1, "threads_per_worker": 1, "cpu_budget": 8,
              "memory_budget_gb": 16, "worker_rss_gb": 1}
    fields.update(overrides)
    return C.ExecutionPlan(**fields)


def _wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("local subprocess barrier timed out")
        time.sleep(0.01)


@pytest.mark.parametrize("resource", ["cpu", "ram", "gpu"])
def test_two_independent_processes_cannot_double_admit(tmp_path, resource):
    options = ({"threads_per_worker": 2, "cpu_budget": 2} if resource == "cpu" else
               {"worker_rss_gb": 10, "memory_budget_gb": 12} if resource == "ram" else
               {"device": "cuda:0", "gpu_uuid": "GPU-same-physical", "worker_vram_gb": 8, "gpu_budget_gb": 10})
    payload = asdict(execution_plan(**options))
    script = '''import json,os,sys,time
from pathlib import Path
import apexa_compute as C
root=Path(sys.argv[1]); p=C.ExecutionPlan(**json.loads(sys.argv[2]))
(root / ("ready-"+str(os.getpid()))).touch()
deadline=time.monotonic()+10
while not (root/"start").exists():
    if time.monotonic()>deadline: raise RuntimeError("start timeout")
    time.sleep(.01)
try:
    with C.reserve_worker(p) as lease:
        (root/("admitted-"+str(os.getpid()))).touch()
        while not (root/"release").exists():
            if time.monotonic()>deadline: raise RuntimeError("release timeout")
            time.sleep(.01)
        print(json.dumps({"state":"admitted","id":lease.id}),flush=True)
except C.ResourceUnavailable:
    print(json.dumps({"state":"refused"}),flush=True)
'''
    # Different CUDA ordinals can refer to the same physical GPU in two
    # environments. The UUID, rather than ordinal, must govern admission.
    second = dict(payload)
    if resource == "gpu":
        second["device"] = "cuda:3"
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), json.dumps(p)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for p in (payload, second)]
    try:
        _wait(lambda: len(list(tmp_path.glob("ready-*"))) == 2)
        (tmp_path / "start").touch()
        _wait(lambda: len(list(tmp_path.glob("admitted-*"))) >= 1)
        _wait(lambda: any(process.poll() is not None for process in processes))
        assert len(list(tmp_path.glob("admitted-*"))) == 1
        (tmp_path / "release").touch()
        outputs = [process.communicate(timeout=5) for process in processes]
        assert all(process.returncode == 0 for process in processes), outputs
        states = sorted(json.loads(stdout)["state"] for stdout, stderr in outputs)
        assert states == ["admitted", "refused"]
        assert C._reserved("") == {"cpu": 0, "ram": 0, "gpu": {}}
    finally:
        (tmp_path / "release").touch()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX child ownership")
def test_dead_owner_does_not_free_live_descendant_capacity(tmp_path):
    plan = execution_plan(threads_per_worker=2, cpu_budget=2)
    script = '''import json,os,subprocess,sys
import apexa_compute as C
with C.reserve_worker(C.ExecutionPlan(**json.loads(sys.argv[1]))) as lease:
    child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    lease.attach_child(child.pid,process_group=child.pid)
    print(json.dumps({"id":lease.id,"child":child.pid}),flush=True)
    os._exit(7)
'''
    owner = subprocess.run([sys.executable, "-c", script, json.dumps(asdict(plan))],
                           capture_output=True, text=True, timeout=5)
    assert owner.returncode == 7, owner.stderr
    receipt = json.loads(owner.stdout)
    child = psutil.Process(receipt["child"])
    try:
        assert child.is_running()
        registry = C.resource_registry()
        assert registry.reconcile() == [receipt["id"]]
        assert registry.get(receipt["id"])["state"] == "uncertain"
        assert C._reserved("")["cpu"] == 2
        with pytest.raises(C.ResourceUnavailable):
            with C.reserve_worker(plan):
                pytest.fail("orphan capacity must remain charged")
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
        assert C._reserved("")["cpu"] == 2  # no automatic release even now
        with pytest.raises(ValueError, match="evidence"):
            registry.release_verified(receipt["id"], evidence="")
        assert registry.release_verified(receipt["id"], evidence="test supervisor verified attached process group terminated")
        with C.reserve_worker(plan):
            pass
    finally:
        try:
            os.killpg(receipt["child"], signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=5)
        except psutil.TimeoutExpired:
            pass


def test_live_attached_child_prevents_scope_release(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with C.reserve_worker(execution_plan()) as lease:
            lease.attach_child(child.pid)
        row = C.resource_registry().get(lease.id)
        assert row["state"] == "uncertain"
        assert C._reserved("")["cpu"] == 1
    finally:
        child.terminate()
        child.wait(timeout=5)
    C.resource_registry().release_verified(lease.id, evidence="test child joined after termination")
    assert C._reserved("")["cpu"] == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_reaped_child_attachment_keeps_group_identity():
    with C.reserve_worker(execution_plan()) as lease:
        child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        child.wait(timeout=5)
        C.attach_current_child(child.pid, process_group=child.pid)
        row = C.resource_registry().get(lease.id)
        assert row["children"][0]["process_group"] == child.pid
        assert row["children"][0]["exited_before_attach"] is True
    assert C.resource_registry().get(lease.id)["state"] == "released"


def test_context_attachment_keeps_all_children_and_resets():
    C.attach_current_child(-1)  # no active context: no-op
    children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(2)]
    try:
        with C.reserve_worker(execution_plan()) as lease:
            for child in children:
                C.attach_current_child(child.pid)
            children[1].terminate()
            children[1].wait(timeout=5)
            assert len(C.resource_registry().get(lease.id)["children"]) == 2
        assert C.resource_registry().get(lease.id)["state"] == "uncertain"
        C.attach_current_child(-1)  # restored context after scope exit
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
    C.resource_registry().release_verified(lease.id, evidence="test joined both owned children")


def test_explicit_uncertainty_survives_owner_scope_exit():
    with C.reserve_worker(execution_plan()) as lease:
        lease.mark_uncertain("external adapter could not verify cleanup")
    assert C.resource_registry().get(lease.id)["state"] == "uncertain"
    assert C._reserved("")["cpu"] == 1


def test_optimistic_caller_cannot_expand_existing_capacity():
    first = execution_plan(threads_per_worker=2, cpu_budget=2)
    with C.reserve_worker(first):
        with pytest.raises(C.ResourceUnavailable):
            with C.reserve_worker(execution_plan(cpu_budget=100)):
                pytest.fail("caller cannot raise another active reservation's ceiling")


def test_distinct_physical_gpus_admit_independently():
    first = execution_plan(device="cuda:0", gpu_uuid="GPU-A", worker_vram_gb=8, gpu_budget_gb=10)
    second = execution_plan(device="cuda:0", gpu_uuid="GPU-B", worker_vram_gb=8, gpu_budget_gb=10)
    with C.reserve_worker(first), C.reserve_worker(second):
        assert C._reserved("")["gpu"] == {"GPU-A": 8, "GPU-B": 8}


def test_missing_gpu_identity_and_broken_registry_fail_closed(tmp_path, monkeypatch):
    with pytest.raises(C.ResourceUnavailable, match="physical GPU UUID"):
        with C.reserve_worker(execution_plan(device="cuda:0", worker_vram_gb=2, gpu_budget_gb=10)):
            pytest.fail("unidentified physical GPU admitted")
    monkeypatch.setenv("APEXA_RESOURCE_DB", str(tmp_path))  # directory, not a database
    plan = C.plan(1, profile=C.TaskProfile("fit", rss_gb=1),
                  resources=C.Resources(cores=8, mem_total_gb=16, mem_free_gb=16))
    assert plan.n_workers == 0 and "registry unavailable" in plan.reason
    with pytest.raises(C.ResourceUnavailable):
        with C.reserve_worker(execution_plan()):
            pytest.fail("unavailable registry admitted work")


def test_remote_scope_is_explicitly_controller_only():
    plan = C.plan(1, host="unrelated-remote-host", profile=C.TaskProfile("fit", rss_gb=1),
                  resources=C.Resources(cores=8, mem_total_gb=16, mem_free_gb=16))
    assert plan.admission_scope == "controller_host_only"
    with C.reserve_worker(plan):
        assert C._reserved("unrelated-remote-host")["cpu"] > 0
        assert C._reserved("")["cpu"] == 0
