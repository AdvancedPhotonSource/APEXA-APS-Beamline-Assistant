import type { VizArtifact } from '../api/types'
import { resultIdentity } from './resultIdentity.ts'

/** Plot snapshots share a tab only when source, operation, settings and data match. */
export function vizResultId(endpoint: string, file: string, plotly: unknown, extras: Record<string, string> = {}): string {
  return `file-viz-${resultIdentity({ tool: `viz:${endpoint}`, status: 'completed',
    data: { file, extras: Object.fromEntries(Object.entries(extras).sort(([a], [b]) => a.localeCompare(b))), plotly } })}`
}

/** Local and remote paths are distinct resources even when the strings match. */
export function fileViewId(type: 'structure' | 'sequence', path: string, location = 'local'): string {
  return `canvas-${type}-${location === 'remote' ? 'remote' : 'local'}-${path}`
}

/** Reconcile old saved tabs and all entry points without losing viewer settings. */
export function canonicalFileArtifact(artifact: VizArtifact): VizArtifact {
  if ((artifact.type !== 'structure' && artifact.type !== 'sequence') || !artifact.data || typeof artifact.data !== 'object') return artifact
  const data = artifact.data as { path?: unknown; location?: string }
  return typeof data.path === 'string'
    ? { ...artifact, id: fileViewId(artifact.type, data.path, data.location) }
    : artifact
}
