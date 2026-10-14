import type { ToolResult, VizArtifact } from '@/api/types'
import { reportFileArtifact } from './reportArtifacts.ts'

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
      results.push({ tool: 'midas_auto_calibrate', status: 'completed', data })
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

function inferStatus(data: Record<string, unknown>): ToolResult['status'] {
  if (data.status === 'error' || data.status === 'failed' || data.success === false || data.error) return 'error'
  if (data.status === 'running' || data.status === 'submitted' || data.status === 'partial' || data.status === 'unknown') return data.status
  if (data.status === 'completed' || data.status === 'complete' || data.success === true) return 'completed'
  if (data.status === 'warning') return 'warning'
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
      return { tool, status: 'success', data: { text_result: resultJson } }
    }
  }
  return null
}

// Content identity is stable across live events, CLI import and page reload.
// Identical tool output reuses a view; a changed result creates a new one.
function resultIdentity(result: ToolResult): string {
  const text = JSON.stringify([result.tool, result.data])
  let a = 2166136261, b = 5381
  for (let i = 0; i < text.length; i++) {
    a = Math.imul(a ^ text.charCodeAt(i), 16777619)
    b = Math.imul(b, 33) ^ text.charCodeAt(i)
  }
  return `${(a >>> 0).toString(36)}-${(b >>> 0).toString(36)}`
}

export function extractArtifacts(results: ToolResult[], messageId: string): VizArtifact[] {
  const artifacts: VizArtifact[] = []

  for (const result of results) {
    const key = resultIdentity(result)
    const toolName = result.tool.split(':').pop()
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
          : `canvas-${canvas.type}-${canvas.data.path}`, type: canvas.type,
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
          artifacts.push({ id: `structure-${source}-${path}`, type: 'structure', title: source.trim(),
            data: { path, source }, sourceMessageId: messageId,
            provenance: { inputs: [path], tool: result.tool, timestamp: Date.now() } })
        }
      }
    }
    if (result.tool === 'midas_auto_calibrate' && result.status !== 'error') {
      const calParams = (result.data.calibrated_parameters ?? result.data) as Record<string, unknown>
      if (calParams && Object.keys(calParams).length > 0) {
        artifacts.push({
          id: `cal-${key}`,
          type: 'table',
          title: 'Calibration Parameters',
          data: calParams,
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
