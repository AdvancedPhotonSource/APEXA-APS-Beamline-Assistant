# APEXA Web UI guide

The maintained UI is the React workspace served by `web_server.py` at
`http://localhost:8001`. Start it with `./start_web_viewer.sh`.

- [Canvas workspace](docs/CANVAS_WORKSPACE.md): tabs, automatic opening, comparison,
  favorites, keyboard controls, themes, and rebuild/restart instructions.
- [Shared sessions](docs/SESSIONS.md): resume between WebUI and CLI, saved viewer
  state, recovery and storage limits.
- [Scientific workflows](docs/SCIENTIFIC_WORKFLOWS.md): reviewed workflow recipes,
  approval gates, jobs and cancellation.
- [GSAS-II workbench](docs/gsas2-workbench.md): refinement, evidence and decisions.
- [Deployment guide](docs/setup/deployment.md): installation and service operation.
- [Web runtime policy](docs/WEB_RUNTIME_POLICY.md): supported access boundaries.

Use Data to open real files or server paths, Analysis to configure a workflow,
Results to reopen recorded outputs, and Chats to resume a conversation. The
Chat / Split / Canvas header controls how much room each area receives. The
original blue/cyan palette is available as Classic light.

This guide replaces the historical prototype's fixed three-column layout and
sample-data/Quick Phase ID instructions. The React workspace's current controls
and validated scientific workflows are described in the linked guides.
