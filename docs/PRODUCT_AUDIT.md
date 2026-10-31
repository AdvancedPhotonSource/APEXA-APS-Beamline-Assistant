# APEXA product and end-to-end audit

## Implementation update — 2026-10-15

Since this audit, APEXA has added a powder-refinement canvas and registered a
TomoPy-backed tomography server. The tomography tools have a real execution path
when `APEXA_TOMO_PYTHON` is configured, but registration does not establish
facility commissioning. The remaining technique gaps below still need their own
ingestion, reduction and analysis adapters.

Reviewed 2026-10-14 against the current working tree in
`~/Git/beamline-assistant-dev`, with the earlier MIDAS implementation review in
[WEBUI_REVIEW.md](WEBUI_REVIEW.md). This is a software and product audit, not APS
scientific commissioning. The live browser/server was not reachable from this
tool environment. User feedback supplies the observed first-launch issue.

## Recommendation

Keep React and the existing scientific canvas. The next investment should be in
dataset identity, explicit scientific setup, reliable jobs and results, and
visible engine readiness. Replacing the UI framework would not resolve those
gaps. Use chat to explain, suggest and orchestrate; expose repeatable scientific
controls as forms and direct actions as well.

The existing five rail entries were insufficient for the intended beamline
product. They mixed conversation history, prompt shortcuts, file browsing,
desktop-viewer launchers and hardware operations. The revised labels are
**Data, Analyze, Results, Chats**, with **Motors** separated below. Jobs and Setup are required additions
with real backend support, not empty navigation placeholders.

## Changes made in this audit

- Follow-up: combined layout/model/theme controls into one compact top strip;
  Attach/Expand/Send share the prompt's upper strip. Results now lists and
  filters session artifacts, reopens closed reports, and accepts explicit HTML
  report paths on the local or configured remote host. Data clicks, uploads and
  the agent's native open tool also open HTML in canvas. See the report format
  and verification boundaries in [WEBUI_REVIEW.md](WEBUI_REVIEW.md).
- First launch shows an equal-width **Chat + Canvas** split when the workspace
  can fit both. Chat and Canvas can each fill the workspace. Explicit layout
  choices are remembered; narrow windows use full-height views.
- Startup now restores artifacts recorded in the selected saved conversation.
  Previously it restored messages only, leaving the canvas empty after refresh.
  Shared sessions now also retain manual imports, drafts and viewer state; detector
  images reopen from source paths. See [SESSIONS.md](SESSIONS.md) for scope.
- Analyze now contains **15 searchable guided tasks**, grouped into inspection,
  preparation/reduction, reconstruction, interpretation and operations. These
  are inspection/setup requests, not claims that the engines are installed.
- Welcome suggestions follow the selected artifact type. They no longer start
  calibration with an arbitrary CeO2/61.332 keV default or offer motor commands
  as a primary first-use suggestion. The legacy image calibration quick-action
  now requests measured metadata and a plan rather than filename inference.
- The canvas upload area accepts CIFs and opens detector files as native frame
  sequences with dark/mask controls. It previously rejected CIF uploads and
  reduced detector uploads to a single-image view. Files are still validated by
  the scientific readers; an extension only chooses which reader to request.
- Tasks launched from the canvas or sidebar reveal their conversation, including
  when the user was inspecting a full-width canvas.

## Highest-priority findings

P0 = must resolve before trusting the affected production path; P1 = common
workflow or continuity gap; P2 = subsequent capability/usability work.

| ID | Priority | Evidence and impact | Required change |
|---|---|---|---|
| A01 | P0 | Legacy `/api/analysis/phases` and `/api/analysis/peaks` call tool names absent from the inspected registry, then wrap a returned error in `success: true`. `/api/calibrate` calls arbitrary model text “Calibration completed.” In-process probes reproduced all three. These are separate from the typed chat result cards. | Retire these routes or route them through the same typed executor/result validation as chat. Completion must reference verified current-run outputs. |
| A02 | P0 | The WS context builder still enumerates every image in the global image cache and tells the agent these are “this image,” even when explicit selected-canvas context is present. It also computes full-array statistics inside the async handler. [Source](../web_server.py#L867). | Use only the selected dataset/run identity for a request; store statistics at ingestion; isolate context per project/session. |
| A03 | P0 | Chat tasks are created independently and serialized around one shared client. Disconnect/reconnect does not identify or reconcile a durable compute job. SSH timeout does not prove a remote child stopped. | Persistent run IDs, bounded queue, worker ownership, cancellation, restart reconciliation, idempotent submission and output locking. |
| A04 | P0 for shared deployment | HTTP/WS have no user authentication, the WS handshake has no origin check, CORS is broad, and image/model state is shared. Several legacy upload routes join the supplied filename directly onto the upload directory. | Authenticated sessions, origin/access controls, permitted roots, isolated model/data state, safe upload names and limits. Preserve localhost-only use until shared deployment is qualified. |
| A05 | P0 for affected calibration route | `/api/auto_calibrate` writes a shared `calib_params_temp.txt`, saves a mask without using `mask_path` in the command, uses workstation-specific fallback paths, and can return an existing refined-parameter file. [Source](../web_server.py#L1541). | One calibration adapter, isolated run directories, explicit mask application, configured executors, fresh-output validation. |
| A06 | P1 | `/api/status` reports `mcp_client_connected: true` whenever a client object exists. A probe with zero connected sessions still reported connected. | Separate HTTP liveness, MCP health, model reachability, engine availability, data-host reachability and reference validation. |
| A07 | P1 | The workflow recommender still contains calibrant/path heuristics and a note claiming energy/pixel detection from filenames. [Source](../midas_comprehensive_server.py#L11313). Improved UI wording does not remove backend heuristics. | Versioned dataset manifest; explicit provenance and conflicts; measured-value confirmation before execution. |
| A08 | P1 | Browser Data listing is local and returns the first 500 entries with no pagination or truncation flag. A 501-file fixture silently returned 500. Remote inspection exists through the science tunnel, but remote browsing and remote result plots/reports are incomplete. | Host-aware browser with pagination/search, explicit totals/limits, remote HDF tree, typed result retrieval and no local-path fallback. |
| A09 | P1 | Chat history uses localStorage and silently ignores quota errors. Canvas imports, pins, comparison, auto-rendered integration plots and viewer edits are not a durable project record. Deleting a selected conversation does not fully reconcile canvas state. | Server project/run/artifact store, source-based rehydration, bounded browser cache and visible persistence failures. |
| A10 | P1 | Image cache is unbounded; uploads read entire bodies into memory; some readers return full previews. Sequence manifests are bounded to 32, but this does not bound all image/artifact memory. | Stream uploads; data-path-first access; byte-budgeted caches; eviction/expiry UI; decimation/tiles and request backpressure. |
| A11 | P1 | There are two detector experiences: file-browser/chat attachment single-image viewing and the native sequence viewer. Correction, interaction and persistence behavior differ. | One detector source/transform/view contract used by every entry point and quantitative executor. |
| A12 | P1 | Eight newer capability modules are unresolved in APEXA's default Python during this audit. Registered tool names are still visible independently of installed executors. | Host-specific capability records and prerequisite checks; select a staged capability interpreter where appropriate. |
| A13 | P1 | First launch hid the canvas; refresh did not restore recorded artifacts. | **Fixed here:** split default, remembered choices, recorded-artifact restoration and regression coverage. |
| A14 | P2 | Ideal ring overlays omit detector tilt/distortion/intensities; the sequence image is an SVG with no dedicated pan/zoom/ROI/pixel-inspector controls. | Expose the present model scope; add calibrated reciprocal-space inspection and interactive image tools. |

Probe results: [webui-audit-probes.json](webui-audit-probes.json). No real
reconstruction, refinement, model request or hardware operation was used by
these probes. Their stub failures deliberately exercise HTTP result contracts.

## Recommended left navigation

Keep the rail compact. Put detailed choices inside the selected section and show
technique-specific forms only when relevant. Existing IDs can remain internal.

| Section | What belongs here | Current status |
|---|---|---|
| **Data** | Projects, recent datasets, local/SSH hosts, file/HDF tree, import manifest, acquisition metadata, detector/axis selection, associated dark/flat/mask/calibration, integrity report | Local browser and recommendations exist; manifest/remote browser needed |
| **Analyze** | Technique search; setup, prerequisites, parameter review, dry run/plan, execute, validated presets, advanced settings, examples | 15 guided tasks now exposed; typed setup forms and readiness gating needed |
| **Jobs** | Active/queued/history, host and resources, stage progress, logs, cancellation, checkpoint resume, retry, reconnect/reconcile, output validation | No common durable jobs interface; refinement series has a narrower submit/status path |
| **Results** | Current project artifacts, gallery, filters by scan/run/type/status, compare, provenance, reports, exports, archived runs | Searchable session list, report filter/path opener, canvas/gallery/history and directory viewer exist; project-wide persistence/index needed |
| **Chats** | New/resume/search conversation, rename, export, linked project/run, interrupted request status | Basic local history exists; server persistence and project linkage needed |
| **Beamline** | Acquisition readbacks, approved devices, motor limits/interlocks, read-only mode, deliberate motion controls, live acquisition links | Current Motors panel is mostly chat actions; caget availability must be checked |
| **Setup** | Beamline profile, hosts/data roots, engines/versions/devices, model/network policy, offline assets, storage budgets, diagnostics, logs, theme/layout, export configuration | Mostly environment files and status labels; no unified setup page |

In the current version, **Results** opens analysis files, reports and session artifacts; **Motors** retains
its narrower name. Do not rename Motors to a full Beamline console until the
acquisition/readiness controls are actually present.

### First launch and default suggestions

1. Show Chat and a clearly labelled Canvas together on a laptop/desktop. Leave
   the data sidebar closed to preserve width. Remember an explicit layout choice.
2. Lead with **Inspect a dataset**, **Open a structure**, **Calibrate**,
   **Integrate**, **FF-HEDM**, **NF-HEDM**. Analyze exposes the full catalog.
3. Suggestions should change with an explicitly selected dataset/artifact and
   verified prerequisites. Selecting a plot alone does not establish a technique.
4. Show a selected project/sample/scan/detector breadcrumb and local/remote host.
   “This image” must resolve to one accepted frame and correction state.
5. Start an unfamiliar analysis with a reviewable plan. Ask only for missing or
   conflicting values. Preview/inspection should work directly without an LLM.
6. Preserve full-width Chat, prompt expansion, split resizing and full Canvas.
   A visible result count lets a user writing in full Chat open results explicitly.
7. Mark example prompts/data as examples. Never silently import a demonstration
   beam energy, detector distance, calibrant, chemistry or frame convention into
   the user's experiment.
8. Show **Unavailable**, **Needs setup**, **Ready to run**, **Running**,
   **Needs review**, **Validated**, **Partial**, **Failed** and **Interrupted**
   as separate states. A green HTTP connection indicator cannot imply scientific
   readiness or validation.

## Options the scientific workspace should expose

The following is the target inventory. “Needed” is a recommendation, not a
claim that an option or adapter exists today.

| Area | Required options | Current / next |
|---|---|---|
| Project identity | Experiment/proposal, beamline, sample, scan, detector, operator, timestamps; saved workspace | Conversation identity exists; project identity needed |
| Data sources | Local path, mounted storage, configured SSH host, detector stream, file upload; visible locality | Local paths/upload and remote sequence API exist; unified source picker needed |
| Dataset inspection | File signature, array shape/dtype, frames vs files, HDF dataset tree, compression/plugins, external links, metadata/units, missing/truncated frames | Content inspection exists; navigable metadata and integrity UI needed |
| Scan associations | Rotation/translation/time axes, multi-detector alignment, frame ordering, completion signal, sample/dark/flat/calibrant grouping | Mostly heuristic/current snapshots; acquisition-ID mapping needed |
| Detector viewing | Frame jump, play/speed/loop, omega/time label, fit/1:1, pan/zoom, pixel coordinate/intensity, histogram, line/area ROI, ROI statistics | Playback/contrast/profile exist; spatial interaction and axis labels needed |
| Display | Linear/log/gamma, percentile auto contrast, manual limits, locked contrast, colormap, saturation/masked-pixel display, export figure | Distributed across the two current viewers; unify controls |
| Corrections | Dark selection/average, exposure scaling, flat/gain, hot/dead/saturated pixels, mask import/draw/edit/undo, raw/corrected comparison, correction provenance | Single dark subtraction, mask and raw/corrected toggle exist in sequence view; other controls needed |
| Geometry | Energy or wavelength with units/source, pixel pitch, distance, center, tilts, orientation/transforms, distortion, calibration file import/version | Native ring fields and calibration tools exist; one reusable geometry editor needed |
| Ring inspection | Select hkl/phase, d/Q/2θ, predicted/observed profiles, azimuth sectors, residual map, calibrant exclusions | Ideal CIF rings and radial profile exist; fitted geometry/residual interaction needed |
| Structures | Local CIF, MP/COD search, identifiers/version/citation, occupancy and symmetry, unit cell/supercell, site labels, bonds with an explicit convention, phase comparison/export | Symmetry-expanded CIF view exists; database browser, supercells and richer structure controls needed |
| Integration | 1D/2D, radial units/range/bins, azimuth sectors, masks/darks/flat, polarization/solid angle, uncertainties, batch output, numerical backend | Tools exist; typed controls and shared correction contract needed |
| Workflow review | Inputs and metadata sources, assumptions/conflicts, stages, engine/version/device, resource estimate, output location, overwrite policy, validation criteria | Guided plans exist; enforceable typed plan/form needed |
| Job control | Submit once, stage progress, logs, queue limits, cancel, checkpoint, resume, retry failed stage, reconnect, output validation | Partial tool status exists; common durable system needed |
| Visualization | Artifact tabs, close/reopen/pin, gallery/compare, linked axes/cursors, inspect metadata, source/run navigation, download | Tabs/gallery/compare/provenance exist; linked selection and persistent artifacts needed |
| Reports | Linked live inspection, immutable run snapshot, scientific verdict, assumptions, correction/config hashes, QC, errors, versions, citations; HTML/PDF/JSON/figure export | Linked inspection and static HTML/structured reports exist; unified run reporting/export needed |
| Live acquisition | Latest-frame preview, scan progress, throughput/queue age/dropped frames, detector state, correction version, completion contract, stale-data warning | Watcher/streaming primitives exist; dedicated live console and recovery needed |
| Performance | Preview resolution/tiles, bandwidth budget, bounded caches, bounded plots/history, virtualized lists, worker limits, latency telemetry | Preview size and async/threadpool work exist; resource budgets and telemetry needed |
| Offline/remote | Host test, data-root mapping, engine probe, tunnel state/reconnect, staged assets manifest, local CIF/catalog, model policy | Configuration/SSH primitives exist; setup UI and per-technique commissioning needed |

Mask conventions, detector transformations and uncertainty propagation must be
part of the numerical pipeline. A display correction does not automatically
change the inputs of a later reconstruction or refinement.

## Technique-specific setup and acceptance

| Technique | Setup options that must be visible | Required result / review |
|---|---|---|
| FF-HEDM | Rotation/frame map, detector geometry, phase/lattice/symmetry, thresholds and spot fitting, indexing parameters, grain fitting, completeness filters, CPU/GPU, resume/output policy | Observed/predicted spots, completeness, residuals, orientations, positions, strain/size, uncertainty; compare with an accepted reference |
| NF-HEDM | Multiple detector distances, frame/omega alignment, preprocessing/mask, sample/grid bounds, voxel size, orientation seeds/search, confidence threshold, device/memory | Orientation/microstructure slices and 3D views, confidence/unindexed fraction, morphology, convergence, alignment with FF where applicable |
| PF-HEDM | Scan translations, rotation mapping, beam size/profile, geometry, reconstruction region, indexing/reconstruction stages | Coverage/sinogram checks, grain maps and stage validation. PF-ODF input loading must not be labelled an inversion |
| Powder / Rietveld | Pattern/uncertainties, instrument profile, wavelength, background, phase candidates, fitting range/exclusions, constraints, parameter stages, acceptance/rollback | Observed/calculated/difference traces, R factors and residual structure, phase fractions/lattice parameters with uncertainty, warnings and trust verdict |
| Refinement series | Ordered scan/temperature/load/time axis, shared vs per-pattern parameters, continuation policy, failed-pattern handling | Parameter trends, uncertainty, outliers, per-pattern quality, partial-failure report. Current adapter does not implement warm-start continuation |
| PDF / total scattering | Composition, sample/container/background, measured wavelength, corrections/normalization, Qmin/Qmax, window, r grid and resolution | I(Q), S(Q), F(Q), G(r), correction provenance, Q-cutoff sensitivity and uncertainty. Current adapter's G(r) output does not establish a complete corrected PDF or structural-refinement workflow |
| Grain stress / slip | Reference d0, stiffness and units, crystal/sample/load frames, strain convention, grain masks, loading axis | Stress tensors, resolved shear/Schmid quantities where supported, quality filters, propagated uncertainty and reference checks |
| Texture / defects | Symmetry, orientation convention, grain weights, angular/grid resolution; q-space voxel data, grain/reference identity for defect analysis | Numerical executor outputs and quality limits; verify staged capability modules before presenting Run |
| DFXM / coherent 2D / pink beam | Technique-specific data and instrument models, plus actual adapter readiness | Current APEXA wrappers have simulation/input-only/deferred boundaries. Put these in an explicitly labelled advanced/experimental area |
| Tomography | TomoPy stack pre-flight and reconstruction adapter with a configured native environment | Registered through `tomo_server.py`; beamline-specific data, geometry and reference validation still require commissioning |
| Other beamline methods | Explicit method identification and installed ingestion/reduction/analysis adapter | CDI/ptychography, SAXS, XAS/XAFS and other methods still need verified execution paths. MIDAS `XAF` here means cross-axis faceted HEDM, not X-ray absorption analysis |

There should be a beamline profile for each commissioned instrument at APS.
Profiles must supply measured units, detector orientation and conventions,
permitted devices/data roots, validated engine versions and reference cases.
Do not assume one geometry or energy applies across the requested beamlines.

## Dataset and execution contracts

Use NeXus conventions (`NXdata`, `NXdetector`, `NXbeam`, `NXsample`,
`NXtransformations`/`depends_on`) where available, plus explicit adapters for
APS Data Exchange, detector headers, MIDAS archives and acquisition sidecars.
HDF5 alone neither identifies the technique nor chooses the workflow.

A versioned dataset manifest should preserve:

- Exact source identity and locality, scan/detector axes and completion state.
- Raw and normalized metadata, units, source location, confidence and conflicts.
- Exact sample/dark/flat/mask/calibration associations and file identities.
- Readability, dimensions, link/compression requirements and integrity findings.
- Candidate workflows, matching evidence, missing prerequisites and measured
  executor readiness; record ambiguity instead of inventing a selection.

Each accepted run should freeze that manifest and its parameters, assign a
persistent run ID, claim an output directory, execute through the same typed
adapter regardless of chat/form entry point, and verify output contents. Store
artifacts and reports against that run ID. Resume and retry need explicit rules
for stale or partial outputs and remote processes.

Reports need two modes: an inspection view linked to the current accepted frame,
and an immutable snapshot of a completed/partial run. Changing a viewer slider
must not silently rewrite a previously accepted reconstruction report.

## Implementation order and acceptance

1. **Trust and context:** eliminate false success in legacy endpoints, fix
   calibration path/mask handling, isolate the selected data context, and expose
   truthful readiness. Regression cases must include missing engines, refused
   execution, stale outputs and conflicting metadata.
2. **Dataset setup:** implement the manifest/HDF/host browser and one correction
   and geometry editor. A deliberately misleading filename must not override
   metadata; a multi-detector file must require selection when ambiguous.
3. **Jobs:** build persistent run records and workers; add Jobs only with submit,
   inspect, cancel, reconnect and supported resume semantics. Verify restart,
   network loss, duplicate submission and output-directory contention.
4. **Technique forms and QC:** commission calibration/integration, FF, NF, PF,
   refinement and PDF independently using site-approved reference datasets and
   tolerances. Importability is an earlier check, not scientific acceptance.
5. **Projects and results:** persistent source-based artifact recovery, reports,
   exports, synchronized comparison and searchable run history. Verify browser
   reload, server restart, cache eviction and storage-quota failure.
6. **Live/offline qualification:** measure acquisition-to-preview and
   scan-completion-to-validated-result separately (p50/p95/p99), memory, queue
   age, missing frames and recovery. Stage every dependency before disconnecting.
   Keep deterministic per-frame processing outside the LLM loop.

For demonstrations, use a disclosed, frozen reference dataset. Show data and
metadata inspection, a plan with verified prerequisites, a short real analysis,
linked plots/report, and a deliberately failing input. Label replay/simulation
clearly. Do not demonstrate restart recovery as implemented until it is.

## Verification for this audit

- Python suite: **348 passed, 1 skipped**.
- Frontend suite after onboarding changes: **19 passed**; new cases cover split
  first launch, narrow-screen choice, stored-artifact restoration and grounded
  setup suggestions.
- TypeScript/production build and scoped ESLint passed. Lazy Plotly remains
  about 4.61 MB uncompressed; its chunk warning is not a browser benchmark.
- Controlled in-process probes reproduce false-success responses, misleading
  connected status with zero sessions, and silent directory truncation.
- APEXA's default interpreter cannot resolve `midas_pdf`, `midas_defect`,
  `midas_grain_odf`, `midas_2d`, `midas_dfxm`, `midas_xaf`, `midas_pf_odf`,
  `midas_pink`. A separately configured capability interpreter must be checked
  independently. No dependencies were installed.
- Browser rendering, real model/tool calling, MP/COD retrieval, target-host SSH,
  GPU throughput and scientific reference reconstructions were **not** verified
  by this audit. Passing contract tests does not establish those capabilities.
