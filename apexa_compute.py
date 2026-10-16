"""Resource-aware execution: how many workers, how many threads, which device.

The failure this exists to prevent
----------------------------------
APEXA under-uses the hardware it runs on. Measured on copland (96 cores, 2 TB
RAM, 2x RTX A6000) against the real 43-frame VarexD rotation series:

    one calibration, CPU 48 threads  230.0 s
    one calibration, CPU  4 threads  221.1 s
    one calibration, GPU (A6000)     224.7 s

A single four-stage fit is **not compute-bound**: four threads match 48, and the
GPU matches both. It occupies roughly one effective core and ~7 GB. So 43 frames
ran serially for ~2.7 hours while 95 cores and two GPUs sat idle.

Fan-out is the entire win, and it was measured too:

    1 concurrent  225 s  (1.0x)
    4 concurrent  250 s  (3.6x)
   12 concurrent  317 s  (8.5x)

-> 43 frames in ~19 minutes instead of 2.7 hours.

What was missing
----------------
``_pick_compute_target()`` decides **where one job runs** and has no concept of
**how many run at once** — the axis that actually mattered. Worse, its rule
"local accelerator present -> use the GPU" is wrong for this workload: the A6000
is no faster here, and its float64 throughput (0.56 TFLOP/s measured) is 4x
*below* copland's own CPUs (2.33 TFLOP/s). A layer that infers "GPU exists, so
use it" produces a slower run and an idle machine at the same time.

The design
----------
Three facts decide a plan, and two of the three are *measured, never assumed*:

1. **Inventory** — what the host actually has free right now.
2. **TaskProfile** — how this particular task behaves (effective cores, memory,
   whether a GPU helps *at all*). Unmeasured tasks default to
   ``gpu_speedup=1.0``, i.e. no benefit, so the layer never routes to a GPU on
   the mere evidence that one exists.
3. **Politeness** — beamline hosts are shared. copland sat at load 40 from other
   users during the measurements above. Plans are made against *currently free*
   resources with a reserve held back.

The same planner handles opposite strategies because the *profile* differs, not
the code: calibration (``effective_cores=1``) fans out wide, while FF
reconstruction (``internally_parallel=True``, it already shards through
parsl/OMP) correctly gets exactly one worker with many threads.
"""
from __future__ import annotations

import json
import math
import os
import shlex
import sys
import time
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

APEXA_DIR      = Path.home() / ".apexa"
PROFILES_JSON  = APEXA_DIR / "task_profiles.json"
INVENTORY_JSON = APEXA_DIR / "compute_cache.json"

#: Inventory is cached for the same reason apexa_network caches reachability: a
#: probe on the hot path is how APEXA once hung on a beamline host. Short enough
#: that a machine filling up with other users' work is noticed within a batch.
INVENTORY_TTL_S = 120

#: Fraction of the machine to leave for everyone else. Beamline analysis hosts
#: are shared; taking every core is antisocial and gets APEXA throttled socially
#: long before it gets throttled technically.
DEFAULT_RESERVE = float(os.environ.get("APEXA_COMPUTE_RESERVE", "0.25"))

#: A GPU must be convincingly faster before we pay the transfer and the risk of
#: a second code path. 1.0 means "measured as no better than CPU".
GPU_MIN_SPEEDUP = float(os.environ.get("APEXA_GPU_MIN_SPEEDUP", "1.25"))


# ── inventory ────────────────────────────────────────────────────────────────

@dataclass
class GPU:
    index: int
    name: str
    mem_total_gb: float
    mem_free_gb: float
    util_pct: float = 0.0


@dataclass
class Resources:
    host: str = ""
    cores: int = 1
    load1: float = 0.0
    mem_total_gb: float = 0.0
    mem_free_gb: float = 0.0
    gpus: List[GPU] = field(default_factory=list)
    source: str = "local"

    @property
    def free_cores(self) -> float:
        """Cores not already busy, floored at 1.

        Uses the 1-minute load average rather than a snapshot of CPU percent:
        load is what the other users on this host are actually costing us, and
        it is the number a human looks at before launching work on a shared box.
        """
        return max(1.0, float(self.cores) - max(0.0, self.load1))

    def describe(self) -> str:
        g = f", {len(self.gpus)} GPU" + ("s" if len(self.gpus) != 1 else "") if self.gpus else ", no GPU"
        return (f"{self.host or 'local'}: {self.cores} cores "
                f"({self.free_cores:.0f} free, load {self.load1:.1f}), "
                f"{self.mem_free_gb:.0f}/{self.mem_total_gb:.0f} GB free{g}")


#: Emitted on the remote host and parsed back as JSON. Kept dependency-free
#: (stdlib + an optional torch import) so it runs under whatever interpreter the
#: remote MIDAS environment happens to be.
_PROBE_SRC = r'''
import json, os
d = {"cores": os.cpu_count() or 1, "load1": os.getloadavg()[0], "gpus": []}
try:
    mt = mf = 0
    for line in open("/proc/meminfo"):
        k, v = line.split(":")[0], line.split()[1]
        if k == "MemTotal":     mt = int(v) / 1048576
        elif k == "MemAvailable": mf = int(v) / 1048576
    d["mem_total_gb"], d["mem_free_gb"] = mt, mf
except Exception:
    d["mem_total_gb"] = d["mem_free_gb"] = 0.0
try:
    import torch
    for i in range(torch.cuda.device_count()):
        free, total = torch.cuda.mem_get_info(i)
        d["gpus"].append({"index": i, "name": torch.cuda.get_device_name(i),
                          "mem_total_gb": total / 2**30, "mem_free_gb": free / 2**30,
                          "util_pct": 0.0})
except Exception:
    pass
print(json.dumps(d))
'''


def _local_inventory() -> Resources:
    cores = os.cpu_count() or 1
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        load1 = 0.0
    mem_total = mem_free = 0.0
    try:
        import psutil                       # already a dependency (pyproject.toml)
        vm = psutil.virtual_memory()
        mem_total, mem_free = vm.total / 2**30, vm.available / 2**30
    except Exception:
        pass
    gpus: List[GPU] = []
    try:
        import torch
        for i in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(i)
            gpus.append(GPU(i, torch.cuda.get_device_name(i),
                            total / 2**30, free / 2**30))
    except Exception:
        pass
    return Resources("", cores, load1, mem_total, mem_free, gpus, "local")


def _remote_inventory(host: str, python: str = "") -> Optional[Resources]:
    """One SSH round trip. Returns None when the host cannot be probed."""
    try:
        from apexa_remote_exec import remote_run, host_record
    except Exception:
        return None
    interp = python
    if not interp:
        try:
            rec = host_record(host) or {}
            mp = rec.get("midas_path") or ""
            interp = f"{mp}/python" if mp else "python3"
        except Exception:
            interp = "python3"
    cmd = f"{shlex.quote(interp)} -c {shlex.quote(_PROBE_SRC)}"
    try:
        r = remote_run(host, cmd, timeout=30)          # never raises, by contract
    except Exception:
        return None
    if not r.get("success"):
        return None
    try:
        d = json.loads((r.get("stdout") or "").strip().splitlines()[-1])
    except Exception:
        return None
    return Resources(host=host, cores=int(d.get("cores", 1)),
                     load1=float(d.get("load1", 0.0)),
                     mem_total_gb=float(d.get("mem_total_gb", 0.0)),
                     mem_free_gb=float(d.get("mem_free_gb", 0.0)),
                     gpus=[GPU(**g) for g in d.get("gpus", [])],
                     source="remote")


def inventory(host: str = "", *, force: bool = False,
              ttl: int = INVENTORY_TTL_S) -> Resources:
    """What ``host`` ("" = local) has available, from cache unless stale."""
    key = host or "__local__"
    if not force:
        try:
            blob = json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
            hit = blob.get(key)
            if hit and (time.time() - float(hit.get("at", 0))) < ttl:
                r = hit["res"]
                return Resources(host=r["host"], cores=r["cores"], load1=r["load1"],
                                 mem_total_gb=r["mem_total_gb"],
                                 mem_free_gb=r["mem_free_gb"],
                                 gpus=[GPU(**g) for g in r["gpus"]],
                                 source=r.get("source", "cache"))
        except Exception:
            pass
    res = (_remote_inventory(host) if host else _local_inventory())
    if res is None:                     # unreachable: a conservative stand-in,
        res = Resources(host=host, cores=1, source="unreachable")   # 1 worker
    try:
        APEXA_DIR.mkdir(parents=True, exist_ok=True)
        blob = {}
        if INVENTORY_JSON.exists():
            try:
                blob = json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
            except Exception:
                blob = {}
        blob[key] = {"at": time.time(), "res": asdict(res)}
        INVENTORY_JSON.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass                            # a read-only home must never be fatal
    return res


# ── task profiles ────────────────────────────────────────────────────────────

@dataclass
class TaskProfile:
    """How one work item of a given task behaves. Measured, not assumed."""

    task: str
    #: Cores a single item can actually keep busy. Calibration measured ~1:
    #: 4 threads (221.1 s) matched 48 threads (230.0 s).
    effective_cores: float = 1.0
    #: Threads to give each worker. Not the same as effective_cores — calibration
    #: is fastest at 4 even though it only *uses* about one, and a 2-thread run
    #: was pathologically slow (12 concurrent failed to finish in 600 s).
    threads_per_worker: int = 4
    #: Peak RSS per worker, GB. Calibration measured ~7.
    rss_gb: float = 2.0
    #: Measured GPU wall-time ratio (cpu/gpu). 1.0 == no benefit, which is the
    #: DEFAULT for anything unmeasured, and what calibration actually measured
    #: (224.7 s GPU vs 230.0 s CPU).
    gpu_speedup: float = 1.0
    #: True for tasks that already shard internally (FF/NF via parsl/OMP). The
    #: planner then returns ONE worker with many threads instead of fanning out.
    internally_parallel: bool = False
    #: Provenance, so a stale profile is recognisable.
    measured_on: str = ""
    measured_at: str = ""
    note: str = ""


#: Seeded from the measurements in the module docstring. Everything absent here
#: falls back to DEFAULT_PROFILE, which assumes no GPU benefit on purpose.
BUILTIN_PROFILES: Dict[str, TaskProfile] = {
    "calibration": TaskProfile(
        task="calibration", effective_cores=1.0, threads_per_worker=4, rss_gb=7.0,
        gpu_speedup=1.0, internally_parallel=False,
        measured_on="copland", measured_at="2026-10-14",
        note=("43-frame VarexD series: 4 threads 221.1 s == 48 threads 230.0 s == "
              "A6000 224.7 s. Fan-out 1/4/12 -> 225/250/317 s (8.5x at 12).")),
    "integration": TaskProfile(
        task="integration", effective_cores=2.0, threads_per_worker=4, rss_gb=3.0,
        gpu_speedup=1.0, note="inherits the pre-existing n_parallel behaviour"),
    "ff_reconstruction": TaskProfile(
        task="ff_reconstruction", effective_cores=0.0, threads_per_worker=0,
        rss_gb=16.0, internally_parallel=True,
        note="shards itself via midas-pipeline parsl/OMP; must NOT be fanned out"),
    "nf_reconstruction": TaskProfile(
        task="nf_reconstruction", effective_cores=0.0, threads_per_worker=0,
        rss_gb=16.0, internally_parallel=True,
        note="same as FF: internally parallel"),
}

DEFAULT_PROFILE = TaskProfile(task="unknown", note="unmeasured; assumes no GPU benefit")


def load_profiles() -> Dict[str, TaskProfile]:
    """Built-ins overlaid with anything ``benchmark`` has measured on this host."""
    out = {k: v for k, v in BUILTIN_PROFILES.items()}
    try:
        raw = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
    except Exception:
        return out
    for key, body in (raw or {}).items():
        if not isinstance(body, dict):
            continue
        fields = {k: v for k, v in body.items() if k in TaskProfile.__dataclass_fields__}
        fields.pop("task", None)
        base = out.get(key)
        try:
            out[key] = replace(base, **fields) if base else TaskProfile(task=key, **fields)
        except TypeError:
            continue
    return out


def profile_for(task: str) -> TaskProfile:
    return load_profiles().get(task) or replace(DEFAULT_PROFILE, task=task)


def save_profile(p: TaskProfile) -> bool:
    try:
        APEXA_DIR.mkdir(parents=True, exist_ok=True)
        raw = {}
        if PROFILES_JSON.exists():
            try:
                raw = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        raw[p.task] = asdict(p)
        PROFILES_JSON.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


# ── planning ─────────────────────────────────────────────────────────────────

@dataclass
class ExecutionPlan:
    n_workers: int = 1
    threads_per_worker: int = 1
    device: str = "cpu"
    host: str = ""
    reason: str = ""
    # Carried for the audit trail / the `compute` block tools already emit.
    task: str = ""
    n_items: int = 0
    resources: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def plan(n_items: int, task: str = "", *, host: str = "",
         profile: Optional[TaskProfile] = None,
         resources: Optional[Resources] = None,
         reserve: float = DEFAULT_RESERVE,
         max_workers: int = 0) -> ExecutionPlan:
    """Decide concurrency, threads, and device for ``n_items`` of ``task``.

    Three caps, and the binding one is named in ``reason`` so a surprising plan
    can be explained without re-deriving it.
    """
    prof = profile or profile_for(task)
    res  = resources if resources is not None else inventory(host)
    n_items = max(0, int(n_items))
    if n_items == 0:
        return ExecutionPlan(0, 1, "cpu", host, "no work items", task, 0, res.describe())

    device = _choose_device(prof, res)

    # A task that already shards internally must not be fanned out: two of them
    # would simply fight over the same cores.
    if prof.internally_parallel:
        thr = max(1, int(res.free_cores * (1.0 - reserve)))
        return ExecutionPlan(
            1, thr, device, host,
            f"{task or 'task'} parallelises internally — 1 worker with {thr} threads "
            f"(not fanned out)", task, n_items, res.describe())

    thr = max(1, int(prof.threads_per_worker or 1))

    budget_cores = max(1.0, res.free_cores * (1.0 - max(0.0, min(0.9, reserve))))
    by_cores = max(1, int(budget_cores // thr))
    by_mem = (max(1, int(res.mem_free_gb // prof.rss_gb))
              if (prof.rss_gb > 0 and res.mem_free_gb > 0) else 10**6)
    caps = {"cores": by_cores, "memory": by_mem, "items": n_items}
    if max_workers and max_workers > 0:
        caps["max_workers"] = int(max_workers)

    n = max(1, min(caps.values()))
    binding = min(caps, key=lambda k: caps[k])
    reason = (f"{n} worker(s) x {thr} thread(s) on {host or 'local'} "
              f"[{binding}-bound: cores {by_cores}, memory {by_mem}, items {n_items}"
              + (f", cap {max_workers}" if max_workers else "") + "]")
    return ExecutionPlan(n, thr, device, host, reason, task, n_items, res.describe())


def _choose_device(prof: TaskProfile, res: Resources) -> str:
    """A GPU is used only when it was MEASURED to help.

    This is the rule that ``_pick_compute_target`` got wrong: it routed to a GPU
    whenever one existed. On copland that sends float64, latency-bound work to an
    A6000 that is 4x slower than the host's own CPUs.
    """
    if prof.gpu_speedup < GPU_MIN_SPEEDUP:
        return "cpu"
    if not res.gpus:
        return "cpu"
    best = max(res.gpus, key=lambda g: g.mem_free_gb)
    if prof.rss_gb and best.mem_free_gb < prof.rss_gb:
        return "cpu"                    # would not fit; silently thrashing is worse
    return f"cuda:{best.index}"


# ── execution ────────────────────────────────────────────────────────────────

@dataclass
class BatchResult:
    records: List[Any]
    plan: ExecutionPlan
    elapsed_s: float
    n_ok: int
    n_fail: int


def run_batch(items: Sequence[Any], fn: Callable[[Any], Any],
              execution_plan: ExecutionPlan, *,
              label: Callable[[Any], str] = str,
              ok: Callable[[Any], bool] = lambda r: isinstance(r, dict)
              and r.get("status") == "success",
              progress: bool = True) -> BatchResult:
    """Run ``fn`` over ``items`` at the plan's concurrency.

    Generalises the pool ``midas_integrate_series`` already had, and keeps its
    three hard-won properties:

    * **a worker crash never aborts the batch** — the item gets an error record;
    * **input order is restored**, so outputs stay deterministic even though
      completion order is not;
    * progress goes to stderr, so it streams into the agent/CLI immediately.
    """
    t0 = time.monotonic()
    items = list(items)
    n = len(items)
    if n == 0:
        return BatchResult([], execution_plan, 0.0, 0, 0)

    results: Dict[int, Any] = {}
    done = 0

    def _emit(i: int, rec: Any) -> None:
        if progress:
            state = "ok " if ok(rec) else "FAIL"
            print(f"  [{state}] ({done}/{n}) {label(items[i])}", file=sys.stderr)

    if execution_plan.n_workers <= 1:
        for i, it in enumerate(items):
            try:
                rec = fn(it)
            except Exception as e:                       # noqa: BLE001
                rec = {"status": "error", "error": f"worker crashed: {e}",
                       "item": label(it)}
            results[i] = rec
            done += 1
            _emit(i, rec)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=execution_plan.n_workers) as ex:
            futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    rec = fut.result()
                except Exception as e:                   # noqa: BLE001
                    rec = {"status": "error", "error": f"worker crashed: {e}",
                           "item": label(items[i])}
                results[i] = rec
                done += 1
                _emit(i, rec)

    ordered = [results[i] for i in range(n)]
    n_ok = sum(1 for r in ordered if ok(r))
    return BatchResult(ordered, execution_plan, time.monotonic() - t0, n_ok, n - n_ok)


async def run_batch_async(items: Sequence[Any], fn: Callable[[Any], Any],
                          execution_plan: ExecutionPlan, *,
                          label: Callable[[Any], str] = str,
                          ok: Callable[[Any], bool] = lambda r: isinstance(r, dict)
                          and r.get("status") == "success",
                          progress: bool = True) -> BatchResult:
    """``run_batch`` for coroutine work functions.

    Every MCP tool is ``async def``, so a series tool that wants to reuse an
    existing tool (and therefore all of its gates, verification and manifest
    writing, rather than reimplementing them) needs this form. Concurrency is
    bounded by a semaphore instead of a pool; the guarantees are identical —
    crashes are recorded not raised, and input order is restored.
    """
    import asyncio

    t0 = time.monotonic()
    items = list(items)
    n = len(items)
    if n == 0:
        return BatchResult([], execution_plan, 0.0, 0, 0)

    sem = asyncio.Semaphore(max(1, execution_plan.n_workers))
    results: Dict[int, Any] = {}
    done = 0

    async def _one(i: int, it: Any) -> None:
        nonlocal done
        async with sem:
            try:
                rec = await fn(it)
            except Exception as e:                       # noqa: BLE001
                rec = {"status": "error", "error": f"worker crashed: {e}",
                       "item": label(it)}
            results[i] = rec
            done += 1
            if progress:
                state = "ok " if ok(rec) else "FAIL"
                print(f"  [{state}] ({done}/{n}) {label(it)}", file=sys.stderr)

    await asyncio.gather(*(_one(i, it) for i, it in enumerate(items)))
    ordered = [results[i] for i in range(n)]
    n_ok = sum(1 for r in ordered if ok(r))
    return BatchResult(ordered, execution_plan, time.monotonic() - t0, n_ok, n - n_ok)


def worker_env(execution_plan: ExecutionPlan, base: Optional[Dict[str, str]] = None
               ) -> Dict[str, str]:
    """Environment that pins a worker to its thread budget.

    Without this the pool oversubscribes catastrophically: every BLAS/OMP
    runtime defaults to "all cores", so N workers each spawn N_cores threads.
    """
    env = dict(base if base is not None else os.environ)
    t = str(max(1, execution_plan.threads_per_worker))
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[var] = t
    return env
