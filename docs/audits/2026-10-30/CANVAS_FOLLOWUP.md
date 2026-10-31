# Canvas, appearance and release follow-up

Work resumed on 30 October; final verification completed on 31 October 2026.
This completes the interrupted canvas review on top of the
[additional audit fixes](../2026-10-28/ADDITIONAL_FIXES.md).

## Changes

- New visual results open automatically and reveal Canvas. Duplicate delivery
  updates the original entry without reopening closed tabs or changing the chosen
  layout. Supporting text logs do not hide the active plot.
- Selecting a second comparison tab preserves the first selection. Gallery's
  View comparison reopens both results; opening an unrelated result exits compare.
- File viewers share identities across Data, tool results and saved sessions,
  with local and remote sources distinct. Legacy saved IDs migrate together with
  open/closed state and favorites. Viewer settings survive repeated file delivery.
- Plot identities include the source, operation, settings and returned data;
  repeated folder previews and radial profiles reuse unchanged results. Pinning
  a chat response reuses one favorite.
- Tabs support keyboard selection/closing and reveal the active tab in a long
  strip. History exposes favorites, and the empty/active views explain tab behavior.
- Multi-file upload continues after the first result replaces the empty view.
  Delayed file/upload/CSV responses are rejected after a session switch.
- Classic light uses the original white/cobalt/cyan palette. A shared SVG APEXA
  mark is used by the header and favicon. The web launcher detects public-asset
  changes, and the production frontend has been rebuilt.
- README, the Web UI entry guide and documentation navigation now point to the
  [Canvas guide](../../CANVAS_WORKSPACE.md) and [repository layout](../../REPOSITORY_LAYOUT.md).
  Two loose generated output locations were archived under ignored `scratch/`;
  no scientific inputs were deleted.

## Verification

| Check | Result |
| --- | --- |
| Offline Python suite | 963 passed; one optional native MIDAS engine test skipped; three warnings |
| Frontend Node suite | 78 passed |
| Canvas browser interactions | 14 passed in Chrome 156 |
| Workflow browser interactions | 10 passed in Chrome 156 |
| Desktop and phone-sized preview | No JavaScript errors or horizontal page overflow; screenshots inspected |
| TypeScript and ESLint | Passed |
| Production frontend | Built successfully; Vite retains a large-chunk warning for the main/Plotly bundles |
| Dependency lock | `uv lock --check --offline` passed |
| Whitespace | `git diff --check` passed |

The Python suite ran with an isolated temporary `APEXA_HOME`,
`APEXA_NETWORK=data` and `APEXA_OFFLINE=1`. Browser fixtures intercepted service
requests; no live science jobs, model inference or motor writes were performed.
Browser scripts are `frontend/tests/browser-canvas.mjs` and
`frontend/tests/browser-workflows.mjs`; their external Playwright setup is
documented in [frontend performance](../../FRONTEND_PERFORMANCE.md).

The production assets are rebuilt, but this change does not restart an existing
Python service. Follow the [restart instructions](../../CANVAS_WORKSPACE.md#see-the-changes)
to load the backend changes. Numerical/reference qualification, hardware
commissioning and external-job reconciliation remain the deployment boundaries
identified in the earlier audit.

The local change to `knowledge_base/chroma_db/chroma.sqlite3` is excluded from
the source release and preserved on disk. The application packages, generated
scientific viewer, local data and credentials are not included in the commits.
