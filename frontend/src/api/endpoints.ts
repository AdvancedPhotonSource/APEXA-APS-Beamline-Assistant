import type { ServerStatus, BrowseResult, ImageLoadResult, CsvData, ViewerAdjustResult } from './types'

const BASE = ''

export async function fetchStatus(): Promise<ServerStatus> {
  const res = await fetch(`${BASE}/api/status`)
  if (!res.ok) throw new Error(`Status request failed: ${res.status}`)
  return res.json()
}

export async function fetchModels(): Promise<{ models: Record<string, Record<string, string>>; selected: string }> {
  const res = await fetch(`${BASE}/api/models`)
  if (!res.ok) throw new Error(`Models request failed: ${res.status}`)
  return res.json()
}

export async function uploadFile(file: File): Promise<{ file_id: string; filename: string; path: string }> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/api/upload`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
  // Backend returns `saved_path`; normalize to `path` for callers.
  const j = await res.json()
  return { file_id: j.file_id, filename: j.filename, path: j.saved_path ?? j.path }
}

export async function sendChatHttp(message: string, threadId?: string): Promise<{ response: string }> {
  const form = new FormData()
  form.append('message', message)
  // Thread id → backend keeps per-thread conversation memory (HTTP fallback).
  if (threadId) form.append('thread_id', threadId)
  const res = await fetch(`${BASE}/api/chat`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Chat failed: ${res.status}`)
  return res.json()
}

// Phase 2 endpoints

export async function browseDirectory(path: string = '.'): Promise<BrowseResult> {
  const res = await fetch(`${BASE}/api/browse?path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error(`Browse failed: ${res.status}`)
  return res.json()
}

export async function loadImageByPath(path: string): Promise<ImageLoadResult> {
  const form = new FormData()
  form.append('path', path)
  const res = await fetch(`${BASE}/api/viewer/load_path`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Image load failed: ${res.status}`)
  return res.json()
}

export async function adjustImage(
  fileId: string,
  opts: { vmin?: number; vmax?: number; gamma?: number; colormap?: string }
): Promise<ViewerAdjustResult> {
  const form = new FormData()
  form.append('file_id', fileId)
  if (opts.vmin != null) form.append('vmin', String(opts.vmin))
  if (opts.vmax != null) form.append('vmax', String(opts.vmax))
  if (opts.gamma != null) form.append('gamma', String(opts.gamma))
  if (opts.colormap != null) form.append('colormap', opts.colormap)
  const res = await fetch(`${BASE}/api/viewer/adjust`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Adjust failed: ${res.status}`)
  return res.json()
}

export async function fetchCsvData(path: string): Promise<CsvData> {
  const res = await fetch(`${BASE}/api/data/csv?path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error(`CSV load failed: ${res.status}`)
  return res.json()
}

export async function fetchRadialProfile(fileId: string, centerX?: number, centerY?: number) {
  const params = new URLSearchParams({ file_id: fileId })
  if (centerX != null) params.set('center_x', String(centerX))
  if (centerY != null) params.set('center_y', String(centerY))
  const form = new FormData()
  form.append('file_id', fileId)
  if (centerX != null) form.append('center_x', String(centerX))
  if (centerY != null) form.append('center_y', String(centerY))
  const res = await fetch(`${BASE}/api/viewer/radial_profile`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Radial profile failed: ${res.status}`)
  return res.json()
}

export async function fetchPixelValue(fileId: string, x: number, y: number) {
  const res = await fetch(`${BASE}/api/viewer/pixel_value?file_id=${fileId}&x=${x}&y=${y}`)
  if (!res.ok) throw new Error(`Pixel value failed: ${res.status}`)
  return res.json()
}

export async function fetchColormaps(): Promise<{ colormaps: string[] }> {
  const res = await fetch(`${BASE}/api/viewer/colormaps`)
  if (!res.ok) throw new Error(`Colormaps failed: ${res.status}`)
  return res.json()
}

// ── Feedback ────────────────────────────────────────────────────────────────
// Records stay on this machine (JSONL under ~/.apexa/feedback). Nothing is
// posted outside ANL — beamline hosts run at network tier `internal` or `data`
// and may reach nothing external at all.

export type FeedbackKind = 'up' | 'down' | 'report'

export type FeedbackCategory =
  | 'wrong_result' | 'failed_to_run' | 'misleading' | 'slow'
  | 'feature_request' | 'other'

export interface FeedbackContext {
  model?: string | null
  servers?: string[]
  versions?: Record<string, string>
  tools?: { name: string; status: string; elapsed_s?: number }[]
}

/** What APEXA would attach to a report — fetched so the dialog can show the
 *  user the real payload before they send it, not a description of it. */
export async function fetchFeedbackContext(): Promise<FeedbackContext> {
  const res = await fetch(`${BASE}/api/feedback/context`)
  if (!res.ok) throw new Error(`Feedback context failed: ${res.status}`)
  return res.json()
}

export async function sendFeedback(payload: {
  kind: FeedbackKind
  comment?: string
  category?: FeedbackCategory | ''
  session_id?: string
  message_id?: string
  model?: string
  context?: FeedbackContext | Record<string, unknown>
  redact?: boolean
}): Promise<{ ok: boolean; id?: string; error?: string }> {
  const res = await fetch(`${BASE}/api/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return res.json()
}
