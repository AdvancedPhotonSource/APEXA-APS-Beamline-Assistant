# B0: fixed MIDAS tilted-detector procedure

This baseline is specified before its first numerical run and before the main
agent cohort. It uses the existing development dataset and a published local
MIDAS procedure; it is not a blinded method-selection experiment.

Run `midas_calibrate_v2.pipelines.tilted.calibrate_tilted` from the frozen MIDAS
0.26.0 environment. Supply no nominal distance or tilt. Keep its built-in polish
and numerical fallback enabled. This is a fixed program without an LLM, including
the package's existing gate-based selection among numerical candidates. It does
not demonstrate APEXA reasoning. Record library/source hashes and random seeds;
do not promise bitwise determinism across machines.

Use the same operator notes as the native harnesses: 63.314 keV (wavelength
0.19582430178665108 angstrom), 150 micrometre pixels, frames 1 through 20 of 21
averaged, and ImTransOpt 2 applied once. Use the filename's calibrant prefix,
also visible to the agents. The embedded dark array is an inspected all-zero
placeholder, not a measured correction; require that property and subtract
nothing. Changed shape, frame count, nonfinite input or a nonzero dark triggers
a preprocessing failure requiring a documented protocol revision.

The installed solver's ring-position verdict controls the decision: PASS is
accepted, WARN is provisional, FAIL is rejected. Do not change gate thresholds
to obtain an acceptance. Save the full solver verdict, effective inputs, mask,
geometry and adopted-artifact manifest. The evaluator separately checks relative
tilt consistency against the same leave-one-out reference used for the agents.
The solver's image check is not independent pose, wavelength or distance truth.

Budget: 1,800 seconds and four requested CPU threads per exposure, no model
requests. Run the two pilot scans once each, sequentially after the native pilot
releases the study lock. Startup/input/worker failures and timeouts remain
visible. Do not rerun selectively or mix repaired revisions. Main baseline
execution remains blocked until the pilot is reviewed.

`baseline.py prepare` records code hashes, the exact recipe and its decision
rule in `setup/baseline/plan.json`; it performs no fits. `baseline.py run --cohort
pilot` receives the raw input read-only in Bubblewrap and writes only in its
dedicated result case. The worker receives no reference values. Its supervisor
alone grades the adopted geometry. The existing frozen native-harness code is
unchanged.
