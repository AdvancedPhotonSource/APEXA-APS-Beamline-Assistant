# APEXA scientific-agent evaluation and CVPR study plan

Prepared 28 October 2026. This is a proposed study, informed by the [software audit](AUDIT.md) and existing local data. It contains no new harness ranking and no new numerical reconstruction results.

## Recommended contribution and publication scope

The strongest research question is: **Can an execution and acceptance layer that checks scientific evidence reduce accepted-but-wrong results while preserving useful completion, across different agent planners and scientific inverse problems?** APEXA's contribution would be a method for maintaining dataset identity, executing reviewable plans, recovering jobs, validating physical outputs, and binding claims to artifacts. The UI and number of tool integrations support the system but are not sufficient research contributions.

The visual inverse problems are meaningful: infer detector geometry from diffraction images, and infer grain orientations/positions from rotating diffraction observations. Present the measurement model, ambiguity and uncertainty, and how an agent diagnoses a failure and selects the next defensible analysis. Include image evidence, held-out reprojection, and numerical comparisons. A deployment narrative without a clearly evaluated method will be vulnerable to the criticism that this is primarily software integration.

The [CVPR 2027 call for papers](https://cvpr.thecvf.com/Conferences/2027/CallForPapers) lists **Agentic AI** and **AI for Science and Scientific Discovery** as main-program topics. This is not evidence of separate guaranteed submission tracks. It lists paper enrollment on **10 November 2026**, submission on **16 November**, and supplementary material on **23 November**, all 23:59 Anywhere on Earth. As of this audit, the paper deadline is 19 days away. Verify the live policies and submission categories before enrollment; future workshop calls have their own rules.

A defensible initial paper could focus on calibration plus a smaller FF-HEDM generalization study. Make FF-HEDM a co-primary task only if its data, trusted evaluation and repeatable execution can be frozen promptly. Do not claim general beamline autonomy from either offline analysis task. Autonomous alignment requires a separate commissioned hardware study.

## Existing data and what it actually establishes

### Task 1: detector rotation calibration

The resolved data root is `~/Box/01-Research/01-APEXA/ai_tune_rot_series`, with analysis records in `APEXA-Analysis/` and exposures in `data/rot_series/`.

The final directory/stat inspection lists **43 HDF5 files, 14,988,968,724 bytes (14.99 GB)**, covering scans 261–303. Earlier observations found six and then 30 files while the directory changed. The final count does not certify that all cloud-backed bytes are locally hydrated or intact. Freeze and fingerprint a complete acquisition copy before trials. See [rotation-availability-final.json](rotation-availability-final.json).

The inspected sample has `/exchange/data` with shape `(21, 2880, 2880)` and `uint16` pixels, plus dark/white datasets. Existing analysis records describe skipping frame 0, averaging frames 1–20, 63.314 keV energy, 150 µm pixels and `ImTransOpt 2`. Those are recorded procedure choices requiring confirmation against the acquisition; their presence in a previous report is not independent validation.

| Existing-summary observation | Result | Interpretation |
|---|---:|---|
| Calibrants / exposures | CeO2: 22; LaB6: 21 | One related acquisition series, not 43 independent beamlines/specimens |
| Existing gate outcomes | 35 pass, 8 fail | These are the summary's labels |
| Accepted fits within 0.1° of the leave-one-out robust trend | 35 / 35 | Strong internal consistency on the selected scalar; not absolute metrology |
| Failed fits outside 0.1° of that trend | 8 / 8 | Useful challenge cases and failure diagnostics |
| Accepted fits with any recorded bound flag | 23 / 35 | Includes periodic phase parameters; a flag alone does not prove an incorrect fit |
| Accepted fits with a non-`phi*` bound flag | 11 / 35 | Deserves identifiability and model-sensitivity analysis |

These counts come from `rotation_series_calibration_summary.csv` and the captured benchmark's `load_truth(..., value_col='tz_deg')`; [rotation-inspection.json](rotation-inspection.json) retains the derived rows. [The diagnostic figure](rotation-consistency.png) visualizes this consistency check. No new calibration was run.

**Correct the existing benchmark narrative:** scans 269 and 276 at exactly −3° and +3° are failed-gate cases in this summary. They are not examples of the 35 accepted geometries being wrong. Corrected wider-bound runs and a 12-row reduced-distortion control summary already exist; this audit inspected those records but did not independently rerun them. A boundary warning, a low residual and an accurate tilt are three different observations.

The current leave-one-out trend is estimated from other APEXA calibrations. It can detect deviations from the series but shares systematic errors with those fits. It also tests only one reported component. It cannot establish absolute distance, tilt, center or distortion accuracy. The README's description of this reference as being graded directly against the goniometer is stronger than the inspected evaluator supports.

Small motor arrays were read from 30 exposures. `SMS/aero` varies within the 21-frame files; `WM/AeroX` is constant at about 0.3102 and detector `detY` is about 900. Scan start/end metadata is generally −11→10, with a 169→190 record. These do not, by themselves, identify an independent per-exposure detector-tilt truth. Resolve which device moved, its coordinate transform, offset, direction and uncertainty with acquisition records/instrument staff. Do not substitute the sample rotation coordinate for detector pose. See [rotation-motor-metadata.json](rotation-motor-metadata.json).

**Reference hierarchy:** first, calibrated independent pose/readback/metrology with uncertainty; second, held-out diffraction constraints and independently implemented geometry checks; third, simulation with hidden truth and realistic nuisance effects; fourth, cross-fit series consistency as a secondary diagnostic. Agreement with another optimizer helps but may retain the same physics/convention errors. A synthetic generator using the same model as the fitter also needs mismatch tests to avoid an inverse crime.

Use held-out rings/azimuth sectors or exposures for scientific validation, with the holdout definition fixed before fitting. Report geometry error only for identifiable quantities, uncertainty/coverage, ring reprojection error, residual structure by radius/azimuth, downstream integration quality, sensitivity to distortion choice, and selected correction provenance. Frame splitting alone may leave strongly correlated images on both sides.

### Task 2: gold FF-HEDM grain reconstruction

The local MIDAS checkout found is **`~/Git/MIDAS`**, rather than `~/opt/MIDAS`. Useful assets are:

| Asset | Role and limit |
|---|---|
| `FF_HEDM/Example/GrainsSim.csv`, `Parameters.txt` | Synthetic reference/configuration. The actual current CSV declares **250 grains**; a historical manual describes a three-grain example. Pin the file, not the historical count. |
| `tests/test_ff_hedm.py` | Existing simulation/reconstruction harness with grain-count/seed controls; a starting point for hidden-truth tests |
| `packages/midas_transforms/tests/data/c_goldens_px/Au_FF_000001_pf.analysis.MIDAS.zip` | Approximately 15.76 MB local processed fixture; not established here as a complete raw gold acquisition or independent ground truth |
| `manuals/ff-hedm/LAB_NOTEBOOK.md` | Records `Au3_cubes_ff_000008`, APS 1-ID GE5, 1440 used frames, 95 keV, 200 µm pixels; documents analysis and reference disagreements |
| Notebook paths including `copland:/home/s20a/nfdev_jul26_ff_hs/` | Leads to beamline-resident data; no SSH/data transfer or reconstruction was performed in this audit |

The inspected `GrainsSim.csv` SHA-256 is `b71b40bc0dfa0fc195e1a8b6cd19121ba5e6642db7ca7be94afdba6e3be776b5`. Keep simulation truth inaccessible to the planner but available to the evaluator.

The notebook argues for a parent grain plus a Σ3 twin in one real gold dataset and reports that a C pipeline split them into six grains. These are existing notebook claims, not conclusions independently reproduced by this audit. They show why grain count or C/Python agreement cannot serve as ground truth. The repository also describes multiple gold acquisitions: establish an exact dataset ID before mixing records.

Start with hidden-seed simulated gold grains, including realistic noise, missing/overlapping spots, twins, detector transforms and geometry perturbations. Pair this with a curated real dataset whose raw frames, omega array, corrections, geometry, energy and coordinate conventions are complete. Predeclare which parameters may be fitted and which require operator evidence.

Score one-to-one grain matching using crystal-symmetry-aware misorientation and physically justified matching thresholds. Report grain precision/recall, split/merge/false grains, orientation error, positions and strain where identifiable, held-out spot reprojection/assignment, completeness and twin preservation. Report failure and abstention separately. Weight the independent experimental unit appropriately: thousands of spots from one grain are not thousands of independent validation cases.

Keep gold notebook answers, expected grain counts/orientations and prior reconstruction outputs out of benchmark knowledge. General scientific procedures can be retained after sanitization. Freeze the sanitized corpus and inspect automatic capsule/retrieval paths too.

## Experimental design that separates the questions

Use a **fixed numerical engine/build** to compare orchestration. Put a second engine, such as an independently qualified calibration implementation, in a separate science-validation experiment. Otherwise improvements may simply reflect numerical software changes.

| Experiment | Hold fixed | Vary | Supported claim |
|---|---|---|---|
| Fixed recipe baseline | Inputs, engine, initial estimates, compute budget | No language model; scripted qualified workflow | Whether adaptive agency adds value beyond the recipe |
| Tool-matched planners | Model endpoint/version, data, task, manuals, typed adapters, engine, compute/token limits | APEXA, Pi, Claude Code, OpenCode | Effect of planner/harness configuration under the shared scientific interface |
| Common shell/CLI condition | Same documented CLI, sandbox, data, engine and budget | Harness | Ability to operate the same low-level interface; do not silently retain APEXA-only typed tools |
| Native systems | Data, task, reporting/evaluation rules; document model/tool differences | Each system's practical configuration | End-to-end deployed-system performance, with differences disclosed |
| APEXA component ablations | Planner/model, tool surface, capsules, metadata, budgets | One mechanism at a time | Causal evidence for the proposed contribution |
| Engine/reference validation | Approved input/geometry contract and independent evaluator | Numerical backend or reference method | Scientific accuracy/robustness, distinct from harness effects |

For component ablations, separately vary: acceptance enforcement; capsule/retrieval knowledge; metadata clarification policy; deterministic workflow vs free agent loop; claim-to-artifact binding; and durable recovery under injected interruptions. Start with the few mechanisms central to the paper. The current A1→A2 change disables stage/capsule injection and changes metadata inference as well as enforcement, so it is not a single-mechanism contrast.

**Current baseline correction:** [Pi's official site](https://pi.dev) now documents built-in MCP and Codemode, and installed Pi 1.2.0 includes `docs/mcp.md`. Remove the “Pi has no MCP” design assumption. Version probes found Claude Code 2.1.312 and OpenCode 1.18.23; [harness-versions.json](harness-versions.json) records executable paths/hashes. These version checks did not start model sessions. Test each adapter with a no-model fixture before launching paid trials.

Hold requested and actual model identity, reasoning settings, token limits, context limits, retries and stopping rules fixed where supported. The same gateway nickname does not prove equivalent requests: capture resolved model identifiers and normalized provider metadata, including transport differences. If a harness cannot support a common setting, report it as a constraint rather than claiming a controlled model-only comparison. Record cold/warm cache condition and separate queue time from compute time.

For shared tools, use the same server build and schemas, read/write roots, engine environment and permissions. Reset harness user configuration, extensions, memories, sessions and caches with task-specific configuration directories. A new working directory alone is insufficient. Run sequentially or with explicit resource partitions so competing arms do not change one another's compute budget. Randomize balanced trial order.

An optional infrastructure candidate is [NeMo Fabric](https://github.com/NVIDIA/NeMo-Fabric), whose current adapter matrix includes Claude, OpenCode and Pi. However, the captured matrix marks Pi's MCP configuration as not exposed by that adapter, and OpenCode's normalized tool policy as not exposed. Native Pi's MCP support is a separate fact. Do not adopt Fabric and assume it makes the experiment tool-matched automatically. Evaluate a no-model contract fixture first; keep or extend direct adapters if needed.

## Outcomes and independent grading

Require a small common final artifact manifest: selected result ID/path/hash, execution run ID, `accepted`/`provisional`/`rejected`/`abstained`, quantities/units, evidence references and explanation. Score numerical artifacts in an evaluator inaccessible to agents. Preserve natural-language finals for a separate claim audit; schema-invalid responses are recorded, not repaired silently by the grader.

The captured classifier labels “The initial attempt succeeded numerically, but validation failed. Do not use this geometry” as accepted. Its artifact extractor can choose a recent seed/intermediate file instead of the adopted result. Replace both before collecting evidence. Signed/durable execution records, explicit artifact adoption and input/output identities are stronger than modification times or transcript keywords.

| Metric | Definition / reporting requirement |
|---|---|
| Accepted-wrong rate | Accepted result fails independent numerical/physical criteria, divided by all trials; also report conditional risk among accepted results |
| Valid completion / coverage | Scientifically valid accepted results divided by all trials; prevents a system that rejects everything from appearing best |
| Unsupported quantitative claim rate | Final claims lacking the required artifact/evidence binding, with a blinded audit of disputed cases |
| Scientific error | Geometry or grain metrics above, reported separately from narrative confidence and process exit code |
| Recovery reliability | Completed/reconciled jobs after injected UI/API/worker interruption; duplicate submissions and orphaned processes |
| Efficiency | Wall time, time to valid result, model/tool calls, tokens/cost, CPU core-seconds, peak RAM/VRAM, I/O and retries |
| Operator burden | Required clarifications/interventions and time to reviewed result, with a fixed intervention protocol |

Do not combine all these into an arbitrary single score. Present scientific risk against coverage and cost. A lower accepted-wrong rate bought by abstaining on nearly every case is not sufficient improvement.

Predeclare failure categories and budgets, including timeout, engine unavailable, malformed artifact, metadata conflict, validation rejection and infrastructure fault. Distinguish a wrong scientific estimate from a benchmark startup failure. Keep an intention-to-run accounting and a clearly justified sensitivity analysis for infrastructure faults; do not silently remove failed baseline trials.

Use paired tasks and repeat stochastic trials, initially a small pilot to estimate variance and costs. Final trial counts should follow the effect size and power/precision target. Report paired uncertainty and clustering by acquisition/specimen; do not treat repeat seeds or neighboring rotation exposures as independent physical replications. With only two calibrant groups, broad generalization confidence remains limited even with many agent repeats. Additional independent acquisitions are more valuable than excessive reruns of the same 43 files.

## Fix the rig before spending on comparison runs

1. Freeze the runner, clean runtime configuration, engine builds, sanitized knowledge and input manifests. Retain withdrawn historical results only as development records.
2. Repair sandbox startup. In the captured source, the denied benchmark subtree also contains APEXA's own driver; a real `--help` probe failed before startup. Move the runnable driver into an allowed minimal runtime while keeping evaluator/reference files inaccessible. Validate all arms' permitted paths and denied-reference canaries.
3. Verify identical tool access and one-factor ablations. Disable undeclared APEXA MCP tools in shell-only conditions. Repair the Pi capability assumption.
4. Exercise artifact selection and result classification against adversarial fixtures: rejected successful intermediate, missing output, stale cache, nonfinite values, altered input, quoted success text and multiple candidates.
5. Predeclare scientific thresholds using independent references and uncertainty. Do not choose 0.1° merely because it separates current plots; justify the task tolerance physically.
6. Run a small smoke study, inspect full traces and fix infrastructure only. Freeze again before the held-out comparison. Make development and evaluation splits explicit.

## Feasible submission sequence

| Dates in 2026 | Deliverable | Decision |
|---|---|---|
| Oct 28–31 | Freeze task contracts; repair benchmark startup/scoring; locate independent rotation metadata and exact gold data | If gold inputs/reference remain unresolved, make calibration primary and limit gold claims |
| Nov 1–4 | Qualified fixed-recipe baseline; pilot matched harnesses and central ablations; measured cost/variance | If scientific truth or common-model/tool access is inadequate, narrow the claim before scaling |
| Nov 5–9 | Held-out runs, paired analysis, failure taxonomy and reproducible figures | Stop adding mechanisms; quantify remaining uncertainty |
| By Nov 10 AoE | Enrollment, title/abstract reflecting the actual supported contribution | Do not promise autonomous hardware if only offline analysis was evaluated |
| Nov 10–15 | Write, independent scientific review, reproducibility package, policy/anonymity checks | Include negative cases and limitations; keep test references private until release policy permits |
| Nov 16 / Nov 23 AoE | Paper / supplement | Check live conference requirements; no assumption of deadline extension |

This schedule is conditional on available independent references and compute. If those are not ready, a rigorous calibration systems study or later venue is stronger than an unsupported two-task superiority claim. The current audit establishes engineering hypotheses and evaluation defects; it does not establish novelty or publishable effect sizes. A focused related-work review of scientific agents, tool-using agents, diffraction calibration and uncertainty-aware acceptance is still required.
