import { useState, useCallback, useEffect, useRef } from 'react'
import { useVizStore } from '@/stores/vizStore'
import { useChatStore } from '@/stores/chatStore'
import { vizResultId } from '@/lib/canvasIdentity'
import { resultIdentity } from '@/lib/parseToolResult'

interface VizFile {
  file: string
  peaks_csv?: string | null
}

interface DiscoverResult {
  lineout: VizFile[]
  calibrant: VizFile[]
  caked: VizFile[]
  peaks_h5: VizFile[]
  grains: VizFile[]
  spots: VizFile[]
  microstructure: VizFile[]
}

interface VizResult {
  plotly: { data: unknown[]; layout: unknown }
  tables: { title: string; data: unknown }[]
  title: string
  error?: string
}

const API = '/api/viz'

function basename(path: string): string {
  return path.split('/').pop() || path
}

export function VizLauncher() {
  const sessionId = useChatStore(state => state.currentSessionId)
  return <SessionVizLauncher key={sessionId ?? 'no-session'} sessionId={sessionId} />
}

function SessionVizLauncher({ sessionId }: { sessionId: string | null }) {
  const [dir, setDir] = useState('')
  const [files, setFiles] = useState<DiscoverResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [vizLoading, setVizLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const addArtifacts = useVizStore((s) => s.addArtifacts)
  const discoveryRequest = useRef<AbortController | null>(null)
  const viewerRequest = useRef<AbortController | null>(null)
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    const abortRequests = () => { discoveryRequest.current?.abort(); viewerRequest.current?.abort() }
    // Invalidate immediately, including a switch away and back before React renders.
    const unsubscribe = useChatStore.subscribe((next, previous) => {
      if (next.currentSessionId !== previous.currentSessionId) abortRequests()
    })
    return () => { mounted.current = false; abortRequests(); unsubscribe() }
  }, [])
  const current = useCallback((controller: AbortController) => mounted.current && !controller.signal.aborted
    && useChatStore.getState().currentSessionId === sessionId, [sessionId])

  const discover = useCallback(async (path: string) => {
    if (!path.trim()) return
    discoveryRequest.current?.abort()
    viewerRequest.current?.abort()
    const controller = new AbortController()
    discoveryRequest.current = controller
    setLoading(true)
    setVizLoading(null)
    setFiles(null)
    setError(null)
    try {
      const res = await fetch(`${API}/discover?path=${encodeURIComponent(path)}`, { signal: controller.signal })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Discovery failed')
      }
      const data: DiscoverResult = await res.json()
      if (!current(controller)) return
      setFiles(data)
    } catch (e) {
      if (!current(controller)) return
      setError((e as Error).message)
      setFiles(null)
    } finally {
      if (current(controller)) setLoading(false)
    }
  }, [current])

  const openViewer = useCallback(async (endpoint: string, file: string, extras?: Record<string, string>) => {
    viewerRequest.current?.abort()
    const controller = new AbortController()
    viewerRequest.current = controller
    setVizLoading(file)
    setError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      if (extras) {
        for (const [k, v] of Object.entries(extras)) {
          body.append(k, v)
        }
      }
      const res = await fetch(`${API}/${endpoint}`, { method: 'POST', body, signal: controller.signal })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Visualization failed')
      }
      const result: VizResult = await res.json()
      if (!current(controller)) return
      const provenance = { tool: `viz:${endpoint}`, inputs: [file, ...Object.values(extras ?? {})], params: { endpoint, ...extras } }

      const artifacts = []
      // Main plotly artifact
      if (result.plotly) {
        artifacts.push({
          id: vizResultId(endpoint, file, result.plotly, extras),
          type: 'plotly' as const,
          title: result.title || basename(file),
          data: result.plotly,
          sourceMessageId: '', provenance,
        })
      }
      // Table artifacts
      for (const tbl of result.tables || []) {
        artifacts.push({
          id: `viz-table-${resultIdentity({ tool: `viz:${endpoint}`, status: 'completed', data: { file, extras, table: tbl } })}`,
          type: 'table' as const,
          title: tbl.title,
          data: tbl.data,
          sourceMessageId: '', provenance,
        })
      }
      if (artifacts.length > 0) {
        addArtifacts(artifacts, { activate: true })
      }
    } catch (e) {
      if (!current(controller)) return
      setError((e as Error).message)
    } finally {
      if (current(controller)) setVizLoading(null)
    }
  }, [addArtifacts, current])

  const totalFiles = files
    ? files.lineout.length + files.calibrant.length + files.caked.length +
      files.peaks_h5.length + files.grains.length + files.spots.length +
      files.microstructure.length
    : 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: 13 }}>
      {/* Directory input */}
      <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--apexa-border)' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          <input
            type="text"
            value={dir}
            onChange={(e) => setDir(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && discover(dir)}
            placeholder="/path/to/analysis"
            style={{
              flex: 1,
              padding: '5px 8px',
              fontSize: 12,
              borderRadius: 5,
              border: '1px solid var(--apexa-border)',
              background: 'var(--apexa-input-bg)',
              color: 'var(--apexa-text)',
              outline: 'none',
            }}
          />
          <button
            onClick={() => discover(dir)}
            disabled={loading || !dir.trim()}
            style={{
              padding: '5px 10px',
              fontSize: 11,
              borderRadius: 5,
              border: 'none',
              background: '#3b82f6',
              color: 'white',
              cursor: 'pointer',
              opacity: loading || !dir.trim() ? 0.5 : 1,
            }}
          >
            {loading ? '...' : 'Scan'}
          </button>
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div style={{
          padding: '6px 10px',
          fontSize: 11,
          color: '#ef4444',
          background: 'rgba(239,68,68,0.1)',
          borderBottom: '1px solid var(--apexa-border)',
        }}>
          {error}
          <button onClick={() => setError(null)} style={{ float: 'right', background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', fontSize: 11 }}>x</button>
        </div>
      )}

      {/* File list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
        {files && totalFiles === 0 && (
          <div style={{ padding: '20px 10px', textAlign: 'center', color: 'var(--apexa-text-muted)', fontSize: 12 }}>
            No analysis files found in this directory.
          </div>
        )}

        {files && files.lineout.length > 0 && (
          <ViewerSection
            title="Lineout Results"
            iconPath="M3 12h4l3-9 4 18 3-9h4"
            files={files.lineout}
            onOpen={(f) => openViewer('lineout', f.file, f.peaks_csv ? { peaks_csv: f.peaks_csv } : undefined)}
            loading={vizLoading}
          />
        )}

        {files && files.calibrant.length > 0 && (
          <ViewerSection
            title="Calibrant QC"
            iconPath="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
            files={files.calibrant}
            onOpen={(f) => openViewer('calibrant', f.file)}
            loading={vizLoading}
          />
        )}

        {files && files.caked.length > 0 && (
          <ViewerSection
            title="Caked Heatmap"
            iconPath="M3 3h18v18H3V3z"
            files={files.caked}
            onOpen={(f) => openViewer('caked', f.file)}
            loading={vizLoading}
          />
        )}

        {files && files.peaks_h5.length > 0 && (
          <ViewerSection
            title="Caked Peaks"
            iconPath="M12 2L2 22h20L12 2z"
            files={files.peaks_h5}
            onOpen={(f) => openViewer('caked_peaks', f.file)}
            loading={vizLoading}
          />
        )}

        {files && files.grains.length > 0 && (
          <ViewerSection
            title="FF Grain Map"
            iconPath="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 4a3 3 0 1 1 0 6 3 3 0 0 1 0-6z"
            files={files.grains}
            onOpen={(f) => openViewer('grains', f.file)}
            loading={vizLoading}
          />
        )}

        {files && files.spots.length > 0 && (
          <ViewerSection
            title="Spot Matrix"
            iconPath="M12 2l2 7h7l-5.5 4 2 7L12 16l-5.5 4 2-7L3 9h7z"
            files={files.spots}
            onOpen={(f) => openViewer('spots', f.file)}
            loading={vizLoading}
          />
        )}

        {files && files.microstructure.length > 0 && (
          <ViewerSection
            title="NF Microstructure"
            iconPath="M4 4h6v6H4V4zm10 0h6v6h-6V4zm-10 10h6v6H4v-6zm10 0h6v6h-6v-6z"
            files={files.microstructure}
            onOpen={(f) => openViewer('microstructure', f.file)}
            loading={vizLoading}
          />
        )}

        {!files && !loading && (
          <div style={{ padding: '20px 10px', textAlign: 'center', color: 'var(--apexa-text-muted)', fontSize: 12 }}>
            Enter a directory path and click Scan to discover MIDAS analysis outputs.
          </div>
        )}
      </div>
    </div>
  )
}


function ViewerSection({
  title,
  iconPath,
  files,
  onOpen,
  loading,
}: {
  title: string
  iconPath: string
  files: VizFile[]
  onOpen: (f: VizFile) => void
  loading: string | null
}) {
  return (
    <div style={{ marginBottom: 2 }}>
      <div style={{
        padding: '6px 10px',
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.5px',
        color: 'var(--apexa-text-2)',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d={iconPath} />
        </svg>
        {title}
        <span style={{ fontSize: 10, color: 'var(--apexa-text-muted)', fontWeight: 400 }}>({files.length})</span>
      </div>
      {files.map((f) => {
        const name = basename(f.file)
        const isLoading = loading === f.file
        return (
          <button
            key={f.file}
            onClick={() => onOpen(f)}
            disabled={isLoading}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              width: '100%',
              padding: '5px 10px 5px 26px',
              fontSize: 12,
              textAlign: 'left',
              border: 'none',
              background: 'transparent',
              color: 'var(--apexa-text)',
              cursor: isLoading ? 'wait' : 'pointer',
              opacity: isLoading ? 0.5 : 1,
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--apexa-rail-active)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          >
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
            {f.peaks_csv && (
              <span style={{ fontSize: 9, color: '#3b82f6', flexShrink: 0 }}>+peaks</span>
            )}
            {isLoading && <span style={{ fontSize: 10 }}>loading...</span>}
          </button>
        )
      })}
    </div>
  )
}
