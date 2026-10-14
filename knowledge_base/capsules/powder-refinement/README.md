# Powder refinement

**Scope.** Laboratory and synchrotron powder observations, compatible CW/TOF
joint projects, independent batches and sequential/live tracking through the
separately installed Agentic-GSAS-II engine. A completed fit is not a validated
structure or quantitative composition.

## Order of operations

1. Bind each measured pattern to axis units, uncertainty, instrument file and
   candidate CIFs. Use `propose_structures` when candidate retrieval is needed.
2. Submit a single structured batch through `powder_refine_submit`. Use
   independent, sequential, joint or search mode explicitly.
3. Poll `refinement_status`; inspect failures, budget stops and evidence coverage.
4. Review per-histogram phase fractions and conditional uncertainties, plus
   reference and effective cells. Report the normalization basis.
5. For diagnosis, use `powder_evidence`, optionally `powder_advise`, and
   `powder_trial`. Image proposals are experiments; the numerical engine decides
   whether the isolated result passes its checks.
6. Ready acquisition observations can enter through `powder_live_ingest`.
   An explicit completion marker is preferred to a file-stability heuristic.

## Traps

- Never infer trustworthy composition from a low Rwp.
- Never treat absent diagnostics or fixed-parameter zero esds as passed checks.
- Joint mode needs matching sample and conditions; heterogeneous unrelated
  samples belong in an independent batch.
- Legacy string-only model transport does not carry images. A configured
  image-capable structured provider is required for `powder_advise`.
- QPA excludes unmodeled/amorphous material; RRUFF remapped intensities are
  unsuitable for ordinary QPA/ADP interpretation.
- GSAS-II uses the existing scientific interpreter, not APEXA's web interpreter.
- Live input rate and external-engine parity require deployment qualification.
