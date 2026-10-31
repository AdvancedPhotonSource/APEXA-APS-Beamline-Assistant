# GSAS-II workflows in the APEXA canvas

APEXA now exposes the official tutorial catalog, installed scripting operations,
agentic powder jobs, policy comparisons and versioned continuation sessions.
The catalog contains **64 tutorials fetched on 21 October 2026**, with original
URLs, source hashes and full readable text. Each entry distinguishes native
operations from missing external or specialized workflow steps.

## Launch the WebUI

From the APEXA repository, configure the scientific environment and start the UI:

```bash
export APEXA_GSASII_PYTHON="$HOME/miniconda3/envs/GSASII/bin/python"
export APEXA_AGENTIC_GSAS2="$HOME/Git/Agentic-GSAS-II"
./start_web_viewer.sh
```

Open `http://localhost:8001`. Keep `gsas2:gsas2_server.py` enabled in
`servers.config`. An existing APEXA provider configuration is needed for chat;
learned diffraction models and their checkpoints are not required. Restart the
APEXA service after updating the backend so it advertises the new tool schemas.

## Live fit evidence and decisions

The refinement canvas reads durable job state every three seconds while a job
is active, then stops polling on completion, cancellation or failure. Refreshes
do not require model calls and never start a broker or refine a project. Restart
APEXA and reload the browser after installing this backend/frontend update.

New worst-fit runs record a `decision_stream` beside the project. Use the step
selector to inspect observed/calculated profiles, weighted residuals, reflection
markers, individual peak windows, actual candidate scores, exclusions and
rollback reasons. The trial curves are captured before rollback. The requested
parameter group and the parameters actually varying in the trial are separate.
Per-histogram residuals and window losses show whether an improvement in the
combined objective hides deterioration in another histogram. Retained changes
that increase Rwp are explicitly labeled as worsening.

The overview stays visible above the step inspector: current kept Rwp, change
from the first recorded decision baseline, both datasets' residuals, the full
step history and the largest remaining residual regions. Selecting an earlier
step does not stop the overview following the run. Trial curves remain separate
from the kept fit until their disposition is recorded.

To follow the same run in a terminal, from the APEXA repository:

```bash
.venv/bin/python scripts/watch_powder_refinement.py gsas2-<group-id>
```

The monitor prints a cumulative step table, per-histogram and peak-window
changes, final checks and the report path. Add `--log /path/to/live.log` to save
the text or `--details` for candidate scores and skipped-parameter reasons.
It reads existing records and exits after completion and report publication.

Classification scores are heuristic evidence, not probabilities. In the current
joint strategy only histogram 0 supplies the residual-shape ranking bias; the
other histograms remain visible for diagnosis and contribute to joint derivatives.
The canvas labels this scope rather than claiming all displayed regions chose
the action. Overlapping window losses must not be added. Overview curves are
bounded using extrema-preserving sampling; window losses use all fitted points.

Older jobs retain their saved decisions and final PNGs. Missing historical peak
snapshots and candidate rankings are shown as unavailable. A separately generated
final-fit inspection is labeled as analysis after completion, never as evidence
of a past decision. The job's GPX and result JSON remain unchanged.

Reports include final fit PNGs and saved decisions. A legacy joint job's Le Bail
`Rwp` refers to histogram 0; it must not be compared with combined Rietveld Rwp
as a joint fit floor. Report generation success is separate from scientific
qualification, including covariance failure and unavailable calibration evidence.

### Where the files go

Durable execution keeps its original artifacts under `APEXA_GSAS2_JOBS/engine`.
Submissions also start a separate publisher that copies each terminal job into
`output_dir/<engine-job-id>/`: GPX projects, fit PNGs, result JSON, listings,
logs and any recorded decision evidence. The group `.outputs.json` maps source
paths to exported paths and SHA-256 hashes. Files inside `result.json` retain
their original paths for provenance. Changed copies receive new names rather
than overwriting user edits. Publication resumes when the MCP server restarts;
it never starts or repeats a scientific calculation. Failed jobs may export
diagnostic artifacts and retain their failed state.

When all jobs in a submission finish, the publisher automatically writes
`output_dir/powder_report_<group-id>.html`. This self-contained report includes
fit plots, recorded decisions, per-histogram metrics and scientific checks,
including blocked or failed outcomes. The `.outputs.json` manifest and status
response include the report path, hash and generation status. Report generation
does not require another chat prompt. A report failure is retried independently
of refinement. `powder_report(job_id)` also defaults to the recorded output
folder for group and engine IDs; `output_path` overrides the destination.
Run IDs and content suffixes preserve earlier reports when a folder is reused.

## Start from a prompt

> Show the GSAS-II tutorial catalog in the canvas. Find the CW neutron YIG
> example, read its refinement and constraint steps, and inspect the operations
> available in this installation. Use my supplied pattern, instrument file and
> structure to carry out the applicable workflow and show progress and results.

`gsas2_tutorials` searches or reads a page. `gsas2_capabilities` returns the
installed operation signatures. `gsas2_workflow_submit` executes a structured
plan in a new GPX copy and returns immediately. `gsas2_workflow_status` updates
the canvas with progress, failures, project location and interactive numerical
plots. `gsas2_workflow_cancel` stops a running workflow.

The canvas supports filtering tutorials, opening sources, requesting a workflow,
zooming fit/residual plots and entering the next requested analysis. The scientific
model and input metadata must be supplied or established before execution.

## Native operation example

This illustrates parameter operations on a **prepared project**. The agent must
select the appropriate constraints, flags and stage order for the actual data.

```json
[
  {"target":"project", "method":"set_refinement", "kwargs":{
    "refinement":{"set":{"Background":{"no. coeffs":6,"refine":true}}}}},
  {"target":"project", "method":"refine"},
  {"target":"phase", "id":0, "method":"set_refinements", "args":[{"Cell":true}]},
  {"target":"project", "method":"refine"},
  {"target":"phase", "id":0, "method":"export_CIF", "kwargs":{"outputname":"refined.cif"}}
]
```

Supply this to `gsas2_workflow_submit` with `input_gpx`, a fresh `output_dir`,
and a wall-clock budget. Supported operation families include constraints,
parameter limits, explicit refinement recipes, atomic properties, single-crystal
refinement, powder simulation, background/peak fitting, profile exports,
image calibration/masking/integration, PDF calculation, prepared SASD models,
texture, strain, indexing, charge flipping and stacking calculations.

Methods and numerical functions are enumerated; Python source and refinement
callbacks are not accepted. `$ref` refers to a prior operation's named output;
`$data` references an explicit path in the current GSAS-II project; `$array`
constructs a numerical array. `set_data` supports explicit scientific model setup
where GSAS-II has no high-level setter. These operations require knowledge of the
GSAS-II data schema and do not automatically generate a correct physical model.

## Advanced tutorial operations

Use `kind="analysis"`, a `name`, and `kwargs` matching `gsas2_capabilities`.

| Tutorial family | Operation | Required setup |
|---|---|---|
| Parametric sequential fitting | `expression`, `parametric_fit` | Named numerical arrays, formula, parameter guesses and optional uncertainties |
| Cluster/outlier analysis | `cluster_patterns` | Histograms with identical axes; explicit normalization and metric |
| Fundamental-parameters calibration | `fundamental_profile`, then native peak fitting/calibration | NIST convolver settings; lengths in meters |
| Magnetic propagation vectors | `k_vector_search` | Nuclear structure, satellite d-spacings and seekpath; general search also needs kvec_general |
| Magnetic subgroup generation | `SUBGROUPS` / `Bilbao.BCS_API` functions | Network access and the user's Bilbao API key |
| Distortion modes | `isodistort` | Prepared ISODISTORT phase settings; network access and structure submission |
| PDFfit/fullrmc/RMCProfile | `kind="external_model"` | Prepared phase RMC settings and an installed external runtime |
| Small-angle scattering | `GSASIIsasd` functions | Prepared SASD project; the upstream small-angle importer is a placeholder |

For example, the following fits a linear trend to supplied sequential results:

```json
{"kind":"analysis","name":"parametric_fit","kwargs":{
  "formula":"a + b*T","variables":{"T":[100,200,300,400]},
  "observed":[5.01,5.02,5.03,5.04],"sigma":[0.002,0.002,0.002,0.002],
  "parameters":{"a":5,"b":0.0001}
}}
```

These illustrative numbers are not experimental results. The operation returns
the fit, residuals, convergence and local covariance. It does not account for
correlations among input sequential results. Pseudo variables use arithmetic,
`pi`, and `exp/log/sqrt/sin/cos/tan/arcsin/arccos/abs/minimum/maximum`.

To prepare external model files from an existing configured phase:

```json
{"kind":"external_model","kwargs":{
  "engine":"pdffit","phase":0,"data_dir":"/absolute/path/to/pdf-data",
  "execute":false
}}
```

Use `execute=true` when execution is requested and the runtime is configured.
The adapter stages data, generates native inputs, and records logs and hashes
under the workflow directory. It starts a fresh external model. GSAS-II's
sequential PDFfit script template still requires explicit per-dataset operations.
External results are retained as artifacts and are not automatically imported
into the GPX. `read_array` loads numeric text exports as columns; `plot` binds
these or prior operation arrays into a canvas figure.

Example prompts:

> Refine this prepared neutron project using the YIG tutorial's constraints and
> stage order. Show each stage and the final residuals in the canvas.

> Cluster histograms 0 through 7 with L2 normalization and correlation distance.
> Show the distance matrix and report any outliers, without removing patterns.

> Fit a thermal-expansion expression to my sequential cell results and show the
> residuals, fitted parameters and uncertainty limitations.

## Agentic refinement policy

Queued jobs now expose the same relevant controls as single-pattern refinement:

| Option | Meaning |
|---|---|
| `refine_atoms` | Enable atomic coordinate/ADP/occupancy candidates in the numerical scheduler |
| `bkg_terms` | Initial background order, 1–18 |
| `max_steps` | Scheduler step budget, 1–1000 |
| `max_wall_seconds` | Numerical scheduler budget per fit, positive and at most 86400 |
| `run_lebail_companion` | Request the companion profile comparison |
| `phase_search` | Bounded candidate-search settings, only in search mode |
| `optimization` | Numerical policy-search settings, only in optimize mode |

The engine validates policy before accepting a job and again in its worker.
Configured candidates do not prove that each parameter was actually released.
Inspect the recorded decision trace. Scheduler time is distinct from total
process time, including preparation, companions and output generation.

## Continue diagnosis and refinement

1. Run a powder job or `refine_pattern`. Retain the full engine `result_path`.
2. Call `powder_session_create(result_json=..., output_dir=..., refine_atoms=...)`.
   This copies the completed project and preserves its scientific limitations.
3. Call `powder_improve(session_dir=..., max_rounds=3, max_wall_seconds=900,
   auto_adopt=true)` with a configured image-capable provider.
4. Poll `powder_session_status`. The canvas shows the active result, rounds,
   trial decisions, stop reason and saved revisions.

Each round creates fresh evidence and evaluates up to three isolated proposals.
The accepted candidate with the lowest global Rwp can be adopted. Adoption checks
the active revision, observation, baseline hash, request hash, candidate hash,
engine version and recorded acceptance evidence before publishing a new head.
Every previous version is retained. Stale evidence cannot advance the session.

Use `auto_adopt=false` for one round of proposals and trials without adoption.
Manual adoption uses `powder_adopt` with the returned decision path and expected
revision. `powder_session_control(action="stop")` requests a stop at an operation
boundary; an in-flight trial may finish. Call `powder_improve` again to resume
from the active revision. Use `action="restore"` with both revision numbers to
restore a saved version after stopping the loop.

Numerical acceptance does not resolve external calibration or phase-completeness
limitations. Such limitations remain in the active result, which can remain
blocked despite a numerically accepted improvement. No result is promoted to
scientifically qualified by this loop.

## Compare numerical alternatives

`powder_strategy_sweep_submit` runs 2–32 explicit policy alternatives on one
observation using isolated durable jobs. `powder_compare` checks equality of
measured arrays, weights and fitted domains before ranking completed fits.
Selection requires passed physical-state, covariance and convergence checks.
All records, failures and exclusions remain visible. Reported phase-fraction
ranges measure sensitivity across evaluated alternatives; they are not confidence
intervals. A comparison does not adopt a model automatically.

`powder_refine_submit(mode="optimize")` adaptively searches background order and
size/microstrain refinement choices using Gaussian-process expected improvement.
For example, pass `optimization={"n_trials":12,"seed":7,"background_min":3,
"background_max":12,"trial_seconds":120}` and a total `max_wall_seconds` budget.
The report retains every trial, selection score, exclusion and proposal method.
The optimizer uses numerical fits and requires no trained checkpoints. Its
surrogate uncertainty is not a calibrated probability that a phase is present.

Search mode optionally accepts `phase_search={"group_equivalent":true}` to group
candidate CIFs with pymatgen before screening. It records every representative,
member, matching tolerance and parse failure. The default keeps distinct file
contents separate; structural matching is not proof of indistinguishable powder
patterns.

## Coverage limits

The tutorial catalog and research inventory expose gaps explicitly. General
FullProf/BGMN/MAUD comparison adapters, calibrated model probabilities and
instrument-specific robotic feedback remain
outside this implementation. Learned models are deferred at the user's request.
Prepared external models and service-dependent operations require their own
inputs and dependencies; a route in the catalog does not make them runnable on
an unconfigured machine.

Read-only discovery on 21 October 2026 found **116 native methods and 33 scientific
functions**, all callable in the installed GSAS-II. The workbench also exposes
six advanced analyses and one external-model operation. PDFfit, fullrmc and
RMCProfile runtimes were **not configured** in that environment. No tutorial
reproduction, numerical acceptance run or external-engine execution was performed
as part of this implementation pass.

Calling a native method is not evidence that an entire tutorial has been
reproduced, that the refinement is accurate or that a method outperforms the
frameworks discussed in the paper. This implementation does not change the
paper's benchmark measurements or qualification claims.
