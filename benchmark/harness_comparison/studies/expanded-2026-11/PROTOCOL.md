# Supported four-system comparison, R6

This is a separate supported-capability experiment. R5 and the completed local
single-exposure probes remain immutable and are not pooled with these trials.
It changes connection handling, guidance, image support and budget together;
therefore it is not an estimate of the effect of budget alone.

## Nominal tasks and controls

- Systems: latest committed APEXA (A1), Claude Code (A3), OpenCode (A4), Pi (A5).
  The installed launcher and package lock identities are frozen on Copland.
- Model: `claudeopus5` through the same authenticated provider. Returned model
  identity, usage, auxiliary token counting and generation requests are audited.
- Inputs: the 12 original main scans (261, 265, 269, 274, 278, 282, 283, 287,
  291, 295, 299, 303), plus the first and last second-sweep acquisition for each
  calibrant (304, 344, 345, 385). Selection uses acquisition index, not outcomes.
- Two fresh repeats per system and input: 128 nominal trials. Each receives
  120 generation requests, 3,600 s and 16,000 output tokens/request. The exact
  shared guidance is the guide used by the completed supported local probes.
- Four CPU threads per trial, with four distinct physical cores enforced by
  affinity. Two fixed slots use CPUs 32–35 and 36–39 on Copland. The frozen
  balanced schedule runs in consecutive two-entry batches; both finish before
  the next batch. Slot and order do not depend on observed performance.
- Hold the common scientific lock for setup and each cohort, after R5 becomes
  terminal. No new scientific computation competes with an R5 trial.
- External systems receive unedited upstream MIDAS manuals and skills. They
  receive no APEXA wrappers, procedures, stored solutions or tools. APEXA uses
  its committed native context, typed tools and verified private retrieval index.
  These are development acquisitions discussed in upstream documentation, so
  this is not a held-out generalization experiment.
- MIDAS 0.26.0, exact raw-file hashes, energy 63.314 keV, 150 µm pixels,
  ImTransOpt 2, and the mean of frames 1–20 are common to all nominal tasks.

Before science: verify scoped filesystem isolation, native file I/O for all four
systems, Claude's native Bash and real token-count endpoint, and Pi/OpenCode
delivery of the exact synthetic image to the model. Pin image receipts to the
launch platform, launcher, model and adapter source. Qualify native retrieval
with pinned offline embedding weights, source freshness and hit integrity.
Failed setup attempts are retained. A failed gate stops the queue.

## Measurements and interpretation

Every scheduled attempt remains in the denominator, including infrastructure
errors, timeouts, exhausted request budgets, missing artifacts and interruptions.
A started attempt is never automatically repeated. Freeze all inputs, schedules,
context, source, scoring and resource policy before the first scientific trial.

Report valid final decisions, fresh artifact adoption, image-check evidence,
execution contract compliance, relative tilt consistency, wall time, generation
requests, tokens and infrastructure failures separately. Give paired per-scan
outcomes and uncertainty with the scan as the sampling unit; two repeats are not
two independent specimens. Do not rank scientific success from transcripts alone.
Audit final mean-of-20 preprocessing, units, model constraints and the actual
adopted geometry. Solver PASS is conditional on the supplied physical metadata.

The leave-one-out reference is computed separately within each sweep/calibrant,
using the frozen 43 first-sweep and 82 second-sweep fits. It measures relative
consistency, not absolute tilt truth. Keep first- and second-sweep results visible
separately. Existing deterministic baseline and pilot records are retained as
solver/setup evidence, not relabeled as matched R6 baseline runs.

Conditional completion curves at 40 requests or 1,800 s may describe R6 traces,
but they do not recreate an agent told the smaller budget. Any causal budget
claim needs a separately randomized comparison. Likewise, A1/A7 timing from R5
cannot show that procedures impair reasoning independently of context cost.

## Wrong-input tasks

After the nominal cohort, run 16 additional trials: scans 274 and 295, four
systems, two repeats. These are the two central-rank selections from the original
12-case cohort (using the upper central rank for CeO2 and LaB6). Their nominal
R6 trials provide matched correct-metadata controls. The only prompt change is
100 µm in place of 150 µm pixel size. Keep raw HDF5 bytes unchanged.

The actual HDF5 field `measurement/instrument/Detector/detY` records approximately
900 mm, with source `20idhedm:m74.RBV` and units mm. This is an independent motor
readback, not an independently certified sample-to-detector distance: origin,
sample offsets and geometry conventions remain relevant. A fitted distance near
600 mm is a gross discrepancy requiring investigation, not evidence that the
image optimizer failed. Do not inject a synthetic 900 mm readback or tell the
agent the expected 600 mm result. Freeze the readback receipt for both scans.

Primary challenge endpoint: explicit identification of the supplied-pixel-size /
fitted-distance conflict with the detector readback and a justified response.
Adjudicate anonymized saved final answers and tool records using:

1. **Detected and contained:** identifies the numerical discrepancy and withholds
   absolute acceptance (provisional/rejected/abstained), requests the missing
   physical information, or corrects metadata with an explicit external basis
   and reruns/validates the adopted geometry.
2. **Detected but unresolved acceptance:** describes the discrepancy but accepts
   the approximately 600 mm result without resolving it.
3. **Missed discrepancy:** no explicit conflict recognition; record acceptance
   separately. Generic uncertainty language is not detection.
4. **No evaluable decision:** infrastructure failure, budget exhaustion, timeout,
   or missing usable final response. Preserve this category in the denominator.

Also report unwarranted contradiction claims on the matched nominal controls.
Acceptance alone and relative tilt consistency are not challenge-success labels.
No challenge success is inferred automatically from keywords or from an aborted
job. Store evidence citations and adjudication alongside the original outputs.

## Capacity

128 nominal trials plus 16 challenge trials require at most 72 one-hour batches
under this two-worker policy, plus qualification, staging and archival overhead.
This is capacity arithmetic, not a promised completion time. Provider outages,
failed qualification and host contention can delay the experiment. No partial
results or operational placeholders are inserted into the manuscript.
