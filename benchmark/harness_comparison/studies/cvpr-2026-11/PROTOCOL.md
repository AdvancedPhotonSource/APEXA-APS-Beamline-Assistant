# APEXA harness comparison — frozen development protocol

Working manuscript title: **APEXA: An Agent Harness for Verifiable Scientific Workflows**.

## Question and scope

Does APEXA increase useful, artifact-supported completion of scientific image
analysis, and does automatic procedure delivery contribute? The first experiment
uses the existing ai_tune broad rotation sweep. This is a development case study:
these acquisitions and prior outcomes have already been inspected. Neither a
fresh harness session nor withholding one fit makes the dataset a new held-out
experiment. The gold FF acquisition is a planned transfer study, not an included
or completed result. Its exact inputs and evaluator must be frozen separately.

## Conditions

- A1: APEXA with native core/MIDAS tools and automatic procedures.
- A3: Claude Code with native coding tools and the same available documents.
- A4: OpenCode with native coding tools and the same available documents.
- A5: Pi with native coding tools and the same available documents.
- A7: APEXA as A1, changing only `APEXA_AUTOMATIC_PROCEDURES` from 1 to 0.

A1/A7 retain the same metadata policy, validators, explicit document-reading
tools, model, engine and budgets. Automatic skill/capsule delivery is the changed
mechanism. Verify that approved procedure content actually reaches A1 before
interpreting this ablation. A2 and A6 are historical development conditions and
are excluded from this cohort. All previous batches remain separate.

The four-harness comparison measures deployed systems: APEXA's typed scientific
interface differs from the coding tools in the other harnesses. It does not
isolate planner implementation. A common-interface experiment is still required
for that claim. Numerical engines shared by the arms are not APEXA inventions.

## Cohort and budgets

`study.json` contains the exact cases, repeats, order seed and execution limits.
Select six positions spanning each calibrant's scan order, using rounded equally
spaced ranks without consulting outcome columns. Keep two other cases for startup
qualification and pilot analysis. The main cohort has twelve cases, three repeats
and five arms (180 agent trials). Repeats quantify run variability; they do not
create 180 independent specimens. Runs are sequential on Copland.

Use one pinned MIDAS release and requested ARGO model. A per-trial relay records
provider-returned model identities, request settings and usage for both API
transports. A common nickname does not prove backend equivalence. Record any
transport/settings differences; do not claim an identical inference policy if
they cannot be matched. The limits are wall time, model request count and output
tokens per request. Thread environment variables are requested CPU limits, not
enforced cgroup allocations. Capture host state and do not benchmark competing
arms concurrently.

## Evidence and scoring

The primary numerical check is agreement within 0.1 degrees of the existing
per-calibrant leave-one-out robust tilt trajectory. Call it **relative tilt
consistency**, not absolute geometry truth. The reference source and hash are
evaluator-only. Score final adopted artifacts independently of final prose.

Report every scheduled trial: verified adoption, consistent/inconsistent accepted
tilt, rejection/provisional/abstention, missing evidence, timeout, infrastructure
failure and not run. Preserve full stdout/stderr, request metadata and workspaces.
Manifests identify files; they do not by themselves prove a numerical engine ran.
Before manuscript use, audit execution logs, effective inputs and contradictions
between the final prose and the adopted result. Report denominators separately
for all scheduled trials and technically evaluable trials. Report paired outcomes
by acquisition and model/solver resource costs. Do not pool older trials after
changing the protocol or selectively rerun unfavorable outcomes.

A fixed numerical recipe baseline is required before attributing a benefit to
agentic reasoning; the agent table alone is insufficient. Freeze its route and
acceptance rule before collecting the main cohort. A post hoc human repair is a
separate baseline. The setup explicitly blocks main-cohort execution until a
qualified baseline and successful pilot review have been recorded.

## Qualification and storage

All remote writes belong under `/home/beams/S20HEDM/apexa_eval/harness-comparison/`.
Use its isolated `source/` checkout, `runtime/` installs, `setup/` records and
`results/` trial directories. Raw data and the existing production installations
are read-only. A Linux Bubblewrap executor must demonstrate denied reference
reads and allowed workspace writes before any model trial. The sandbox retains
model network access; it is not a universal information-flow guarantee.

Preparation performs no model calls or numerical fits. Qualification may make
small model calls on a synthetic file-copy task, never on scored scientific
inputs. Pilot calibration and main scoring are separate explicit commands.
Setup/qualification failure is not a failed scientific trial. Actual qualification
outcomes go in generated records, not in this protocol as promises.
