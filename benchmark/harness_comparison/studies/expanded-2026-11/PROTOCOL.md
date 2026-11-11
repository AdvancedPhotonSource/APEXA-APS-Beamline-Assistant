# Reconciled R6: supported calibration, procedure ablation and metadata challenges

This design combines the repaired adapters and generous common guidance with
both acquisition sweeps, the A1/A7 ablation, a fixed numerical baseline and the
wrong-input challenge. It was selected on November 11 before any R6 qualification,
setup freezing or scientific trial. Earlier unstarted R6 queue/source records are
retained. R5 and completed local probes are not modified or pooled with R6.

## Scientific questions

With the same model, written guidance and sufficient resources, how reliably do
native APEXA and general-purpose coding agents deliver a verifiable calibration,
and what time and interaction cost does completion require? Does automatic
procedure delivery change those outcomes? Can the systems recognize a large
conflict between an image-conditioned result and independent acquisition metadata?

Both sweeps have prior solver analyses. This is a supported comparison on known
acquisitions, not a claim that the data were previously unused or unseen during
all development. The large-rotation sweep retains challenging calibration
conditions; the narrower second sweep provides additional acquisitions. Report
these strata separately as well as together.

## Systems, acquisitions and schedule

- A1: current committed APEXA with automatic procedure delivery enabled.
- A7: the same application, tools, retrieval and metadata policy, with automatic
  procedure delivery disabled. This is the predeclared reference arm for paired
  contrasts; both A1 and A7 remain fully reported.
- A3, A4, A5: Claude Code, OpenCode and Pi, using native tools and unedited upstream
  MIDAS manuals/capsules/skills. No APEXA wrapper, private procedure, session,
  evaluator reference or worked artifact is exported to these systems.
- B0: fixed MIDAS tilted recipe, one fresh run per acquisition under the R6 budget.
  Earlier B0 runs remain prior validation evidence and retain their old identity.

The main cohort has 24 acquisitions, chosen by acquisition rank, not R6 outcome:

| Sweep | CeO2 | LaB6 |
| --- | --- | --- |
| First, original 12 main cases | 261, 265, 269, 274, 278, 282 | 283, 287, 291, 295, 299, 303 |
| Second, ranks 0, 8, 16, 24, 32, 40 of 41 | 304, 312, 320, 328, 336, 344 | 345, 353, 361, 369, 377, 385 |

Two repeats per agent/system/acquisition give **240 nominal agent trials**.
Use `seeded_cyclic_latin_blocks_v1`, seed 20261111. One block contains all five
systems on one scan/repeat. Five concurrent slots receive those five entries;
all finish before the next block. The Latin order balances systems across slots.
All inputs, schedules, reference files, source and analysis rules are frozen
before the first scientific trial. Every attempt and failed setup check is kept.
No started scientific trial is automatically repeated.

## Resource and support policy

Model `claudeopus5` through the same authenticated ARGO endpoint; 120 generation
requests, 3,600 s wall and 16,000 output tokens/request; four CPU threads per trial.
Auxiliary token-count requests are recorded separately and do not consume the
allowance for model generations. Provider model identity, usage, timing, errors,
image delivery and APEXA tool results are retained.

Five slots use physical CPU sets 32–35, 36–39, 40–43, 44–47 and 28–31: 20 of
Copland's 48 physical cores. No two slots share a physical core. Keep GPU use
disabled for these trials; the successful tilted route is CPU-only. Copland has
two RTX A6000s that passed a small CUDA test, but changing numerical backends is
not part of R6. Other users' jobs continue; host load and memory are recorded.
The common scientific lock excludes overlap with our R5 and other locked studies,
not all work by other users on this shared host.

Every arm receives the original operator facts: 63.314 keV, 150 µm pixels, no
binning, ImTransOpt 2, 21 frames, skip frame 0 and average frames 1–20. Append
the exact common 120-request/3,600-second guide, hash
`90addd5fba251860f32d3ac8a8a828a27255fbdfae71794a99d521be7f12d704`.
Archive each delivered prompt and hash. The result_manifest.json contract is
unchanged. This changes guidance, connection support and budgets jointly from
R5, so it does not isolate the causal effect of budget or guidance alone.

## Qualification and launch

Wait for R5's complete terminal cohort and the common scientific lock. Rebuild
and check a private native retrieval index with pinned offline embedding assets.
Qualify the actual Copland installations before numerical trials:

1. Native Claude Bash/file copy with substantial context and a successful real
   token-count request; preserve auxiliary errors rather than ignoring them.
2. Pi/OpenCode native image reads: exact fixture-image delivery to the model,
   correct content-dependent answer, matching platform/launcher/source/model.
3. Concurrent synthetic file copies by all five arms on the declared CPU slots,
   with model transport/identity, usage and exact byte-copy checks. Verify A1
   automatic procedures are delivered and A7 automatic delivery is absent.
4. Scoped filesystem checks: trial work is writable, outside writes are blocked,
   evaluator setup and the actual APEXA-Analysis/RS2 locations are hidden.

Every gate must pass; a failed qualification stops the queue without science.
There are no extra scientific smoke runs on a scored scan. The first complete
five-arm block is retained as cohort data and provides the first actual timing
receipt. Neither its scientific result nor later outcomes may trigger a hidden
configuration change, selective rerun or replacement of a failed trial.

## References, baseline and analysis

The primary reference combines the original 43 first-sweep fit values with all
82 retained second-sweep tilted-route values. Predictions are leave-one-out
LMedS followed by inlier least-squares refit, separately within sweep/calibrant;
tolerance 0.10 degrees. Retain a second-sweep sensitivity reference from all
82 four-stage outputs. Source artifact hashes and reference bytes are frozen in
setup, inaccessible to every trial. These references measure relative trajectory
consistency, not independent absolute detector-angle or distance truth.

Freeze `analysis-plan.md` before science. Primary completion requires an accepted
final decision, valid fresh adopted artifact/manifest/hash, relative agreement,
completed model transport/usage checks and completion within the trial budget.
Report every planned cell, administrative cutoff, infrastructure error, timeout,
budget failure, accepted-inconsistent result and nonaccepted decision separately.
Independent execution, preprocessing and raw-image audits remain necessary before
scientific interpretation; a solver PASS does not establish physical correctness.

Average two repeats within each acquisition. Give all 24 acquisition rows,
separate first/second-sweep summaries and each agent arm minus A7, including
A1 minus A7. Use 10,000 paired bootstrap resamples within sweep/calibrant, seed
20261111, percentile 95% intervals and no significance stars. B0 is alongside
these results, one run per acquisition. Costs use attempted trials and completed
trials separately; never-launched cells have no measured cost. Do not promote an
incomplete-block subset into the entire denominator. Paired intervals are withheld
if any planned agent cell is unlaunched or nonterminal.

B0 uses the fixed, reviewed MIDAS 0.26.0 tilted recipe, without a nominal tilt or
distance hint, the same frame selection and transform, four threads and 3,600 s.
Its 24 runs use up to five CPU slots after the agent cohorts. Pin the baseline
source/spec and verify its actual artifacts against the same primary reference.

## Wrong-input cohort

After nominal trials, run scans 274 and 295 with all five agent configurations,
two repeats: **20 challenge trials**. These scans are the upper central ranks of
the original main selections, one per calibrant. Their nominal R6 runs provide
matched controls. The only prompt change is pixel size 100 µm instead of 150 µm.
Keep the raw HDF5 bytes unchanged and do not tell agents the expected discrepancy.

The raw field `measurement/instrument/Detector/detY` is approximately 900 mm,
source `20idhedm:m74.RBV`, units mm. It is an independent motor readback whose
origin and sample offsets still need interpretation, not certified absolute
distance. A result near 600 mm should prompt investigation of the physical scale.

Adjudicate saved answers/tool records separately from nominal scoring:

- Detected and contained: explicitly recognizes the numerical scale conflict and
  withholds absolute acceptance, requests essential information, or corrects the
  metadata with an external basis and reruns/validates the adopted result.
- Detected but unresolved acceptance: notes the conflict but accepts without
  resolving it.
- Missed discrepancy: no explicit recognition; record acceptance separately.
  Generic uncertainty is not detection.
- No evaluable decision: infrastructure/budget/timeout or missing usable response.

Report false contradiction claims on the matched correct-input controls. Relative
tilt agreement or nonacceptance alone is not challenge success. Keep evidence
citations for every adjudication; never infer detection from keywords alone.

## Cutoff, accounting and capacity

Stop admitting new scientific blocks at **Friday November 13, 2026, 12:00 noon
America/Chicago** (`2026-11-13T12:00:00-06:00`). Already admitted blocks finish
within their individual budgets. Remaining planned cells get a distinct
`not_launched_cutoff` record, not an invented attempted failure. All planned cells
remain in the denominator; any complete-block view is explicitly secondary.

Maximum nominal batch budget: 48 hours. Challenges: 4 hours. B0: up to 5 hourly
batches. The total is **57 batch hours plus setup/staging/archiving overhead**,
not a promised duration or a guarantee of completing before the cutoff. Actual
runtime is measured from the first retained cohort block. Protocol/source changes
after freezing require a new documented experiment, never silent edits.

Keep the single existing Box mirror under
`harness_runs/r5_local_audit/guided_followup/r6/`, using rsync. Operational state
belongs in queue receipts and audit notes, not manuscript placeholders. Scoring
is a separate offline step using `analyze_r6.py`; this queue does not publish or
interpret its scientific results.
