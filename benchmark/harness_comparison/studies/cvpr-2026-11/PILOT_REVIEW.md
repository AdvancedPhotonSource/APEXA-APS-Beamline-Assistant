# Scientific pilot record review

All scheduled trials are retained. Columns describe separate evidence and can overlap.

| Trial | Recorded state | Final manifest / artifact | Relative error (degrees) | Budget rejections | Transport errors | Seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| A5/284 | wall timeout | absent / missing_manifest | — | 0 | 1 | 1800.05 |
| A4/284 | wall timeout | absent / missing_manifest | — | 5 | 0 | 1800.11 |
| A7/284 | exit 0 | accepted / verified | 0.012364 | 0 | 0 | 1252.86 |
| A3/284 | request budget exhausted | absent / missing_manifest | — | 22 | 0 | 681.40 |
| A1/284 | request budget exhausted | absent / missing_manifest | — | 3 | 0 | 774.67 |
| A3/262 | request budget exhausted | absent / missing_manifest | — | 11 | 5 | 1638.26 |
| A1/262 | exit 0 | accepted / verified | 0.005310 | 0 | 0 | 1130.30 |
| A5/262 | request budget exhausted | absent / missing_manifest | — | 4 | 0 | 994.88 |
| A4/262 | request budget exhausted | absent / missing_manifest | — | 6 | 0 | 1381.85 |
| A7/262 | wall timeout | absent / missing_manifest | — | 0 | 0 | 1781.50 |

- No trials rerun; original runner outcomes preserved.
- Budget exhaustion remains in the scheduled denominator, separate from transport errors.
- No final manifest means no explicit adoption, even if a geometry exists.
- Relative reference is the value saved by the frozen evaluator, not absolute truth.
- Hashes verify saved identity, not raw preprocessing, solver execution or prose claims.
- Reported token totals include available partial-response usage; missing usage is not zero cost.
- No-completed-record does not distinguish running from unstarted; check live processes.

## Review conclusion — 6 November 2026

All ten scheduled revision-2 pilot trials have terminal records. The detached
runner exited 0 and was absent from the live process table after completion.
The launch receipt retains a stale `completed: 9` and active-trial field; its
terminal status, the ten distinct run records and the exited process establish
completion. Do not restart from that stale counter.

Two trials contain explicit accepted manifests with matching geometry hashes and
relative tilt consistency: A1 on CeO2 scan 262, and A7 on LaB6 scan 284. Eight have
no final manifest. Six exhausted the relay request budget, three reached a
wall-time limit (including APEXA's inner timeout), and two encountered transport
errors. These categories overlap. B0 completed both pilot scans without an LLM,
with ring PASS and relative tilt errors 0.005700 and 0.010683 degrees.

The frozen scorer conflates expected budget exhaustion with infrastructure failure.
Its legacy artifact fallback also calls Pi/262 accepted-wrong despite no final
manifest. The new post-run review reports missing adoption and preserves the
original outcome rather than editing records or converting an intermediate into
a scientific decision. It additionally recognizes exit 124 as a timeout. No
scientific job was repeated or protocol limit changed.

A7/284's tool manifest names CeO2 and its seed uses CeO2, while the adopted
parameter file contains SpaceGroup 221 and lattice parameter 4.15689 A (LaB6).
The frozen runner uses a single-calibrant argument for seeding while retaining
the template's phase for the fit. This provenance mismatch must be disclosed;
it is not evidence that the final fit used a CeO2 lattice. The no-distortion
artifact has p0 through p14 zero and a saved LaB6 ring-position PASS record.

The preserved traces do not retain the automatically injected procedure contents.
The A1/A7 environment toggle alone does not prove which content was delivered.
Neither the two adopted outcomes nor this one-repeat development pilot establish
a harness ranking, a benefit over B0, or an automatic-procedure effect.

Before main scoring: repair strict adoption/budget accounting in a new frozen
revision; retain delivered-procedure evidence without secrets; reconcile
calibrant/phase provenance; review resource ceilings and transport settings.
These changes need synthetic qualification before any new scientific cohort.
The 180-trial main run remains unstarted. Preserve both earlier pilot revisions.

`review_pilot.py` is an offline reviewer. Reproduce this table from a local copy
of the named run directory; it writes to a new output directory and never
rewrites the original run. `PILOT_REVIEW.json` records all consumed file hashes.
