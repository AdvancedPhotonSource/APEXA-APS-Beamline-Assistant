# Harness comparison: artifact-based development rig

This directory contains adapters for APEXA, Claude Code, OpenCode and Pi, plus a rotation-calibration development benchmark. It does **not** currently establish a harness ranking, independent absolute geometry accuracy, or a tool-matched comparison. See the [audit study plan](../../docs/audits/2026-10-28/CVPR_STUDY_PLAN.md) for the publication protocol.

`run_comparison.py`'s historical 31 records remain **withdrawn**. The old rig leaked a reference file into the workspace, admitted a nominal filename answer through its tolerance, assumed APEXA-specific output names, and inferred execution from file modification times. Its provider/CLI adapters are reused; its legacy scoring is not a publication reference. This README supersedes earlier claims that these scripts isolate the harness by construction.

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

A1/A2 explicitly set both `APEXA_STAGE_GUARDRAILS` and `APEXA_ASSUME_SCAN_METADATA`, rather than inheriting ambient values. The former affects procedure/capsule injection and some verification behavior; the latter changes metadata inference. Numerical tool validators remain present. Therefore A1/A2 is not a pure enforcement ablation. The historical boolean `enforcement` is retained in records only for compatibility; `policy_bundle` records the real switches.

APEXA's rotation driver connects only the **core and MIDAS** analysis servers. It does not start motor, alignment, GSAS-II, tomography or synthesis servers. APEXA receives its typed scientific interface and approved manifest knowledge; the other arms do not receive an equivalent common MCP configuration. Differences therefore include tools and knowledge, not only planner implementation.

Current Pi supports **native MCP**, including stdio/HTTP configuration; the earlier “Pi has no MCP” assumption was outdated. An adapter failing to expose MCP is a separate limitation. A publication comparison should add a deliberately tool-matched condition or a common shell/CLI condition and verify each adapter's actual permissions, context, model settings and budget. The current rotation script does not implement those conditions automatically.

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

This is a denylist, not proof that all answer sources have been removed. Sanitize and freeze normative manuals, capsules, notebooks, user/project instructions, prior sessions and network access. APEXA can still read its application code and permitted knowledge paths. Add all other known reference locations through `--deny`. Use a sealed benchmark image/identity for stronger isolation. The script refuses to run without its supported sandbox; a qualified Linux sandbox adapter is still needed.

Run on an analysis identity with no EPICS write rights. Shell access inside an agent is not a hardware protection boundary. APEXA's general shell policy is now read-only unless the operator explicitly enables exploratory analysis under appropriate OS containment; record that deployment setting consistently when comparing systems. Long external child processes and remote jobs still need a qualified cancellation/reconciliation policy.

## Offline validation and running

No model calls are required for the regression suite:

```bash
APEXA_NETWORK=data APEXA_OFFLINE=1 .venv/bin/python -m pytest tests/test_rotation_benchmark.py tests/test_knowledge_policy.py -q
APEXA_KNOWLEDGE_MANIFEST=/path/to/reviewed-manifest.json \
APEXA_KNOWLEDGE_MANIFEST_SHA256=<exact-manifest-sha256> \
  .venv/bin/python benchmark/harness_comparison/_apexa_driver.py --preflight
```

For a prepared, sanitized acquisition copy, first inspect a dry run:

```bash
.venv/bin/python benchmark/harness_comparison/rotation_benchmark.py \
  --ground-truth /path/to/existing_summary.csv --value-col tz_deg \
  --data-root /path/to/raw --manuals knowledge_base/study_manuals/rotation \
  --knowledge-manifest /path/to/reviewed-manifest.json \
  --arms A1 A2 A3 A4 A5 A6 --trials 1 --dry-run
```

`--ground-truth` is a retained CLI name; with this loader it supplies prior fits for a consistency reference. Do not interpret its name as a guarantee of independent truth. The 0.1° default is a diagnostic tolerance, not a universally qualified physical acceptance threshold. Remove `--dry-run` only after fixing the intended model, budget, installed versions, engine builds, knowledge, sandbox and scientific evaluator.

The adapters request a shared model through compatible gateway transports: Claude Code uses Anthropic Messages; OpenCode and Pi use their configured provider interfaces. A common model nickname is not proof of identical backend behavior. The runner records its/driver's source hashes and requested configuration, but actual response model identity and all token/retry/compute budgets still require trace-based capture. Do not infer model parity from CLI flags alone.

Raw inputs are cloned or independently copied, never hardlinked. Kept workspaces omit the staged raw exposure and credential profiles; output symlinks are preserved without dereferencing them through the evaluator. Results and configuration are written under the selected output directory. No new ranking or scientific reconstruction was produced while implementing these fixes.
