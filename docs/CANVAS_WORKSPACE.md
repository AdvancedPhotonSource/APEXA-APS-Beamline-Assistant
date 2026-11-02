# Canvas workspace

Canvas holds results from the current conversation: plots, detector views, crystal
structures, refinement workbenches, reports, tables and pinned responses. Tabs are
individual results, not workflow stages. A tool can produce several results.

## Opening and selecting results

| Action | What happens |
| --- | --- |
| A new visual result arrives | A tab opens, becomes active, and reveals Canvas. Chat-only layout becomes Split. |
| A tool returns a plot and a table | Both tabs open; the plot is selected. A background text log does not hide the plot. |
| The same result arrives again | Its existing entry updates without stealing focus or reopening a tab you closed. |
| Open from Data, Results, Gallery, History or a tool card | The requested result is selected, reopened if necessary, and shown in Active. An unrelated comparison closes. |
| Open the same local CIF or detector sequence through another entry point | Reuse the same viewer and its saved settings. A remote path is a separate resource. |
| Recompute a plot with different data or settings | Create another result so the previous plot remains available for comparison. |
| Close a tab | Hide it from the strip; keep its result in History. The neighboring tab becomes active. |
| Pin a result | Mark a History favorite. Closing its tab still works. |
| Pin a chat response | Open one stable text result and mark it as a favorite; repeated clicks reuse it. |
| Remove from History | Remove that session's canvas entry. This does not delete the source file or rerun a tool. |

**Active** shows open tabs. **Gallery** is a bounded visual overview. **History**
includes closed results and a Favorites filter. Results in the left navigation
also exposes saved session artifacts and report opening.

## Comparing two results

Open the first result and click **Compare**, then select another tab and click
**Compare** again. Selecting that second tab preserves the first selection.
Gallery also lets you select two cards and choose **View comparison**; this
reopens either result if its tab was closed. A new visual result automatically
returns to the new result, matching the selected automatic-opening behavior.

## Keyboard and session behavior

Within the tab strip, Left/Right Arrow selects the neighboring tab; Home/End
selects the first/last. Delete closes the focused tab. New active tabs scroll
into view without taking keyboard focus away from chat.

Shared sessions retain viewer settings, open/closed tabs, active selection and
favorites. Old structure/sequence IDs migrate to shared file identities on
restore. An upload, folder visualization or CSV read that finishes after a
session switch cannot insert its result into the new chat. See
[Shared sessions](SESSIONS.md) for storage and recovery limits.

## Appearance and logo

**Classic light** is the first-use default: white and pale-gray surfaces, cobalt
buttons and selected controls, cyan accents and dark text. The header theme
button cycles Classic light, Dark and Midnight; an existing saved preference is
respected. Choose Classic light if a returning browser still shows a dark theme.
The palette is independent of the Chat / Split / Canvas layout: changing colors
does not change panel arrangement or controls.

Desktop controls use compact sizing: a 32 px top bar, a model selector up to
160 × 24 px, and a 52 px navigation rail. The feature-request lightbulb retains
its tooltip and accessible label. Touch input keeps larger controls, and narrow
screens can wrap the header. Panel placement and Chat / Split / Canvas behavior
are independent of these dimensions.

The APEXA mark uses an indigo A on a glass tile with a pale centre and blue
edges, crossed by one metallic gold photon beam and a bright sample diamond.
Three faint, smooth diffraction rings sit behind the glass sheen. A defined blue rim separates the
tile from white surfaces, with a subtle shadow in Classic light. The pale centre
keeps the indigo A clear. A fine gold edge defines the beam against the pale
glass. These logo colors do not alter
the application's Classic palette.

Its canonical SVG is `frontend/public/apexa-mark.svg`, also copied to
`frontend/public/favicon.svg`. A 1024 × 1024 PNG export
with transparent corners is saved at `frontend/public/apexa-mark.png` for direct
preview and reuse in documents. The full wordmark in `frontend/public/apexa-logo.svg`
combines the same icon with custom geometric vector lettering, so it requires
no installed font. The README uses this full wordmark; the compact icon fits
chat avatars and toolbar controls. Its transparent PNG export is `frontend/public/apexa-logo.png`
(1536 × 448). The shared `ApexaLogo`
component bundles that source for the header, navigation, welcome screen,
assistant replies and the Thinking indicator shown after sending a message.
Chat avatars use 28 px, while the header mark uses 24 px.
Chat avatars have an accessible APEXA label. No separate image request, external
font, runtime image processing or service is needed. When updating the mark,
keep the favicon, wordmark icon and PNG exports in sync and rebuild `frontend/dist`.

## See the changes

For frontend-only updates, including the logo, rebuild the frontend (or pull
the updated tracked `frontend/dist`) and refresh the browser. Use **Cmd+Shift+R**
on macOS or **Ctrl+Shift+R** on Windows/Linux if it still shows old assets.
The running Python server does not need a restart for these asset changes.
An already-open tab retains its loaded UI until refreshed. If the controls still
look enlarged after a hard refresh, reset browser zoom with **Cmd+0** or
**Ctrl+0**. At desktop width with a mouse/trackpad, the top bar is 32 px high,
the model selector is 24 px high, and the navigation rail is 52 px wide.
Touch screens use larger controls, and a narrow window may wrap the top bar.

For the Python-served UI at `http://localhost:8001`, finish or cancel active work
through its normal controls before a backend restart, stop the web server in its
terminal, then run:

```bash
cd ~/Git/beamline-assistant-dev
./start_web_viewer.sh
```

The launcher rebuilds stale frontend sources when Node dependencies are available.
Refresh the browser after startup; use a hard refresh if the old assets remain.
Backend changes require a process restart. A browser refresh cannot reload Python.
For a deliberate build before restart:

```bash
cd ~/Git/beamline-assistant-dev/frontend
npm run build
```

The repository includes the production `frontend/dist` so a deployment can use
reviewed assets without installing Node. During frontend development, Vite
updates the UI automatically; it does not restart the Python backend.

### Interpreting web-server logs

- `PATCH .../workspace` → 405 followed by `PUT` → 200 means the new frontend is
  using its compatible autosave fallback with an older backend. Restart the backend
  to enable smaller PATCH saves; the successful PUT still saved the workspace.
- `/apexa-mark.svg` → 404 requires the current backend's named logo route and a
  built `frontend/dist/apexa-mark.svg`. The production server serves that SVG
  explicitly; a working Vite preview alone does not verify production routing.
- A 403 can come from the Host/Origin boundary. Open `http://127.0.0.1:8001`
  consistently. Current backends log the rejection's policy reason without
  printing request headers or credentials; keep the boundary enabled.
- WebSocket close code 1001 during a browser refresh is a normal disconnect.
- Repeated status polls do not mean a chat job is running. For Argo retries or
  acknowledgments without tool results, see [LLM transport troubleshooting](ARGO_NATIVE_ENDPOINT.md#webui-startup-and-replies-that-do-not-execute).

## Design basis

The workspace follows [JupyterLab's document areas and saved workspaces](https://jupyterlab.readthedocs.io/en/stable/user/interface.html)
and [VS Code's tabs and split editors](https://code.visualstudio.com/docs/getstarted/userinterface),
with [WAI-ARIA tab keyboard behavior](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/).
These references were checked on 30 October 2026. APEXA adds result provenance,
stable file views, retained plot snapshots, explicit two-result comparison and
session-aware asynchronous delivery. These are product design choices, not a
claim of benchmark superiority or scientific validation.

The [2 November consistency check](audits/2026-11-02/CONSISTENCY.md) covers the
combined release, including automatic integration plots and saved-chat shortcuts.
