import type { VizArtifact } from '@/api/types'
import { PlotlyChart } from './PlotlyChart'
import { ResponseView } from './ResponseView'
import { ReportView, type ReportData } from './ReportView'
import { DiffractionViewer } from '@/components/viewer/DiffractionViewer'
import { FileListCard } from '@/components/cards/FileListCard'
import { cardFor } from '@/components/cards/ToolResultCard'
import { isDirectoryData, inferToolFromData } from '@/lib/artifactInfer'

/**
 * ArtifactBody renders a single artifact's content. Extracted from VizPanel so the
 * Artifact Canvas can render two side-by-side in compare mode.
 */
export function ArtifactBody({ artifact }: { artifact: VizArtifact }) {
  const a = artifact

  if (a.type === 'plotly') {
    return (
      <div style={{ padding: 16, height: '100%' }}>
        <PlotlyChart data={a.data} />
      </div>
    )
  }

  if (a.type === 'report') {
    return <ReportView data={a.data as ReportData} />
  }

  if (a.type === 'diffraction') {
    return <DiffractionViewer />
  }

  if (a.type === 'image') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', padding: 16 }}>
        <img
          src={a.data as string}
          alt={a.title}
          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', borderRadius: 8 }}
        />
      </div>
    )
  }

  if (a.type === 'table') {
    return (
      <div style={{ padding: 16, height: '100%', overflow: 'auto' }}>
        {isDirectoryData(a.data) ? (
          <FileListCard result={{ tool: 'list_directory', status: 'success', data: a.data as Record<string, unknown> }} />
        ) : (
          cardFor({ tool: inferToolFromData(a.data), status: 'success', data: a.data as Record<string, unknown> })
        )}
      </div>
    )
  }

  if (a.type === 'text') {
    return <ResponseView text={String(a.data)} />
  }

  return null
}
