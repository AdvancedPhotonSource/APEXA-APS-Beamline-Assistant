import type { VizArtifact } from '@/api/types'

/** Only source identity and accepted settings enter chat; no images/plot arrays. */
export function canvasContext(artifact?: VizArtifact): Record<string, unknown> | undefined {
  if (!artifact) return undefined
  const data = artifact.data as { path?: string; location?: string; analysisKey?: string;
    appliedContext?: { source?: { path?: string }; sequence_frame?: number; corrections?: unknown; settings?: unknown } }
  return { type: artifact.type, title: artifact.title,
    path: data?.appliedContext?.source?.path ?? data?.path,
    location: data?.location ?? (artifact.type === 'sequence' ? 'configured data host' : 'APEXA host'),
    inputs: artifact.provenance?.inputs, analysis_key: data?.analysisKey,
    frame_index: data?.appliedContext?.sequence_frame,
    corrections: data?.appliedContext?.corrections, settings: data?.appliedContext?.settings }
}
