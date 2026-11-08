# R5 host stop and continuation — November 8, 2026

This is an execution record written after the first 17 outcomes. It does not
change the prespecified analysis, frozen schedule, context, numerical settings,
per-trial budgets or host-admission thresholds.

## Observed stop and retained evidence

The original main supervisor stopped before starting trial 18. Its log ends
with the exact 900-second host-preflight stop; the one-minute load remained
roughly 303–359 on 96 cores. All 17 started trials already had terminal records:
three automated accepted/relatively consistent results, eight request-budget
failures and six timeouts. The incomplete cohort does not establish a ranking.
This stop does not prove that host load caused every preceding trial failure.

Original log: `revisions/r5/setup/comparison-main-1794085514769696244.log`.
The first 17 JSONL rows have SHA-256
`5b62828d0c8bed5076ee04db268e07f54caeaf0de2db1f8bc440fb40fd9852a2`.
All 12 retained R4 baseline rows still have SHA-256
`57a92ebaf325335e6ebe6ce611ee7ed184387116e5f9198e8df9062b8476a242`.

After host load recovered to about 18, the same frozen supervisor was resumed.
It skipped the terminal outcomes and started schedule entry 18: A3/Claude Code,
scan 278, repeat 2. The retained first-17 bytes were verified again after launch.
Previous receipts are archived under `reports/resume-20261108-1/`; the resume
receipt is `revisions/r5/setup/resume-20261108-1.json`.

## Current processes and reporting

At 15:46 UTC: main supervisor 2403160, worker 2403496, reporting watcher 2403170.
Current launch/progress: `revisions/r5/setup/main-launch.json` and
`revisions/r5/results/main/progress.json`. The old `setup/continuation.json`
describes the original failed outer supervisor and is historical.

The original watcher exported incomplete accounting to `reports/r5-main-terminal/`.
The current watcher writes `reports/r5-main-terminal-resume1/`; its live receipt
is `reports/r5-analysis-resume1-status.json`. These are separate snapshots, not
additional trials. See [the checked process/outcome receipt](MAIN_PROGRESS_20261108.json).

## Narrow recovery for another host-preflight stop

Guardian PID 2415359 watches the existing supervisor. It has not launched a new
supervisor as of the recorded snapshot. Its code is deployed outside frozen R5
under `reporting/20261108-recovery/`; status is
`reports/r5-recovery-20261108/status.json`. Code SHA-256:
`9884a5d4b10c6c50ccabcf7cef67f9ad6b9627537428e140c0028fa710819662`.

Recovery requires the exact final host-preflight stop message, absent previous
supervisor/worker, no active trial, and a one-to-one match between the unchanged
terminal schedule prefix and all started records. Changed outcomes, mismatched
source/admission pins, a changed baseline, an interrupted trial or any other
failure stop automatic recovery for review. A per-revision lock prevents two
guardians. The unchanged runner retains its numerical-study lock and checks.

Each allowed continuation archives the prior launch/progress receipts and the
retained-outcome hash, starts the same frozen supervisor, and attaches a new
reporting watcher. It never retries a terminal scientific outcome. Persistent
host problems can cause another 900-second preflight stop before any trial;
the guardian can resume that same unstarted schedule suffix again.

Validation: 23 targeted recovery/reporting tests pass, including retained
timeouts, partial/modified/duplicate records, active or interrupted trials,
live previous processes, unrelated failures, schedule mismatches, and one
complete simulated continuation with attached reporting.
