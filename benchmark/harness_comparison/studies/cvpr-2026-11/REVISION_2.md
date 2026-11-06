# Revision 2 — document discovery and filesystem scope

Revision 1 is retained under the original Copland study root. Pi's first trial
(LaB6 scan 284) reached the 1,800-second limit while a `find /` command searched
for `phase-4-calibrate.md` and `HARD_RULES.md`, referenced by the staged invocation
skill but not supplied by the study. No calibration program or geometry artifact
was produced. The following OpenCode trial was interrupted for setup repair;
its interruption is not a scored harness failure. All unstarted trials remain
not run. These records diagnose the study setup and do not establish a ranking.

The revised pilot lives under `harness-comparison/revisions/r2/`, preserving the
original source, frozen setup and diagnostic results. Its five conditions, two
pilot acquisitions, operator notes, solver, requested model and budgets are
unchanged. The same balanced schedule is used. Changes are limited to:

- Explicitly identifying which documents are staged, explaining that upstream
  capsule references are unavailable, and directing API discovery to the installed
  numerical package instead of a host-wide search. No solution, fitted value or
  case-specific repair was added to the agent context.
- Hiding non-system top-level filesystem trees, including site-specific network
  mounts, and restoring only the study software, its Python dependencies and the
  current trial workspace. Existing answer/material denials still apply inside
  the restored source. The shared solver's required editable `midas-ckernel`
  package is explicitly available. No other acquisition is exposed through the
  raw-data mount; the trial receives its staged input.
- Extending sandbox qualification to check that unrelated data roots are empty
  in the agent view. Existing reference and write-containment checks remain.
- Giving APEXA a private state directory, including a mount of its legacy
  `~/.apexa` cache path into the trial profile. This preserves its normal cache
  behavior while removing access to the operator's pre-existing state.

Network access remains available for the model relay; this is not a universal
information-flow guarantee. System and permitted software files remain readable.
Model/usage audits and artifact scoring are unchanged. Revision 2 must pass fresh
startup qualification before scientific trials. Do not pool revision 1 and 2.

The fixed B0 baseline proceeds under its separately frozen, no-LLM recipe in the
original root. Its worker reads only the declared input and has no document
discovery step. Its raw input, solver and reference fingerprints can be checked
against revision 2 before any combined scientific report.

The active study ID is `cvpr-2026-11-ai-tune-r2c`. Failed preflights are retained
under `setup/preflight-r2a/` and `setup/preflight-r2b/`: they exposed an interpreter
symlink alias and APEXA's legacy cache location. These were startup checks with
no scientific fits. The final revision passed the sandbox checks and all five
synthetic qualifications (22 model requests with matching normalized reported
model identity and usage). Local regression tests: 210 passed. A detached
supervisor restarted the complete ten-trial pilot; its live receipt is
`setup/pilot-launch.json` and its log is `setup/pilot.log` in the revision root.
