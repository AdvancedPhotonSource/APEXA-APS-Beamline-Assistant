# Guided development follow-up

Status: preparation and adapter qualification, not a launched scientific cohort.
This is a post-audit development experiment. All R5 prompts, source snapshots,
outcomes and denominators remain frozen. Do not rescore R5 using these repairs.

The question is whether supported configuration, upstream technique resources and
clear completion guidance allow general-purpose harnesses to finish scientific
work reliably. Success by Claude Code, Pi or OpenCode is informative, not an
undesirable result. No outcome is assumed in advance.

## Two questions and controls

- Capability pilot: latest fixed APEXA (A1), Claude Code (A3), OpenCode (A4), Pi
  (A5), and the retained automatic-procedure ablation (A7), each on development
  scans 262 and 284 with the same guidance. Ten trials, one repeat, blocked by
  acquisition. This is a feasibility check, not a powered ranking.
- Guidance effect: only if pursued after qualification, run the same ten cells
  without the supplement on the *same* source, adapters, data and resources.
  Freeze and interleave both conditions before starting any scientific cell if
  estimating this effect. Do not compare a repaired guided system against R5 to
  attribute a difference to guidance. Retain every attempt, including failures.

No new full 180-trial cohort is implied. The familiar pilot acquisitions cannot
serve as unseen confirmation data. A later confirmatory comparison needs a frozen
design and independent acquisitions; select them before inspecting outcomes.

## Common support

The original operator prompt and final artifact schema remain intact. Append
the exact bytes of guidance.md to every arm's task and archive each delivered
prompt and its hash. Do not selectively supply the supplement to weak arms.
Keep 1,800 seconds, 40 requests, 16,000 output tokens/request and four requested
CPU threads. Do not count a provisional or rejected decision as an accepted fit.

External arms receive unedited upstream MIDAS manuals/capsules/skills, their
native read/shell/edit tools and the installed MIDAS software. No APEXA wrapper,
tool implementation, session, worked solution, reference geometry or evaluator
answer is exported. Resource path pointers may be supplied equally. Record
upstream commit and file hashes; acknowledge that these manuals contain prior
development examples. Further help must be an explicit new condition, not an
unrecorded intervention during a run.

APEXA keeps its native tools/context. Freeze its latest repaired source and
qualify its available retrieval rather than claiming unmeasured parity. Adapter
configuration is system support, and its changes must be reported separately
from changes in scientific guidance or application code.

## Admission and execution

1. Log sanitized method/path for rejected relay requests. Determine which
   requests Claude actually makes before changing their policy. Unknown endpoints
   remain protocol violations; do not silently ignore them or fabricate success.
2. Exercise Pi's and OpenCode's native image reads with opt-in image-capable
   providers. Require
   an image in the outgoing model request, a successful provider response with
   matching model/usage and the correct content-dependent answer. A config flag
   or screenshot-free text response alone is insufficient. Qualify the exact
   launch-host harness version before using the setting in a scientific cohort.
   The runner requires a passing receipt, matching platform/model, exact image
   delivery and matching adapter/launcher hashes before admitting either flag.
3. Requalify all participating arms for model transport, tool/file I/O and
   sandbox containment. Freeze qualified versions, source, context, prompts,
   fixtures, schedules and receipts in a new dedicated revision.
4. Run scientific jobs sequentially under the existing common scientific lock.
   Copland's R5 trial timing must not be affected by this follow-up. Local native
   adapter checks use synthetic fixtures and are not scientific benchmark rows.
5. Keep original operational outcome, explicit final decision, verified artifact,
   gate/coverage, model calls, elapsed time and any human interventions separately.
   Retain budget failures in all-started rates. The existing +/-0.1-degree fitted
   rotation-trend comparison measures relative consistency, not absolute truth.

Report whether completion improves and how much support it costs. If strong
guidance closes the gap, narrow the APEXA claim to reduced setup/operator effort
and integrated verification, where measured. Do not claim other systems inherently
cannot run MIDAS or necessarily keep asking the user; the retained R5 audit did
not establish those claims.

## Storage

Use the existing Box project at
`01-Research/01-APEXA/ai_tune_rot_series/harness_runs/r5_local_audit/guided_followup/`
for preparation and local qualification records, synced with rsync. It is clearly
separate from the immutable `evidence/` R5 audit snapshot. No second main local
audit folder is created. Scientific results, if launched, receive a new revision
identity and never append to an R5 results file.

## Reproduce adapter checks

Run from the repository with its existing Python environment and configured
ARGO credentials. Every output directory must be new; failed attempts are kept.

```sh
.venv/bin/python benchmark/harness_comparison/qualify_guided_adapters.py \
  --arm A5 --test image --image-input --output /new-revision/setup/image-input/A5
.venv/bin/python benchmark/harness_comparison/qualify_guided_adapters.py \
  --arm A4 --test image --image-input --output /new-revision/setup/image-input/A4
.venv/bin/python benchmark/harness_comparison/qualify_guided_adapters.py \
  --arm A3 --test copy --context-lines 200 --output /new-revision/setup/claude-context
```

These native-CLI checks use temporary sandboxed profiles, a random synthetic
fixture, at most eight requests, 2,000 output tokens/request and 240 seconds.
They are not calibration rows. Image qualification checks the actual outgoing
image hash, provider response identity/usage and a content-dependent answer.

The scientific revision's separate source-local specification sets
`guidance_file` (relative to the source root), its exact `guidance_sha256`,
`pi_image_input: true` and `opencode_image_input: true`. Its receipt directories
must match the paths above. Run `study.py` with `--spec` pointing to that new
specification for prepare/verify/qualify/run; keep the R5 default specification
unchanged. Both image flags default to false for historical compatibility.
Do not use the existing R5 main supervisor to launch this pilot.
