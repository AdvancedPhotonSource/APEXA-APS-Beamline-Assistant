# User intervention audit — specified before main trials

Research question clarified by the operator on November 7, 2026: does APEXA's
integrated scientific context and tooling deliver verified results from the
same natural-language request with less beamline-expert steering than general
coding harnesses? A general coding harness can invoke MIDAS; the study concerns
the surrounding scientific workflow and the effort needed to complete it.

The existing primary endpoint remains first-pass completion with explicit
adoption and relative tilt consistency. Preserve the 180-trial schedule and
budgets. Trials are unattended after the initial prompt, so they do not measure
the total number of human replies or expert minutes needed to rescue a run.
Report requests for intervention as a secondary outcome, not a measured human
time saving or usability result.

Review the final assistant response together with its terminal state and tool
trace. Use these categories, preserving quoted evidence and its source:

- **Required additional user input:** the system stops or defers completion and
  explicitly requests information or a decision from the operator.
- **Unnecessary scientific clarification:** requested information is already
  explicitly supplied in the common task: exact energy, pixel pitch, image
  transform, frame count, frame selection/reduction, input location, or output
  contract. Cite both the request and the corresponding supplied fact.
- **Potentially justified clarification:** essential information is absent or
  contradictory. Missing instrument truth cannot be supplied by confident
  guessing. Do not label all questions as failures.
- **Execution or scientific impasse:** a tool/configuration/fit failure prevents
  a usable result, without an explicit request. This suggests a need for help
  but is not an observed human-intervention count.
- **Optional follow-up offer:** an offer after completion, such as making another
  plot, is not required intervention and is excluded from question counts.
- **No final response:** retain timeouts, budget exhaustion, transport failures
  and missing output. Do not interpret missing text as question-free success.

Tool authorization prompts are not the scientific intervention endpoint. The
native coding adapters already permit unattended tools; configurable permission
dialogs are not evidence of a scientific advantage for APEXA.

For each system report the scheduled denominator, verified first-pass completion,
required-input trials, unnecessary-clarification trials, unsupported acceptance,
missing-output/failure categories and resource cost. A rejected result can be a
scientifically appropriate response; low question count alone is not success.

Show paired traces on the same acquisition and repeat. Link a missing or
automatically supplied procedure to the subsequent action and independently
checked artifact. Include APEXA failures and cases where other harnesses succeed.
Select illustrative traces by a written outcome rule, retain the complete table,
and avoid presenting a selected successful trace as a population result.
