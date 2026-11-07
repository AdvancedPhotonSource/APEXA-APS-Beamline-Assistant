# Revision 5 — qualify native retrieval before collecting agent outcomes

The user requested repair and maintainability of Copland's native semantic
retrieval. R4's waiting/launch supervisor was stopped before any main agent trial;
the numerical baseline continued independently. Its 12 outcomes are retained:
10 accepted with consistent relative tilt, one provisional/off-trend result
(scan 261) and one timeout (scan 283). Do not repeat that completed baseline.

R5 keeps the R4 native-system design, all 180 scheduled agent trials, acquisition
hashes, numerical engine and budgets. APEXA-specific resources are still not
exported to external harnesses: they receive the same unedited upstream MIDAS
snapshot. No scientific parameter or solver change is introduced by the retrieval
repair. No R2/R3/R4 outcome is pooled into R5 agent outcomes.

## Native retrieval configuration

The deployed database lacked a verified generation and embedding identity, and
Copland lacked the embedding assets. Stage Nomic model revision
`e9b6763023c676ca8431644204f50c2b100d9aab` and code revision
`7710840340a098cfb869c4f65e87cf2b1b70caca`. Both cache aliases are fixed to those
commits: the pinned upstream Nomic weight reader internally resolves its main
alias. The first offline setup attempt exposed the absent alias and is retained
in `setup/retrieval-attempt-1/`; it collected no scientific agent outcomes.

The existing native loader and indexer rebuild a separate immutable generation
from the shipped source corpus. Model assets and aliases are hash-pinned, query
and index settings agree, and loading is offline. CPU visibility is explicit.
Every trial gets a private writable Chroma copy and module cache. The production
application and its live sessions are not changed by the isolated deployment.

`scripts/check_knowledge.py` must pass real native queries for powder calibration,
FF-HEDM and NF-HEDM, with verified generation/model identity, source freshness and
valid excerpt hashes. This is a deployment-health check, not a measured scientific
answer-quality result. The procedure is in `docs/RETRIEVAL_MAINTENANCE.md`.

Freeze the completed native generation, model assets, environment and code,
then repeat startup qualification and main admission. Retain R4 B0 by checking
its inputs, solver, numerical worker and budgets for equality. The supervisor
uses its existing terminal records and does not launch duplicate numerical trials.

The known-dataset limitation remains: native and upstream documents discuss the
development acquisitions. Fresh numerical execution and raw-image checks remain
necessary; no unseen-data or blind-recovery claim is supported by these runs.
