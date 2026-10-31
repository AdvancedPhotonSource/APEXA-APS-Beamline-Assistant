# Frontend performance and support boundary

The CLI and React WebUI are the maintained deployment and regression targets. Their qualification is limited to the checks recorded for a release and the commissioned beamline configuration; this document does not certify native scientific engines or hardware operation. `gradio_ui.py` and `trame_ui.py` are experimental interfaces. Do not assume they share React's session recovery, access boundary, cancellation, scientific status handling or performance coverage. Keep them out of an operator deployment until they pass equivalent tests.

## Changes in this pass

- Conversations mount at most 60 messages. **Older**, **Newer** and **Latest** provide explicit navigation. A late answer leaves an older page anchored. Automatic scrolling follows the latest page only when the operator was already near the bottom.
- Each message initially renders at most 24,000 text characters and 12 tool results. Full text is available on request; tool results have pages. Generic JSON inspection limits traversal, nesting, strings and collections, with a separate full-result download. These are presentation limits, not changes to numerical data.
- The result gallery mounts 60 matching results per page. Search covers the entire in-memory result catalogue and resets pagination. Recorded directory cards show 100 entries per page.
- File browsing requests 100 entries at a time, displays the total, and supports Previous/Next/Refresh/Cancel. Navigation, session restoration and unmount cancel the request; late responses cannot replace a newer directory. Pages replace existing rows so repeated navigation does not grow the DOM. The API enforces a maximum page size of 500. Directory offsets are not immutable snapshots: refresh after acquisition or file mutations change the listing.
- Workspace capture remains debounced. Modern browsers use IndexedDB for their fallback session cache. Immutable message arrays, artifact objects and large artifact strings are stored as separate payload records; draft/settings updates reuse their references. The snapshot and new records commit in one transaction. Unreferenced payload records are reclaimed after a successful snapshot update.

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

## Remaining limits

The current server workspace API still serializes and sends complete artifact arrays on a debounced save. Server-side immutable artifact IDs with versioned source/settings references are the next step for reducing network and backend serialization cost. Do not replace numerical arrays with lossy previews in scientific records. Browser cache references alone do not eliminate that server cost.

All loaded sessions/results still occupy application memory; paging bounds mounted UI, not the full data model. Initial message hydration and the first structured clone of a new large payload still have costs. Downloading a complete result or explicitly opening full text may perform substantial work. Large Plotly traces and scientific viewers need their own data-level decimation/tiling and numerical qualification. The production build still reports a large main chunk (about 617 kB) and Plotly chunk (about 4.61 MB); Plotly remains loaded on demand. Scientific engine, retrieval, model and filesystem latency must be measured separately.

Browser backup storage is not encrypted by this change and follows the browser origin/profile. Deployment data policy must account for shared workstation profiles. The benchmark does not establish accessibility coverage, all-browser equivalence, or commissioned operator workflows.
