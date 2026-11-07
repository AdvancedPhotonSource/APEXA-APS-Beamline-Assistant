# Current status — November 7, 2026, 21:06 UTC

**Native semantic retrieval is repaired and has passed its deployment checks.**
All five R5 startup qualifications and main admission passed. The 180-trial
main cohort is now running: its first saved-order trial is OpenCode on scan 261,
repeat 2. At this launch snapshot no main agent trial is terminal.

## Completed work

- Calibration audit and its numerical controls are complete.
- R2 development pilot: all ten trials are terminal and retained. See
  [PILOT_INTERPRETATION.md](PILOT_INTERPRETATION.md); no ranking is established.
- R4 fixed MIDAS baseline: all 12 main acquisitions are terminal. Ten were
  accepted with relative tilt agreement, scan 261 was provisional/off-trend,
  and scan 283 timed out. R5 validates compatibility and reuses these records.
- Fresh gold FF reconstruction and candidate audit are complete. Initial and
  geometry-updated runs produced 13 and 19 candidates. The physical grain count
  remains unresolved; these are scripted typed-tool runs, not autonomous trials.
- R5 native retrieval: a new 1,208-chunk generation passed real powder, FF-HEDM
  and NF-HEDM queries with matching technique labels, excerpt hashes, generation
  identity, pinned embeddings and source fingerprints. This establishes deployment
  health, not scientific answer quality or an observed retrieval benefit.

## Running work and locations

Copland root: `/home/beams/S20HEDM/apexa_eval/harness-comparison/`.

- R5 continuation PID 338771; main supervisor PID 347845; main worker PID 348176.
  Live receipts: `revisions/r5/setup/continuation.json` and `setup/main-launch.json`.
- Retrieval evidence: `revisions/r5/setup/retrieval-health.json` and
  `retrieval-build.json`. The index worker has finished; do not restart it.
- Fresh startup checks: `revisions/r5/results/qualification/checks.jsonl`. All pass,
  with 22 model requests (A1=3, Claude Code=7, OpenCode=5, Pi=3, A7=4).
  Automatic procedure delivery is observed for A1 and absent for A7.
- Main execution: `revisions/r5/setup/main-launch.json` and
  `revisions/r5/results/main/progress.json`.
- Main outcomes: `revisions/r5/results/main/runs.jsonl` once the first trial ends.
- Retained baseline: `revisions/r4/results/baseline-main/runs.jsonl`.
- Reporting watcher PID 352202: `reports/r5-analysis-status.json`. It will export
  `reports/r5-main-terminal/` when the existing main supervisor ends. A failed
  cohort gets incomplete accounting only; the watcher never starts trials.
- FF report/figures: manuscript `author_notes/FF_CASE_STUDY.md` and
  `author_notes/ff_case_study/`; remote `/home/beams/S20HEDM/apexa_eval/ff-case-study/`.

The continuation froze retrieval/assets, qualified all five conditions, checked
unchanged baseline inputs/solver and admitted main. Its baseline resume emitted
no numerical-trial launch lines, and the retained outcomes still match admission
SHA-256 `57a92ebaf325335e6ebe6ce611ee7ed184387116e5f9198e8df9062b8476a242`.
Check its live receipt before restarting anything. Old R3/R4 supervisors are
stopped; neither collected a main scientific agent trial.

## Reading and analysis

Start with [COMPARISON_GUIDE.md](COMPARISON_GUIDE.md), the exact
[PROTOCOL.md](PROTOCOL.md), and [REVISION_5.md](REVISION_5.md).
[ANALYSIS_PLAN.md](ANALYSIS_PLAN.md) was copied to R5 with a timestamp/hash receipt
before any main agent outcomes. It preserves the scheduled denominator and uses
paired acquisition-level comparisons with a stratified bootstrap.
`analyze_main.py` exports accounting, a 12-row acquisition table, all trial rows,
and—only for a completed cohort—paired estimates, figures and prespecified trace pairs.
It writes outside the frozen studies. Eighteen reporting/adoption checks pass;
the deployed comparison/retrieval regression suite previously passed 192 checks.

APEXA retains its native integration; other harnesses receive unedited upstream
MIDAS resources, without APEXA-specific integration. Those documents discuss the
development acquisitions, so these are not unseen-data trials. See
[retrieval maintenance](../../../../docs/RETRIEVAL_MAINTENANCE.md) for deployment
pins, rebuild conditions and health checks. Production sessions remain untouched.

The main comparison is running; its completed outcomes and independent artifact review are pending. There is
no measured APEXA superiority or reduction in expert intervention to report.

---

The records below describe historical revisions and are not current launch status.

# Copland setup status — 2026-11-06

Current continuation: revision 2 is complete and retained. [Revision 3](REVISION_3.md)
implements strict decisions, budget accounting, request-level procedure evidence
and resumability for the unchanged 180-trial main cohort. Local comparison checks:
173 passed. New remote qualification and main admission are pending; the historical
setup and pilot records below describe their own revisions.

Installed and qualified under `/home/beams/S20HEDM/apexa_eval/harness-comparison/`
(canonical `/home/beams45/S20HEDM/apexa_eval/harness-comparison/`).

| Condition | Installed version | Synthetic exact-copy qualification | Model requests |
| --- | --- | --- | --- |
| A1 APEXA | Isolated source snapshot; hashes in frozen setup | Passed | 3 |
| A3 Claude Code | 2.1.318 | Passed | 4 |
| A4 OpenCode | 1.18.23 | Passed | 5 |
| A5 Pi | 1.2.0 | Passed | 4 |
| A7 APEXA, automatic procedures off | Same source as A1 | Passed | 4 |

All 20 requests returned a spelling of Claude Opus 5 matching the declared
normalization rule, with usage records and no relay violations. This establishes
reported model identity for the smoke task, not independent verification of ARGO
routing or identical inference settings. Bubblewrap blocked reference reads and
writes outside the workspace while permitting workspace writes. The temporary
trial directory was empty afterward.

Preparation inspected and hashed 14 original inputs without modifying them,
verified MIDAS calibrate v2 0.26.0, and froze the pilot/main schedules. At completion
of startup qualification, only `results/qualification/` existed and scientific
runs numbered zero. The 180 main and ten pilot trials had not started. These smoke results are not a harness
ranking or a calibration result. Local regression validation: 195 tests passed.

Remote evidence under `setup/`:

| Record | SHA-256 |
| --- | --- |
| `frozen.json` | `04011a30298159e39d0b9254b609b232b3dab6035924c1357dac0eeeee159ee0` |
| `qualification.json` | `b24d3c072084c1dcdae156fb0840c3e0853116dd7e7838d09a5fad1b7e08d9a6` |
| `sandbox.json` | `013ef46a8d0a1c1a63786412e8d5d2f2325f34779b355b15db47588ca4fe4a78` |

Installation attempts are preserved in `bootstrap-r1.log`, `bootstrap-r2.log`
and `bootstrap-r3.log`, with repair records. The first two failed during setup
(npm configuration duplication, then an OpenCode version probe needing isolated
XDG directories); the third succeeded. No scientific outcomes were collected or
discarded during these repairs.

Use `setup/run-study.py verify` on Copland to check the frozen installation.
The launcher reads the existing matching ARGO login into memory for model actions.
It does not copy that login into study evidence. Do not overwrite frozen or
qualification records to repair a future failure; preserve the revision first.

Before main execution: qualify a deterministic numerical recipe, complete and
review the scientific pilot, verify procedure delivery in A1, and audit execution
evidence, effective inputs and final decisions. Main execution remains blocked
in code. The gold FF transfer study still needs its exact acquisition and
evaluation protocol identified.

## Scientific pilot launched on 2026-11-06

Following the user's instruction to run, the ten-trial native-system pilot was
launched on Copland under a detached supervisor. Its live receipt and log are
`setup/pilot-launch-r1.json` and `setup/pilot-r1.log`; the run directory is
`results/pilot-20261106-112931/`. The original frozen native-harness code and
schedule are unchanged. Do not infer completion from this launch record.

The [B0 fixed numerical baseline](BASELINE.md) was separately frozen and its
two pilot trials queued behind the same scientific-run lock. It uses MIDAS's
documented tilted-detector pipeline with no LLM and no distance/tilt hints.
All fourteen embedded dark arrays were checked against its explicit zero-dark
precondition. Its local tests (12) and Copland sandbox/startup probe passed;
those checks performed no numerical fits. Live baseline status is in
`setup/baseline-launch-r1.json`; output goes to `results/baseline-pilot-r1/`
once it acquires the lock. Main scoring remains blocked pending review of these
scientific pilot results.

## Revision 2 and completed fixed baseline

The first native pilot was stopped after Pi timed out searching the host for
unprovided manuals; OpenCode was interrupted for setup repair. Preserve that
attempt as diagnostic evidence, separate from scientific comparisons. The
[revised protocol](REVISION_2.md), study ID `cvpr-2026-11-ai-tune-r2c`, passed
fresh qualification for all five conditions and restarted the full pilot under
`revisions/r2/`. Its live progress is in `revisions/r2/setup/pilot-launch.json`.
It uses the same inputs, solver, reference and condition schedule as the first
attempt, with corrected document scope, filesystem exposure and private state.

The separately frozen B0 baseline completed both pilot cases:

| Scan | Scientific decision | Deviation from relative tilt reference | Wall time |
| --- | --- | --- | --- |
| 262 (CeO2) | Accepted; ring-position gate PASS | 0.005700 degrees | 90.91 s |
| 284 (LaB6) | Accepted; ring-position gate PASS | 0.010683 degrees | 816.88 s |

These are fixed MIDAS results with no LLM, not evidence of APEXA superiority or
independent absolute geometry accuracy. Original records, masks, effective
inputs and adopted artifacts remain in `results/baseline-pilot-r1/`. Source,
input and reference fingerprints were checked for agreement with revision 2
before the native pilot restart. Main scoring is still pending scientific
review of the revised pilot.

## Completed pilot review

All ten revision-2 pilot trials are terminal. See [PILOT_REVIEW.md](PILOT_REVIEW.md)
and its JSON receipt for the final accounting and unresolved main-study gates.
Do not relaunch from the stale completed count in the supervisor receipt.
