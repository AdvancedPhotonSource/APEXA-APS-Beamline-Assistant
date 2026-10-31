# Diffraction software survey and APEXA UI fit

**Reviewed:** 2026-10-15<br>
**Purpose:** identify capabilities that belong in APEXA's user interface and distinguish
integrated engines from software that needs a separate installation or license.

Public DNS was unavailable from the review environment. The links below point to
the projects' first-party documentation or repositories, but could not be fetched
live during this pass. Capability mapping also uses APEXA's registered tools,
dependency declarations, refinement adapter, and existing UI.

## Tool roles

| Tool | Strongest use | APEXA status | UI recommendation |
|---|---|---|---|
| [pyFAI](https://pyfai.readthedocs.io/en/latest/) | Calibrating detector geometry and integrating area-detector images into 1D or 2D azimuthal patterns, with detector corrections, masks, uncertainty options and CPU/GPU methods. | `pyfai` is in APEXA's optional `extra` dependency group, but no registered APEXA execution tool calls it. MIDAS is the current registered integration path. | Keep a future pyFAI adapter optional. Show it as available only after checking the selected interpreter and detector support. Do not imply that installing the Python package alone wires it into a beamline workflow. |
| [DIOPTAS](https://dioptas.readthedocs.io/en/latest/) | Interactive high-pressure diffraction reduction: detector calibration, masks, background, integration and inspection across image sequences. | No DIOPTAS adapter is registered. It is a useful standalone comparison/reference for the detector workflow. | Preserve the same concepts in APEXA's image workspace; support file handoff when operators already use DIOPTAS. |
| [MIDAS](https://github.com/AdvancedPhotonSource/MIDAS) | APS HEDM processing, including detector calibration, spot/grain analysis, and far-field, near-field and pencil-beam workflows. | The primary registered APS analysis path. APEXA exposes MIDAS calibration, integration, reconstruction, reports and readiness tools. | Keep MIDAS as the native HEDM workflow. Show prerequisites, data/parameter provenance, progress, partial output and validation status. |
| [GSAS-II](https://github.com/AdvancedPhotonSource/GSAS-II) | General powder and single-crystal diffraction analysis, Rietveld refinement, multiple histograms and scripted workflows. | APEXA has both MIDAS powder-refinement support and a separate Agentic-GSAS-II job service. The latter supports independent, sequential, joint and bounded search jobs when its configured interpreter is available. | Use GSAS-II as the native powder engine. Show fit overlays, residuals, phase markers, parameters, uncertainties and scientific qualification together. |
| [MAUD](https://www.ing.unitn.it/~maud/) | Rietveld and whole-pattern analysis with texture, stress, microstructure, line-profile and quantitative phase workflows. | A limited optional MAUD path exists inside MIDAS `run_gsas_refinement(engine="maud")` for MIDAS caked `.zarr.zip`/`.MIDAS.zip` data. It requires the MAUD installation, MILK wrapper and a JDK. Plain `.xy`/`.xye` inputs take the GSAS-II path instead. | Expose the existing MAUD mode with an explicit readiness check and its input limits. Use file handoff for other MAUD workflows; do not describe MAUD as a general-purpose APEXA executor. |
| [TOPAS](https://topas.webspace.durham.ac.uk/) | Advanced whole-pattern/Rietveld refinement with a scriptable model language and broad profile/structure modelling. TOPAS is commercial software and requires an appropriate license. | No TOPAS executor or license check is configured. | Offer handoff preparation only; never imply that APEXA can run TOPAS without an installed, licensed executable. |
| [FullProf](https://www.ill.eu/sites/fullprof/) | X-ray and neutron Rietveld/profile refinement, including magnetic structures and specialized crystallographic workflows. | No FullProf executor is configured. | Keep as a possible external handoff. It does not need to be another always-visible native-engine button. |
| [Mantid](https://docs.mantidproject.org/) | Instrument-specific reduction and analysis, especially neutron and muon time-of-flight workflows; also supports selected X-ray workflows. | No Mantid adapter is registered in APEXA. | Consider only for experiments whose facility workflow uses Mantid. Reuse its instrument-specific reduction rather than replacing it with a generic powder form. |
| [xrayutilities](https://xrayutilities.sourceforge.io/) | Python coordinate transforms, reciprocal-space mapping and analysis of area-detector/high-resolution X-ray scattering data. | It is already a base APEXA dependency and parses CIFs for the structure viewer; there is no dedicated reciprocal-space-map workflow in this UI. | Add a reciprocal-space-map view when the supported beamline datasets and coordinate metadata are defined. It is not a substitute for a powder Rietveld engine. |

## What belongs in the APEXA interface now

APEXA already has the relevant processing engines for its core APS use cases:
MIDAS for HEDM and detector reduction, and Agentic-GSAS-II/GSAS-II for powder
refinement. The practical UI gap was that refinement outputs and job state were
mostly exposed as generic JSON or disconnected files.

The current UI work adds:

- A Powder diffraction entry area for calibration, integration, ring inspection,
  agentic refinement and external-engine handoff planning.
- A refinement canvas view for job states, per-observation Rwp, fit plots,
  normalized phase-fraction trends, decision traces, diagnostics, qualification
  and image-backed peak-window evidence.
- A phase-transformation workflow entry, plus lineout-series contours and
  continuity-linked peak trajectories. CSV or explicit axis values carry verified
  time/position into those plots; sequential GSAS-II refinements can also plot
  modeled phase fractions against their supplied scan axis.
- A TIFF background combiner that requires a reviewable output path, checks the
  input frame shapes, writes float32 means/medians and records provenance. It does
  not determine whether the selected frames match the sample or should be subtracted.
- Direct rendering of the refinement PNGs already produced by Agentic-GSAS-II,
  without copying large images into chat or session records.
- Explicit copy that Rwp is a profile-agreement statistic, not proof of phase
  identity, a correct model or trustworthy quantitative phase analysis.

`TOPAS`, `FullProf`, `DIOPTAS` and `Mantid` have no registered APEXA executor in
the current setup. MAUD has only the limited optional MIDAS path described above.
The handoff shortcut prompts the agent to inspect program availability and the
pattern, instrument/calibration context and uncertainty semantics before
recommending a route. It is guidance, not a code-level executable/license probe
or a file-conversion service, and it does not launch external programs.

## Next integration decisions

1. **pyFAI:** add an adapter only if APEXA needs a second integration backend for
   detector formats or methods that the selected MIDAS workflow does not cover.
   The adapter should use an explicit geometry/calibration file, dark/flat/mask,
   radial units, uncertainty model and integration method, and return those
   settings with the 1D/2D result. Its optional package status should be visible.
2. **External refiners:** expose the existing limited MAUD path with an explicit
   readiness check and input-format boundary. If users need in-app TOPAS or
   FullProf, add each behind an environment/readiness check and a documented
   input/output contract. For TOPAS, include license availability. Do not send
   beamline data to a cloud service by default.
3. **Refinement visualization:** when the engine supplies numeric fit arrays,
   render them as interactive Plotly layers so users can zoom into peaks, toggle
   phase reflection markers and inspect weighted residuals. Until then, show only
   fit/evidence image paths returned by the engine; do not infer a `hist.png`
   filename from a project path when the engine returned no plot.
4. **Dynamics qualification:** correlate tracked features with candidate CIF
   reflections and preserve per-frame calibration/background/exposure provenance.
   Peak continuity IDs remain unassigned and are not phase labels or validated
   phase fractions.

## First-party references

- pyFAI docs and project: <https://pyfai.readthedocs.io/en/latest/> and <https://github.com/silx-kit/pyFAI>
- DIOPTAS docs and project: <https://dioptas.readthedocs.io/en/latest/> and <https://github.com/Dioptas/Dioptas>
- MIDAS: <https://github.com/AdvancedPhotonSource/MIDAS>
- GSAS-II: <https://github.com/AdvancedPhotonSource/GSAS-II>
- MAUD: <https://www.ing.unitn.it/~maud/>
- TOPAS reference material: <https://topas.webspace.durham.ac.uk/>
- FullProf: <https://www.ill.eu/sites/fullprof/>
- Mantid: <https://docs.mantidproject.org/>
- xrayutilities: <https://xrayutilities.sourceforge.io/>

## Current powder integration and manuscript evidence

The [powder workflow guide](powder-refinement.md) synchronizes the APEXA bridge
with the 15 October 2026 manuscript audit (`741add8`). It includes the architecture,
scientific status contract, image-advice provenance, exact isolated-trial policy
and a [frozen benchmark snapshot](data/powder-benchmark-20261015.json).
The 1,676 numerical records do not benchmark the complete APEXA/LLM/VLM loop.
The manuscript supplement retains the complete surveyed-source inventory;
software availability does not establish comparative refinement accuracy.
