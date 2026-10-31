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

The APEXA mark combines an A-shaped peak with an incoming photon beam and outgoing
diffraction arcs. Its SVG source is `frontend/public/apexa-mark.svg`, shared by
the header and favicon. No external font, image request or image model is needed.

## See the changes

For the Python-served UI at `http://localhost:8001`, finish or cancel active work
through its normal controls, stop the web server in its terminal, then run:

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

## Design basis

The workspace follows [JupyterLab's document areas and saved workspaces](https://jupyterlab.readthedocs.io/en/stable/user/interface.html)
and [VS Code's tabs and split editors](https://code.visualstudio.com/docs/getstarted/userinterface),
with [WAI-ARIA tab keyboard behavior](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/).
These references were checked on 30 October 2026. APEXA adds result provenance,
stable file views, retained plot snapshots, explicit two-result comparison and
session-aware asynchronous delivery. These are product design choices, not a
claim of benchmark superiority or scientific validation.
