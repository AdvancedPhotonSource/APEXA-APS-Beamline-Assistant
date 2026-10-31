export interface WorkflowOption {
  id: string
  name: string
  description: string
  group: string
  task: string
  inspectOnly?: boolean
}

// These are guided tasks, not installation/readiness claims. Scientific inputs
// come from the selected dataset and its metadata, never hardcoded beam energy.
export const WORKFLOW_OPTIONS: WorkflowOption[] = [
  { id: 'inspect', name: 'Inspect a dataset', group: 'Start here', inspectOnly: true,
    description: 'Read dimensions, metadata, units and missing inputs.',
    task: 'Inspect the selected dataset or ask me for its path. Read file contents, array shapes, frame counts, detector datasets, metadata and declared units. Identify ambiguities and missing inputs, then recommend suitable workflows with the supporting evidence.' },
  { id: 'structure', name: 'Open a crystal structure', group: 'Start here', inspectOnly: true,
    description: 'View a local CIF or find an MP / COD candidate.',
    task: 'Help me open a crystal structure in the native canvas. Ask for a local CIF or material/composition if none is selected. If retrieval is needed, check MP/COD network access and credentials first; offer a staged local CIF when unavailable. Preserve source identifiers and inspect the CIF contents.' },
  { id: 'calibrate', name: 'Calibrate a detector', group: 'Prepare and reduce',
    description: 'Confirm calibrant, measured geometry and quality checks.',
    task: 'Prepare a detector calibration using the selected data. Confirm the actual calibrant, measured energy or wavelength, pixel size, distance estimate, detector transforms and applicable dark/mask. Inspect embedded metadata and acquisition records, and propose calibration residual checks.' },
  { id: 'integrate', name: 'Integrate images or a series', group: 'Prepare and reduce',
    description: 'Use the configured MIDAS path; compare pyFAI availability for detector integration.',
    task: 'Prepare 1D integration or 2D caking for the selected image or series. Check accepted calibration, sample/dark/mask associations, exposure scaling, detector orientation, radial units, azimuth range, binning, uncertainty propagation and output formats. Use the registered MIDAS tools unless a pyFAI executor is explicitly configured in the selected analysis environment; check pyFAI availability before recommending it and do not fall back to guessed shell commands.' },
  { id: 'rings', name: 'Inspect diffraction rings', group: 'Prepare and reduce', inspectOnly: true,
    description: 'Compare observed profiles with CIF ring guides.',
    task: 'Open the selected detector sequence in the native canvas and help me inspect its radial profile and predicted rings. Ask for a CIF and measured geometry if missing. State the ideal untilted ring model limitations and distinguish a guide from a fitted detector calibration.' },
  { id: 'ff', name: 'FF-HEDM reconstruction', group: 'Reconstruct',
    description: 'Plan spot finding, indexing, fitting and grain QC.',
    task: 'Prepare an FF-HEDM reconstruction. Inspect the actual rotation scan, detector geometry, omega/frame mapping, phases, lattice parameters, dark/mask, thresholds and selected executor. Propose stages, CPU/GPU resources, output directory, expected grain/spot artifacts and scientific acceptance checks.' },
  { id: 'nf', name: 'NF-HEDM reconstruction', group: 'Reconstruct',
    description: 'Set distances, preprocessing, grid and confidence QC.',
    task: 'Prepare an NF-HEDM reconstruction. Verify near-field geometry, detector distances, omega alignment, preprocessing, voxel grid, orientation seeds, confidence thresholds, backend and memory budget. Explain the NF stages and expected microstructure outputs.' },
  { id: 'pf', name: 'PF-HEDM reconstruction', group: 'Reconstruct',
    description: 'Check translations, rotation mapping and PF outputs.',
    task: 'Prepare a PF-HEDM reconstruction. Check pencil-beam scan positions, translation/rotation associations, geometry, sample coverage and executor support. Specify expected outputs and validation; distinguish reconstruction from PF-ODF input inspection.' },
  { id: 'refine', name: 'Agentic powder refinement', group: 'Refine and interpret',
    description: 'Plan multiphase, batch, sequential and joint fits with visual evidence.',
    task: 'Prepare an Agentic-GSAS-II powder refinement for the selected pattern or series. Check wavelength, a matching instrument model for each observation, background, candidate structures and phase evidence. Choose independent, sequential, joint or bounded phase-search mode based on sample identity and conditions. Plan review of observed/calculated profiles, difference and weighted residuals, phase reflection markers, parameter decisions, uncertainty and qualification; do not use Rwp alone as a trust verdict.' },
  { id: 'gsas-tutorials', name: 'GSAS-II tutorial workbench', group: 'Refine and interpret', inspectOnly: true,
    description: 'Explore tutorial workflows, available operations and research capabilities.',
    task: 'Use gsas2_tutorials to show the official GSAS-II tutorial catalog in the canvas, and gsas2_capabilities to inspect available operations and the capabilities discussed in the paper. Explain the supported route and any required inputs or external dependencies for the requested analysis. Distinguish callable operations from validated tutorial reproduction.' },
  { id: 'refinement-handoff', name: 'Prepare an external-engine handoff', group: 'Refine and interpret',
    description: 'Check whether TOPAS, MAUD or FullProf is configured before preparing inputs.',
    task: 'Prepare a powder-pattern handoff for a named external refinement program such as TOPAS, MAUD or FullProf. First inspect the selected pattern axis, units, uncertainty semantics, instrument calibration, candidate structures and whether the requested program and license are actually available. Explain the engine-specific input files and which information APEXA can preserve. Do not claim the external engine is installed, generate guessed syntax, or convert data without stating any lost weighting, background or calibration information.' },
  { id: 'pdf', name: 'PDF / total scattering', group: 'Refine and interpret',
    description: 'Plan backgrounds, normalization, Q range and G(r).',
    task: 'Prepare a pair distribution function analysis. Verify composition, measured wavelength, sample/container/background measurements, absorption and scattering corrections, normalization, Qmin/Qmax, resolution, Fourier window and the installed PDF executor. Distinguish quicklook G(r) from quantitatively corrected PDF and structural fitting.' },
  { id: 'phases', name: 'Evaluate candidate phases', group: 'Refine and interpret',
    description: 'Compare peaks and chemistry with candidate structures.',
    task: 'Plan phase identification from the selected diffraction pattern. Check calibration, chemical constraints and peak evidence, verify an available phase-search executor or catalog, and report competing candidates and uncertainty. Distinguish database retrieval from phase identification.' },
  { id: 'dynamics', name: 'Phase-transformation dynamics', group: 'Refine and interpret',
    description: 'Map frames to time or position, then plan peak and phase trajectories.',
    task: 'Prepare a phase-transformation dynamics analysis for an ordered diffraction series. Establish the frame-to-time or frame-to-position mapping from acquisition metadata; never infer physical coordinates from frame numbers alone. Verify calibration, detector/background treatment, exposure and a representative pilot subset. For separate TIFF frames, use midas_integrate_series with the explicit TIFF pattern. Combine multiple background TIFFs only with combine_detector_backgrounds after verifying that their acquisition conditions match and the facility method calls for subtraction; never auto-match an ambiguously named Background folder. Use the registered series contour and peak-trajectory tools when their inputs are available. Treat tracked peaks as unassigned features; correlate with supported candidate structures, then propose sequential or independent GSAS-II refinements with a matching instrument model. For sequential refinement, pass verified axis_values and axis_units to refine_series_submit so the canvas can plot modeled phase fractions against physical time or position. State which conclusions are qualitative peak evolution and which would require validated phase fractions, uncertainty and texture/background checks.' },
  { id: 'stress', name: 'Grain stress and slip', group: 'Refine and interpret',
    description: 'Verify d0, stiffness, frames and strain conventions.',
    task: 'Prepare grain stress and slip analysis from the selected reconstruction. Confirm the reference lattice/d0, stiffness tensor and units, crystal/sample frames, strain convention, uncertainty and grain filters. Verify the stress executor and expected outputs.' },
  { id: 'texture', name: 'Texture and grain defects', group: 'Refine and interpret',
    description: 'Check orientation / q-space data and installed tools.',
    task: 'Assess whether the selected data supports grain ODF/texture or grain-defect analysis. Inspect actual orientations, symmetry, weights or q-space voxel/grain inputs, and installed capability modules. Explain the available numerical operations and missing prerequisites.' },
  { id: 'live', name: 'Plan live analysis', group: 'Operations',
    description: 'Define scan completion, throughput and recovery.',
    task: 'Prepare a live analysis plan for this acquisition. Verify data host, acquisition completion protocol, frame/scan identity, dark/calibration updates, expected rate, preview budget, queue limits and recovery behavior. State which watcher/streaming and restart guarantees are implemented.' },
  { id: 'readiness', name: 'Check analysis readiness', group: 'Operations', inspectOnly: true,
    description: 'Inspect engines, network mode and data-host access.',
    task: 'Perform a read-only analysis readiness review: selected model/network mode, configured data and compute hosts, registered tools, installed executor versions and staged dependencies. Distinguish configured, reachable, importable and reference-validated. Report unverified checks explicitly; do not install packages, connect to an unconfigured host or operate hardware.' },
]

export function workflowPrompt(option: WorkflowOption): string {
  return `${option.task}\n\nUse the selected canvas source when applicable. Treat filenames only as discovery hints; confirm scientific values from file contents, acquisition records or user input. ${option.inspectOnly
    ? 'Keep this step to inspection, visualization and recommendations.'
    : 'Check executor availability and missing inputs, then present a reviewable plan with parameters and expected outputs. Do not start processing or modify data in this setup step.'}`
}

export function suggestedWorkflows(artifactType?: string): WorkflowOption[] {
  const ids = artifactType === 'structure' ? ['structure', 'rings', 'refine', 'phases', 'pdf', 'readiness']
    : artifactType === 'sequence' || artifactType === 'diffraction' ? ['inspect', 'calibrate', 'rings', 'integrate', 'dynamics', 'ff', 'nf']
    : artifactType === 'plotly' ? ['inspect', 'dynamics', 'refine', 'pdf', 'phases', 'stress', 'readiness']
    : ['inspect', 'structure', 'calibrate', 'integrate', 'ff', 'nf']
  return ids.map(id => WORKFLOW_OPTIONS.find(option => option.id === id)!)
}
