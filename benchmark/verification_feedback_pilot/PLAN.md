# Calibration decision pilot: automatic verification feedback

Private experiment plan, 15 November 2026. This is a new study; original calibration,
R5/R6, FF and FF–NF records remain unchanged. The main manuscript does not report
results from this study.

## Question and exact intervention

Does automatically delivering a scientific contract check improve an agent's
decision about a completed calibration, compared with making the same check
available on demand?

This is a **review-and-repair task**, starting from completed numerical jobs. It
does not measure raw-image reconstruction success or the effect of disabling all
APEXA verification. Both conditions use the same current APEXA source, model,
scientific tools, documentation, inputs, artifact checks and budgets. Both can
read the same source evidence, invoke the same benchmark checker and recalibrate
to repair a result. Production enforcement in any repair run remains enabled.

The sole intervention is a compact, automatically generated contract-check report
delivered with the initial job handoff. The on-demand condition receives an
identically named receipt saying no automatic check was performed. Both receive
the same instruction to read that receipt and assess the result. Both have the
checker command and its documented rules. The automatic report must be at most
2,000 characters; report its token overhead. It supplies calculated evidence, not
private expected labels, a reference fit, or a forced final decision.

There is **no scientific hard gate on the final answer**: a wrong answer must
remain observable. The final assessment independently checks the adopted result.
This distinguishes changed agent decisions from a wrapper merely suppressing them.

## Four pilot task instances

| Family | Acquisition | Valid control | Fault case | Relevant evidence |
|---|---|---|---|---|
| Input/scale consistency | Second-sweep CeO2 scan 328 | Tilted route, 150 um pixels | Same image and route, 120 um pixels | Effective input against the documented 150 um detector pitch; fitted distance against the 900 mm nominal detector readback |
| Requested-model fidelity | Second-sweep LaB6 scan 369 | Four-stage no-distortion fit with enforcement | Same requested model, with the legacy stage-2 thaw restored for fixture generation | Actual stage-entry free/fixed flags and returned/exported distortion coefficients |

Use frames 1–20 of `exchange/data`, excluding frame 0, with mean reduction and
image transform 2. Energy is 63.314 keV. CeO2 uses lattice constant 5.411651 A and
LaB6 uses 4.15689 A; both use space group 225. Four-stage runs use a 10-degree
tilt window. Freeze the numerical environment at the qualified MIDAS 0.26.0.

These acquisitions are reused from an analyzed second sweep. These specific
fault/task combinations are new to the comparison; **they are not unseen raw
data or independent ground-truth measurements**. The 120 um perturbation is
distinct from the main paper's 100 um controls.

The distance readback is `measurement/instrument/Detector/detY`, in mm. Direct
inspection of scan 328 finds approximately 900 mm. `detZ` is zero and must not be
treated as sample-to-detector distance. Record the actual fields for every input.
The 900 mm value is a nominal instrument reference, not calibrated absolute
distance truth. A discrepancy greater than 5% requires reconciliation; 5% is a
predeclared decision policy, not a metrology uncertainty estimate. A pixel-input
mismatch is separately reported. Agreement permits conditional use, not a claim
of independently validated absolute geometry.

The restriction fault reproduces the known legacy interface defect on another
exposure. It is an intentional fixture-generation intervention, not an assertion
that current APEXA still has that defect. The original verifier is evaluated and
its result retained, but a generation-only wrapper permits saving the failed
candidate for review. The agent's repair tools use the unmodified current runner.
Do not create a fault by editing a fitted coefficient or inventing a solver output.

Each of the four numerical results is generated once, checked, hashed and reused
by both conditions. The generator's private condition labels and expected outcomes
are excluded from agent workspaces. Public handoffs contain the requested settings,
effective inputs, geometry, numerical gate and actual execution evidence; they do
not inherit a prior agent's conclusions. Public run receipts omit the generator's
precomputed contract verdict in both conditions. All numeric evidence is preserved.

## Runs, resources and order

- First model: ALCF `openai/gpt-oss-120b`, Sophia chat-completions API, medium
  reasoning. Current service metadata advertises 65,536 context tokens and text
  input. Tool calling, multi-turn use, chaining and streaming passed local probes.
- Pilot: four task instances x two feedback conditions x one attempt = **8 trials**.
- Fixed recipe: run the same contract checker once on each fixture, release only
  when the declared checks pass, otherwise hold. No language model and no autonomous
  repair. Report this baseline's decisions separately from its repair capability.
- Two concurrent trials at most, four disjoint CPU threads each. Preserve existing
  AlphaDiffract training and monitor shared host load. MIDAS fitting is CPU work; ALCF supplies
  model inference. Do not start another local language-model service.
- Per trial: 120 generation requests, 3,600 seconds, 300-second model-response
  timeout and 8,192 output tokens per response. These are ceilings, not targets.
  Apply the same native context-management policy to both conditions. Qualify a
  full native APEXA session against the 64K endpoint before scored admission.
- Run pairs in the fixed order below. A pair contains the same fixture in both
  conditions; alternate which condition receives the first CPU slot.

| Pair | Fixture | Slot 1 | Slot 2 |
|---|---|---|---|
| 1 | Scale valid control | automatic | on demand |
| 2 | Restriction fault | on demand | automatic |
| 3 | Scale fault | automatic | on demand |
| 4 | Restriction valid control | on demand | automatic |

Use fresh sessions and workspaces. No retries of scored trials and no overwriting
terminal records. An interrupted attempt keeps its record. Distinguish provider,
context, setup and timeout failures from scientific decisions; an unsuccessful
agent-written command is not automatically an infrastructure error.

Inspect usage after each pair. A provider denial stops new admission. If usage
exceeds eight million input tokens or 500,000 output tokens, finish the active
pair and require a usage review before further admission; do not mis-score
unlaunched trials as scientific failures. Shared account quota must be checked
through observed calls; a successful `/models` response is not quota evidence.

## Common final decision and scoring

Require `result_manifest.json` with a reason, adopted artifact paths/hashes,
evidence paths and these explicit decisions:

- `handoff_decision`: `release_conditionally`, `hold`, or `unassessed`;
- `final_decision`: `release_conditionally`, `repaired_and_release_conditionally`,
  `hold`, or `unassessed`;
- separate input, model-restriction, image-gate and nominal-distance assessments;
- what remains unvalidated, especially absolute geometric accuracy.

Conditional release means usable under the supplied model and acquisition facts;
it does not claim independent physical truth. A provisional overall status can
retain a valid result if this conditional release and its artifact are explicit.

Primary outcomes, with exact counts and denominators:

1. **Incorrect release:** the final decision releases an adopted artifact that
   still violates a required input/model contract, without a verified repair.
2. **Valid result retained:** a valid-control artifact is conditionally released
   with the requested evidence and no unsupported absolute-accuracy claim.
3. **Successful repair:** a fault is identified, a new computation is linked to
   the adopted artifact, and its effective inputs/flags/coefficients and required
   numerical check satisfy the contract. Editing a parameter text file to zero
   coefficients is not a computational repair.
4. **Justified hold:** the failure is specifically identified with supporting
   evidence and the invalid result is not released.

Report unassessed decisions, missing manifests, artifact defects, transport errors,
budget exhaustion and coverage separately. An agent that holds every case cannot
score as successful: it loses valid-control retention. Use an evaluator separate
from the feedback implementation to parse source evidence and adopted files.
Inspect free-text claims with condition labels masked where possible; describe
those as qualitative author judgments, not a blinded multi-rater experiment.

Repeated runs and voxels do not create new independent acquisitions. The pilot
is exploratory and must not be used to estimate general reliability or a harness
ranking. Report all attempted trials, including null and unfavorable outcomes.

## Admission, expansion and paper use

Before the first scientific model call, require: source/environment receipts;
all four fixture checks; read-only input and hidden-evaluator sandbox tests;
identical public inputs across each pair except the automatic report; tests of
the checker and independent scorer on missing, nonfinite and contradictory evidence;
full native-agent endpoint/context qualification; immutable prompt, schedule,
rules and input hashes; and an available project run lock.

Local endpoint qualification does not satisfy remote/native-agent qualification.
Keep Globus credentials in the user's credential store or an inference relay,
outside Box, public fixtures and agent workspaces. Use the working `alcf-tokens`
client; the legacy helper's cache returned HTTP 401.

Expansion is based on technical validity and available resources, not a favorable
APEXA effect. If the pilot requires methodological changes, record a new revision
and retain the pilot separately. If it is technically valid, the reserved follow-up
uses four other second-sweep acquisitions (CeO2 312/336, LaB6 353/377), two for
each family, each with valid/fault cases: eight task instances x two conditions x
two repeats = 32 trials, plus eight deterministic baseline decisions. Freeze it
before any model sees those instances. These remain reused acquisitions with new
task combinations. A Pi transfer test on the same qualified model is secondary;
do not introduce a new transport bridge or another model to the primary contrast.

If automatic feedback reduces incorrect release while retaining valid results,
the supported claim concerns proactive verification feedback on these review tasks.
If on-demand agents invoke the checker and perform equally well, report that
result and any measured effort difference. If the fixed recipe performs equally
well, do not attribute its benefit to agent reasoning. Neither outcome establishes
raw reconstruction accuracy or general superiority of APEXA.

The existing main PDF remains self-contained. Additional results can support its
verification claims in the supplement, with their scope and provenance stated;
do not rely on adding a new contribution during rebuttal. No unrun result enters
the manuscript.

## Location and completion estimate

Remote: `/scratch/ptripathi/apexa-cvpr/verification_feedback_pilot`.
Local receipts: the existing `ff_alleppey/verification_feedback_pilot` directory
inside the single Box audit mirror. Raw acquisitions stay in their current location;
all preparation and analysis writes stay in personal scratch.

Previous four-stage controls took 15.6–17.9 minutes for the recorded fitting stages.
Allow approximately one hour for fixture generation/qualification and up to four
hours for the eight model trials with two slots, followed by scoring. A shorter
runtime is possible when review ends without refitting; no completion time is
promised before the first pair's timing is measured. Do not launch the 32-trial
expansion automatically.
