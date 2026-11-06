# Powder-diffraction calibration procedure

This document specifies a general analysis procedure. It contains no fitted
geometry, scan-specific repair, reference trajectory or expected outcome.

## Where to find instructions

Use the procedure and skills staged in this `manuals` directory. Repository
capsule paths mentioned inside the invocation skill describe an upstream
deployment; those external manuals are not supplied for this study. Do not
search the host filesystem for them or for other acquisitions. Inspect the
installed `midas_calibrate_v2` Python package, its module docstrings, signatures
and CLI help when more API detail is needed. The configured `python` on PATH
is the shared scientific environment. Keep all trial outputs in the workspace.

## Inputs and units

Inspect the HDF5 groups, frame dimensions, dtype and correction arrays before
processing. Follow the acquisition instructions supplied with the task. Apply
the declared frame selection before reducing a stack. Record whether a dark
array exists, is finite, has the right shape and contains a measured correction.
Do not invent a dark measurement or subtract the same correction twice.

Use the operator-supplied energy, pixel pitch and image transform. A filename is
a discovery hint, not a measurement of energy, distance or binning. MIDAS uses
micrometres for pixel pitch and distance, angstroms for wavelength and lattice
parameters, and degrees for detector tilts. Record any approximate initialization
separately from refined quantities. Obtain the calibrant identity from the task
and acquisition record; CeO2 and LaB6 require their respective crystal models.

## Fit and inspect

The installed numerical package is `midas-calibrate-v2`. Inspect its installed
CLI/API before choosing a route; interfaces change across releases. Use the
declared version. In APEXA, request `calibration_engine="v2"` explicitly to prevent
fallback to another numerical engine. General coding harnesses have the same
numerical Python environment and console entry points on PATH.

Preprocess consistently, identify usable rings, initialize geometry, and refine
with an appropriate calibrant model. Retain input parameters, effective settings,
solver logs, exported geometry and any independent validation products. Apply
image transformations exactly once. For a parameter update, rerun affected
stages and confirm that the effective exported configuration changed as intended.

Assess ring overlays or available held-out residuals before accepting a result.
Distinguish optimizer termination, image agreement and scientific validity.
Inspect active bounds and data coverage; an active periodic phase boundary alone
does not establish an invalid fit. An inadequate residual or failed check calls
for diagnosis, justified recovery or a qualified rejection. Do not simply relax
an acceptance threshold to obtain a pass.

Image agreement alone does not establish absolute wavelength, scale or handedness.
Scope the conclusion to the supplied metadata and checks that actually ran.
State missing checks and unresolved ambiguities. A low residual is not evidence
that every input or requested model intervention was correct.

## Final result

Write the adopted-artifact manifest requested in the task. Its hash must identify
the exact final geometry file. Separate the final scientific decision from earlier
failed attempts, and report the evidence supporting it. A provisional result or
an abstention is preferable to unsupported acceptance. Do not infer any reference
geometry by searching outside the trial workspace and installed documentation.
