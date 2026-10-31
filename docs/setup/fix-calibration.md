# MIDAS calibration troubleshooting

**Reviewed:** 2026-10-15

Older instructions on this page were specific to one S1IDUSER checkout and a
legacy `AutoCalibrateZarr.py` path. The current `midas_auto_calibrate` tool
selects among supported MIDAS calibration engines and reports the selected
engine, reason, and fallbacks. Read the
[MIDAS environment guide](../MIDAS_SETUP.md) before changing a beamline
installation.

## Diagnose the reported result

1. Confirm `midas:midas_comprehensive_server.py` is enabled in
   `servers.config`.
2. Read the complete structured result from `midas_auto_calibrate`, including
   `engine`, `engine_reason`, `fallbacks`, and any `fix` field.
3. Check the prerequisites for the selected engine and the input format. The
   [calibration skill](https://github.com/AdvancedPhotonSource/APS-Beamline-Assistant/blob/main/.agents/skills/midas-calibrate/SKILL.md)
   documents engine selection and supported inputs.
4. For a MIDAS pip environment, set `APEXA_MIDAS_BIN` to its `bin/` directory.
   Set `MIDAS_PATH` only when the selected workflow needs the native repository
   or legacy tools.
5. After changing configuration or code, stop and restart the top-level APEXA
   process with `./start_beamline_assistant.sh` or `./start_web_viewer.sh`.

Do not assume that `AutoCalibrateZarr.py`, a specific conda environment name,
or a workstation-specific path is required for every calibration. The tool
result identifies when a legacy route is selected and what it needs.

## Common checks

- Verify the image exists and is readable on the APEXA host.
- Confirm the wavelength or energy, pixel size, detector identity, and parameter
  template are correct for this acquisition.
- Inspect calibration residuals and the returned convergence/qualification
  fields; successful execution alone does not establish a scientifically
  acceptable calibration.
- If an engine import or executable fails, preserve the error and check the
  configured interpreter or executable directly before retrying.

For installation and runtime selection, see [MIDAS setup](../MIDAS_SETUP.md).
