"""Resource-aware planning and batch execution.

The bugs these guard against are the two that motivated the layer:

* routing work to a GPU because one *exists* rather than because it *helps*
  (measured: calibration on an A6000 is 224.7 s vs 230.0 s on CPU, and the
  A6000's float64 rate is 4x below copland's own CPUs);
* planning concurrency from nominal thread counts rather than free resources,
  which left 95 of 96 cores idle for 2.7 hours.
"""
import pytest

import apexa_compute as C


def res(cores=96, load1=0.0, mem_free=1800.0, gpus=1, gpu_free=40.0):
    return C.Resources(
        host="testhost", cores=cores, load1=load1,
        mem_total_gb=2000.0, mem_free_gb=mem_free,
        gpus=[C.GPU(i, f"GPU{i}", 48.0, gpu_free) for i in range(gpus)])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "APEXA_DIR", tmp_path)
    monkeypatch.setattr(C, "PROFILES_JSON", tmp_path / "task_profiles.json")
    monkeypatch.setattr(C, "INVENTORY_JSON", tmp_path / "compute_cache.json")
    yield


# ── the device gate ──────────────────────────────────────────────────────────

def test_gpu_declined_when_measured_no_faster():
    """The headline fix: two idle GPUs present, still CPU."""
    prof = C.TaskProfile(task="calibration", gpu_speedup=1.0, rss_gb=7.0)
    p = C.plan(43, profile=prof, resources=res(gpus=2))
    assert p.device == "cpu"


def test_gpu_used_when_measured_faster():
    prof = C.TaskProfile(task="t", gpu_speedup=8.0, rss_gb=4.0)
    assert C.plan(10, profile=prof, resources=res(gpus=2)).device.startswith("cuda")


def test_gpu_declined_when_it_would_not_fit():
    prof = C.TaskProfile(task="t", gpu_speedup=8.0, rss_gb=60.0)
    assert C.plan(10, profile=prof, resources=res(gpu_free=12.0)).device == "cpu"


def test_unmeasured_task_defaults_to_cpu():
    """An unknown task must not inherit optimism about hardware."""
    assert C.DEFAULT_PROFILE.gpu_speedup < C.GPU_MIN_SPEEDUP
    assert C.plan(10, "never-measured", resources=res(gpus=2)).device == "cpu"


# ── concurrency caps ─────────────────────────────────────────────────────────

def test_core_bound_plan_matches_measured_sweet_spot():
    """96 cores idle, 4 threads each, 25% reserve -> 18 workers."""
    prof = C.TaskProfile(task="calibration", threads_per_worker=4, rss_gb=7.0)
    p = C.plan(43, profile=prof, resources=res(load1=0.0))
    assert p.n_workers == 18 and p.threads_per_worker == 4
    assert "cores-bound" in p.reason


def test_load_from_other_users_shrinks_the_plan():
    """copland sat at load 40 from other users during the measurements."""
    prof = C.TaskProfile(task="calibration", threads_per_worker=4, rss_gb=7.0)
    busy = C.plan(43, profile=prof, resources=res(load1=80.0)).n_workers
    idle = C.plan(43, profile=prof, resources=res(load1=0.0)).n_workers
    assert busy < idle and busy >= 1


def test_memory_can_be_the_binding_cap():
    prof = C.TaskProfile(task="hog", threads_per_worker=1, rss_gb=100.0)
    p = C.plan(43, profile=prof, resources=res(mem_free=350.0))
    assert p.n_workers == 3 and "memory-bound" in p.reason


def test_never_more_workers_than_items():
    prof = C.TaskProfile(task="t", threads_per_worker=1, rss_gb=1.0)
    p = C.plan(3, profile=prof, resources=res())
    assert p.n_workers == 3 and "items-bound" in p.reason


def test_reserve_is_honoured():
    prof = C.TaskProfile(task="t", threads_per_worker=1, rss_gb=1.0)
    greedy = C.plan(999, profile=prof, resources=res(), reserve=0.0).n_workers
    polite = C.plan(999, profile=prof, resources=res(), reserve=0.5).n_workers
    assert polite < greedy


def test_explicit_cap_wins():
    prof = C.TaskProfile(task="t", threads_per_worker=1, rss_gb=1.0)
    p = C.plan(999, profile=prof, resources=res(), max_workers=6)
    assert p.n_workers == 6


def test_internally_parallel_task_is_not_fanned_out():
    """FF/NF already shard via parsl/OMP; two of them would just contend."""
    prof = C.TaskProfile(task="ff", internally_parallel=True)
    p = C.plan(8, profile=prof, resources=res())
    assert p.n_workers == 1 and p.threads_per_worker > 1
    assert "internally" in p.reason


def test_zero_items_is_not_an_error():
    assert C.plan(0, "calibration", resources=res()).n_workers == 0


def test_unreachable_host_plans_conservatively(monkeypatch):
    monkeypatch.setattr(C, "_remote_inventory", lambda *a, **k: None)
    assert C.inventory("nope", force=True).cores == 1


# ── execution ────────────────────────────────────────────────────────────────

def _plan(n_workers):
    return C.ExecutionPlan(n_workers=n_workers, threads_per_worker=1)


@pytest.mark.parametrize("workers", [1, 4])
def test_results_come_back_in_input_order(workers):
    """Completion order is not input order; outputs must not depend on timing."""
    items = list(range(12))
    out = C.run_batch(items, lambda i: {"status": "success", "i": i},
                      _plan(workers), progress=False)
    assert [r["i"] for r in out.records] == items


@pytest.mark.parametrize("workers", [1, 4])
def test_a_crashing_item_never_aborts_the_batch(workers):
    def fn(i):
        if i == 5:
            raise RuntimeError("boom")
        return {"status": "success", "i": i}
    out = C.run_batch(list(range(10)), fn, _plan(workers), progress=False)
    assert len(out.records) == 10
    assert out.n_ok == 9 and out.n_fail == 1
    assert "boom" in out.records[5]["error"]


def test_empty_batch():
    out = C.run_batch([], lambda x: x, _plan(4), progress=False)
    assert out.records == [] and out.n_ok == 0


def test_worker_env_pins_every_thread_runtime():
    """Without this, N workers each spawn N_cores BLAS threads and thrash."""
    env = C.worker_env(C.ExecutionPlan(n_workers=12, threads_per_worker=4), base={})
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        assert env[var] == "4"


# ── profiles ─────────────────────────────────────────────────────────────────

def test_measured_calibration_profile_is_shipped():
    p = C.profile_for("calibration")
    assert p.gpu_speedup == 1.0 and p.threads_per_worker == 4
    assert p.rss_gb == pytest.approx(7.0)
    assert "copland" in p.measured_on


def test_saved_profile_overrides_the_builtin():
    C.save_profile(C.TaskProfile(task="calibration", gpu_speedup=9.0))
    assert C.profile_for("calibration").gpu_speedup == 9.0


def test_builtin_survives_a_partial_override():
    C.save_profile(C.TaskProfile(task="calibration", gpu_speedup=9.0, rss_gb=7.0))
    assert C.profile_for("calibration").threads_per_worker == 4


def test_corrupt_profile_file_falls_back_to_builtins():
    C.PROFILES_JSON.write_text("{not json", encoding="utf-8")
    assert C.profile_for("calibration").threads_per_worker == 4


# ── server integration: the measured device gate ─────────────────────────────

def test_pick_compute_target_declines_gpu_for_a_measured_task(monkeypatch):
    """With task= supplied, a measured no-benefit profile beats 'a GPU exists'."""
    import midas_comprehensive_server as M
    monkeypatch.setattr(M, "_local_accelerator", lambda: (True, "cuda"))
    t = M._pick_compute_target(43, 8.3, task="calibration")
    assert t["target"] == "local-cpu" and "measured" in t["reason"]


def test_pick_compute_target_keeps_legacy_rule_without_a_task(monkeypatch):
    """Callers that pass no task must behave exactly as before.

    Integration's GPU benefit has never been measured; flipping it on an
    assumption would repeat the bug this layer exists to fix.
    """
    import midas_comprehensive_server as M
    monkeypatch.setattr(M, "_local_accelerator", lambda: (True, "cuda"))
    assert M._pick_compute_target(43, 8.3)["target"] == "local-gpu"


def test_calibration_device_resolution(monkeypatch):
    import midas_comprehensive_server as M
    monkeypatch.delenv("APEXA_MIDAS_DEVICE", raising=False)
    assert M._resolve_calib_device("cuda:1") == "cuda:1"          # explicit wins
    monkeypatch.setenv("APEXA_MIDAS_DEVICE", "cpu")
    assert M._resolve_calib_device("") == "cpu"                   # env honoured
    assert M._resolve_calib_device("auto") in ("cpu", "cuda", "mps") or \
        M._resolve_calib_device("auto").startswith("cuda:")       # auto resolves
