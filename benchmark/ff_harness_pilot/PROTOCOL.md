# Real-data FF pilot on Alleppey

This is separate from the frozen calibration comparisons and the synthetic FF
workstream. It uses the operator-selected 20-ID nfdev_jul26 gold acquisition
1181, with a fresh CeO2 calibration from acquisition 1187. Acquisition 1188 is
retained as a calibration diagnostic, not selected by reconstruction outcome.
All inputs are copied from the original HDF5 files and hash-checked before use.
Prior fitted geometry, grain lists and worked outputs are evaluator-only.

First execute a fixed upstream MIDAS reference workflow in the private environment
on eight physical CPU cores. This is a setup/development measurement, not an agent
trial. Preserve any failure and its cause. Measure calibration and reconstruction
time separately. Installation/import checks alone do not qualify the workflow.
Freeze the agent protocol and budget only after this reference is executable.

The intended pilot contains APEXA (complete native configuration), Claude Code
and Pi, two attempts each, with one acquisition. It estimates feasibility and
documents failures; it does not estimate population reliability or establish a
system ranking. Each receives identical operator facts, eight CPU cores, a common
model and written completion guidance. Coding systems receive unedited upstream
MIDAS procedures and skills; APEXA retains its own procedures and tools.

The provisional generation allowance is 120 requests. The common wall limit is
set before agent trials from the fresh reference time: at least 3,600 seconds,
or twice the complete reference duration plus 900 seconds, rounded up to a
five-minute boundary, whichever is larger. No outcome-dependent budget extension
or replacement of an attempted trial is allowed. Synthetic transport/file/image
qualifications must pass on the actual host before scientific agent runs.

Operator facts: 63.314 keV; gold FCC, space group 225, lattice 4.0782 Angstrom;
150 micrometre pixels, 2880 square detector, ImTransOpt 2. The gold frames are
already dark-subtracted and their embedded dark is zero; the calibrant requires
its own measured embedded dark. Skip the nearly empty leading gold frame. Use
the documented negated omega convention (nominal start 180 degrees, step -0.25
degrees). The 0--20 degree scan-header range conflicts with the nearly full-turn
readback and must not silently replace these conventions. Absolute angular zero,
energy accuracy and physical grain count remain unverified.

The reference uses the reviewed FF recipe: CeO2 calibration in FF mode, nominal
900 mm seed, no prior refined geometry; gold rings 1--5, thresholds 20/20/20/20/30,
minimum four pixels and the full -180/180 indexing seed window. These are known
development settings. They are not a blinded optimization or an answer key.

Keep distinct outcomes for: completed stages; effective input/parameter integrity;
fresh, valid adopted artifacts; resource/transport failures; requests for essential
information; and the scientific justification for acceptance. Two physical cubes
do not establish two crystallographic orientations. Never score a grain count of
two as success or discard extra candidates to achieve it. Structural checks and
in-sample spot matches are not physical certification. A reflection prediction
test may be added only after identifying data genuinely excluded from fitting and
freezing its selection and negative controls before inspecting those predictions.

Remote root: `/scratch/ptripathi/apexa-cvpr`. Input files, installation records,
reference output, native context, qualifications and agent attempts have separate
subdirectories inside this one project. The local receipt mirror stays within
the existing Box comparison audit. R5 and R6 remain unchanged.

## Execution and records

`qualify.py` checks native shell and image transport, an explicit optional
token-count endpoint probe, and concurrent file I/O with APEXA's procedure
delivery. A native CLI need not issue a token-count request during its small
fixture. All required requests it does issue must pass. A separate sandbox
check imports the installed solver and verifies that original inputs are hidden.
The first reference failed after numerical calibration because the FF plotting
dependency `matplotlib` was missing; the private environment was repaired and
the full reference was explicitly restarted as attempt 2. The original numerical
outputs, failure, package freezes and setup qualification attempts are retained.

`run.py --root /scratch/ptripathi/apexa-cvpr --wait-for-gates` waits for the
reference and native qualification. It checks fresh reference artifacts and the
declared operator parameters before freezing `setup/pilot-frozen.json`. Failed
gates stop admission. Two waves run three systems concurrently, on disjoint
eight-core slots; the system-to-slot assignment rotates in the second wave.
The process wall timer excludes input staging; the relay enforces the common
120-generation allowance. Token-count traffic is audited separately. Trial
directories are independent copies and only the current trial is writable.

The final prompt, upstream/native context, solver versions, code hashes, time
allowance, inputs and schedule are frozen before any scientific attempt. No
scientific attempt is silently repeated. `results/agents/trial_status/` and
`results/agents/summary.json` retain every admitted attempt, including failures.
Traces, prompts, model request metadata and adopted artifact hashes are retained.

`evaluate.py` checks the final decision manifest, fresh artifact identities,
declared operator facts, finite geometry and near-orthogonal orientation matrices.
It does not certify physical correctness or infer that a declared parameter file
was actually consumed; effective execution, stage completion, requests for help
and unsupported scientific claims require the retained command/tool trace audit.
There is no automatic win/loss ranking or requirement to recover two grains.
