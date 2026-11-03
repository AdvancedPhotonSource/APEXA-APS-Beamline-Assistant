# Scientific deployment acceptance

The first intended slice is calibration plus integration on `ai_tune_rot_series`.
APEXA's existing fits are not independent geometry truth. Use measured pose,
hidden synthetic truth, or a separately reviewed reference analysis, together
with held-out diffraction checks.

Prepare a manifest from `examples/calibration-integration-acceptance.json`.
Record input/reference hashes, independent reference source and reviewer, the
exact engine executable/argv and geometry/curve tolerances. List the reviewed
wrapper and environment lock in `software` with their hashes; these are rechecked
before and after execution. Also freeze the installed engine environment:
hashing an interpreter or a lockfile does not verify installed package bytes.
The example intentionally fails until real reviewed values are supplied.

```bash
# Validate prerequisites; no engine executes:
uv run --no-sync python scripts/qualify_science.py /private/acceptance.json
# Execute the reviewed command on the intended host:
uv run --no-sync python scripts/qualify_science.py /private/acceptance.json --execute
```

Results default to `~/.apexa/qualification/<run-id>/`. `--output-dir` can select
a new directory outside the checkout. Existing run directories are never reused.
The runner records intent before spawning, owns timeout/cancellation cleanup,
retains logs and writes `run.json`. Inputs/references are rechecked afterward.
Missing outputs, nonfinite values, grid mismatch and failed tolerances cannot
be accepted.

Wall time and sampled worker CPU/RSS accompany each result. GPU utilization is
explicitly unmeasured by this runner: record site NVML/DCGM measurements and
compare accepted CPU/GPU outputs before adopting a compute profile. A requested
device is not evidence that the engine used it. Sampling can miss short peaks.

Also qualify the producer contract in `ACQUISITION_RUNTIME.md`, workflow restart
behavior in `SCIENTIFIC_WORKFLOWS.md`, and scheduler behavior in
`REMOTE_EXECUTION.md`. Keep host, versions, configuration, reference and evidence
together. Distinguish scientific rejection, failed execution and unknown outcome.

Tests use synthetic fixtures. No real rotation-series reference, beamline engine,
GPU, acquisition writer or cluster is certified by those tests.
