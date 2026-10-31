# Resume analysis between WebUI and CLI

APEXA now uses one session store for both interfaces. The WebUI **Chats** panel
lists sessions saved by the CLI as well as browser conversations. Select a
session to restore its transcript and canvas; **Refresh** discovers changes
made by another process. A fresh browser selects the last active server session
when one exists. The previous browser selection takes priority on return visits.

Inside the APEXA CLI:

```text
session list
session load <session-id>
session resume
```

**Chats → Resume this session in CLI** shows the exact load command. `session
resume` selects the most recently activated shared session. CLI `session new`
starts a new identity; `session save <new-name>` copies the current session.
Renaming a WebUI title keeps the ID, so existing checkpoints stay associated.
Archive hides a conversation without deleting its record or files. A known
archived ID can still be loaded from CLI; a new turn makes it visible again.

## What is restored

- Human messages, recorded tool results, full structured model messages and the
  compacted model summary. Display messages and model memory are separate.
- Experiment metadata, selected model and the agent's active dataset directory.
- Canvas artifacts, open/closed tabs, selected view, pins and comparison.
- Prompt draft, attachment paths and the Data browser directory.
- Detector image source paths and display settings. Temporary server image IDs
  are recreated on opening. Missing sources show an error and Retry.
- Sequence source/dataset, frame, dark/mask settings, display settings, geometry
  and ring guides. Reopening fetches the saved source; it does not rerun a
  reconstruction. Reports reopen their referenced files; recorded structured
  reports and plot data are retained in the workspace.

Workspace edits save after a 600 ms debounce. **Save workspace now** flushes the
current edits. A failed or conflicting save retains the local draft and reports
an error. **Reload saved workspace** explicitly discards those local canvas and
draft edits in favor of the last server snapshot. Browser cache preserves pending
workspace edits for recovery after reload; it is subject to browser storage quota.
Wait for saving to finish before closing when disk persistence is required.

## Storage and migration

Default directory: `~/.apexa/sessions`. Set `APEXA_SESSION_DIR` in both processes
to choose another directory. CLI and WebUI must use the same server user or the
same explicitly configured store, with the same dataset paths available. This is
host storage, not synchronization between a Mac and a separate beamline host.
Use the CLI on the APEXA host (including over SSH) for that shared workspace.

Legacy CLI `.json` / `.jsonl` records and sibling `web_threads/*.json` are read
lazily. A saved JSON record is upgraded in place; JSONL and legacy web files
are retained. On first WebUI startup, browser-only histories are imported
idempotently. Existing authoritative model memory is preserved. Old transcripts
cannot recover tool outputs that were never recorded. Keep a backup before
rolling back to an older APEXA version, which does not understand the new schema.

If Chats reports `Not Found` for `/api/sessions` or `/api/sessions/import`, the
web server may still be running code from before the session API was added. The
WebUI keeps chat and browser-local history available, and labels the session
panel as browser-local. Restart with `./start_web_viewer.sh` from the current
checkout, then choose **Retry after restart** in Chats. Static frontend files
can update on disk while an already running Python process continues serving its
older route table. Browser-local sessions are not shared with the CLI until the
current server's session API is available and the browser has reconnected.

Snapshots are written atomically. Per-session process locks serialize turns;
workspace revisions reject conflicting writes from another window. Record and
workspace limits are 64 MB and 20 MB respectively; oversized writes leave the
previous snapshot intact. Store these files on a local filesystem with working
advisory locks. Network filesystem and Windows behavior need deployment-specific
qualification. This remains a single-user host workspace, without authenticated
multi-user session isolation.

## Interrupted analysis and FF-HEDM gates

The prompt is saved before a turn starts, and completed tool callbacks are saved
before delivery to the browser. On interruption, saved results are retained for
the next turn. Reopening a conversation does **not** restart external jobs.
Inspect their status and outputs before retrying an interrupted request.

With `APEXA_WORKFLOW_MODE=graph`, FF-HEDM gate resume uses the same session ID in
both interfaces. Both processes must also use the same `APEXA_FFHEDM_DB`
(default `~/.apexa/ffhedm_graph.sqlite`) and its `.state.json` index. Live
processes refresh the index before checking gates; writes merge one session
under a process lock. The database and index must be backed up/restored together.

Missing/corrupt checkpoint state and a step interrupted during execution stop
with an explicit error. They cannot silently start another reconstruction.
After inspecting external jobs, `abandon workflow` drops that session's checkpoint
association; it does not stop jobs or delete outputs. A new session also starts
with a new workflow identity. In-memory checkpoints cannot resume after a restart.

NF reconstruction, refinement and remote scheduler jobs retain their recorded
commands/results in session context. This is not a general durable job queue or
a guarantee of resuming arbitrary external computation from its last instruction.

## Verification

The 16 October session fallback and refinement-canvas follow-up passed the
TypeScript and production frontend build. No test suite was run for that
follow-up. The most recent full test run recorded earlier in this review was
**393 Python tests passed, 1 skipped; 38 frontend tests passed**; scoped ESLint
also passed then. Vite still reports large main/Plotly chunks; bundle size
remains a performance follow-up.

Isolated tests exercise legacy migration, CLI/Web model-memory round trips,
interrupted result recovery, cross-process locks, stale-save rejection, API
access without a model, workspace restore, delayed responses, image source
reload and FF gate persistence. No real reconstruction or hardware control is
performed by these tests. Browser rendering and APS commissioning remain separate
acceptance checks.
