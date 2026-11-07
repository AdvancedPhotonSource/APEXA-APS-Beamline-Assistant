# What the completed pilot says about APEXA's practical value

Re-read on November 7, 2026 against the operator's intended question: can
APEXA's integrated context and scientific tools deliver verified results with
less expert steering than Pi, OpenCode and Claude Code?

## Observations from the preserved ten-trial revision-2 pilot

The two explicitly adopted, relatively consistent results came from APEXA
conditions: A1/CeO2 and A7/LaB6. All six external-harness trials lacked final
manifests. **This is preliminary completion evidence, not an established ranking.**
A1 and A7 each succeeded on only one of their two cases; the pilot does not
establish a benefit from automatic procedure delivery. The fixed MIDAS recipe
passed both cases without any model calls.

| System / case | Observed behavior | Interpretation for the intended claim |
| --- | --- | --- |
| A1 / CeO2 | Accepted artifact after 33 requests, 1,130 s; performed frame/transform checks and independent ring calculations | An example of reaching an inspectable result without a human reply, with substantial computation and reasoning overhead |
| A1 / LaB6 | Recovered a fit with 17.8 microstrain held-out residual, then repeatedly pursued validation; exhausted 40 requests before finalization/manifest | APEXA also needs a reliable completion policy; a fitted intermediate is not a delivered result |
| A7 / LaB6 | Accepted artifact after 30 requests, 1,253 s; diagnosed phase/seed problems and compared distortion models | Integration can succeed without automatic procedure delivery; not evidence that the automatic mechanism caused success |
| A7 / CeO2 | Incomplete template caused integration failure; diagnosed missing radial-bin settings, repaired seeding, then timed out | A typed interface did not prevent configuration/recovery problems in this trial |
| Claude Code / CeO2 | Inspected APIs, diagnosed oscillating ring radii after a failed narrow-window interpretation, reached request limit | Procedural/API discovery and numerical recovery consumed the budget |
| Claude Code / LaB6 | Extensive API/template inspection, launched numerical work, reached request limit | Did not deliver a final adopted result within the request budget |
| Pi / CeO2 | Diagnosed solver divergence and repeatedly attempted an independent geometric fit; budget exhausted | Recovery effort without verified completion |
| Pi / LaB6 | Diagnosed ineffective seed-window forwarding and supplied a measured center; timed out during further checks, with a transport error | Mixed recovery and infrastructure evidence; not a clean harness-only failure |
| OpenCode / CeO2 | Estimated a plausible tilt but later identified a corrected/raw coordinate mix in its own arc extraction; budget exhausted | A concrete integration/convention failure followed by self-correction, not a final accepted wrong result |
| OpenCode / LaB6 | Identified an off-by-one ring assignment and a wrong distance basin; timed out while rerunning | Recognized a bad numerical result but did not finish its recovery |

Sources: retained `runs.jsonl`, per-trial stdout/stderr, model-call records and
workspaces under `revisions/r2/results/pilot-20261106-123856/`. Numerical adoption
and overlapping budget/timeout/transport counts are in [PILOT_REVIEW.md](PILOT_REVIEW.md).

## The repeated-question hypothesis is not demonstrated here

The saved external-harness assistant messages describe autonomous inspection,
coding and recovery. A search for explicit requests to the operator, followed
by review of the extracted assistant text, found no evidence that repeated
clarification requests caused these six failures. The harnesses were configured
for unattended tools. They usually used the available budget rather than stopping
for the user. Missing final text is not question-free success.

The observations support testing **first-pass workflow completion and recovery
efficiency**. They do not support saying that the other harnesses failed because
they repeatedly asked the user what to do. An interaction study would need an
explicit response policy or human participants to measure actual expert time
and the number of replies needed for eventual completion.

Similarly, intermediate wrong fits that the agent recognizes and rejects are
not final unsupported acceptance. No external trial in this pilot has an
explicitly accepted wrong artifact. Report failure to deliver separately from
delivery of an incorrect result.

## Does this test the intended complete APEXA environment?

Not fully. The pilot deliberately supplied only a generic procedure and one
invocation skill, blocked the referenced technique capsules and disabled
retrieval. It therefore tested current analysis code under restricted shared
knowledge. Revision 3 fixes artifact/budget accounting and measures actual
automatic skill delivery, but inherits the same documentation restriction.
All 107 frozen source identities match committed APEXA `39f3514`; current code
identity alone does not establish full production context.

Before starting the main cohort, review and freeze the relevant procedural
bundle needed by the integrated workflow, excluding previous answers, scored
geometries, manuscript material and unrelated operator memories. Give the other
harnesses access to the same approved documentation. Record which native
context mechanisms are actually enabled and exercised. Preserve earlier
revisions and their outcomes; do not pool them with a changed context condition.

## Additional claim audit

Even the accepted A1/CeO2 response overstates one cross-check: it calls the
900-mm detector readback something the fitter "never saw", while also saying
the pipeline used that nominal distance. That is not independent evidence.
The accepted A7/LaB6 run retains the seeder/template-phase provenance mismatch
already documented in the pilot review. Relative tilt success cannot certify
every sentence in a final scientific explanation.

The practical paper should show a larger, fully specified comparison plus
paired traces that tie context/tool integration to checked outcomes. The
pilot supplies concrete failure modes to address and preserve, rather than
a finished proof that APEXA is superior.
