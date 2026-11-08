# How to read the APEXA comparison

Start here; the exact design is in [PROTOCOL.md](PROTOCOL.md), the completed
development pilot in [PILOT_REVIEW.md](PILOT_REVIEW.md), the pilot interpretation in [PILOT_INTERPRETATION.md](PILOT_INTERPRETATION.md),
and the current system setup in [REVISION_5.md](REVISION_5.md). Reader copies
also live in the manuscript repository under `author_notes/comparison/`.

## What is being tested?

Can each system calibrate one raw X-ray diffraction exposure and produce a
defensible, explicitly adopted detector geometry within a fixed budget?
The agent must inspect the input, run MIDAS, assess the result, and write its
final decision and artifact hash. A successful solver message alone is insufficient.

| Condition | What it runs | What the comparison can establish |
| --- | --- | --- |
| A1 | APEXA, native scientific tools, automatic procedure delivery | Utility of the complete APEXA system |
| A3 | Claude Code, native coding tools, upstream MIDAS capsules and skills | Complete-system comparison against A1 |
| A4 | OpenCode, native coding tools, upstream MIDAS capsules and skills | Complete-system comparison against A1 |
| A5 | Pi, native coding tools, upstream MIDAS capsules and skills | Complete-system comparison against A1 |
| A7 | APEXA with automatic procedure delivery disabled | Effect of automatic delivery; manual reading and validators remain available |
| B0 | Fixed MIDAS calibration recipe, no LLM | Whether an agent adds value beyond an existing numerical recipe |

A1/A3/A4/A5 have different tools. This is **not a planner-only comparison**.
A1/A7 differ in automatic procedure delivery, **not in numerical validation**.
Neither comparison by itself establishes that APEXA's model reasons better.

## What is held fixed?

The main agent cohort contains **12 acquisitions × 3 repeats × 5 conditions =
180 trials**. Six acquisitions are selected across each calibrant's scan order.
These are previously inspected development data, not an unseen test set.

Each trial gets the same raw exposure, operator notes,
MIDAS release and requested model (Claude Opus 5 through ARGO). Provider-reported
model names and usage are logged; a gateway nickname does not prove identical
backend routing or inference settings. Tools and native inference settings may
differ and must be reported. APEXA keeps its native context; the other systems
receive MIDAS resources directly, without APEXA-specific copies. See revisions 4 and 5
for the known-dataset documentation overlap and native retrieval deployment. Trials run sequentially in a balanced saved order.

The per-trial ceilings are **30 minutes, 40 model requests, and 16,000 output
tokens per request**. B0 runs once per main acquisition with no model calls.
Three repeated trials of one acquisition do not create three independent samples.

## What does success mean?

1. The execution completes within the declared budget.
2. A valid final `result_manifest.json` explicitly accepts a generated geometry
   and supplies its matching SHA-256 hash.
3. The adopted tilt is within **0.1 degrees of a per-calibrant leave-one-out
   fitted trajectory**. This measures relative consistency, not absolute accuracy.
4. Before publication, effective settings, solver execution and raw-image checks
   must support the interpretation. An artifact hash does not validate physics.

Also report inconsistent acceptance, provisional/rejected results, abstentions,
missing manifests, budget exhaustion, timeout, transport/identity problems,
interruptions and unrun trials. Show the full scheduled denominator. Failures
and infrastructure problems are not silently deleted or selectively rerun.

## What has actually finished?

The separate revision-2 pilot has ten terminal trials. Two produced explicit
accepted, relatively consistent artifacts: A1 on CeO2 scan 262 and A7 on LaB6
scan 284. Eight lacked final manifests. Six budget exhaustions, three timeouts
and two transport-error cases overlap. B0 passed both pilot cases.

**This does not establish a ranking or an APEXA advantage.** Revision 3 repairs
the evaluator and records actual procedure delivery. Revision 3 was held before any main trial; revision 4 implements the clarified
context design. Revision 5 restores and verifies native semantic retrieval before
agent trials; all 12 R4 fixed-MIDAS baseline outcomes are retained (ten accepted
with relative consistency, one provisional/off-trend, one timeout). The 180-trial main cohort is separate from the pilot; consult [STATUS.md](STATUS.md) for its latest status.

## Where are the files?

- Local code: `/Users/b324240/Git/beamline-assistant-dev/benchmark/harness_comparison/`
- GitHub: `AdvancedPhotonSource/APEXA-APS-Beamline-Assistant`, same directory.
- Copland study: `/home/beams/S20HEDM/apexa_eval/harness-comparison/`
- Completed pilot: `revisions/r2/results/pilot-20261106-123856/`
- Current main revision: `revisions/r5/`; frozen inputs/schedule under `setup/`.
- Current main records: `revisions/r5/results/main/runs.jsonl`,
  with per-trial `traces/`, `model_calls/`, `workspaces/`, and `progress.json`.
- Host-stop/resume accounting: [RESUMPTION_20261108.md](RESUMPTION_20261108.md).
- Retained fixed baseline: `revisions/r4/results/baseline-main/runs.jsonl`.
- Statistical analysis and trace-selection rules: [ANALYSIS_PLAN.md](ANALYSIS_PLAN.md).
- Native retrieval deployment/health: [REVISION_5.md](REVISION_5.md).
- Gold FF case study: `/home/beams/S20HEDM/apexa_eval/ff-case-study/`.
  It is a separate workflow demonstration and is not included in the 180 trials.

Private raw traces and workspaces stay on Copland or in ignored local snapshots;
the public-facing documents must not imply that they have all been released.
