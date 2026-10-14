# APEXA analysis-plan schema (`APEXA_plan.yaml`)

A **plan** is a reviewable, reproducible YAML record of an integration/scattering
run: inputs, geometry, dark handling, grid, compute target, plus the assumptions
the planner *inferred* and why. It is the artifact the **acquisition watcher**
([`apexa_acquisition_watcher.py`](../apexa_acquisition_watcher.py)) writes when a
live scan completes, and the data model it uses to project a
`midas_integrate_series` call.

The idea is adapted from the reflectometry `nr-analyzer`/AuRE pipeline
(`context.md → plan/job_*.yaml → models/*.py → fit`): provenance (`assumptions`)
so a run reproduces without re-invoking the model, and a `perform_execution`
gate.

### Scope — this vs. the FF-HEDM graph

Read this together with `docs/LANGGRAPH_FF_HEDM_SPEC.md`. The two do **not**
overlap:

| | Owns |
|---|---|
| **FF-HEDM graph** (`APEXA_WORKFLOW_MODE=graph`) | The gated FF procedure — calibrate → in-plane tx → reconstruct, with human decision gates, checkpoint/resume, and per-step idempotency. It writes `APEXA_ffhedm_workflow.json`. NF/PF need their matching procedures and tools. |
| **Plan schema + watcher** (this doc) | **Acquisition-driven triggering** for the azimuthal-integration family (`waxs`/`saxs`/`integration`): detect a completed scan and fire one idempotency-guarded `midas_integrate_series`. |

So the plan YAML is **not** a second agent-facing planning paradigm — for HEDM
the watcher writes the plan for the record but hands execution to the graph
(which alone can answer the handbook gates). `midas_integrate_series` carries the
`@idempotent` guard, so a stem the watcher fires twice replays the prior result
rather than re-integrating.

## Quick start

```python
from apexa_plan import APEXAPlan, Sample, Beam, Calibration, DataSpec, DarkSpec

plan = APEXAPlan(
    technique="waxs",
    instrument="1-ID",
    sample=Sample(name="JL_0Nb", material="Ni-alloy", expected_phases=["FCC"]),
    beam=Beam(energy_keV=61.332),
    calibration=Calibration(calibrant="CeO2", parameter_file="ceria_params.txt"),
    data=DataSpec(image_dir="/scratch/beam/JL_0Nb", pattern="JL_0Nb_*.vrx.h5",
                  stem="JL_0Nb", data_location="exchange/data", expected_count=180),
    dark=DarkSpec(source="file", kind="after", location="exchange/data"),
)
plan.assume("dark.kind", "after", "beamline default is a trailing dark")
issues = plan.validate()          # [] means ready to run
plan.to_yaml("APEXA_plan.yaml")
kwargs = plan.to_integrate_series_kwargs()   # -> midas_integrate_series(**kwargs)
```

## Top-level fields

| Field | Meaning |
|---|---|
| `technique` | `ff-hedm` \| `nf-hedm` \| `pf-hedm` \| `waxs` \| `saxs` \| `integration` \| `calibration` |
| `instrument` | beamline id (e.g. `1-ID`, `20-ID`) |
| `describe` | one-line human summary |
| `sample` | `name`, `material`, `space_group`, `lattice{a,b,c,alpha,beta,gamma}`, `expected_phases[]`, `hypotheses[]` |
| `beam` | `energy_keV` or `wavelength_A` |
| `detector` | `name`, `distance_mm`, `pixel_size_um`, `beam_center_px[x,y]`, `tilt_deg[]` |
| `calibration` | `calibrant`, `parameter_file` |
| `data` | `image_dir`, `pattern`, `stem`, `data_location` (HDF5 path), `expected_count`, `exclude_substring` |
| `dark` | `source` (file\|embedded\|none), `kind` (after\|before\|any), `dir`, `pattern`, `location` |
| `grid` | integration grid in **any** convention: `r_*`, `two_theta_*`, `q_*`, `n_channels`, `eta_*` |
| `compute` | `target` (auto\|local-cpu\|local-gpu\|remote-gpu), `n_cpus`, `machine`, `n_nodes`, `shard_gpus` |
| `result_folder` | where outputs are written |
| `assumptions` | list of `{field, value, reason}` — provenance for inferred defaults |
| `perform_execution` | gate: `false` requires human review before anything runs |

`data_location`/`dark_location` matter: a separate `.h5` dark's frame is at
`exchange/data`, **not** the integrator's default `exchange/dark` (see
`CLAUDE.md` → batch integration).

Watcher plans also record `data.images`, the exact list of files observed for
that directory/stem. Execution passes this list to the integration tool instead
of expanding the stem wildcard again. `expected_count` counts **files**, not
frames within a multi-frame HDF5 file. The watcher requires non-empty regular
files and `--quiet-seconds` since the newest write even when a count is supplied;
an excess count requires review of grouping. A quiet period is a heuristic,
not proof that an HDF5 writer has closed its file. Failed/partial executions
retry at the polling interval, and successful jobs are remembered in process
memory only. `--result-folder` is an output root with separate subfolders for
each relative acquisition directory and stem.

## Examples

### WAXS / azimuthal integration series

```yaml
technique: waxs
instrument: 1-ID
describe: Azimuthal integration of JL_0Nb WAXS series, CeO2-calibrated.
sample:
  name: JL_0Nb
  material: Ni-alloy
  expected_phases: [FCC]
beam:
  energy_keV: 61.332
calibration:
  calibrant: CeO2
  parameter_file: ceria_params.txt
data:
  image_dir: /scratch/beam/JL_0Nb
  pattern: JL_0Nb_*.vrx.h5
  stem: JL_0Nb
  data_location: exchange/data
  expected_count: 180
dark:
  source: file
  kind: after
  location: exchange/data
grid:
  q_min: 0.5
  q_max: 12.0
  n_channels: 2500
compute:
  target: auto
  n_cpus: 8
assumptions:
- {field: dark.kind, value: after, reason: no explicit dark timing; beamline default is trailing dark}
perform_execution: false
```

### SAXS (Q-space, stitched segments)

```yaml
technique: saxs
instrument: 9-ID
sample: {name: polymer_A}
beam: {energy_keV: 21.0}
calibration: {calibrant: AgBeh, parameter_file: agbeh_params.txt}
data: {image_dir: /scratch/saxs/polymer_A, pattern: polymer_A_*.h5, stem: polymer_A}
dark: {source: embedded}
grid: {q_min: 0.004, q_max: 0.4, n_channels: 1000}
perform_execution: false
```

### FF-HEDM (grid carries ω-frame series; distance/energy from params)

```yaml
technique: ff-hedm
instrument: 1-ID
sample:
  name: Ti64_grain
  material: Ti-6Al-4V
  space_group: P6_3/mmc
  lattice: {a: 2.95, c: 4.68}
  expected_phases: [alpha-Ti, beta-Ti]
  hypotheses: [residual strain, strong basal texture]
beam: {energy_keV: 61.332}
calibration: {calibrant: CeO2, parameter_file: ff_params.txt}
data:
  image_dir: /scratch/hedm/Ti64
  pattern: Ti64_*.ge5
  stem: Ti64
  expected_count: 1440   # e.g. 1440 omega frames = complete rotation
dark: {source: file, kind: before}
compute: {target: remote-gpu, machine: polaris, n_nodes: 2, shard_gpus: true}
perform_execution: false
```

## Validation rules (`validate()`)

Returns a list of human-readable problems; empty means ready.

- `technique` must be recognised.
- integration/HEDM/`calibration` techniques require `calibration.parameter_file`
  and either `data.image_dir` or `data.stem`.
- beam energy/wavelength is required **only** when it can't come from a
  parameter file (i.e. bare `calibration`, or no `parameter_file` set) — for
  integration/HEDM the energy lives in the MIDAS params.
- `dark.source` must be `file`, `embedded`, or `none`.

## Acquisition watcher

The watcher turns a live scan directory into plans + runs:

```bash
# dry-run: print the plan each completed stem WOULD run
python apexa_acquisition_watcher.py /scratch/beam/live \
    --technique waxs --param-file ceria_params.txt \
    --data-location exchange/data --expected-count 180

# actually integrate each completed stem, once
python apexa_acquisition_watcher.py /scratch/beam/live \
    --technique waxs --param-file ceria_params.txt --execute
```

Completeness is judged by `--expected-count` when known, otherwise by a
`--quiet-seconds` idle period on the newest frame (scan finished). Files
containing `--exclude-substring` (default `dark`) are excluded from the sample
set. Each stem fires at most once; the plan is written as
`APEXA_plan_<stem>.yaml` beside the data.
