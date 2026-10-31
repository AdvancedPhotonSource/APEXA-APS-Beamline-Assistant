# Agentic powder refinement in APEXA

Current workflow, synchronized with the 15 October 2026 manuscript audit.
See [the integration guide](docs/powder-refinement.md) for configuration, scientific
status definitions, trial policy and the frozen benchmark snapshot.

## 1. Submit a durable tutorial job

In APEXA, request:

> Submit the PbSO4 X-ray tutorial as an independent powder job. Resolve the
> configured Agentic-GSAS-II checkout and use
> `tutorials/PbSO4_independent/PBSO4.XRA`, `INST_XRY.PRM` and `PbSO4.cif`.
> Set the native reader to GSAS, axis to two_theta, units to degree, limits to
> [12, 150], and max_steps to 12. Preserve the supplied instrument calibration;
> do not infer geometry or claim calibration is validated. Return the job IDs.

The equivalent bridge submission, from the APEXA repository with its Python
interpreter, is:

```python
import json
import os
from pathlib import Path
from apexa_powder import submit

repo = Path(os.environ.get("APEXA_AGENTIC_GSAS2", Path.home() / "Git/Agentic-GSAS-II")).expanduser()
python = Path(os.environ.get("APEXA_GSASII_PYTHON", Path.home() / "miniconda3/envs/GSASII/bin/python")).expanduser()
store = Path(os.environ.get("APEXA_GSAS2_JOBS", Path.home() / ".apexa/gsas2_jobs")).expanduser() / "engine"
data = repo / "tutorials/PbSO4_independent"
request = [{
    "data_path": str(data / "PBSO4.XRA"),
    "instrument_path": str(data / "INST_XRY.PRM"),
    "cif_paths": [str(data / "PbSO4.cif")],
    "format": "GSAS", "axis": "two_theta", "units": "degree",
    "source": "GSAS-II PbSO4 tutorial", "sample_id": "PbSO4-demo",
    "limits": [12.0, 150.0],
}]
print(json.dumps(submit(python, repo, store, request,
                       mode="independent", workers=1, policy={"max_steps": 12}), indent=2))
```

This launches real refinement work. Record the returned IDs. No fixed runtime,
residual, convergence outcome or cell value is promised by the demonstration.

## 2. Poll and inspect

Ask APEXA to poll the returned batch ID (or an individual engine ID from the
Python example) with `refinement_status`, then generate `powder_report` after
execution. The job is durable; the conversational session need not remain open.

Show:

- Execution state separately from `scientific_status` and `evidence_coverage`.
- Rwp together with observed/calculated profiles, weighted residuals and reflections.
- Covariance and solver convergence. A scheduler stop does not prove convergence.
- Reference cells alongside `effective_cells` after HStrain and their conditional
  uncertainty scope. Missing/fixed uncertainties are unavailable, not zero error.
- The actual decision trace. Configured candidate parameters are not proof that
  those terms were refined.

A blocked result is a useful demonstration of a configured failure, not a
validated phase or composition. The current engine has no `qualified` output path.

## 3. Generate evidence and optionally request advice

Use the completed result's `gpx_path` and its returned observation identity for
`powder_evidence`, with a fresh output directory. Open the resulting global and
peak-window panels before choosing a proposal.

With an image-capable endpoint configured, call `powder_advise` on the generated
`evidence.json`. The default evaluates up to three proposals as shadow trials.
Without such an endpoint, inspect the panels and supply a typed proposal to
`powder_trial` only when the evidence supports a specific action.

Explain each trial's accepted/rejected decision, covariance/convergence evidence,
Rwp change and combined window loss. Acceptance does not require every cited
window to improve. A shadow candidate never silently replaces the baseline.
The `uiso` action name refers to release of the existing atomic displacement
parameters and does not guarantee isotropic motion.

## 4. Extend to mixtures, batches and acquisition

- Add candidate CIFs to an observation for multiphase refinement.
- Submit independent laboratory/synchrotron observations with their own instrument
  files and metadata. Select individual jobs in the canvas; the batch headline
  counts all jobs rather than displaying the first fit as a group result.
- Use sequential mode for ordered frames with compatible state transfer. Inspect
  `warm_start` for actual transfer or cold-start reasons.
- Use joint mode only for matching sample, conditions and phase identity.
- Use `powder_live_ingest` with completed-frame markers. Poll `jobs.fast` and
  `jobs.full` independently; do not claim sustained throughput from this demo.

For retrieval, `propose_structures` can rank COD candidates when network access
is available. M20 is a heuristic, not a probability of correctness.

## Evidence limits to state during the demonstration

The audited numerical run contains 1,676 records: 1,514 completed blocked,
157 provisional and five execution errors. The 20 supplied-phase DARA mixtures
are all blocked (pooled median absolute composition error 24.97 percentage
points); the 17-frame temperature series is all blocked; the joint CW/TOF suite
timed out. Separate small workflow examples establish transfer and isolated-trial
mechanics. Neither those checks nor this walkthrough establishes quantitative
accuracy, benefit from image advice or reliable live acquisition performance.

Source: [versioned audit snapshot](docs/data/powder-benchmark-20261015.json).
