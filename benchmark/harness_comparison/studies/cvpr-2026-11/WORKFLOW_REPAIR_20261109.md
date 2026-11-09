# Native calibration workflow repair — November 9, 2026

These changes are separate development work. Frozen R5 remains at source
`5a87b4c` with its recorded overlay. No completed trial, B0 outcome, capsule,
input or budget is replaced. External harnesses retain unedited upstream MIDAS
resources. The main comparison does not yet establish full APEXA superiority.

## Failure evidence and changes

A1 traces show repeated handmade averaging, API construction and transform checks,
followed by request exhaustion or missing final adoption. The public calibration
tool did not expose frame controls supported by its internal worker. Its large
outdated description also called v1 the default although selection was `auto`.
These observations motivate repairs; they do not establish a causal retrieval effect.

- Expose the native calibration executor immediately for explicit detector/powder
  calibration requests; retain domain procedures as scientific references.
- Add exact HDF5 frame count, skip and reduction controls. A temporary virtual
  dataset preserves integer sentinels; the raw file is unchanged. Separate dark
  stacks do not inherit the image-frame skip.
- Add an explicit typed adapter to upstream MIDAS `calibrate_tilted`, preserving
  the existing four-stage default. Carry the template's actual lattice into the
  solver. Refuse unsupported panels, phases, refinement restrictions and gate
  bypass. Preserve PASS, WARN and FAIL; FAIL does not export reusable geometry.
- Return effective frames, image/mask hashes, lattice, wavelength, pixel pitch,
  distance-seed provenance, gate and artifact paths/hash. Ignoring a distance hint
  at the caller's request is explicitly distinguished from testing that hint.
- Reserve a model request for finalization and prevent internal budget instructions
  from becoming the answer. Preserve execution-ledger checks on forced finalization.
- Pass the selected runtime environment to ordinary local MCP servers, including
  solver and isolated session locations. Add optional exact-byte SHA-256 to typed
  file inspection. General shell permission is unchanged.
- Stop treating slash-separated prose inside words as an absolute filesystem path;
  genuine absolute paths remain checked.

## Direct raw-image development checks

The local solver environment uses an unchanged MIDAS checkout at
`13b091e2353e16b3f478db1e6fbaca368af6dd5e`, calibrate v2 0.26.0. Exact pilot raw
inputs were copied from the study and hash checked. The first environment failed
qualification because of duplicate OpenMP runtimes; neither attempted solver ran.
Those refusal receipts remain. A clean isolated environment passed imports.

Both subsequent typed-tool runs used 63.314 keV, 150 um pixels, transform 2,
`exchange/data`, frames 1–20, mean reduction, CPU and the nominal 900 mm hint.
These are development checks, not autonomous trials or new R5 outcomes.

| Scan | Native wall time | Ring gate | Median / p95 residual (px) | Relative tilt error (degrees) |
| --- | --- | --- | --- | --- |
| 262, ceria | 77.60 s | PASS | 0.01338 / 0.04306 | 0.005760 |
| 284, LaB6 | 93.43 s | PASS | 0.02051 / 0.06960 | 0.021609 |

Independent HDF5 slicing and averaging exactly reproduce both oriented-frame and
reader-mask hashes. Raw hashes are unchanged; lattice, geometry and distortion
exports match at their written precision. Upstream export rounds wavelength to
six decimal places; no exact-decimal identity or absolute accuracy is claimed.

## Autonomous development review

The first ceria run returned in 501.15 s, with 14 completed model calls, four native
calibrations and an accepted final manifest whose byte hash matches. Its adopted
tilt is relatively consistent (0.005760 degrees from the frozen reference).
However, it duplicated a hint test, ran two checks with identical effective data
seeds and overstated these as deliberately perturbed initializations. Its final
prose therefore needs a recorded qualification; artifact success is not enough.
Two shell checks were denied, and a false path warning caused additional calls.
Worker provenance fields were extended during that development attempt, so it is
diagnostic evidence, not a fixed-source comparative trial. No semantic retrieval
call was observed; local retrieval health is not established by this run.

The separate LaB6 autonomous check completed from the fixed source/corpus snapshot
in 821.71 seconds, with five completed model calls and two native calibrations.
It saved an accepted final manifest, independently re-hashed the artifact through
typed file inspection, and used no shell calls or follow-up questions. The source
matches pushed repair `0b11e66`; its adopted geometry and frame hashes match the
independently checked direct run (relative tilt error 0.021609 degrees).

The artifact checks pass, but the final explanation still has qualifications:
there was an all-zero embedded dark, so “no dark frame existed” was too broad;
the applied transform is hash-verifiable, while its physical correctness remains
unverified; and the scope gate predicts ring reach rather than observing rings.
These issues remain in the review receipt. The seed-only check used a distinct
initialization and returned a nearby geometry with a worse ring residual.
All traces and unsuccessful attempts are retained. Further repaired-system
comparisons require a separate frozen revision and must not replace R5.

## Checks and locations

Regression suite: 168 passed, one skipped. It includes actual HDF5 selection and
a real local MCP environment round trip; model-loop tests use stub providers.
The real numerical and autonomous runs above are identified separately.

Local parent-repository evidence is in ignored `build/workflow-repair-20261109/`:
`input-receipt.json`, `native-r2-source.json`, `native-r2-validation.json`,
`autonomous-review.json`, `source-r2-receipt.json`, all native output directories,
and autonomous prompts/tool records/answers. Condensed native and autonomous
review receipts accompany this note. Identifying review notes are excluded from
the anonymous supplement. Production and frozen Copland code are not modified.
