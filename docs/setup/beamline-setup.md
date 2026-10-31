# Beamline host setup

**Reviewed:** 2026-10-15

Use a validated APEXA checkout and configure the engines available on the target
host. Older versions of this page described a specific S1IDUSER installation
and treated `AutoCalibrateZarr.py` as a universal calibration requirement. The
current MIDAS adapter supports additional routes; follow the environment checks
below instead of assuming one filesystem layout.

## Install and configure APEXA

From the checkout root:

```bash
uv sync --locked
./setup_user.sh
```

Review `.env` and `servers.config`. Preserve the validated code revision and
dependency lockfile on a beamline host; do not pull unreviewed code or update
scientific environments during an acquisition.

## Configure engines and hardware

- **MIDAS:** Follow [MIDAS setup](../MIDAS_SETUP.md). Set
  `APEXA_MIDAS_BIN` for a MIDAS pip environment. Set `MIDAS_PATH` only when a
  workflow needs the native repository or a legacy C/Zarr path. Use
  `validate_midas_installation` to inspect the routes detected by APEXA.
- **Agentic-GSAS-II:** Configure `APEXA_GSASII_PYTHON` and
  `APEXA_AGENTIC_GSAS2` when the defaults do not point to the installed
  interpreter and checkout.
- **Tomography:** Configure `APEXA_TOMO_PYTHON` for an environment that imports
  `tomopy` and `dxchange`. See [TomoPy setup](../TOMO_SETUP.md).
- **EPICS motors:** Install `caget` and `caput`, verify IOC connectivity, and
  review motor limits and site procedures before enabling motion tools.

MAUD is supported only as an optional MIDAS backend for the documented caked
archive path. pyFAI is an optional Python package, but the default configuration
does not register a pyFAI executor. See the
[diffraction software survey](../diffraction-software-survey.md) for other
external-tool boundaries.

## Verify and launch

Start APEXA with `./start_beamline_assistant.sh` or
`./start_web_viewer.sh`. Read startup warnings and each tool result as a
separate readiness signal; a registered tool or successful HTTP connection does
not prove that its engine, data path, or hardware is ready.

For a persistent Web UI, authentication, and SSH access, see the
[Web UI deployment guide](../DEPLOYMENT.md). Keep the Web UI on a trusted local
host unless an authenticated proxy protects both HTTP and WebSocket routes.
