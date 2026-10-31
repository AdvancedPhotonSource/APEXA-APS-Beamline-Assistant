# Implemented audit fixes

This is the first implementation batch and its preserved validation record. [Additional fixes](ADDITIONAL_FIXES.md) records the subsequent alignment, acquisition, compute, observability, retrieval and frontend work.

Implementation date: 28 October 2026. This document records changes after the [original audit](AUDIT.md), whose captured evidence remains historical. Existing unrelated edits were preserved. No real hardware action, model comparison, remote beamline job, or new numerical reconstruction was run. The running APEXA service and deployment settings were not changed.

## Changes

| Audit area | Implemented behavior | Remaining boundary |
|---|---|---|
| D01 motor checks | Exact PV registry; read-only default; finite/complete state and target checks; fixed large-move policy; checked relative/tweak moves; jog cleanup; independently serviced STOP; completion readback verification | Physical commissioning, device-specific interlocks, authenticated immutable-plan approval, control-service identity |
| D02 command policy | Default argv-only local inspection; explicit operator opt-in for shell/Python/SSH; raw EPICS write CLI refused; owned local process-group cleanup | The exploratory opt-in is not an OS sandbox; typed/external engines still need a restricted analysis identity |
| D03/D04 web boundary | HTTP/WS Host/Origin checks; token/root requirements for external access; single-operator mode explicit; bounded generated upload paths; direct viewer path restrictions | Multi-user identity, project ownership, per-principal MCP state and complete OS confinement |
| D05 result semantics | Common versioned execution/scientific outcome; failures and rejected/provisional states do not become successes; all MCP content retained; matching UI states | Independent scientific acceptance for every technique and quantitative claim-to-artifact binding |
| D06 result reuse | Content/engine/nested-output fingerprints; integrity checks; atomic manifests; same-output serialization; unknown external engine disables replay | Qualified engine/data manifests and remote transactional job ownership |
| D07 tomography | Normalize → intensity phase retrieval → minus-log; finite/valid geometry; nonfinite/constant volumes rejected before publication; center sweep explicitly provisional | Trusted real TomoPy numerical comparison and coordinate/handedness commissioning |
| D08 workflow/process lifecycle | Durable FF-HEDM checkpoint initialization fails closed; status exposes durability; local subprocess timeout/cancel drains and reaps owned process groups | Common durable job API, crash reconciliation and verified remote cancellation |
| D09 selected data | WS prompt uses explicit selected canvas; global image/calibration cache no longer contaminates context | Universal acquisition/correction manifests and per-principal state |
| D10 legacy calibration | Explicit route invokes canonical adapter in a unique output directory; underspecified route refuses; unsupported uploaded mask refuses instead of silently ignoring it | Qualified mask support and migration of all remaining legacy analysis adapters |
| D13 release checks | Offline Python suite and frontend tests/types/lint/build CI; lockfile validation; portable synthetic CIF fixture | First hosted Linux run and repository branch-protection configuration; native-engine qualification jobs |
| D15 study rig | Hash-bound adopted artifact manifest; conservative rejection/scoring; no transcript-only geometry; separate stderr; staged sandbox driver; no-model preflight; isolated Pi profile; corrected Pi/reference/ablation descriptions | Independent scientific truth, full tool/model/budget parity, genuine one-factor ablations and held-out comparison runs |
| D16/D21 frontend | Expensive workspace capture/serialization debounced; lifecycle/race-safe flush; artifact extraction memoized; five lint errors repaired; legacy backend image cache bounded | Browser latency/accessibility/load testing; binary/tiled previews; artifact-store/IndexedDB migration; large Plotly bundle |
| D19 asynchronous work | Calibration/integration child execution yields; worker thread limits passed explicitly; native adapter isolated in a child; shared TIFF alias removed | Real workload speedup/equivalence measurements, additional legacy subprocess paths |
| D20 resource admission | Zero-worker refusal; CPU/RAM/VRAM constraints; process-local leases; explicit GPU assignment/visibility; integration uses common planning; isolated benchmark work dirs | Cross-process/facility scheduling, globally enforced quotas and measured host/stage profiles |

D11's complete alignment optimizer, D12's durable acquisition event contract, D14's correlated production tracing, D17's broader adapter/frontend consolidation and D18's retrieval/index lifecycle remain larger work packages. They are not represented as completed by these patches.

## Verification

The final full offline Python suite passed **746 tests**, with **one optional MIDAS calibration-engine test skipped** because `midas_calibrate_v2` is not installed in the APEXA environment. Frontend verification passed **48 Node tests**, TypeScript, ESLint and a temporary production build. The lockfile check passed. The complete run includes the remote-path reservation and float32 artifact-overflow regressions.

Tests exercise failure/status precedence, multipart path escape and request limits, foreign-origin access, selected-image isolation, modified inputs and nested outputs, duplicate/forced call locking, tomography order/nonfinite volumes, failed checkpoint startup, real tiny child-process overlap/termination, process-local resource leases, motor stop races with stubs, and benchmark sandbox startup without models or MCP servers.

One frontend regression verifies that **100 rapid draft/viewer edits cause zero plot serializations before flush**. This establishes elimination of repeated synchronous hot-path work; it does not measure browser p95 latency. The production bundle remains about 607 KB for main JS and 4.61 MB for lazy Plotly before gzip.

Evidence logs and the validation record are collected under [fix-validation/](fix-validation/). The Python run reported three deprecation warnings (PyPDF2 and two synthetic TIFF writes); Vite reported the remaining large JavaScript chunks. Test counts do not establish numerical correctness of external engines, physical motor safety, scientific superiority, hosted CI success or deployment readiness.

## Applying and reviewing the changes

Read [execution policy](../../EXECUTION_POLICY.md) for the deliberate command/motor default changes and [web/runtime policy](../../WEB_RUNTIME_POLICY.md) for proxy configuration, route migration, cache behavior and checkpoint semantics.

Review current source before committing because this working tree also contains pre-existing development changes. The production frontend was built into temporary output during verification, preserving the existing `frontend/dist` work. Rebuild that directory from the reviewed current source and restart APEXA at a suitable point before expecting the running UI/MCP processes to use these changes. No hardware write settings should be enabled as part of an ordinary UI update.

For beamline rollout, the next acceptance milestone remains one qualified slice: select a dataset → review explicit metadata → execute once under an owned resource allocation → survive interruption/restart → display verified artifacts and an independent scientific verdict.
