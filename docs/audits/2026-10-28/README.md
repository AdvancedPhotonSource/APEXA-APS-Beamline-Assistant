# APEXA audit — 28 October 2026

Start with [AUDIT.md](AUDIT.md): deployment assessment, 21 findings, module-family review, harness comparisons and release roadmap.

- [FIXES.md](FIXES.md): subsequent implementation, final regression results, migration notes and remaining work. The original audit evidence below remains historical.
- [ADDITIONAL_FIXES.md](ADDITIONAL_FIXES.md): second implementation batch, shared LangGraph workflows, browser measurements, durable acquisition and shared compute, alignment simulation, tracing and retrieval lifecycle.
- [STACK_AND_PERFORMANCE.md](STACK_AND_PERFORMANCE.md): what LangGraph actually does, measured performance probes, compute policy and recommended stack.
- [CVPR_STUDY_PLAN.md](CVPR_STUDY_PLAN.md): calibration/gold data assessment, corrected harness comparison design, independent grading and submission schedule.
- [module-inventory.csv](module-inventory.csv): 212 captured source files, scope labels, declarations and textual test associations.
- [validation.txt](validation.txt): observed checks, reproduction guidance and limits; raw check outputs are in [logs/](logs/).
- [source-manifest.json](source-manifest.json): base commit and captured file hashes. [source-drift-at-packaging.json](source-drift-at-packaging.json) identifies later changes observed during the audit.
- [REFERENCES.md](REFERENCES.md): official documentation sources and local evidence provenance.
- [rotation-consistency.png](rotation-consistency.png) / [PDF](rotation-consistency.pdf): diagnostic visualization of existing results, with an APEXA-derived consistency reference rather than independent truth.

The audit added documentation, evidence and reproduction scripts only. It did not change application code, enable graph mode, launch model comparisons, run new scientific reconstructions, connect to a beamline host or actuate hardware. The repository already contained substantial uncommitted work, and some benchmark files changed during the review.

The inventory distinguishes structural review from detailed execution-path review. Textual test associations are not a measured coverage report, and passing offline tests are not scientific-engine or hardware commissioning. Source links point into the repository; use the captured hashes when assessing findings against later edits. The original captured source tree is retained locally at `/tmp/apexa-audit-20261028/source-snapshot` for this session, but temporary storage is not a permanent archive.

Evidence JSON files are small captured observations, not a benchmark leaderboard. Reproduction scripts under [reproduce/](reproduce/) contain the local paths and offline stubs used in the audit; read their setup before running on another host. Preserve engine/data isolation when turning these probes into regression tests.
