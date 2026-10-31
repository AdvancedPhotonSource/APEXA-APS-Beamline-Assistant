# Web runtime and result changes

The October 2026 audit fixes preserve the existing local single-operator workflow. They do not introduce a multi-user service. Apply the changed source and rebuilt frontend together on the next planned restart; this implementation work did not restart a running server or change a local `.env`.

## Web access

By default, HTTP and WebSocket access require a loopback connection, a loopback Host, and an allowed browser Origin when present. Same-origin requests are accepted; the development origins `http://localhost:5173` and `http://127.0.0.1:5173` are also allowed. Foreign origins and DNS-rebinding-style Host values are refused. Non-browser local clients may omit Origin.

For a trusted reverse proxy serving one operator, explicitly configure:

```text
APEXA_WEB_HOSTS=apexa.example.org
APEXA_WEB_ORIGINS=https://apexa.example.org
APEXA_DATA_ROOTS=/data/proposal:/scratch/apexa:/path/to/apexa/uploads
APEXA_WEB_TOKEN=<operator-managed random token>
```

The proxy must authenticate its operator, remove any caller-supplied backend authorization header, and inject `Authorization: Bearer <token>` for HTTP and WebSocket upgrades. Preserve the approved Host/Origin and configure the ASGI server's proxy trust deliberately. Do not place the token in URLs, browser localStorage, or repository files. Remote access requires token and root configuration even when an external Host arrives through a loopback proxy. `APEXA_DATA_ROOTS` uses the host's path separator (`:` on POSIX, `;` on Windows).

This token represents one operator identity. It does not distinguish multiple scientists, authorize proposal ownership, isolate their MCP clients, or sandbox numerical workers. `APEXA_WEB_MODE` currently accepts only `single_operator`. Use separate instances/OS identities for distinct operators until a qualified per-principal runtime exists. Filesystem permissions and analysis-process isolation remain necessary. [Motor/command policy](EXECUTION_POLICY.md) is a separate execution boundary.

Configured data roots constrain the browser and scientific viewer's direct input paths, including dark/mask paths. They are not a universal sandbox for tools, shells, symlinks nested in every scientific file format, or remote workers. Root restrictions are checked by the corresponding host; align the remote viewer's roots separately.

## Uploads and legacy routes

Uploads use generated basenames and exclusive file creation. Parent paths, separators, malformed extensions and oversized requests are rejected. Requests and uploaded files are bounded by `APEXA_UPLOAD_MAX_BYTES` (default 512 MiB). Large detector data should be opened by its data-host path rather than copied through the browser. Failed image/calibration parsing removes the generated upload.

`/api/auto_calibrate` now creates an isolated run directory and passes explicit geometry to `midas_auto_calibrate`. It reports the tool's execution and scientific outcome, including failures. Uploaded masks are refused because this adapter cannot yet verify that the canonical engine applies them; a saved-but-unused mask is no longer silently accepted. The canonical masked-input recipe requires separate qualification.

`/api/calibrate` previously asked the model to calibrate and labeled arbitrary prose as completion. It now returns a metadata-required error without running anything. Use the explicit `/api/auto_calibrate` form or the canonical tool with a reviewed template. Legacy phase/peak endpoints return 503 when their named capability is absent; they no longer invent successful results for missing tools.

The legacy image cache has a byte budget, `APEXA_IMAGE_CACHE_BYTES` (default 512 MiB), and evicts the oldest loaded arrays. Reload an evicted image or use the sequence viewer. Chat includes only explicitly selected canvas metadata; globally cached images/calibrations are no longer added to every prompt. Browser persistence defers snapshot construction/serialization until debounce or lifecycle flush, retaining state across switches and reloads. Large plot payloads remain in browser persistence for offline restoration; this is not an IndexedDB/artifact-service migration.

## Outcomes, cache reuse and workflow durability

`apexa_outcomes.ToolOutcome` separates execution (`pending`, `running`, `completed`, `failed`, `denied`, `cancelled`, `unknown`) from scientific verdict (`accepted`, `rejected`, `provisional`, `unassessed`). Explicit errors and conservative verdicts win over conflicting optimistic aliases. MCP multi-block/structured responses preserve content and protocol errors. Legacy tool-specific fields remain available.

A completed invocation is not automatically scientifically accepted. This contract fixes status propagation; it does not independently verify every numerical claim in model prose. Deterministic artifact-level acceptance and claim-to-field binding remain required for the publication study.

Generic idempotent reuse now fingerprints input bytes, implementation/declared engine identity, and nested outputs, and verifies output integrity. Content hashing runs off the event loop and is memoized using filesystem identity/change metadata. Local calls sharing an output directory acquire an OS file lock, including forced/resumed calls. Missing/unqualified engine identity disables reuse while retaining local write serialization. Remote paths do not acquire misleading local locks; remote ownership requires a remote job service.

The four external MIDAS cache decorators currently disable replay because their actual native/pip/legacy fallback builds are not yet represented by a qualified immutable engine manifest. Repeated direct invocations can therefore rerun. This is deliberate: old manifests must not replay unverifiable results. The FF-HEDM graph still retains its workflow-level exactly-once decisions within its supported checkpoints; that is separate from external job deduplication. Do not enable result reuse merely by naming a Python interpreter whose installed engine can change.

LangGraph remains opt-in via `APEXA_WORKFLOW_MODE=graph`; no settings were changed automatically. Requested SQLite checkpoint initialization now fails closed rather than silently falling back to memory. `APEXA_ALLOW_VOLATILE_WORKFLOWS=1` explicitly permits an in-memory development fallback. `/api/status` exposes active workflow mode, checkpoint durability and initialization error, and no longer calls an empty MCP session set connected.

Keep active SQLite/lock state on a qualified local filesystem. These changes do not add a durable shared queue, per-user resource quotas, automatic remote cancellation/reconciliation, or an autonomous alignment executor.

## Compute admission

Calibration and integration now launch owned child processes asynchronously, with numerical-library thread limits passed in each child's environment. Admission considers CPU, host RAM and GPU VRAM separately and refuses when no worker fits. Explicit CUDA execution requires a nonzero `vram_gb` workload profile and available device capacity; existing GPU overrides may therefore need qualified profiles. Unqualified MPS requests are refused.

Resource leases now coordinate cooperating APEXA processes sharing a local SQLite registry. The shared subprocess runner attaches each owned child/process group; uncertain orphaned work retains its reservation until verified release. Remote targets are coordinated only on the controller, and the registry does not replace a facility scheduler or kernel resource enforcement. See [compute reservations](COMPUTE_RESERVATIONS.md). Host/stage profiling and real CPU/GPU equivalence measurements remain required. Combined captured stdout/stderr is bounded to 32 MiB by default; exceeding the limit stops the owned child group and reports failure. Keep large numerical results in artifact files.

The subsequent [operations/retrieval guide](OPERATIONS_AND_RETRIEVAL.md), [acquisition contract](ACQUISITION_RUNTIME.md), [alignment runtime](ALIGNMENT_RUNTIME.md) and [frontend performance report](FRONTEND_PERFORMANCE.md) describe the next implementation batch. These source changes still require a coordinated frontend rebuild and service restart before the running deployment uses them.
