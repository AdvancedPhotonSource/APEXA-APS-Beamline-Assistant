import type { VizArtifact } from '@/api/types'
import { lazy, Suspense, useEffect, useState } from 'react'
import { useImageStore } from '@/stores/imageStore'
import { PlotlyChart } from './PlotlyChart'
import { ResponseView } from './ResponseView'
import type { ReportData } from './ReportView'
import { DiffractionViewer } from '@/components/viewer/DiffractionViewer'
import { FileListCard } from '@/components/cards/FileListCard'
import { cardFor } from '@/components/cards/ToolResultCard'
import { isDirectoryData, inferToolFromData } from '@/lib/artifactInfer'
import { StructureView } from './StructureView'
import { SequenceView } from '@/components/viewer/SequenceView'
import { HtmlReportView } from './HtmlReportView'
const ReportView = lazy(() => import('./ReportView').then(m => ({ default: m.ReportView })))

function SavedDiffractionView({ artifact }: { artifact: VizArtifact }) {
  const fileId = (artifact.data as { fileId?: string }).fileId
  const loaded = useImageStore(s => !!fileId && s.loadedImages.has(fileId))
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (loaded) return
    let active = true
    useImageStore.getState().restoreImage(artifact, () => active).catch(e => { if (active) setError(String(e)) })
    return () => { active = false }
  }, [artifact, loaded, retry])
  if (loaded && fileId) return <DiffractionViewer fileId={fileId} />
  return <div className="p-4 text-sm" role={error ? 'alert' : 'status'}>
    {error || 'Reopening saved detector image…'}
    {error && <button className="apexa-gallery-button ml-2" onClick={() => { setError(''); setRetry(n => n + 1) }}>Retry</button>}
  </div>
}

/**
 * ArtifactBody renders a single artifact's content. Extracted from VizPanel so the
 * Artifact Canvas can render two side-by-side in compare mode.
 */
export function ArtifactBody({ artifact }: { artifact: VizArtifact }) {
  const a = artifact
  if (a.type === 'html-report') return <HtmlReportView key={a.id} {...a.data as { path: string; location?: 'local' | 'remote' }} updatedAt={a.createdAt} />

  if (a.type === 'structure') {
    const data = a.data as { path: string; source?: string }
    return <StructureView key={a.id} {...data} />
  }
  if (a.type === 'sequence') return <SequenceView key={a.id} artifact={a} />

  if (a.type === 'plotly') {
    return (
      <div style={{ padding: 12, height: '100%', minHeight: 340, minWidth: 0 }}>
        <PlotlyChart data={a.data} />
      </div>
    )
  }

  if (a.type === 'report') {
    return <Suspense fallback={<p style={{ padding: 16 }}>Loading report…</p>}><ReportView data={a.data as ReportData} /></Suspense>
  }

  if (a.type === 'diffraction') {
    return <SavedDiffractionView key={a.id} artifact={a} />
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
