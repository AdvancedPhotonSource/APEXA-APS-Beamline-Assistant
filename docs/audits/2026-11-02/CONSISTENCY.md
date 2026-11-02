# Combined release consistency check — 2 November 2026

Reviewed the combined source at `90a139f` after confirming the other editing
session had finished and that this commit was already on remote `main`.
This check extends the [canvas follow-up](../2026-10-30/CANVAS_FOLLOWUP.md)
without rewriting its historical validation records.

## Inconsistencies corrected

- Automatic integration plots now use the same source/operation/settings/data
  identity as folder-opened plots. Streaming delivery followed by the final
  answer cannot create a duplicate or reopen a closed result. Changed data still
  creates a new snapshot.
- Automatic plot reads abort when the conversation changes and reject a late
  result even after switching away and back. Final-answer copies of native-view
  tool output cannot force an explicit reopen; a new open-tool invocation can.
- Saved-chat shortcuts resolve migrated file-view identities. Remote CIF views
  request the remote science service; an unavailable remote resource never falls
  back to a same-named local file. Vite now forwards `/remote` consistently with
  `/local` during development.
- The welcome heading uses the selected palette rather than a hard-coded purple
  gradient. Classic light, dark preferences and the shared SVG mark are retained.
- README, the docs homepage, architecture guide, navigation and source inventory
  agree on seven configured MCP servers and 134 declared MCP tools. Three
  client-owned workflow tools are additional. Alignment configuration comments
  no longer imply that installing optional packages commissions hardware motion.

## Verification

| Check | Result |
| --- | --- |
| Offline Python suite | 963 passed, one optional `midas_calibrate_v2` test skipped, three deprecation warnings |
| Frontend Node suite | 81 passed, including three new chat delivery/session regressions |
| Canvas browser interactions | 16 passed, including legacy shortcuts and remote/local CIF routing |
| Workflow browser interactions | 10 passed |
| TypeScript, ESLint, dependency lock and scoped whitespace | Passed |
| Production frontend | Built and synchronized with the reviewed source |
| Desktop/phone synthetic preview | Logo loaded, Classic light tokens matched, no JavaScript errors or horizontal phone overflow |

[validation/validation.json](validation/validation.json) records commands and
limits; adjacent files preserve logs and source fingerprints. Browser fixtures
use intercepted responses in Chrome 156. They establish interface behavior,
not beamline connectivity, numerical qualification or deployed performance.
The build retains Vite's large main/Plotly chunk warning.

The previous source release's hosted **Quality** workflow succeeded for
`90a139f`; local checks above apply to this follow-up. Hosted results for its new
commit must be checked separately after pushing.

The modified local Chroma database remains excluded from staging. The existing
cleanup archive and research/data directories were preserved; no new archive
moves were needed. The Python service was not restarted. Refresh the browser for
the rebuilt frontend; restart the service at a suitable point if it has not been
restarted since the earlier backend changes. See [the Canvas guide](../../CANVAS_WORKSPACE.md#see-the-changes).
