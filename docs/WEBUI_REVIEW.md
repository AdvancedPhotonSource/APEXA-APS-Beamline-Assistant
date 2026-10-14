# Web UI and beamline workflow review — 2026-10-14

For the subsequent first-launch audit, navigation/options inventory and unresolved
endpoint findings, see [PRODUCT_AUDIT.md](PRODUCT_AUDIT.md).

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
- Sidebar tools and the structured report renderer load on demand. The entry
  bundle is 499 kB (150 kB gzip); shared initial chunks add about 29 kB raw.
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

The source inventory covers 89 registered tools (13 core, 57 MIDAS, 6 GSAS-II,
13 motor). Registration is not executor readiness. The table describes the
implemented adapters and their boundaries, not scientific commissioning.

| Target | Actual adapter / executor | Findings and user-facing boundary |
|---|---|---|
| Detector inspection, calibration, integration | TIFF/HDF5/Fabio; MIDAS calibration/integration tools and series | Native sequence controls, masks/darks, radial profiles and ideal ring guides. Full geometry calibration and quantitative acceptance remain separate. |
| FF-HEDM | `run_ff_hedm_full_workflow` → `midas-pipeline run --scan-mode ff`; `run_ff_pipeline` → module CLI | Existing fresh-grain checks; typed SSH dispatch for the full workflow. Report file now reaches canvas. Reconcile remote jobs before retry. |
| NF-HEDM | `run_nf_hedm_reconstruction` → `midas-nf-pipeline`; NF preprocess/refinement tools | Count only changed output maps. Old voxels plus a fresh unrelated CSV no longer imply success. Cache identity includes solver/precision options. SSH qualification is separate from FF. |
| PF-HEDM | `run_pf_hedm_workflow` → `midas-pipeline --scan-mode pf` | Exit zero alone is no longer success; require fresh nonempty grain output. PF geometry and scan associations need reference validation. |
| Powder/Rietveld refinement | MIDAS caked refinement; `agentic_gsas2.refine_one` for ordinary patterns | Preserve trust verdicts. Series distinguishes partial/error/complete, writes progress atomically, and records startup failure. Warm-start is not implemented by this adapter; lattice-trend limitation is disclosed. |
| PDF / total scattering | `midas_pdf.i_of_q_to_Gr` | Real-data adapter and G(r) canvas plot. Measured wavelength required. Largest peak is labelled strongest, not nearest neighbour. Background/normalization/Q-range and uncertainty need scientific checks. |
| Grain stress, d0, slip | `_stress_runner.py` / `midas_stress` | Actual numerical operations; require correct frames, units, material stiffness, and accepted reference results. |
| Grain defects and grain ODF | `_capability_runner.py` → package CLIs | Real input adapters, with q-space voxel/grain inputs. Installation and accepted datasets are required; module names alone are insufficient. |
| Coherent/ultrafast 2D | `midas_2d.examples` | Generated-input demonstrations; current adapter has no real detector ingestion. |
| XAF | `midas_xaf` synthetic sample/design | Cross-axis faceted HEDM design; this is not an XAS/XAFS absorption reduction tool. |
| DFXM | `midas_dfxm` forward image | Synthetic deformation field; no real-data inversion in this adapter. |
| PF grain ODF | `load_pf_grain` | Input loading only. Successfully loading data no longer claims inversion or a completed real-data workflow. |
| Pink beam | Spectrum construction | Deferred ROI inversion. Returns warning, not a completed analysis claim. |
| Tomography, DCT and other technique handbooks | Vendored knowledge/capsules | A handbook or a MIDAS source package does not establish an APEXA execution adapter. No turnkey claim from this review. |
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
