import { useState } from 'react'
import { useVizStore } from '@/stores/vizStore'
import { isHtmlReport, reportFileArtifact, type ReportLocation } from '@/lib/reportArtifacts'
import { VizLauncher } from './VizLauncher'

export function ResultsPanel() {
  const artifacts = useVizStore(s => s.artifacts)
  const openIds = useVizStore(s => s.openIds)
  const setActive = useVizStore(s => s.setActive)
  const [mode, setMode] = useState<'session' | 'directory'>('session')
  const [filter, setFilter] = useState('')
  const [reportsOnly, setReportsOnly] = useState(false)
  const [path, setPath] = useState('')
  const [location, setLocation] = useState<ReportLocation>('local')
  const [error, setError] = useState('')
  const rows = [...artifacts].reverse().filter(a => (!reportsOnly || ['report', 'html-report'].includes(a.type))
    && `${a.title} ${a.provenance?.inputs?.join(' ') ?? ''}`.toLowerCase().includes(filter.toLowerCase()))

  const openReport = () => {
    const source = path.trim()
    if (!isHtmlReport(source)) { setError('Select an HTML report (.html or .htm).'); return }
    setError('')
    useVizStore.getState().addArtifact(reportFileArtifact(source, '', location))
    useVizStore.getState().showCanvas()
  }

  return <div className="flex h-full min-h-0 flex-col text-xs">
    <div className="flex shrink-0 gap-1 p-2" role="group" aria-label="Result source">
      <button className="apexa-gallery-button" aria-pressed={mode === 'session'} onClick={() => setMode('session')}>This session ({artifacts.length})</button>
      <button className="apexa-gallery-button" aria-pressed={mode === 'directory'} onClick={() => setMode('directory')}>Scan folder</button>
    </div>
    <form className="shrink-0 space-y-2 border-b p-2" style={{ borderColor: 'var(--apexa-border)' }} onSubmit={e => { e.preventDefault(); openReport() }}>
      <label htmlFor="apexa-report-path" className="block">Open an HTML report in canvas</label>
      <input id="apexa-report-path" value={path} onChange={e => setPath(e.target.value)} placeholder="/path/to/report.html"
        className="w-full min-w-0 rounded border p-2" style={{ background: 'var(--apexa-surface-2)', borderColor: 'var(--apexa-border)' }} />
      <div className="flex gap-2">
        <select aria-label="Report host" value={location} onChange={e => setLocation(e.target.value as ReportLocation)}
          className="min-w-0 flex-1 rounded border p-1" style={{ background: 'var(--apexa-surface-2)', borderColor: 'var(--apexa-border)' }}>
          <option value="local">Local APEXA host</option>
          <option value="remote">Configured data host</option>
        </select>
        <button type="submit" disabled={!path.trim()} className="apexa-gallery-button">Open</button>
      </div>
      {location === 'remote' && <p style={{ color: 'var(--apexa-text-muted)' }}>Uses the configured science service through its SSH tunnel.</p>}
      {error && <p role="alert">{error}</p>}
    </form>
    {mode === 'directory' ? <div className="min-h-0 flex-1"><VizLauncher /></div> : <>
      <div className="shrink-0 space-y-2 p-2">
        <input type="search" aria-label="Search session results" placeholder="Search results…" value={filter} onChange={e => setFilter(e.target.value)}
          className="w-full rounded border p-2" style={{ background: 'var(--apexa-surface-2)', borderColor: 'var(--apexa-border)' }} />
        <label className="flex items-center gap-2"><input type="checkbox" checked={reportsOnly} onChange={e => setReportsOnly(e.target.checked)} />Reports only</label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2 space-y-1">
        {rows.map(a => <button key={a.id} className="apexa-workflow-option w-full rounded border p-2 text-left" onClick={() => setActive(a.id)}
          title={`Open ${a.title} in canvas`} style={{ borderColor: 'var(--apexa-border)', background: 'var(--apexa-surface-2)' }}>
          <span className="block truncate font-medium">{a.title}</span>
          <span className="block mt-1 text-[10px]" style={{ color: 'var(--apexa-text-muted)' }}>
            {a.type === 'html-report' ? 'HTML report' : a.type} · {openIds.includes(a.id) ? 'Open' : 'Closed — click to reopen'}
          </span>
        </button>)}
        {rows.length === 0 && <p className="p-2 leading-relaxed" style={{ color: 'var(--apexa-text-muted)' }}>
          {artifacts.length ? 'No matching results.' : 'Your plots, structures and reports appear here. Open a report above or scan an analysis folder.'}
        </p>}
      </div>
    </>}
  </div>
}
