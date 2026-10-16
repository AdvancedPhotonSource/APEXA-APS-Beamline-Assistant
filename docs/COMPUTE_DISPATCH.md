# APEXA Compute Dispatch — standard practice

How APEXA should place data-reduction work on the right compute: **local CPU**,
**local GPU**, or a **remote GPU endpoint inside ANL**. The goal is that small /
interactive tasks stay fast and local, and large batch/HEDM jobs stop grinding on a
CPU-only beamline host.

## The problem this solves
Beamline analysis hosts are often **CPU-only**. APEXA's native PyTorch engines
auto-use a GPU but *refuse to run on CPU* past a pixel budget and fall back to the
CPU OpenMP path (`integrator.py` → `IntegratorZarrOMP`), one 2880² frame at a time.
An overnight scan (hundreds–thousands of frames) then takes hours because nothing
is on a GPU and nothing fans out to a cluster.

## Compute tiers
| Tier | Where | Engines | Use for |
|---|---|---|---|
| **0 — local CPU** | the APEXA host | `integrator.py`/`IntegratorZarrOMP`, `IndexerOMP`, native PyTorch on CPU (small only) | single-frame integrate/calibrate, viewers, validation, knowledge |
| **1 — local GPU** | same host, CUDA/MPS present | native `midas_integrate`/`midas_calibrate`, `midas-integrate-v2`, `IntegratorFitPeaksGPUStream`, `IndexerGPU`/`FitPosOrStrainsGPU` | batch integration, FF/PF/NF index+refine when a local GPU exists |
| **2 — remote GPU endpoint** | ANL GPU node / ALCF Polaris | `midas-pipeline --machine … --device cuda --shard-gpus auto` (parsl), or the same tool run on the GPU host | large batch/HEDM on a CPU-only host |

## Decision rule

Two questions, not one. The original version of this document only answered the
first, which is why APEXA could run for 2.7 hours on a 96-core machine using one
core.

### 1. How many at once? (`apexa_compute.plan()`)

For a batch of independent work items, concurrency is planned from what the host
has **free right now**:

    workers = min( free_cores x (1 - reserve) / threads_per_worker,
                   free_RAM / rss_per_worker,
                   n_items )

`free_cores` subtracts the 1-minute load average, so other users on a shared
beamline host shrink the plan rather than being trampled. `APEXA_COMPUTE_RESERVE`
(default 0.25) holds back a share. The binding cap is named in the plan's
`reason`, so a surprising number explains itself.

A task whose profile says `internally_parallel` (FF/NF, which already shard via
parsl/OMP) gets **one** worker with many threads instead — the same planner,
opposite strategy, decided by the profile rather than by special-case code.

### 2. CPU or GPU? — **measured, not inferred**

The previous rule here was "a local accelerator is present, therefore use it".
That is wrong, and measurably so. On copland, one four-stage calibration:

| | wall |
|---|---|
| CPU, 48 threads | 230.0 s |
| CPU, 4 threads | 221.1 s |
| GPU, RTX A6000 | 224.7 s |

The fit is not compute-bound at all, and the A6000's float64 throughput
(0.56 TFLOP/s measured) is **4x below copland's own CPUs** (2.33 TFLOP/s). A
rule that routes float64 work to that GPU produces a slower run *and* an idle
machine. For contrast, chiltepin's A100 measures 18.8 TFLOP/s float64 — so
"has a GPU" is not even a property of the lab, let alone of a job.

So a GPU is used only when `TaskProfile.gpu_speedup` exceeds
`APEXA_GPU_MIN_SPEEDUP` (default 1.25). **Unmeasured tasks default to 1.0** —
no benefit — so the layer never gambles on hardware it has not timed.

Measure a host with:

    midas_calibrate_series(images="...", benchmark=True)

which times one frame on CPU, the same frame on GPU, and a small concurrency
sweep, then writes `~/.apexa/task_profiles.json`. Capability flags published by
vendors have been wrong in both directions in this stack already (Metis claimed
tool calling it lacks; Minerva omitted a flag for tool calling it has), which is
why nothing here is taken on trust.

### 3. Where? (unchanged)

`cost = n_frames x megapixels` still decides local vs a remote GPU endpoint for
the tools that support it; `prefer` still overrides. `_pick_compute_target()`
keeps its legacy behaviour for callers that pass no `task`, so integration —
whose GPU benefit has **not** been measured — is unaffected until someone
measures it.

## Configuration (env)
| Var | Meaning |
|---|---|
| `APEXA_GPU_MACHINE` | parsl machine name for `midas-pipeline --machine` (e.g. `polaris`) |
| `APEXA_GPU_ENDPOINT` | ssh `user@host` or a Globus-Compute endpoint id for a GPU node |
| `APEXA_GPU_AUTO_DISPATCH` | if set, `midas_integrate_series` returns a dispatch recommendation instead of running a large CPU job |
| `APEXA_COST_LOCAL_CPU` | cost ceiling for staying local on a CPU-only host (default 40) |

## What the tools now expose
- **FF / PF workflows** (`run_ff_hedm_full_workflow`, `run_pf_hedm_workflow`):
  `machine`, `n_nodes`, `shard_gpus` → forwarded to `midas-pipeline`
  (`--machine/--n-nodes/--shard-gpus`). This is the ANL-GPU path (parsl → Polaris
  or a Slurm cluster from `midas_parsl_configs`).
- **Series integration** (`midas_integrate_series`): `compute_target`
  (`auto|local|remote|cpu|cuda|mps`). Every run reports a `compute` block
  (target + reason + cost). `compute_target="remote"` (or `APEXA_GPU_AUTO_DISPATCH`)
  returns a dispatch recommendation for a large batch instead of grinding on CPU.

## The binding constraint: data locality
Put the GPU where it can already see the data. Two ANL options:
1. **A lab GPU node that mounts the beamline filesystem** (e.g. `/scratch/s1iduser`)
   — pragmatic, low-latency, no queue, **no data movement**. Register it as
   `APEXA_GPU_ENDPOINT` (ssh) or a Globus-Compute endpoint. **Best for interactive
   beamtime.**
2. **ALCF Polaris** via the shipped `midas_parsl_configs` `polarisConfig`
   (`--machine polaris`). Best for very large sweeps, but Polaris cannot mount
   `/scratch`, so **stage data via Globus** first.

## Standard practice (summary)
- Single frame / calibration / QC → **stay local** (Tier 0/1), never prompt.
- Batch series / FF-PF-NF on a **GPU host** → run local GPU (Tier 1),
  `device=cuda`, `--shard-gpus auto`.
- Batch series / FF-PF-NF on a **CPU-only host** → **Tier 2**: offload to the GPU
  endpoint that sees the data; only fall back to CPU (with a warning) if no
  endpoint is configured.
- Always emit the `compute` decision in the result so the run is auditable, and
  keep the outcome-manifest + verify-before-report discipline for remote runs.
