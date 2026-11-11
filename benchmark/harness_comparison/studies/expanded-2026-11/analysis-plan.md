# Prespecified R6 analysis

Frozen before all R6 scientific trials. Source: `analyze_r6.py`; seed 20261111.
The full protocol defines hardware, software, reference provenance and challenges.

## Populations and denominator

24 nominal acquisitions × 5 agents × 2 repeats = 240 scheduled agent cells.
The fixed baseline contributes one cell per acquisition (24). Report first-sweep
and second-sweep strata separately, along with an overall descriptive result.
The two repeats are averaged within acquisition; they are not two independent
specimens. The challenge cohort has 20 separate cells and is never pooled into
nominal calibration success.

The denominator is all scheduled cells. A cell not launched by the fixed cutoff
has no measured model or solver cost and is not relabeled as an attempted failure.
Show its status separately from interruption, infrastructure/adapter/transport
failure, timeout, request exhaustion, invalid/missing artifact and nonacceptance.
No dropping, replacing or rerunning a cell based on its outcome. Complete-block
accounting is a secondary view, not a replacement for the planned denominator.

## Endpoint and validation

Require all of: accepted final scientific decision; schema-valid manifest;
workspace-relative fresh adopted artifact with matching SHA256; finite adopted
tz; absolute deviation from the frozen within-sweep/calibrant leave-one-out
prediction ≤ 0.10 degrees; completed executable and qualified scientific model
transport/identity/usage; no exhausted request/wall budget. Check the saved
artifact against the terminal record again during offline scoring.

Report accepted-but-inconsistent and rejected/provisional/abstained decisions
separately. A lower residual is not evidence of absolute accuracy. Review the
actual mean-of-20 preprocessing, transform, units, calibrant/lattice convention,
model constraints and raw-ring validation before using these labels to make
scientific claims. These execution audits are separate from the automated endpoint.

## Estimation and costs

For each arm report completion count/planned cells and every operational status.
Costs: median and minimum/maximum generation requests and observed wall time over
attempted trials, and separately over completed endpoint trials. Count auxiliary
token requests separately. Interrupted time is unknown, timeout is censored, and
never-launched cost is missing rather than zero. Retain provider usage and latency.

Provide all 24 acquisition rows with each agent's mean of two repeats and B0's
single outcome. Predeclared contrasts are A1, A3, A4 and A5 minus A7; A1 minus A7
is the automatic-delivery contrast. Resample acquisitions 10,000 times within
each of the four sweep/calibrant strata, preserving all arms and both repeats in
every draw, seed 20261111. Use percentile 95% intervals, without significance
stars. Also estimate each sweep separately using its two calibrant strata.
Withhold paired intervals if any planned agent cell is unlaunched or nonterminal;
report full accounting instead. Do not infer generalization to unseen techniques.

## Reference sensitivity and challenge adjudication

For second-sweep artifacts, report relative agreement with the frozen alternative
R2 four-stage leave-one-out line as well as the R1 primary line. Do not change
original decisions or reference values after observing an outcome.

For the pixel-size challenge, assign detected-and-contained, detected-but-unresolved
acceptance, missed-discrepancy, or no-evaluable-decision using anonymized saved
answers/tool evidence. A detected case must identify the actual numerical conflict;
generic caution is insufficient. Report false contradiction claims on matched
nominal controls. Preserve evidence citations; no keyword-only automatic scoring.

## Timing and stopping

Launch cutoff is 2026-11-13T12:00:00-06:00 (America/Chicago). Admission is decided
at a batch boundary, before staging that batch; admitted cells retain their full
budgets. No new batch starts after the cutoff. The first five-arm cohort block
provides timing observations and remains in the cohort; it is not extra training
or a basis for changing the frozen system.
