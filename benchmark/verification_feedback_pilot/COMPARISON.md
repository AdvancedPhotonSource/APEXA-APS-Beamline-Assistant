# Matched harness extension, before scientific trials

15 November 2026. This supersedes only the eight-trial admission schedule in
PLAN.md. The fixture-generation plan and its running files remain unchanged.

The user requested a stronger harness comparison. Run APEXA and Pi on each of
the four qualified calibration review-and-repair cases, with automatic and
on-demand contract-check feedback in each harness: **16 attempts**. Use one
attempt per cell, fresh sessions, and no automatic retries. This is a pilot on
two reused acquisitions, not a reliability estimate or an end-to-end calibration
benchmark. It cannot establish a general harness ranking.

The primary comparison crosses two factors: native harness (APEXA / Pi) and
initial checker delivery (automatic / on demand). Preserve native tools and
context: Pi gets unedited upstream MIDAS manuals and skills, not APEXA code,
procedures or typed tools. Both get the same acquisition facts, evidence,
callable benchmark checker, prompt, final-manifest requirement and resources.
Within each harness only the initial receipt changes. Native context management
and tool granularity can differ between harnesses and must be reported.

Use ALCF GPT-OSS-120B, medium reasoning, text input, 65,536 context tokens,
120 requests, 3,600 seconds, 300-second response timeout, 8,192 maximum response
tokens, and four CPU cores per attempt. Qualify both full native sessions before
scored admission. Do not route Claude Code through an unqualified compatibility
bridge to add another name to the table.

Each row below is one two-slot wave. Feedback order is from PLAN.md; native
harness order alternates between fixtures to reduce a fixed time-order effect.

| Wave | Fixture | Harness | Slot 1 | Slot 2 |
|---|---|---|---|---|
| 1 | scale_valid | APEXA | automatic | on_demand |
| 2 | scale_valid | Pi | automatic | on_demand |
| 3 | restriction_fault | Pi | on_demand | automatic |
| 4 | restriction_fault | APEXA | on_demand | automatic |
| 5 | scale_fault | APEXA | automatic | on_demand |
| 6 | scale_fault | Pi | automatic | on_demand |
| 7 | restriction_valid | Pi | on_demand | automatic |
| 8 | restriction_valid | APEXA | on_demand | automatic |

Retain the original primary outcomes: incorrect release, valid-control retention,
verified computational repair, and justified hold. Compare correctness before
requests and time. Retain all invalid manifests, file errors, context failures,
transport errors and timeouts. Report human claim/provenance adjudication
separately from deterministic scores; unknown evidence is not a verified repair.

The four checker-only decisions are a baseline, not four additional agent trials.
Inspect usage after every wave. Original aggregate usage review thresholds remain
unchanged; provider denial stops admission. Do not automatically launch the
reserved expansion. No manuscript result changes until execution and assessment
are complete.
