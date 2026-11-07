# Revision 4 — native APEXA versus coding harnesses with upstream MIDAS resources

The operator clarified the intended system comparison on November 7, 2026:
leave APEXA's integration intact and give Claude Code, OpenCode and Pi MIDAS's
own capsules and skills. Do not transplant APEXA's custom integration into the
other harnesses. This supersedes the shared restricted-document design of R2/R3.

R3's qualified waiting supervisor was stopped before any main scientific trial.
Its qualification, admission and hold receipt remain under `revisions/r3/setup/`.
The ten R2 pilot trials and two B0 pilot outcomes remain unchanged. R4 retains
the 12 acquisitions × 3 repeats × 5 arms, saved order algorithm, 30-minute and
40-request budgets, 16,000 output tokens/request, requested model and solver.

## What changes

- A1 uses APEXA's native application code and shipped knowledge/skills. No
  benchmark document allowlist replaces its native capsule or retrieval path.
  The benchmark freezes source/context bytes and observes automatic delivery.
- A3/A4/A5 receive unedited `manuals/` and `.claude/skills/` directly from the
  MIDAS git snapshot. The same skill bytes are placed in project directories
  recognized by the coding harnesses. Navigation files point to those resources.
  No APEXA skill, wrapper, validator implementation or custom procedure is copied.
- A7 retains A1's context and tools, with automatic procedure delivery disabled.
- Each trial has fresh user state. Native Chroma gets a private database copy,
  so library writes cannot modify the frozen source or later trials. The corpus
  is not rebuilt. Native retrieval availability must be recorded separately.
- Changes belong to benchmark adapters and study documents, not APEXA's
  application behavior, the MIDAS solver or their procedure content.

APEXA still uses the analysis core/MIDAS server surface and its normal single
agent mode. This calibration experiment does not exercise every beamline server.
Do not equate an available feature with a successfully exercised feature.

## Interpretation

This compares integrated systems supplied with their documented scientific
resources. It is not a matched-context planner experiment. Record both source
commits and file hashes: APEXA's vendored MIDAS capsules can differ from the
current upstream MIDAS docs. That difference is part of the deployed systems.

The unedited MIDAS documentation contains prior results on the same ai_tune
series, including a 43-file summary in `calibrate-integrate/ENVELOPE.md`.
Therefore preserve the development-data label and audit fresh engine execution
and raw-image support. No claim of blind answer recovery or unseen-data accuracy
is supported. A prospective unseen acquisition is a separate future test.

Measure verified first-pass completion, explicit decisions, unsupported
acceptance, requests for missing or already-supplied input, execution failures
and resource cost. The R2 traces do not establish repeated questioning as the
reason external harnesses failed. Keep that proposed mechanism distinct from
observed API discovery, configuration failures and unfinished numerical recovery.

## Receipts

R4 records live under `revisions/r4/`. `setup/native-context.json` freezes both
resource inventories. `setup/frozen.json` binds the sources, cases and schedule.
Startup qualification must pass before admission. Generated status, rather than
this design document, determines whether the 180 main trials have begun.

## Startup outcome, November 7

All five conditions passed (17 model requests), automatic delivery was verified
in A1 and absent in A7, and admission passed. The supervisor is running B0 first,
then the 180 agent trials. Native semantic retrieval is exposed but unavailable
because the deployed embedding revision/cache is not configured; the native
capsule and skill mechanisms work. Keep that limitation in any feature claim.
