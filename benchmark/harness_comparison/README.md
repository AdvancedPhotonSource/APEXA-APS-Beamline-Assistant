# Harness comparison: artifact-based development rig

This directory contains adapters for APEXA, Claude Code, OpenCode and Pi, plus a rotation-calibration development benchmark. It does **not** currently establish a harness ranking, independent absolute geometry accuracy, or a tool-matched comparison. See the [study plan](../../docs/CVPR_STUDY_PLAN.md) for the publication protocol.

`run_comparison.py`'s historical 31 records remain **withdrawn**. The old rig leaked a reference file into the workspace, admitted a nominal filename answer through its tolerance, assumed APEXA-specific output names, and inferred execution from file modification times. Its provider/CLI adapters are reused; its legacy scoring is not a publication reference. This README supersedes earlier claims that these scripts isolate the harness by construction.

## Copland setup, November 2026

The new [development protocol](studies/cvpr-2026-11/PROTOCOL.md) lives in the dedicated remote directory `/home/beams/S20HEDM/apexa_eval/harness-comparison/` (canonical path `/home/beams45/S20HEDM/apexa_eval/harness-comparison/`). Its isolated `source/`, `runtime/`, `setup/`, `work/` and `results/` directories keep installation, evidence and temporary work together. Production APEXA and MIDAS installations are read-only dependencies. Failed setup logs are retained separately from scientific trials.

`bootstrap_remote.py` installs pinned Claude Code 2.1.318, OpenCode 1.18.23 and Pi 1.2.0 with an official checksum-verified Node 22 runtime. Package installation runs on Chiltepin in Bubblewrap, with writes confined to the study tree and private temporary storage. Copland runs the trials. No global package or home configuration is changed.

`study.py prepare` freezes source, solver and launcher identities, the npm lock, raw-input hashes, reference hash and balanced schedules. `verify` checks these identities again, including raw inputs and schedules. `qualify` requires Linux containment checks and performs only a synthetic exact-copy task with all five conditions. Model request metadata, returned identity, usage, full process output and workspaces are retained; raw prompts and credentials are not stored in the model audit. The actual qualification outcome is in `setup/qualification.json`, not implied by installation.

The conditions are A1/A3/A4/A5 plus A7, which disables only automatic procedure delivery. The model relay imposes a request limit and per-request output ceiling and rejects unqualified model traffic from scientific aggregation. Structured CLI events are preserved, but only final assistant text enters prose scoring. These controls do not establish identical inference settings across transports or prevent every possible network information flow.

Use the generated `setup/run-study.py` launcher on Copland with `verify` or `qualify`; it uses the existing APEXA ARGO login in memory. Qualification records are exclusive: preserve a failed revision before repairing and rerunning it. `run --cohort pilot` is separate from setup. Main execution is deliberately blocked until a deterministic numerical baseline and scientific pilot review are complete. The planned 180 main trials are **not run** merely because their schedule exists.

## What the rotation reference measures

`rotation_benchmark.py` uses leave-one-out robust fits within each calibrant group. With the existing APEXA summary, one exposure is compared with a trend fitted from other APEXA calibration results. Holding the graded exposure out prevents direct self-inclusion, but **does not make that trend independent ground truth**. Shared coordinate, model or engine errors can remain in all rows. The benchmark does not directly read a calibrated detector goniometer.

The existing 43-row summary has 35 passed and eight failed gates. All 35 passed rows agree within 0.1° with the inspected leave-one-out trend. Of those 35, 23 have some recorded boundary flag, or 11 when periodic `phi*` parameters are excluded. Boundary flags alone do not establish an inaccurate geometry. Scans 269 and 276 at exactly −3° and +3° are **failed-gate cases**, not examples of accepted-wrong results. Corrected wider-bound results and sensitivity records are development evidence, not new benchmark results.

The script retains legacy outcome keys `correct`, `accepted_wrong`, `honest_failure` and `false_alarm` for compatibility. Its saved records and summaries explicitly label the reference `apexa_derived_leave_one_out_consistency`. Interpret the first two keys as agreement/disagreement with that reference, not as independently measured physical accuracy. Report valid completion/coverage alongside accepted-inconsistent rates; a system that rejects everything is not sufficient improvement.

Absolute accuracy claims require independent pose/readback/metrology with a justified coordinate transform and uncertainty, plus held-out diffraction checks. The scalar tilt trend does not validate all distance, center, distortion, orientation or correction choices. Repeated neighboring scans are correlated; Wilson intervals emitted by the script are descriptive per-trial intervals, not uncertainty about new specimens or beamlines.

## The implemented arms are native-system comparisons

| Arm | Harness | Staged manuals | Actual APEXA intervention |
|---|---|---|---|
| A1 | APEXA | Yes | Stage guidance enabled; inferred metadata requires confirmation |
| A2 | APEXA | Yes | **Bundled change:** stage guidance disabled and inferred metadata accepted |
| A3 | Claude Code | Yes | Native harness configuration |
| A4 | OpenCode | Yes | Native harness configuration |
| A5 | Pi | Yes | Native harness configuration; this rig does not configure common MCP servers |
| A6 | Claude Code | No | Same requested adapter settings as A3, without staged manuals |
| A7 | APEXA | Yes | Automatic procedure delivery disabled; metadata policy and verifier unchanged from A1 |

A1/A2 explicitly set both `APEXA_STAGE_GUARDRAILS` and `APEXA_ASSUME_SCAN_METADATA`, rather than inheriting ambient values. The former affects procedure/capsule injection and some verification behavior; the latter changes metadata inference. Numerical tool validators remain present. Therefore A1/A2 is not a pure enforcement ablation. The historical boolean `enforcement` is retained in records only for compatibility; `policy_bundle` records the real switches.

APEXA's rotation driver connects only the **core and MIDAS** analysis servers. It does not start motor, alignment, GSAS-II, tomography or synthesis servers. APEXA receives its typed scientific interface and approved manifest knowledge; the other arms do not receive an equivalent common MCP configuration. Differences therefore include tools and knowledge, not only planner implementation.

Current Pi supports **native MCP**, including stdio/HTTP configuration; the earlier “Pi has no MCP” assumption was outdated. An adapter failing to expose MCP is a separate limitation. A publication comparison should add a deliberately tool-matched condition or a common shell/CLI condition and verify each adapter's actual permissions, context, model settings and budget. The current rotation script does not implement those conditions automatically.

## Paired trial order and cohort accounting

The runner now uses a seeded, position-balanced schedule (`seeded_cyclic_latin_blocks_v1`) instead of completing all trials for one arm before starting another. Each block contains every selected arm for the same `(scan, trial)`. Block order is shuffled. Within each cycle of N blocks, where N is the number of arms, shuffled cyclic Latin-square rows put each arm in every position once. Across the complete schedule, including a partial last cycle, each arm's position counts differ by at most one. Runs remain sequential; the runner does not start competing trials in parallel.

`--seed` defaults to `0` and controls trial order only. It does not seed a model or numerical engine. Arms and scans are sorted before randomization, so rearranging the same CLI selections does not change the schedule. Choose the cohort, repeats and order seed before observing outcomes. Position balance reduces systematic order differences; it does not equalize elapsed time, guarantee equal load/cache conditions, balance all predecessor effects, or establish statistical independence.

An explicit `--scans` selection must have one matching input file and a usable consistency reference per scan. Missing requested scans, duplicate arm/scan selections and ambiguous input matches fail before trials. Use `--trials` for repeats and narrow `--glob` to distinguish duplicate acquisitions. Without `--scans`, the runner uses the intersection of matching input files and usable references; `selected_scans` in `config.json` records that resolved cohort. A partial cohort must not be described as the entire acquisition.

Before staging the first trial, the runner writes `schedule.json` with the complete intended order and records its SHA-256 in `config.json`. Every `runs.jsonl` result has `schedule_index`, `schedule_block` and `schedule_position` (all one-based); `trial` retains its existing zero-based convention. Compare completed records with the schedule after interruptions: unrecorded trials remain **unobserved**, not successful or excluded. There is no automatic resumption or replay. Study directories have a unique suffix so two launches in the same second cannot mix their results.

The schedule is an order/cohort receipt, not a dataset fingerprint or engine qualification. It does not hash raw inputs or verify their hydration, freeze scientific binaries, or capture actual provider model identity. The explicit saved entries are the authoritative ordering record even if a future Python randomization implementation changes.

## Adopted artifact and decision contract

Every arm is instructed to produce `result_manifest.json` with a final scientific decision:

```json
{
  "schema_version": 1,
  "scientific_status": "accepted",
  "artifact_path": "results/final_geometry.txt",
  "artifact_sha256": "<SHA-256 of the geometry file's exact bytes>"
}
```

Statuses are `accepted`, `rejected`, `provisional` or `abstained`. A non-accepted decision may omit the artifact when no result was adopted. The geometry file must contain one finite `tz` in degrees, either a `tz <number>` line or a JSON numeric `tz`, `Tz` or `tilt_z` field.

The evaluator confines adopted paths to the workspace, rejects symlinks/oversized files, checks the hash, and rejects unchanged staged artifacts. Staged text hashes are recorded before execution without hashing raw detector files. If no manifest exists, exactly one newly generated non-seed geometry artifact can be used as a compatibility fallback. Multiple candidates are an error requiring explicit adoption; the newest candidate is never selected merely because it is newest. Seed/initial/intermediate filenames and staged manuals are excluded from that fallback. Transcript-only numbers are unverified and are not scored as artifact-backed results.

Explicit final rejection such as “validation failed; do not use this geometry” overrides earlier numerical success messages. A clearly labeled `Final decision: ...` can supersede an earlier failed attempt. The manifest's scientific status is recorded separately. Prose parsing remains a conservative compatibility mechanism; independently audit contradictory final claims. A manifest/hash establishes which artifact was adopted, not whether the physics is correct or whether a numerical engine actually ran.

Stdout and stderr are retained separately. Diagnostics on stderr do not become the scientific final answer. A nonzero harness exit is an infrastructure/error outcome, even if intermediate artifacts exist. Timeout is recorded separately. Neither startup failures nor unverified artifacts are silently removed from trial counts.

## Isolation and startup checks

Each trial uses independent staged inputs, a fresh workspace, a separate runtime directory, and temporary profile/configuration directories. Pi uses `PI_CODING_AGENT_DIR` and an explicit provider-file path; it no longer edits the user's global provider file during rotation trials. OpenCode and Claude receive isolated profile paths. Provider credentials are placed outside kept workspace artifacts, file permissions are restricted, and the top-level saved configuration redacts the API key. Qualify each installed harness's configuration precedence before claiming complete memory/plugin isolation.

On macOS, `sandbox-exec` denies the declared reference locations, cloud-storage roots, historical sessions, the current study output directory and benchmark material. The repository is read-only inside trials. APEXA's small driver is copied outside the denied benchmark subtree, and receives the repository path explicitly. A no-model/no-MCP `--preflight` validates the allowed core/MIDAS configuration and pinned knowledge manifest before an APEXA run, including dry runs. The tests also execute `--help` through the sandbox. The evaluator and reference subtree stay denied; the fix does not make the whole benchmark directory readable.

## Reviewed study knowledge

Rotation runs with manuals or APEXA now require `--knowledge-manifest`. The runner stages **only the listed, SHA-256-matching files** under the selected repository-local manual directory. It no longer copies the full capsule/notebook tree. The exact manifest is saved with the study, pinned across trials, and copied read-only into each APEXA profile. The driver verifies it before importing the client or launching MCP processes.

The manifest declares who reviewed each selected body for answer contamination; this software does not perform or certify that review. Construct it after reviewing content, not by automatically accepting an entire directory. No production manifest is supplied by these fixes. An example schema is:

```json
{
  "schema_version": 1,
  "study_id": "rotation-pilot-v1",
  "reviewed_by": "REPLACE WITH REVIEWER",
  "review_note": "REPLACE WITH THE REVIEWED SCOPE AND HELD-OUT ANSWER EXCLUSIONS",
  "files": {
    "knowledge_base/study_manuals/rotation/procedure.md": "REPLACE WITH EXACT LOWERCASE SHA256"
  }
}
```

Use actual 64-character digests of reviewed bytes; placeholders are invalid. Files use normalized repository-relative POSIX paths. Every automatically injected skill/capsule body and capsule pin metadata must be listed to be visible in study mode. Guards execute before warm process caches, and a changed approved file or manifest fails closed. Unlisted bodies produce empty context. The application uses `APEXA_KNOWLEDGE_MANIFEST` and `APEXA_KNOWLEDGE_MANIFEST_SHA256`, both required together; outside study mode, normal knowledge loading is unchanged.

Retrieval is disabled by a manifest without a `retrieval` section. To permit it, add an exact `collection_name`, `corpus_revision`, recorded `embedding` identity, and `sources` list of `{ "source_id": "<64-hex>", "file_sha256": "<64-hex>" }` under `retrieval`. These identities come from a reviewed immutable index generation. Source/hash filtering applies before dense and sparse retrieval, and returned text must match its indexed content hash. A changed active index/embedding fails instead of silently changing the study corpus. A hash establishes identity, not correctness or absence of answers.

The sandbox additionally denies unlisted files in the original capsule/skill directories. When retrieval is disabled, it also denies the original Chroma, paper, book and logbook directories. The pinned manifest and staged driver are read-only. This application policy cannot constrain arbitrary code, other harnesses' plugins/memory, network services, or all information encoded in the application itself. If retrieval is enabled, its complete index remains readable by the APEXA process; direct shell access to that index is not mediated by the retrieval filter. A sealed study image with separately brokered retrieval is still needed for a stronger threat model.

This is a denylist, not proof that all answer sources have been removed. Sanitize and freeze normative manuals, capsules, notebooks, user/project instructions, prior sessions and network access. APEXA can still read its application code and permitted knowledge paths. Add all other known reference locations through `--deny`. Use a sealed benchmark image/identity for stronger isolation. The script refuses to run without its supported sandbox. Linux uses Bubblewrap with a read-only host filesystem, private process/device/temp trees and explicit writable trial roots; `study.py qualify` checks blocked reference reads, allowed workspace writes and blocked writes outside the workspace on the actual host.

Run on an analysis identity with no EPICS write rights. Shell access inside an agent is not a hardware protection boundary. APEXA's general shell policy is now read-only unless the operator explicitly enables exploratory analysis under appropriate OS containment; record that deployment setting consistently when comparing systems. Long external child processes and remote jobs still need a qualified cancellation/reconciliation policy.

## Offline validation and running

No model calls are required for the regression suite:

```bash
APEXA_NETWORK=data APEXA_OFFLINE=1 .venv/bin/python -m pytest tests/test_rotation_schedule.py tests/test_rotation_benchmark.py tests/test_knowledge_policy.py -q
APEXA_KNOWLEDGE_MANIFEST=/path/to/reviewed-manifest.json \
APEXA_KNOWLEDGE_MANIFEST_SHA256=<exact-manifest-sha256> \
  .venv/bin/python benchmark/harness_comparison/_apexa_driver.py --preflight
```

Preview just the trial order without a dataset, manifest, credentials, model call or subprocess:

```bash
.venv/bin/python benchmark/harness_comparison/rotation_benchmark.py \
  --plan-only --arms A1 A2 A3 A4 A5 A6 \
  --scans 261 262 263 --trials 2 --seed 42
```

This prints JSON containing a qualified preview and its `schedule` object. It needs explicit scan numbers and does not inspect data directories, read reference/manifest files, create a study directory, or verify readiness. Even supplied data/manifest paths are ignored in this mode. It is suitable for reviewing order before hydrating cloud-backed acquisitions. Preview scan numbers are examples, not a scientifically selected cohort.

For a prepared, sanitized acquisition copy, inspect a dry run with the same cohort, arms, repeats and seed:

```bash
.venv/bin/python benchmark/harness_comparison/rotation_benchmark.py \
  --ground-truth /path/to/existing_summary.csv --value-col tz_deg \
  --data-root /path/to/raw --manuals knowledge_base/study_manuals/rotation \
  --knowledge-manifest /path/to/reviewed-manifest.json \
  --arms A1 A2 A3 A4 A5 A6 --scans 261 262 263 --trials 2 --seed 42 --dry-run
```

`--dry-run` stages raw inputs and performs sandbox/driver checks, so it can copy or hydrate large files. It is distinct from `--plan-only`; the two flags cannot be combined.

`--ground-truth` is a retained CLI name; with this loader it supplies prior fits for a consistency reference. Do not interpret its name as a guarantee of independent truth. The 0.1° default is a diagnostic tolerance, not a universally qualified physical acceptance threshold. Remove `--dry-run` only after fixing the intended model, budget, installed versions, engine builds, knowledge, sandbox and scientific evaluator.

The adapters request a shared model through compatible gateway transports: Claude Code uses Anthropic Messages; OpenCode and Pi use their configured provider interfaces. A common model nickname is not proof of identical backend behavior. The runner records its/driver's source hashes and requested configuration, but actual response model identity and all token/retry/compute budgets still require trace-based capture. Do not infer model parity from CLI flags alone.

Raw inputs are cloned or independently copied, never hardlinked. Kept workspaces omit the staged raw exposure and credential profiles; output symlinks are preserved without dereferencing them through the evaluator. Results and configuration are written under the selected output directory. No new ranking or scientific reconstruction was produced while implementing these fixes.
