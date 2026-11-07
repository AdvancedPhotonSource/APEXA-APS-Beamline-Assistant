# Main comparison analysis — specified before R5 agent outcomes

## Outcome accounting

Preserve all 180 scheduled agent trials and all 12 retained B0 trials. The
automated endpoint is completion within budget with an explicit accepted artifact
and relative tilt deviation at most 0.1°. Before labeling an outcome scientifically
verified, review fresh numerical execution, effective inputs and independent
raw-image evidence. A passing artifact hash/tilt test is not that review.

The primary denominator is all scheduled trials, including transport failures,
timeouts and request-budget failures. Also report technically evaluable outcomes
with their distinct denominator. Show unrun/interrupted trials separately; an
incomplete cohort is not a final success-rate estimate. Do not turn an artifact
from a failed execution into completed success. Use “accepted inconsistent with
the relative reference,” not “physically wrong,” for the legacy `accepted_wrong`
code. Preserve the reference's shared-solver and development-data limitations.

## Paired comparisons and uncertainty

Within each acquisition and arm, average the three binary completion outcomes.
Compare A1 against A3, A4 and A5 as complete systems; compare A1 against A7 as
the automatic-procedure ablation. B0 contributes one binary outcome per
acquisition, not three duplicated observations.

Report the mean paired acquisition-level difference and the complete 12-row
acquisition table. For descriptive uncertainty on a completed cohort, use
10,000 paired bootstrap samples with seed 20261107, resampling acquisitions
within calibrant (six CeO2 and six LaB6), retaining all arms/repeats together.
Report percentile 95% intervals and no significance stars. These intervals
describe variability across the observed development cases; common beamline,
detector and acquisition conditions limit external generalization. The generic
runner's trial-level Wilson intervals are not publication intervals for this
repeated-acquisition design.

## Cost and intervention

Report wall time and model requests across all terminal trials, with budget
ceilings and censored timeouts explicit. Report conditional cost among successful
trials separately; excluding failures can make an unreliable system look cheap.
Retain provider-reported model identity and usage limitations. Do not infer human
time saved from unattended runs.

Apply [INTERACTION_AUDIT.md](INTERACTION_AUDIT.md) to final assistant responses
and their traces. Distinguish requests for already-supplied information from
justified missing-input requests, optional follow-ups, impasses and missing final
responses. Classification must retain quoted evidence and a source location.
No question count is imputed for a timeout with no final response.

## Illustrative traces and figures

For each A1/external-arm pair, match acquisition and repeat. Select the earliest
pair in the saved schedule in each available outcome stratum: both complete,
A1 only completes, external arm only completes, neither completes. If a stratum
is absent, report that; do not fabricate an example or substitute an easier case.
Choose strata from the full outcome table before interpreting trace mechanisms.

Show actual context delivery, discovery/configuration actions, solver calls,
effective parameters and final adoption. Include APEXA failures and external
successes where present. Separate the observed action sequence from an inferred
causal explanation. A1/A7, not selected anecdotes, tests automatic delivery.

The three-study paper uses calibration for scientific failure modes, the main
comparison for the AI integration claim, and FF for transfer and verification
limits. The gold physical grain count remains unresolved until independently
supported; it is not a successful two-grain reconstruction endpoint.
