import type { ToolResult, VizArtifact } from '@/api/types'
import { reportFileArtifact } from './reportArtifacts.ts'
import { fileViewId } from './canvasIdentity.ts'
import { resultIdentity } from './resultIdentity.ts'
export { resultIdentity } from './resultIdentity.ts'

export function parseToolResults(text: string): ToolResult[] {
  const results: ToolResult[] = []

  // Pattern 1: JSON code blocks
  const jsonBlockRegex = /```(?:json)?\s*\n([\s\S]*?)\n```/g
  let match
  while ((match = jsonBlockRegex.exec(text)) !== null) {
    try {
      const parsed = JSON.parse(match[1])
      if (parsed && typeof parsed === 'object') {
        results.push({
          tool: parsed.tool ?? inferTool(parsed),
          status: inferStatus(parsed),
          data: parsed,
        })
      }
    } catch {
      // Not valid JSON
    }
  }

  // Pattern 2: Inline JSON objects (not in code blocks)
  const inlineJsonRegex = /(?:^|\n)\s*(\{[\s\S]*?\})\s*(?:\n|$)/g
  while ((match = inlineJsonRegex.exec(text)) !== null) {
    // Skip if this was already captured in a code block
    const jsonStr = match[1]
    if (text.indexOf('```') !== -1 && text.indexOf(jsonStr) > text.indexOf('```')) continue
    try {
      const parsed = JSON.parse(jsonStr)
      if (parsed && typeof parsed === 'object' && (parsed.tool || parsed.status || parsed.success !== undefined)) {
        results.push({
          tool: parsed.tool ?? inferTool(parsed),
          status: inferStatus(parsed),
          data: parsed,
        })
      }
    } catch {
      // Not valid JSON
    }
  }

  // Pattern 3: Detect calibration results from text
  if (results.length === 0 && hasCalibrationPattern(text)) {
    const data = extractCalibrationFromText(text)
    if (data) {
      // A number mentioned in prose is not an executed/validated calibration.
      results.push({ tool: 'midas_auto_calibrate', status: 'unknown', data })
    }
  }

  return results
}

function inferTool(data: Record<string, unknown>): string {
  if (data.calibrated_parameters || data.Lsd || data.BC) return 'midas_auto_calibrate'
  if (data.listing || data.entries) return 'list_directory'
  if (data.workflow) return 'hedm_workflow'
  if (data.result && (data.d_spacing || data.wavelength || data.energy)) return 'xray_calculate'
  if (data.integration_result || data.lineout) return 'midas_integrate_2d_to_1d'
  return 'unknown'
}

export function inferStatus(data: Record<string, unknown>): ToolResult['status'] {
  const outcomes = [data._apexa_outcome, data.outcome].filter(value => value && typeof value === 'object') as Record<string, unknown>[]
  const nested = data.outcome && data.result && typeof data.result === 'object' ? inferStatus(data.result as Record<string, unknown>) : null
  const states = [...outcomes.map(outcome => outcome.execution), data.execution_state, data.execution_status, data.status, nested]
    .filter((state): state is string => typeof state === 'string').map(state => state.toLowerCase())
  const verdicts = [...outcomes.map(outcome => outcome.scientific_verdict), data.scientific_verdict, data.scientific_status]
    .filter((verdict): verdict is string => typeof verdict === 'string').map(verdict => verdict.toLowerCase())
  const errors = Array.isArray(data.errors) ? data.errors.length > 0 : !!data.errors
  const failedExit = ['returncode', 'return_code', 'exit_code'].some(key => data[key] != null && data[key] !== 0)
  if (data.isError === true || data.error || errors || failedExit || data.timed_out === true
      || states.some(state => ['error', 'failed', 'failure', 'timeout', 'timed_out'].includes(state))) return 'error'
  if (data.nothing_was_run === true || states.some(state => ['denied', 'blocked', 'permission_denied', 'resource_unavailable'].includes(state))) return 'denied'
  if (states.some(state => ['cancelled', 'canceled'].includes(state))) return 'cancelled'
  // HTTP success=false also represents pending or provisional work. The typed
  // outcome, when present, keeps those separate from failed execution.
  if (states.some(state => ['running', 'in_progress', 'started'].includes(state))) return 'running'
  if (states.includes('submitted')) return 'submitted'
  if (states.some(state => ['queued', 'pending'].includes(state)) || data.completed === false) return 'pending'
  if (verdicts.some(verdict => ['rejected', 'failed', 'fail', 'invalid'].includes(verdict)) || data.gate_passed === false || data.scientifically_valid === false || states.includes('rejected')) return 'rejected'
  if (verdicts.some(verdict => ['provisional', 'warning', 'needs_review'].includes(verdict)) || states.some(state => ['provisional', 'needs_review', 'needs_selection'].includes(state))) return 'provisional'
  if (data.success === false) return 'error'
  if (states.includes('warning')) return 'warning'
  if (states.includes('partial')) return 'partial'
  if (states.includes('unknown')) return 'unknown'
  if (states.some(state => ['completed', 'complete', 'done', 'ok'].includes(state)) || data.success === true) return 'completed'
  return 'success'
}

function hasCalibrationPattern(text: string): boolean {
  const patterns = ['Beam Center', 'beam center', 'BC:', 'Lsd:', 'detector distance']
  return patterns.some((p) => text.includes(p))
}

function extractCalibrationFromText(text: string): Record<string, unknown> | null {
  const params: Record<string, unknown> = {}

  const bcMatch = text.match(/(?:BC|Beam Center)[:\s]*\(?(\d+\.?\d*)\s*,?\s*(\d+\.?\d*)\)?/i)
  if (bcMatch) {
    params.BC_y = parseFloat(bcMatch[1])
    params.BC_z = parseFloat(bcMatch[2])
  }

  const lsdMatch = text.match(/(?:Lsd|detector distance|sample.detector)[:\s]*(\d+\.?\d*)/i)
  if (lsdMatch) params.Lsd = parseFloat(lsdMatch[1])

  const wavelengthMatch = text.match(/[Ww]avelength[:\s]*(\d+\.?\d*)/i)
  if (wavelengthMatch) params.wavelength = parseFloat(wavelengthMatch[1])

  if (Object.keys(params).length === 0) return null
  return { calibrated_parameters: params }
}

export function parseDirectToolResult(tool: string, resultJson: string): ToolResult | null {
  try {
    const parsed = JSON.parse(resultJson)
    if (parsed && typeof parsed === 'object') {
      return { tool, status: inferStatus(parsed), data: parsed }
    }
  } catch {
    if (resultJson && resultJson.trim().length > 0) {
      const failed = /^(?:error\b|failed\b|traceback\b)/i.test(resultJson.trim())
      const denied = /^denied\b/i.test(resultJson.trim()) || resultJson.slice(0, 400).includes('⛔')
      return { tool, status: failed ? 'error' : denied ? 'denied' : 'completed',
        data: failed ? { error: resultJson } : { text_result: resultJson } }
    }
  }
  return null
}

export function extractArtifacts(results: ToolResult[], messageId: string): VizArtifact[] {
  const artifacts: VizArtifact[] = []

  for (const result of results) {
    const key = resultIdentity(result)
    const toolName = result.tool.split(':').pop()
    const powderTools = new Set(['refine_pattern', 'plan_refinement', 'assess_refinement', 'refine_series_submit',
      'powder_refine_submit', 'refinement_status', 'powder_evidence', 'powder_advise',
      'powder_trial', 'powder_live_ingest', 'powder_session_create', 'powder_session_status',
      'powder_session_evidence', 'powder_session_control', 'powder_adopt', 'powder_improve',
      'powder_strategy_sweep_submit', 'powder_compare', 'gsas2_tutorials', 'gsas2_capabilities',
      'gsas2_workflow_submit', 'gsas2_workflow_status', 'gsas2_workflow_cancel'])
    if (powderTools.has(toolName ?? '')) {
      const data = result.data
      const hasRefinementEvidence = ['job_id', 'job_ids', 'jobs', 'gpx_path', 'Rwp', 'panels',
        'proposals', 'trials', 'accepted', 'diagnostic_jobs', 'session_dir', 'workflow_dir',
        'tutorials', 'methods', 'research_capabilities', 'coverage', 'comparison', 'stages'].some(field => field in data)
      if (hasRefinementEvidence) {
        const identity = typeof data.session_dir === 'string' ? data.session_dir
          : typeof data.workflow_dir === 'string' ? data.workflow_dir
          : typeof data.job_id === 'string' ? data.job_id
          : typeof data.gpx_path === 'string' ? data.gpx_path
          : typeof data.observation_id === 'string' ? data.observation_id : key
        const title = toolName === 'refinement_status' || toolName === 'powder_refine_submit'
          || toolName === 'refine_series_submit'
          ? `Powder refinement · ${typeof data.job_id === 'string' ? data.job_id : 'job'}`
          : toolName === 'powder_evidence' ? `Refinement evidence · ${typeof data.observation_id === 'string' ? data.observation_id : 'pattern'}`
          : toolName === 'powder_trial' ? 'Refinement trial'
          : toolName === 'powder_advise' ? 'Refinement diagnosis'
          : toolName?.startsWith('gsas2_') ? 'GSAS-II workbench'
          : typeof data.session_dir === 'string' ? 'Refinement session'
          : 'Agentic refinement'
        const inputPaths = [data.data_file, data.instrument_file, data.gpx_path, data.output_dir, data.artifact,
          ...(Array.isArray(data.cif_files) ? data.cif_files : []),
          ...((Array.isArray(data.jobs) ? data.jobs : []).flatMap(job => {
            if (!job || typeof job !== 'object') return []
            const j = job as Record<string, unknown>
            const r = j.result && typeof j.result === 'object' ? j.result as Record<string, unknown> : {}
            const payload = j.payload && typeof j.payload === 'object' ? j.payload as Record<string, unknown> : {}
            const observations = Array.isArray(payload.observations) ? payload.observations : []
            return [r.gpx_path, ...observations.flatMap(observation => {
              if (!observation || typeof observation !== 'object') return []
              const o = observation as Record<string, unknown>
              return [o.data_path, o.instrument_path, ...(Array.isArray(o.cif_paths) ? o.cif_paths : [])]
            })]
          }))].filter((path): path is string => typeof path === 'string')
        artifacts.push({ id: `powder-refinement-${identity}`, type: 'refinement', title,
          data: { ...data, tool: result.tool }, sourceMessageId: messageId,
          provenance: { inputs: inputPaths, tool: result.tool, timestamp: Date.now() } })
      }
    }
    if (toolName === 'powder_report' && (result.status === 'success' || result.status === 'completed')
        && typeof result.data.path === 'string') {
      artifacts.push(reportFileArtifact(result.data.path, messageId, 'local', 'Powder refinement report'))
    }
    if (toolName === 'generate_ff_reconstruction_report' && typeof result.data.report_html === 'string'
        && (result.status === 'success' || result.status === 'completed')) {
      artifacts.push({ ...reportFileArtifact(result.data.report_html, messageId,
          result.data.location === 'remote' ? 'remote' : 'local', 'FF reconstruction report'),
        provenance: { inputs: [String(result.data.run_dir ?? result.data.report_html)], tool: result.tool, timestamp: Date.now() } })
    }
    // Native canvas requests come from a tool that validated the input contents.
    const canvas = result.data.canvas as { schema_version?: number; type?: string; title?: string; data?: { path?: string; [key: string]: unknown } } | undefined
    if (toolName === 'open_science_view' && (result.status === 'success' || result.status === 'completed')
        && canvas?.schema_version === 1 && (canvas.type === 'structure' || canvas.type === 'sequence' || canvas.type === 'html-report')
        && typeof canvas.data?.path === 'string') {
      artifacts.push({ id: canvas.type === 'html-report'
          ? reportFileArtifact(canvas.data.path, messageId, canvas.data.location === 'remote' ? 'remote' : 'local').id
          : fileViewId(canvas.type, canvas.data.path, canvas.data.location === 'remote' ? 'remote' : 'local'), type: canvas.type,
        title: canvas.title ?? canvas.data.path, data: canvas.data, sourceMessageId: messageId,
        provenance: { inputs: [canvas.data.path], tool: result.tool, timestamp: Date.now() } })
    }
    // MP and COD have different response schemas; both supply a saved CIF.
    // The viewer parses that file, including symmetry, before displaying sites.
    if (result.status !== 'error' && result.status !== 'failed') {
      const entries = toolName === 'fetch_cif_from_mp' ? (result.data.files ?? result.data.results)
        : toolName === 'propose_structures' ? result.data.candidates : undefined
      if (Array.isArray(entries)) {
        for (const entry of entries) {
          const path = entry.cif_file ?? entry.cif
          if (typeof path !== 'string' || !path) continue
          const source = toolName === 'fetch_cif_from_mp' ? `Materials Project ${entry.material_id ?? ''}` : `COD ${entry.cod_id ?? ''}`
          artifacts.push({ id: fileViewId('structure', path), type: 'structure', title: source.trim(),
            data: { path, source }, sourceMessageId: messageId,
            provenance: { inputs: [path], tool: result.tool, timestamp: Date.now() } })
        }
      }
    }
    if (toolName === 'midas_auto_calibrate' && result.status !== 'error' && result.status !== 'failed') {
      const calParams = (result.data.calibrated_parameters ?? result.data) as Record<string, unknown>
      if (calParams && Object.keys(calParams).length > 0) {
        artifacts.push({
          id: `cal-${key}`,
          type: 'table',
          title: 'Calibration Parameters',
          // Preserve the outcome when the parameters become a standalone view.
          data: { ...calParams, status: result.status },
          sourceMessageId: messageId,
        })
      }
    }

    if (result.tool === 'xray_calculate') {
      artifacts.push({
        id: `xray-${key}`,
        type: 'table',
        title: 'X-ray Calculation',
        data: result.data,
        sourceMessageId: messageId,
      })
    }

    if (result.tool === 'list_directory') {
      artifacts.push({
        id: `files-${key}`,
        type: 'table',
        title: 'Directory Listing',
        data: result.data,
        sourceMessageId: messageId,
      })
    }

    // A run report. The tool writes a self-contained .html AND returns the
    // spec it rendered from, so the Canvas shows the same report live -- with
    // the actions as buttons rather than copy-to-clipboard, since in here
    // there is something to call back to.
    if ((result.status === 'success' || result.status === 'completed')
        && result.data.report_spec && typeof result.data.report_spec === 'object' && !Array.isArray(result.data.report_spec)) {
      const spec = result.data.report_spec as Record<string, unknown>
      artifacts.push({
        id: typeof result.data.report === 'string'
          ? `report-spec-${result.data.location === 'remote' ? 'remote' : 'local'}-${result.data.report}`
          : `report-${key}`,
        type: 'report',
        title: (spec.title as string) ?? 'Run report',
        data: spec,
        sourceMessageId: messageId,
        provenance: { inputs: [String(result.data.report ?? spec.source_path ?? '')].filter(Boolean), tool: result.tool },
      })
    } else if (toolName === 'generate_run_report' && (result.status === 'success' || result.status === 'completed')
        && typeof result.data.report === 'string') {
      artifacts.push(reportFileArtifact(result.data.report, messageId, result.data.location === 'remote' ? 'remote' : 'local'))
    }

    // Viz API results (plotly data from /api/viz/* endpoints or run_midas_viewer)
    if (result.data.plotly && typeof result.data.plotly === 'object') {
      artifacts.push({
        id: `viz-${key}`,
        type: 'plotly',
        title: (result.data.title as string) ?? 'MIDAS Visualization',
        data: result.data.plotly,
        sourceMessageId: messageId,
      })
      // Also add any tables from the viz result
      const tables = result.data.tables as Array<{ title: string; data: unknown }> | undefined
      if (tables) {
        for (const [index, tbl] of tables.entries()) {
          artifacts.push({
            id: `viztbl-${key}-${index}`,
            type: 'table',
            title: tbl.title,
            data: tbl.data,
            sourceMessageId: messageId,
          })
        }
      }
    }
  }

  return artifacts
}
