# Web UI and beamline workflow review — 2026-10-14

## Canvas follow-up — 2026-10-30

The [Canvas workspace guide](CANVAS_WORKSPACE.md) describes the completed tab
behavior: automatic opening of new visuals, stable file/result identities,
repeat-delivery suppression, preserved comparison selection, History favorites,
keyboard navigation and session-safe delayed loads. Classic light and the shared
APEXA SVG mark are included. See the dated [follow-up report](audits/2026-10-30/CANVAS_FOLLOWUP.md)
for current verification; older findings below remain historical.

## 2026-10-15 implementation follow-up

The active source configuration declares five MCP servers and 101 tool
definitions (13 core, 60 MIDAS, 13 GSAS-II, 2 tomography, 13 motor). A powder
refinement canvas now renders job state, fit metrics, decision history and
engine-returned plot files. Tomography has a registered TomoPy adapter, but its
native dependencies and facility workflow still need host-specific qualification.
See [analysis-tool-inventory.json](analysis-tool-inventory.json) and
[TOMO_SETUP.md](TOMO_SETUP.md).

For the subsequent first-launch audit, navigation/options inventory and unresolved
endpoint findings, see [PRODUCT_AUDIT.md](PRODUCT_AUDIT.md).

## Phase-series analysis follow-up — 2026-10-15

- The MIDAS server adds `track_diffraction_peak_series` alongside
  `plot_lineout_series_contour`. Both return Plotly payloads that the result
  parser adds to the native Canvas; peak tracking also has a compact result card.
- The contour accepts explicit scan-axis values or a CSV mapping so frame order
  can be shown against verified time or position. Interpolation leaves measured
  range gaps blank rather than extending endpoint intensities.
- Sequential GSAS-II submission accepts ordered scan-axis values and units. The
  refinement Canvas plots modeled phase fractions against that axis when present,
  and falls back to job index when absent.
- `combine_detector_backgrounds` can mean/median combine selected same-shape TIFF
  frames and records source paths, image shape, output dtype and a SHA-256 digest.
  The result card states that matching acquisition conditions and correction
  suitability remain a scientific review step.
- The [phase-dynamics guide](phase-transformation-dynamics.md) documents the TIFF
  pilot, dark/background review, contour, peak tracking, sequential refinement and
  scientific limits. The inspected Pilatus data remains unprocessed because its
  `/gdata` path is not available from this workstation; no runtime or scientific
  validation is claimed here.

The inventory covers 101 registered tools (13 core, 60 MIDAS, 13 GSAS-II,
2 tomography, 13 motor). Registration is not executor readiness.

For this follow-up, the TypeScript/production build passed. No test suite was run.
Vite reports a 562 kB entry chunk and the existing lazy Plotly chunk at 4.61 MB;
the entry chunk-size warning remains visible.

## UI reliability follow-up — 2026-10-16

- The refinement Canvas and result card recognize `refine_series_submit`, so
  sequential-series submissions reach the same job, residual, phase-trend and
  fit-evidence view as direct powder job submissions. `assess_refinement` also
  opens that view when it returns refinement evidence.
- If an older backend returns 404 for the session collection/import routes, the
  UI now keeps chat usable with browser-local history, labels that mode, and
  offers a retry after restarting from the current checkout. Session requests
  also handle non-JSON proxy error pages without replacing the useful message
  with a JSON parsing exception. See [SESSIONS.md](SESSIONS.md).
- TypeScript and the production frontend build passed on 2026-10-16. Vite still
  reports the 562 kB entry bundle and 4.61 MB lazy Plotly bundle. No test suite
  was run for these changes; live browser rendering and the stale-server recovery
  path were not exercised here.

## Compact workspace and report opening follow-up

- The desktop header is one compact strip: brand, Chat/Split/Canvas, model,
  theme and connection. The separate workspace toolbar is removed. Theme is a
  single labelled cycle button; the full model name remains available on hover.
  Narrow windows may wrap controls to preserve access.
- Attach, Expand and Send are 28 px controls in the prompt's upper strip
  (36 px for coarse pointers). The separate bottom action row and repeated model
  label are removed. The two-line writing area uses one focus border and tighter padding. The
  expanding editor, draft retention and Enter/Shift+Enter behavior remain. Sending waits for uploads;
  upload failures now appear inline.
- Left navigation follows **Data → Analyze → Results → Chats**, with **Motors**
  separated below. Results defaults to searchable session artifacts, with a
  Reports-only filter and reopening of closed tabs. The existing analysis-folder
  scanner remains under **Scan folder**. Jobs and Setup still need backend work.
- Open an existing **HTML report (.html/.htm)** from Data, the canvas upload
  area, chat attachments, or Results' explicit path/host selector. The agent can
  call `open_science_view(kind='report', path=...)`; auto also recognizes HTML
  and validates the contents. Explicit tool open requests reveal hidden canvas.
- Generated APEXA structured reports and FF HTML reports use stable artifact
  identities. Failed/running tool results cannot publish a completed report.
  Recorded chat results restore when the conversation is reopened. Manual
  imports and canvas state now persist with shared sessions; see [SESSIONS.md](SESSIONS.md).
  Source datasets remain separate files.
- HTML previews offer Refresh/Retry and **Save preview**, which downloads the
  static displayed document. PNG/JPEG/GIF/WebP figures referenced beneath the
  report directory are checked and embedded. Reads and final HTML are bounded
  to 25 MB; at most 100 distinct relative figures are loaded. Parent paths,
  symlinks escaping the directory, remote figures and active content are not
  loaded. Missing/unsupported assets produce visible limitations.
- The iframe remains script-free and isolated. Interactive standalone Plotly,
  inline SVG, external styles, and report JavaScript are not supported by this
  static preview. Use native plot/structure views for interactive science.
  PDF, DOCX, Markdown and arbitrary JSON files are not report viewers here.
  Upload HTML with embedded figures; use an existing server path when figures
  are stored alongside the document.
- Explicit remote reports use `/remote/api/science/report` through the configured
  loopback SSH science gateway. An unconfigured/unreachable service reports an
  error; it never opens a same-named local file. This forwarding contract is
  tested with a mock transport, not a commissioned beamline SSH connection.

Verification: **367 Python tests passed, 1 skipped; 28 frontend tests passed**,
including component markup checks. TypeScript/production build and scoped ESLint
passed. Live browser rendering and target-host SSH remain unverified.

## Implemented changes

- First launch now shows Chat and Canvas at equal widths with the data sidebar
  closed. Visible Chat / Split / Canvas controls replace the fixed 400 px chat
  column; the explicit layout choice is remembered. Split supports pointer and
  keyboard resizing. Choosing Chat keeps it full width until the user opens
  results explicitly. Narrow windows use full-height views with a result count.
- The prompt starts at four lines, grows with input, and has an Expand prompt
  editor that fills the conversation pane. Messages and prompt have no fixed
  768 px width cap. Drafts and attachments survive workspace changes; hidden
  canvas effects pause using React Activity while viewer state is retained.
  This layout follow-up is checked by TypeScript/production build and scoped
  ESLint; browser rendering has not been verified in this environment.
- Chat has a validated `open_science_view` tool for local CIFs and detector
  sequences. Both WebSocket delivery and HTTP fallback preserve typed artifacts.
  Opening one reveals the Active canvas, including when a comparison was open.
- PDF results contain a native G(r) plot. FF reconstruction HTML reports appear
  in a sandboxed static report pane with embedded figures; scripts and external
  resources are disabled. These are historical reports, not live reconstructions.
- WebSocket setup/cleanup owns one socket and one reconnect timer. Stale socket
  callbacks cannot close a newer connection. HTTPS uses WSS, short-lived
  connections retain backoff, and close codes/reasons/durations are logged.
- Late tool events stay with their originating chat. Returning to a conversation
  restores its recorded artifacts. A disconnected in-flight request is marked
  interrupted; it is not automatically resubmitted, because compute may continue.
- Chat receives the selected canvas source, accepted frame identity/corrections,
  and analysis key, so "this image" refers to the inspected frame. Preview bytes
  and plot arrays are excluded from that context.
- Blocking image/plot routes use FastAPI worker threads. Direct calibration and
  integration subprocess waits are offloaded. FF dispatch, NF/PF runs, capability
  and stress runners, and GSAS refinement waits yield their MCP event loops.
  This does not turn them into durable background jobs or qualify simultaneous
  writes to the same output directory.
- Preview size is selectable (512–4096 px), with a 1024 px default. Nearest-pixel
  sampling is disclosed; statistics, contrast percentiles, and radial profiles
  still use full-resolution corrected data. Raw files are untouched.
- File browsing and contrast changes discard superseded responses. Stable chat
  messages and plots are memoized. Status polling has a timeout and cannot overlap.
- Sidebar tools and the structured report renderer load on demand. The latest
  entry bundle is 562 kB (168 kB gzip); the lazy Plotly chunk is 4.61 MB raw.
- Startup uses staged frontend dependencies, fails on build errors, and no longer
  runs `npm install`. The HTML entry page is revalidated; FastAPI uses lifespan.

## Measured preview improvement

Same workstation, seeded Poisson(100) float32 2048×2048 frame, automatic contrast,
viridis, full-data radial profile, one warmup and five timed calls per version.
This is a function benchmark, not browser latency or beamline throughput.

| Metric | Original | Updated |
|---|---:|---:|
| Median frame payload generation | 475.89 ms | 170.51 ms |
| Encoded preview size, including base64 | 11,129,002 bytes | 3,911,142 bytes |

Preview generation is about 2.8× faster and payload size is 65% smaller in this
case. The updated preview is 1024×1024. Selecting a larger preview trades bandwidth
and encoding time for detail. The large lazy-loaded Plotly bundle remains a
first-plot cost; no browser performance claim is made.

## Technique coverage traced in APEXA

The current source inventory covers 101 registered tools (13 core, 60 MIDAS,
13 GSAS-II, 2 tomography, 13 motor). Registration is not executor readiness. The table describes the
implemented adapters and their boundaries, not scientific commissioning.

| Target | Actual adapter / executor | Findings and user-facing boundary |
|---|---|---|
| Detector inspection, calibration, integration | TIFF/HDF5/Fabio; MIDAS calibration/integration tools and series | Native sequence controls, masks/darks, radial profiles and ideal ring guides. Full geometry calibration and quantitative acceptance remain separate. |
| FF-HEDM | `run_ff_hedm_full_workflow` → `midas-pipeline run --scan-mode ff`; `run_ff_pipeline` → module CLI | Existing fresh-grain checks; typed SSH dispatch for the full workflow. Report file now reaches canvas. Reconcile remote jobs before retry. |
| NF-HEDM | `run_nf_hedm_reconstruction` → `midas-nf-pipeline`; NF preprocess/refinement tools | Count only changed output maps. Old voxels plus a fresh unrelated CSV no longer imply success. Cache identity includes solver/precision options. SSH qualification is separate from FF. |
| PF-HEDM | `run_pf_hedm_workflow` → `midas-pipeline --scan-mode pf` | Exit zero alone is no longer success; require fresh nonempty grain output. PF geometry and scan associations need reference validation. |
| Powder/Rietveld refinement | MIDAS caked refinement; Agentic-GSAS-II for ordinary patterns and durable batches | Refinement canvas shows jobs, Rwp, phase trends, decision trace and returned plot/evidence images. Fit images are rendered only from paths returned by the engine. Preserve scientific qualification and uncertainty limits. |
| PDF / total scattering | `midas_pdf.i_of_q_to_Gr` | Real-data adapter and G(r) canvas plot. Measured wavelength required. Largest peak is labelled strongest, not nearest neighbour. Background/normalization/Q-range and uncertainty need scientific checks. |
| Grain stress, d0, slip | `_stress_runner.py` / `midas_stress` | Actual numerical operations; require correct frames, units, material stiffness, and accepted reference results. |
| Grain defects and grain ODF | `_capability_runner.py` → package CLIs | Real input adapters, with q-space voxel/grain inputs. Installation and accepted datasets are required; module names alone are insufficient. |
| Coherent/ultrafast 2D | `midas_2d.examples` | Generated-input demonstrations; current adapter has no real detector ingestion. |
| XAF | `midas_xaf` synthetic sample/design | Cross-axis faceted HEDM design; this is not an XAS/XAFS absorption reduction tool. |
| DFXM | `midas_dfxm` forward image | Synthetic deformation field; no real-data inversion in this adapter. |
| PF grain ODF | `load_pf_grain` | Input loading only. Successfully loading data no longer claims inversion or a completed real-data workflow. |
| Pink beam | Spectrum construction | Deferred ROI inversion. Returns warning, not a completed analysis claim. |
| Tomography | `tomo` MCP server → isolated TomoPy runner | Pre-flight and reconstruction adapters are registered; `APEXA_TOMO_PYTHON` must provide TomoPy and dxchange. Facility-specific validation and commissioning remain separate. |
| DCT and other technique handbooks | Vendored knowledge/capsules | A handbook or a MIDAS source package does not establish an APEXA execution adapter. No turnkey claim from this review. |
| Motor control | EPICS `caget` / `caput` | Tools are registered, but supplied startup log reports missing `caget`. No hardware operations were exercised. |

## Environment findings

On this workstation, APEXA's `.venv` cannot locate the new capability modules
(`midas_pdf`, `midas_defect`, `midas_grain_odf`, `midas_2d`, `midas_xaf`,
`midas_dfxm`, `midas_pf_odf`, `midas_pink`). Their adapters cannot execute here
until those dependencies are staged. `APEXA_CAPABILITY_PYTHON` can select a
separately staged interpreter; default remains APEXA's own Python with a clean
environment. No packages were downloaded during this review.

The selected `midas_env` interpreter locates FF/NF pipeline, grain fitting,
NF preprocessing and stress modules. This checks module resolution only.
The configured GSAS-II subprocess successfully imported `AgentConfig` and
`refine_one` through the actual adapter path. Neither check is a reconstruction
or refinement acceptance run. `MIDAS_PATH` currently points to an absent
`~/opt/MIDAS_canonical`; resolution falls back to `~/Git/MIDAS`. Set an explicit
validated path before deployment and configure `APEXA_MIDAS_BIN` for native CLIs.

## Verification and remaining gates

Latest checks, including shared sessions: **393 Python tests passed, 1 skipped; 38 frontend tests passed**.
TypeScript/production build, scoped ESLint, Python compilation, shell syntax,
and `git diff --check` passed. Test fixtures stub model/executor behavior where
needed; these counts do not represent successful scientific reconstructions.

In-process HTTP/WebSocket checks cover chat → tool result → typed CIF artifact →
structure API, HTTP fallback, malformed messages, static reports, and status
responsiveness while an image read is blocked. Frontend tests cover socket
ownership/backoff, thread routing, artifact selection, lifecycle states, and
MP/COD schemas. Numerical tests preserve full-data statistics/profile at reduced
preview size and reject stale NF outputs.

Chrome could not start in the sandbox and localhost:8001 was unreachable from
this tool session. Therefore visual layout, drag/zoom, browser memory, user input
latency, and real network reconnection still require an actual browser pass.
No APS reference reconstruction, native GPU benchmark, SSH-host commissioning,
live model session, or live MP/COD request was performed.

Remaining production work: durable run IDs/queues and restart reconciliation;
run cancellation and output-directory locking; explicit scan-completion and
metadata associations; per-technique reference tolerances; authenticated
multi-user/session isolation; staged engine readiness; bounded raw-image and
artifact history storage. Browser socket recovery does not recover a lost job
subscription or prove that an external process stopped.

Restart `./start_web_viewer.sh` and hard-refresh to load the new tool registry
and frontend. Existing server processes retain their previously loaded code.
