# Copland setup status — 2026-11-06

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
verified MIDAS calibrate v2 0.26.0, and froze the pilot/main schedules. Only
`results/qualification/` exists. **Scientific runs: 0.** The 180 scheduled main
trials and ten pilot trials are not run. These smoke results are not a harness
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
