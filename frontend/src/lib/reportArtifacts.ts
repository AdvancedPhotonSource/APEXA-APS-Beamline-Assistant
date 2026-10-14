import type { VizArtifact } from '../api/types'

export type ReportLocation = 'local' | 'remote'

export function isHtmlReport(path: string): boolean {
  return /\.html?$/i.test(path)
}

export function reportApiUrl(path: string, location: ReportLocation = 'local'): string {
  return `${location === 'remote' ? '/remote' : '/local'}/api/science/report?path=${encodeURIComponent(path)}`
}

/** A request to parse a document, not a claim that its contents are valid. */
export function reportFileArtifact(path: string, sourceMessageId = '', location: ReportLocation = 'local', title?: string): VizArtifact {
  return {
    id: `report-file-${location}-${path}`, type: 'html-report',
    title: title || path.split('/').pop() || 'Report',
    data: { path, location }, sourceMessageId,
    provenance: { inputs: [path] },
  }
}
