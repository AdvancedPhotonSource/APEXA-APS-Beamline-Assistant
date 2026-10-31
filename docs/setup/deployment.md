# Running APEXA locally

**Reviewed:** 2026-10-15

This page covers starting the CLI and Web UI from a checkout. For an always-on
service, use the [Web UI deployment guide](../DEPLOYMENT.md). For SSH, internal
network, and disconnected layouts, see [deployment modes](../DEPLOYMENT_MODES.md).

## First launch

From the repository root:

```bash
uv sync --locked
./setup_user.sh
./start_beamline_assistant.sh
```

The setup script creates `.env` with the selected Argo model and host settings.
Review `servers.config` before startup: it controls which MCP servers APEXA
launches. The tool inventory records source registrations; it does not confirm
that an engine, its data, or beamline hardware is ready.

To launch the browser interface:

```bash
./start_web_viewer.sh
```

Open <http://localhost:8001>. The launcher builds the React UI when its source is
newer than `frontend/dist`; it needs `frontend/node_modules` for that build.
Install frontend dependencies with `cd frontend && npm ci` on a connected setup
host, or stage a prebuilt `frontend/dist` for an offline host.

## Runtime boundaries

The APEXA Python environment runs the client, web server, and configured MCP
servers. Scientific engines may run in separate environments:

| Capability | Configuration | Readiness boundary |
|---|---|---|
| MIDAS | `APEXA_MIDAS_BIN`; `MIDAS_PATH` only for native C or legacy paths | See [MIDAS setup](../MIDAS_SETUP.md) and `validate_midas_installation`. |
| Agentic-GSAS-II | `APEXA_GSASII_PYTHON`, `APEXA_AGENTIC_GSAS2` | Requires the configured GSAS-II interpreter and Agentic-GSAS-II checkout. |
| TomoPy | `APEXA_TOMO_PYTHON` | Requires an interpreter that imports `tomopy` and `dxchange`; see [tomography setup](../TOMO_SETUP.md). |
| EPICS motors | `caget` and `caput` on `PATH`, plus reachable IOCs | Registered motor tools do not establish hardware connectivity or motion safety. |

MAUD is an optional, limited backend of MIDAS `run_gsas_refinement` for
supported caked archives. pyFAI, TOPAS, FullProf, DIOPTAS, and Mantid do not have
active execution tools in the default configuration. See the
[diffraction software survey](../diffraction-software-survey.md).

The Web UI has no application login. Keep it on a trusted local host unless it
is protected by an authenticated proxy that covers both HTTP and WebSocket
traffic; see the [deployment guide](../DEPLOYMENT.md).

## Updating a running checkout

Stop the top-level CLI or Web UI process with Ctrl+C, then launch it again from
the intended checkout. A running Python process keeps its loaded routes and
server registry even after files change. The Web UI launcher also checks whether
the frontend needs rebuilding.

Do not force-kill an MCP child by process name or start a second copy of one by
hand. The top-level APEXA process manages those child servers.

## Troubleshooting

### `/api/sessions` or `/api/sessions/import` returns 404

The running Web UI process may have loaded an older route table. Stop it, run
`./start_web_viewer.sh` from the current checkout, and reload the browser.
See [shared sessions](../SESSIONS.md).

### A result image is missing

The refinement canvas only opens plot paths returned by the engine. If the
result has no plot path, the UI reports that the image was not included. If a
returned path fails to load, confirm that the file exists on the APEXA host and
that the latest Web UI server is running; do not assume a file named
`hist.png` was generated.

### A tool is unavailable

Check the tool's structured result and the relevant server logs. Confirm that
the server is enabled in `servers.config`, then check its interpreter,
executable, data path, or hardware prerequisites. Restart the top-level APEXA
process after changing configuration.

### The docs site does not start

The docs-only MkDocs Material package is resolved by `serve_docs.sh` on first
use. That first run needs access to the configured Python package index or a
pre-populated uv cache. APEXA runtime dependencies do not include the docs
theme.
