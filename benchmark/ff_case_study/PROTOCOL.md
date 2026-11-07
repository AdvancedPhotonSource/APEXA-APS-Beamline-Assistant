# Gold FF case study — fresh raw-data reconstruction

The operator selected 20-ID-D `nfdev_jul26` scan 001181 and expects two grains.
Treat two as an operator-supplied physical hypothesis, not a target to optimize.
Do not seed orientations, positions or detector geometry from the old results.
All previous reconstructions remain historical, unvalidated evidence.

Inputs are scan 001181 and CeO2 0723 aero-0/180 scans 001188/001187 at detector
readback 900 mm. Freeze exact hashes, shapes and sampled pixels before fitting.
Energy 63.314 keV is operator-supplied in the historical acquisition record;
the HDF5 contains no measured energy. Condition absolute lattice/strain claims
on that input. Detector pitch is 150 um, shape 2880 square, ImTransOpt 2, and
the station convention negates aero omega. Skip empty leading frame 0, retain
the remaining 1441. The current scan_parameters start/end (0/20 degrees)
conflict with the 360-degree aero readback; preserve this discrepancy. Use the
recorded 0.25-degree step and nominal start 180 degrees under the documented
station convention, and do not claim an independently measured omega zero.

The gold raw frames are sparse and already DAQ dark-subtracted. Its embedded
dark is exactly zero. Use that zero placeholder to prevent second subtraction.
The CeO2 calibration uses its own measured nonzero embedded dark. The old gold
archive instead supplied an external nonzero dark; its grain count is not truth.

Fresh calibration uses maintained MIDAS `--mode ff`, no previous geometry,
nominal distance as a seed, auto beam-center seeding, and a 100-microstrain gate.
Fit both CeO2 views, preselect aero-180 for reconstruction, and report the
0/180 difference. Do not select the view based on the resulting grain count.
Use factory FF search defaults with a full -180/180 seed window, MinNrPx 4,
and ring thresholds 20/20/20/20/30 for rings 1–5. These are development settings,
not a blinded parameter choice. Preserve every candidate and exact effective
archive configuration. Bounds on beam/sample size are search bounds, not size
measurements; derived grain radii are not validated morphology.

Execute the fresh reconstruction through APEXA's typed
`run_ff_hedm_full_workflow`, followed by its independent artifact verifier.
This is a scripted exercise of the scientific-tool interface, not a scored
autonomous LLM trial. Report the numerical engine separately from orchestration.
If needed, measure Wedge/OmegaStep from raw peak-list Friedel pairs and refine
tx with fixed grain poses. At most three declared geometry updates; preserve
all outputs and do not tune thresholds to force two grains. Refitting geometry
requires fresh downstream stages and verified parameter refresh.

Audit all grain candidates using cubic-symmetry orientation differences,
positions, shared spot IDs, indexed-spot support and overlays on raw frames.
Distinguish two spatial objects from two crystallographic grains. Report
unexplained extra candidates and missed evidence. In-sample completeness or
small residual alone is not external validation. A single FF layer supplies
orientation/centroid estimates, not resolved boundaries or an absolute strain
ground truth. Produce a figure with raw-image evidence and the complete
candidate table; do not display a hand-selected two-grain reconstruction.

All new files live under `/home/beams/S20HEDM/apexa_eval/ff-case-study/`.
Raw inputs, production APEXA, shared MIDAS and old analysis outputs are read-only.
Numerical execution takes the comparison's common scientific-run lock.
