# Frontend performance and support boundary

The CLI and React WebUI are the maintained deployment and regression targets. Their qualification is limited to the checks recorded for a release and the commissioned beamline configuration; this document does not certify native scientific engines or hardware operation. `gradio_ui.py` and `trame_ui.py` are experimental interfaces. Do not assume they share React's session recovery, access boundary, cancellation, scientific status handling or performance coverage. Keep them out of an operator deployment until they pass equivalent tests.

## Changes in this pass

- Conversations mount at most 60 messages. **Older**, **Newer** and **Latest** provide explicit navigation. A late answer leaves an older page anchored. Automatic scrolling follows the latest page only when the operator was already near the bottom.
- Each message initially renders at most 24,000 text characters and 12 tool results. Full text is available on request; tool results have pages. Generic JSON inspection limits traversal, nesting, strings and collections, with a separate full-result download. These are presentation limits, not changes to numerical data.
- The result gallery mounts 60 matching results per page. Search covers the entire in-memory result catalogue and resets pagination. Recorded directory cards show 100 entries per page.
- File browsing requests 100 entries at a time, displays the total, and supports Previous/Next/Refresh/Cancel. Navigation, session restoration and unmount cancel the request; late responses cannot replace a newer directory. Pages replace existing rows so repeated navigation does not grow the DOM. The API enforces a maximum page size of 500. Directory offsets are not immutable snapshots: refresh after acquisition or file mutations change the listing.
- Workspace capture remains debounced. Modern browsers use IndexedDB for their fallback session cache. Immutable message arrays, artifact objects and large artifact strings are stored as separate payload records; draft/settings updates reuse their references. The snapshot and new records commit in one transaction. Unreferenced payload records are reclaimed after a successful snapshot update.
- Server workspace uploads reuse unchanged artifacts from the last acknowledged revision. Each PATCH contains updated settings plus either a reference to a previous artifact slot or a complete new artifact. Reordered results and changed data retain their full values. A 405 response from an older backend falls back to full PUT snapshots; timeouts, conflicts and other failures do not trigger an overwrite retry.

## Persistence and recovery

Server sessions remain the authoritative records shared with the CLI. IndexedDB is a browser backup, not a new scientific result format. Browser startup reads the last complete snapshot and restores drafts, viewer settings and artifacts without rerunning tools. A small localStorage recovery journal contains session metadata and workspace settings, excluding messages and artifact payloads. Navigation before an asynchronous commit can recover journaled drafts/settings; new messages or newly added artifacts require a completed cache/server write. Browser termination can interrupt asynchronous writes. Use **Save workspace** before closing when persistence matters.

The legacy `apexa.chat.sessions` JSON cache is imported and removed only after the replacement transaction commits. Uncloneable values, quota failures and unavailable IndexedDB are reported in Session History; they do not silently claim a successful migration. The previous committed snapshot and legacy source survive a failed write. Browsers without an IndexedDB API retain the existing localStorage fallback and its size limitations. Browser storage remains subject to origin quotas and eviction.

Cache revisions reject stale writes from a second tab before deleting/reusing payloads. This is conflict detection, not collaborative editing. The recovery journal retains the most recent local settings; reconcile conflicting drafts with the saved server workspace. Server workspace revision checks continue to protect against overwriting another operator window. Tests cover writes queued behind an earlier save, session switching/reload, edits during slow cache startup, failed atomic transactions and stale-writer rejection.

## Measured synthetic browser check

The reproducible fixture and runner are `frontend/tests/browser-fixture.html`, `browser-fixture.jsx` and `browser-performance.mjs`. They use real React components, stores and IndexedDB in isolated headless Chrome. All application API calls are intercepted; file browsing uses synthetic responses. No model, beamline machine, scientific engine or private data is used.

On 2026-10-28, Chrome 156.0.8078.48 on a 32 GiB Apple M1 Max/macOS 15.8.1, with a 1440×900 viewport and a Vite development server, produced these results:

| Same synthetic conversation | All-message rendering strategy | Bounded rendering |
| --- | ---: | ---: |
| Messages mounted | 1,000 | 60 |
| DOM elements | 61,011 | 3,678 |
| Median synchronous React mount/commit, three samples | 770.5 ms | 110.2 ms |

Each message includes Markdown, a table and two collapsed tool calls. The comparison reproduces the previous all-message mapping with the current message component; it is not a separate checkout of a previous release. Timing uses `performance.now()` around `flushSync`, after module warmup. It is a local rendering measurement, not time to interactive, INP, cold-load performance, a production-build benchmark, or an APEXA/model speedup. Three samples on one machine do not establish a latency distribution.

The same browser run restored every point of a 250,000-point synthetic plot. Subsequent draft-only saves wrote **zero payload records** and a **174-character recovery journal**. It also exercised old-page anchoring, search across 150 results, file pages of 100/100/5 rows, legacy migration, interrupted-write recovery, conflicting cache writers, session reload and edits during slow startup. Raw observations are in [the recorded JSON](development/performance/2026-10-28-synthetic-browser.json).

To reproduce without changing application dependencies, install Playwright into a temporary tooling directory, run Vite on localhost, and use an isolated browser executable:

```sh
npm install --prefix /tmp/apexa-browser-tools --no-audit --no-fund playwright
cd frontend
./node_modules/.bin/vite --host 127.0.0.1 --port 5179 --strictPort
```

In another terminal from `frontend`:

```sh
APEXA_PLAYWRIGHT_MODULE=/tmp/apexa-browser-tools/node_modules/playwright/index.mjs \
APEXA_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
APEXA_PERF_OUTPUT=/tmp/apexa-browser-performance.json \
node tests/browser-performance.mjs
```

Use an appropriate Chrome path on Linux or a Playwright-managed Chromium installation. `APEXA_VITE_URL` can override the localhost URL. The optional browser runner is separate from the dependency-locked Node tests in CI; it requires the external browser/tooling environment. Record the installed Playwright/browser versions for comparisons.

## Workspace upload measurement (2 November 2026)

For one synthetic Plotly trace containing 250,000 exact x/y points, the previous
full-snapshot body for a draft edit was **3,594,744 bytes**. The new draft-only
PATCH was **185 bytes**, with no scientific payload serialized or uploaded.
The first save still sends the full data (3,594,738 bytes in this fixture).
This measures request bodies from the actual frontend transport against an
in-memory HTTP stub; it does not measure browser, network or engine latency.
Reproduce with `cd frontend && node tests/measure-workspace-transport.mjs`.
The [captured request sizes](development/performance/2026-11-02-workspace-transport.json)
record this fixture's output.

## Persistent payload storage and region previews

Session schema 3 stores immutable, SHA-256-addressed payloads under the session
store's `payloads/`. Workspace draft PATCHes update a small manifest and reuse
unchanged references without reading or serializing plot arrays or model history.
Chat/tool updates and renames also reuse unchanged Canvas payloads without
loading their arrays. Raw tool results inside the transcript remain part of the
transcript payload, so their size still affects chat persistence and hydration.
Legacy snapshots migrate without deleting their original JSONL/web records.
Back up manifests and payloads together; older releases cannot read schema 3.

The React client requests lazy saved Canvas artifacts and loads their bodies
when opened. Closing the view releases that component's payload. Results also
embedded in a message can still occupy transcript memory. Older clean
server-backed sessions are unloaded from memory; active, unsaved, local-only and
in-flight sessions are retained. Offline reopening of an unloaded result needs
its APEXA server; export important results before disconnecting.

Fast preview in the sequence viewer uses `/api/science/tile`. HDF5 reads the
requested region/stride directly. Other formats decode one frame before cropping.
Zoom and click-to-center retain detector coordinates. Sampled statistics are
explicitly labeled; turn Fast preview off for full-frame statistics/radial profiles.
Numerical integration is unaffected by display sampling.

Run `uv run --no-sync python scripts/benchmark_workspace.py` to measure real local
storage operations using a synthetic 250,000-point plot. On the development Mac,
30 draft saves measured p50 0.40 ms and p95 0.68 ms. The session manifest was
812 bytes; the full response was about 4.22 MB and lazy response 621 bytes.
Exact arrays survived reload. These are local synthetic storage measurements,
not beamline, network, model or scientific-engine latency guarantees.

Chrome interaction checks on 2 November 2026 verified connection-check errors
and success, save revision conflicts, secret-field clearing, lazy artifact
opening, detector zoom coordinates and full-frame fallback. The header remains
32 px high and the logo 20 px wide. These checks used intercepted synthetic APIs
and did not call a model or scientific engine.

## Remaining limits

New payloads still incur their first serialization and disk write. Chat turns
still update the complete transcript payload. Large active Plotly views can
consume substantial memory; exact arrays are retained. Immutable payloads are
not automatically garbage-collected, so retention/backup must include their disk
usage. The existing 20 MB logical workspace limit remains enforced.

HDF5 chunk layout affects region I/O; compressed TIFF/other formats may still
need a full frame decode. Main/Plotly bundle warnings remain. Target-host p50/p95,
GPU performance, accessibility and sustained multi-user load require measured
qualification on the intended deployment.
