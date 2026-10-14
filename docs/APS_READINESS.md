# APS deployment and capability review

For the subsequent Web UI, reconstruction-status, technique-coverage, and
performance changes, see [WEBUI_REVIEW.md](WEBUI_REVIEW.md). Test counts below
record the initial review; the follow-up contains the newer verification scope.

Review date: 2026-10-14. APEXA base commit `ec33ec2`; MIDAS source base
`4e461fb5`. Local working changes are additional to these revisions. MIDAS is
at `~/Git/MIDAS` on this workstation; `~/opt/MIDAS` is absent. Target beamline
identifiers, hosts, detector configurations, and latency budgets still need to
be recorded before commissioning.

## What was inspected and changed

This review traced implementation bodies and tool signatures. Passing software
tests establishes the exercised contracts; it does not establish scientific
accuracy on an APS detector or throughput on a deployment GPU.

| Area | Evidence in implementation | Change / remaining condition |
|---|---|---|
| Acquisition | `scan_once` previously completed failed jobs; expected count bypassed write settling | Exact file snapshots, directory-scoped jobs, write quiet period, explicit success before completion, retry after failure |
| Data discovery | `_classify_input` treated any HDF5 suffix as detector data | HDF5 signature recognition, numeric non-empty image shapes, ambiguous detector selection deferred; full pixel integrity remains unverified |
| Recommendations | FF/PF recommendations used `result_dir`; tools require `result_folder` | Corrected argument keys and backend value; tests bind recommendations to actual tool signatures |
| Unknown modality | Recommendation defaulted to FF with no decisive signal | Now requests metadata/geometry inspection |
| Metadata units | MIDAS `_probe_hdf5` guessed distance and wavelength units from magnitude | Declared units take precedence; unsupported/invalid declared values produce warnings and are omitted; legacy unit guesses have low confidence |
| FF reconstruction | Wrapper invokes `midas-pipeline run --scan-mode ff`; graph invokes FF wrapper | Confirm native package version, parameter lint, output contents, and independent reference agreement on target host |
| NF reconstruction | Wrapper invokes `midas-nf-pipeline run` with NF stages and outputs | Distinct NF procedure; the FF graph is not a general NF reconstruction engine |
| Streaming | MIDAS `integrate_stream` builds geometry once and iterates a frame source | This is a real integration implementation; with no writer callback it accumulates profiles. APEXA watcher currently processes completed file groups synchronously |
| Web hosting | Shared global MCP client, unauthenticated HTTP/WebSocket routes | Default binding changed to localhost; shared hosting still needs authentication and session/job isolation |

The MIDAS source change must be packaged and installed into the selected
`APEXA_MIDAS_BIN` environment before a deployment can benefit from it. Editing
the source checkout does not update an unrelated installed wheel.

## Dataset understanding contract

Use NeXus/HDF5 conventions where present, plus adapters for APS Data Exchange,
MIDAS Zarr archives, TIFF/GE headers, and acquisition sidecars. HDF5 is a
container format; it does not by itself identify a scientific technique.
NeXus provides structure and metadata conventions, not a universal workflow
recommender. Filename patterns remain discovery hints that need confirmation.

Proposed versioned manifest (not yet fully implemented):

| Record | Required evidence |
|---|---|
| Acquisition identity | Beamline, experiment, scan, sample, detector, timestamps, completion signal |
| Input inventory | Exact paths, sizes, array shapes/dtypes, link targets, accessible/readable state; explicit traversal limits |
| Physical metadata | Energy/wavelength, pixel pitch, detector distance/pose, axis values and units, transformations |
| Associations | Sample frames, darks/flats/masks, calibration, parameter files, acquisition logs; acquisition IDs preferred over proximity/name |
| Provenance | Each value's source file and dataset/attribute/line, declared units, conversion, confidence, conflicts |
| Candidate workflows | Required fields/artifacts, matching evidence, contradictions, missing inputs, available executor/version/device |
| Execution plan | Frozen inputs/configuration, output location, resource budget, scientific checks, restart policy |

Read NeXus `NX_class`, `NXdata.signal`/`axes`, `NXdetector`, beam/sample metadata,
and `NXtransformations`/`depends_on` when supplied. Preserve raw values alongside
normalized values. Resolve detector identity and axis mappings explicitly; a
rotation array alone cannot distinguish HEDM from tomography. Unknown or
conflicting geometry should produce a question, not a reconstruction default.

Verify capabilities in increasing depth: callable signature → installed
executor/version → representative fixture → reference reconstruction → target
host load and recovery. Record which depth passed for each technique. Do not
turn a tool's name or docstring into an availability claim.

## Scientific canvas and deployment additions

The existing React/Plotly UI now has container-aware plot sizing, responsive
canvas layout, artifact-specific diffraction views, a visual gallery, and
comparison that preserves the selected results. MP `files[].cif_file` and COD
`candidates[].cif` responses open symmetry-expanded CIF structure views.

The sequence viewer reads individual HDF5/TIFF/detector frames, applies optional
dark subtraction and nonzero-excluded masks without modifying raw files, and
provides contrast, playback, radial profiles, and ideal CIF ring guides. Ring
positions assume an untilted detector, omit distortion/intensities, and bound
reflection indices to ±8. They are not a calibrated diffraction simulation.
Directory sequence ordering is natural filename order and must be checked
against acquisition metadata. Source size/mtime changes require a new snapshot.

Linked inspection reports update with accepted frames and the current ring
model, recording correction files, display settings, source identity, and an
analysis key. Existing reconstruction reports remain historical snapshots.
Gallery cards do not start additional sequence readers.

All three deployment configurations are described in `DEPLOYMENT_MODES.md`.
An optional standalone science service handles detector inspection on the data
host through a loopback SSH forward. CIFs fetched on the coordinator use the
explicit local science route. Remote-only CIF structure browsing is not yet
exposed in the structure-view UI; ring simulation supports either CIF location.
Tunnel failures surface as errors with no local-data fallback. Actual SSH,
internal-model, and disconnected-model commissioning remains outstanding.

Local verification: APEXA Python suite 330 passed / 1 skipped; MIDAS parameter
suite 244 passed / 2 skipped; frontend state/parser suite 5 passed; TypeScript
and production Vite build passed. Checks include synthetic detector contents,
corrections, CIF/Bragg physics, API provenance, and mocked tunnel forwarding.
Browser interaction could not be verified in this sandbox (listening socket
denied; headless browser startup failed). No live MP/COD retrieval, beamline
reference reconstruction, or target-host performance measurement was performed.

## Gates before beamline production

1. **Acquisition contract:** explicit scan completion or a validated writer
   protocol; distinguish files from frames; verify multi-detector/omega alignment.
   A quiet period can mistake a paused writer for a completed scan.
2. **Durable execution:** persistent job IDs and input/configuration fingerprints,
   atomic claims, bounded retries/backoff, worker timeouts, cancellation, restart
   reconciliation, and output verification. Watcher completion currently lives
   in memory; tool idempotency is not a complete durable scheduler.
3. **Scientific acceptance:** site-approved calibrant residuals and geometry;
   FF grain/spot/strain agreement against independently accepted results; NF
   orientation/morphology/confidence agreement; per-technique tolerances recorded
   before running. Require current-run artifacts, not exit code or file presence alone.
4. **Real-time acceptance:** measure acquisition-to-preview and scan-completion-
   to-verified-result separately (p50/p95/p99), throughput, queue age, memory,
   dropped/missing frames, and recovery time. Keep the LLM outside the per-frame
   processing loop. Agree numerical budgets for each detector and technique.
5. **Service readiness:** authenticated access, isolated sessions, dependency
   readiness probes, bounded resource use, offline startup, logs, and rollback
   to a pinned release. Serving the homepage is only HTTP liveness.

Additional observed follow-ups: `web_server.py` has a legacy direct calibration
route with a developer-specific MIDAS fallback path and blocking subprocess;
the FF graph's final node records returned grain status rather than independently
validating reconstruction artifacts. Existing filename-based technique/calibrant
heuristics and directory inventory still need the manifest contract above.

## Demo acceptance

Use a frozen, approved reference dataset and disclose its source, tool versions,
host, and whether execution is live or replayed. Show:

1. Data inventory and metadata provenance, including one deliberately ambiguous
   input that stops for clarification.
2. Recommended workflow with actual parameter names and unmet requirements.
3. A small calibration/integration or reconstruction job, then plots linked to
   its result manifest and scientific validation outcome.
4. A worker failure and recoverable job state; avoid claiming persistent restart
   recovery until the durable scheduler gate is implemented.
5. Measured latency and a clear distinction between quicklook integration and
   completed FF/NF reconstruction.

The accompanying `tutorials-demo/apexa-readiness.html` is a presentation of this
review and product direction, not a claim of completed beamline commissioning.
