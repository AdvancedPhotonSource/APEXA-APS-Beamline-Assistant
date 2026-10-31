# Additional audit fixes and shared workflows

Follow-up through 30 October 2026. This extends the [first implementation batch](FIXES.md); the original audit and its evidence remain historical. Source changes preserve the existing dirty working tree. No deployed bundle, service, operator environment or beamline configuration was changed.

## Implemented

| Area | Added behavior | Qualification boundary |
| --- | --- | --- |
| Alignment D11 | One persisted bounded diode scan; simulation, replay and read-only shadow; configuration/recipe identity, budgets, freshness checks, stop/recovery, quality-gated fits; six MCP tools and corrected operating instructions | No hardware execution or Xopt optimizer; other task YAMLs remain specifications/helpers |
| Acquisition D12 / jobs D08 | Closed versioned dataset manifests, file/correction hashes, durable event cursor and job identity, claim fencing, explicit cancellation and unknown-outcome recovery; quiet-file mode review-only | Watcher and shared-workflow coverage; independent external supervisors still need verified reconciliation |
| Compute D20 | Atomic CPU/RAM/physical-GPU reservations across cooperating processes on one controller; owned child/group records; orphan capacity stays reserved until verified release | No facility scheduler, kernel quotas or coordination with other users/controllers/non-APEXA workloads |
| Observability D14 | Default correlated query/tool/MCP/subprocess traces, bounded asynchronous SQLite sink, metadata-only new interaction logs, readiness separate from liveness, event-loop lag/RSS, run/trace replay endpoints | Best-effort diagnostics, not an immutable distributed audit log; no invented remote health or scientific qualification |
| Retrieval D18 | Immutable staged Chroma publication, source/chunk hashes and roles, generation-aware bounded BM25 cache, inverted postings, qualified pinned embedding loader, frozen study knowledge policy | No index rebuild/model substitution performed; old generations remain explicitly unverified; reviewed study assets still require human scientific/leakage review |
| Consolidation D17 | Shared retrieval service and traced MCP transport; source/package capability inventory; actual MCP dependency floor; maintained React/CLI surface documented | Remaining large technique adapters and experimental UI parity are not fully consolidated |
| WebUI D16/D21 | Bounded conversation/tool/gallery rendering; cancellable file pages; IndexedDB payload references, migration/reload/race recovery; measured real-Chrome synthetic fixture | Server workspace saves still carry full artifact JSON; loaded-session memory and large plots still need further work |
| Runtime memory/lifecycle | Bounded captured child output with explicit failure and cleanup; noisy-pipe/spawn cancellation regressions; managed bounded WebSocket turns | Local process-group semantics do not establish remote cancellation |
| LangGraph expansion | Shared reviewed runner for eight recipe families, explicit WebUI/CLI gates, schema/plan binding, synchronous checkpoints and durable job receipts; ordinary chat no longer resumes FF gates | Scientific engines and specialized external supervisors retain their own execution/acceptance contracts |

## Evidence and performance

The final combined suite passed **963 Python tests**, with one optional native MIDAS calibration-engine test skipped and three deprecation warnings. **67 frontend tests**, TypeScript, ESLint, a temporary production build and **10 synthetic Chrome workflow interaction checks** also passed. The lockfile, scoped whitespace and relative-document-link checks passed. Commands, logs, source fingerprints and the capability inventory are recorded in [followup-validation/](followup-validation/).

The synthetic Chrome experiment used 1,000 Markdown messages with tables/tool calls. Bounded rendering reduced mounted DOM nodes from **61,011 to 3,678**, and median synchronous React mount time from **770.5 ms to 110.2 ms** across three local samples. This compares rendering strategies with the current components on a warmed Vite development server, not deployed versions or agent/scientific-job latency. IndexedDB restored all 250,000 synthetic plot points; draft-only updates wrote zero payload records. See the [full method and limitations](../../FRONTEND_PERFORMANCE.md).

## Operational changes

Read [shared scientific workflows](../../SCIENTIFIC_WORKFLOWS.md), [acquisition/runtime](../../ACQUISITION_RUNTIME.md), [compute reservations](../../COMPUTE_RESERVATIONS.md), [alignment](../../ALIGNMENT_RUNTIME.md), and [operations/retrieval](../../OPERATIONS_AND_RETRIEVAL.md) before rollout. Remote-code embedding settings now require reviewed pinned assets and a supported loader pipeline; incompatible index identities require a new index generation. Benchmark runs require an explicitly reviewed knowledge manifest; none was fabricated from production data.

The next deployment acceptance work remains native-engine/reference qualification, hardware commissioning, real data-host/cluster crash recovery, authenticated multi-user ownership and workload-specific performance profiling. The supplied code does not establish those outcomes. Rebuild the production frontend and restart services from reviewed source at a planned deployment point; this implementation did neither.
