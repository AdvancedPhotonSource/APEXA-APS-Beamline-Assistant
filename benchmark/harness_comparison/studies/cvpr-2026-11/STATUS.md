# Current status — November 9, 2026, 15:26 UTC

**R5 is running: 77/180 trials terminal; trial 78 (A1, scan 283, repeat 0) is active.**
Native semantic retrieval and all five startup qualifications passed before main.
The overnight supervisor stopped between trials after 900 seconds of excessive
host load. Host load recovered; the unchanged saved schedule was resumed.
See [the resumption record](RESUMPTION_20261108.md) and
[checked process/outcome receipt](MAIN_PROGRESS_20261109.json).

## Completed work

- Calibration audit and numerical controls are complete.
- R2 development pilot: all ten trials are terminal and retained. See
  [PILOT_INTERPRETATION.md](PILOT_INTERPRETATION.md); no ranking is established.
- R4 fixed MIDAS baseline: all 12 main acquisitions are terminal. Ten were
  accepted with relative tilt agreement, scan 261 was provisional/off-trend,
  and scan 283 timed out. R5 reuses these records unchanged.
- R5 main: 13 automated `correct` outcomes, 35 request-budget failures, 28
  timeouts and one infrastructure error among 77 terminal trials. Automated
  accepted/terminal counts: A1 2/15; A3 0/16; A4 0/15; A5 2/16; A7 9/15.
  These are interim automated checks, not independently reviewed scientific
  successes or comparative estimates. Full APEXA superiority is not established.
- Gold FF: fresh calibration, two reconstructions and the complete candidate/raw
  audit are saved. Initial/updated outputs contain 13/19 candidates. Published
  gold-cube calibration practice now informs the framing: unresolved calibration/
  reconstruction behavior, not evidence of extra physical grains. It is a scripted
  typed-tool case, separate from the 180 autonomous comparison trials.
- R5 retrieval: the 1,208-chunk generation passed real powder, FF and NF queries
  with verified source/excerpt/model hashes. This establishes deployment health,
  not a measured retrieval benefit. All startup checks passed in 22 model calls;
  automatic procedure delivery was observed in A1 and absent in A7.

## Running work and locations

Copland root: `/home/beams/S20HEDM/apexa_eval/harness-comparison/`.

- Main supervisor 2403160; worker 2403496. Active entry: A1/APEXA,
  scan 283, repeat 0. Check `revisions/r5/setup/main-launch.json` and
  `revisions/r5/results/main/progress.json` for live progress.
- Main outcomes: `revisions/r5/results/main/runs.jsonl`. First-17 SHA-256:
  `5b62828d0c8bed5076ee04db268e07f54caeaf0de2db1f8bc440fb40fd9852a2`.
- Retained baseline: `revisions/r4/results/baseline-main/runs.jsonl`, SHA-256
  `57a92ebaf325335e6ebe6ce611ee7ed184387116e5f9198e8df9062b8476a242`.
- Reporting watcher 2403170: `reports/r5-analysis-resume1-status.json`, output
  `reports/r5-main-terminal-resume1/`. The earlier watcher exported incomplete
  accounting to `reports/r5-main-terminal/` after the overnight stop.
- Recovery guardian 2415359: `reports/r5-recovery-20261108/status.json`. It resumes
  only a verified between-trial host-preflight stop, preserving outcomes and
  frozen settings. Each recovery creates a new reporting watcher and receipts.
- Old `revisions/r5/setup/continuation.json` describes the failed original outer
  supervisor. Use the current main-launch/resume/guardian receipts above.
- Retrieval evidence: `revisions/r5/setup/retrieval-health.json` and
  `retrieval-build.json`; startup evidence: `revisions/r5/results/qualification/checks.jsonl`.
- FF report/figures: manuscript `author_notes/FF_CASE_STUDY.md` and
  `author_notes/ff_case_study/`; remote `/home/beams/S20HEDM/apexa_eval/ff-case-study/`.

Separate local workflow repairs now expose native frame controls, the MIDAS
tilted pipeline and timely finalization. Two direct raw-image development checks
passed; both autonomous development checks saved final manifests, with
recorded prose qualifications. See the separate review receipts. No repair is deployed into R5.
See [repair evidence](WORKFLOW_REPAIR_20261109.md).

Do not rerun the completed pilot, baseline, retrieval build, FF reconstructions or
terminal main trials. The R5 experiment source remains `5a87b4c` with its recorded
overlay. Reporting and recovery code live outside the frozen experiment.

## Reading and analysis

Start with [COMPARISON_GUIDE.md](COMPARISON_GUIDE.md), the exact
[PROTOCOL.md](PROTOCOL.md), and [REVISION_5.md](REVISION_5.md).
[ANALYSIS_PLAN.md](ANALYSIS_PLAN.md) was recorded before any main agent outcome.
It retains every scheduled trial, averages repeats within acquisition, and uses
paired acquisition-level comparisons with a stratified bootstrap. Incomplete
cohorts receive accounting only, without rankings or comparative figures.
Independent scientific review and quoted interaction-trace classification remain
pending. Twenty-three targeted recovery/reporting checks pass; the previously
deployed comparison/retrieval suite passed 192 checks.

APEXA retains its native integration; other harnesses receive unedited upstream
MIDAS resources. The manuals discuss these development acquisitions, so this is
not unseen-data evaluation. See [retrieval maintenance](../../../../docs/RETRIEVAL_MAINTENANCE.md)
for deployment pins, rebuild conditions and health checks. Production sessions
remain untouched. No measured APEXA superiority or reduction in expert intervention
is established by this incomplete cohort.

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
