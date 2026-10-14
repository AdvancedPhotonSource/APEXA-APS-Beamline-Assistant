import { useEffect, useState } from 'react'
import { reportApiUrl, type ReportLocation } from '@/lib/reportArtifacts'

interface ReportDocument { html: string; title: string; warnings: string[] }
const POLICY = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; font-src data:; base-uri \'none\'; form-action \'none\'">'

export function HtmlReportView({ path, location = 'local', updatedAt = 0 }: { path: string; location?: ReportLocation; updatedAt?: number }) {
  const [revision, setRevision] = useState(0)
  const [loaded, setLoaded] = useState<{ key: string; document?: ReportDocument; error?: string } | null>(null)
  const url = reportApiUrl(path, location)
  const key = `${url}:${updatedAt}:${revision}`
  const current = loaded?.key === key ? loaded : null
  useEffect(() => {
    const controller = new AbortController()
    fetch(url, { signal: controller.signal, cache: 'no-store' })
      .then(async r => {
        const body = await r.json()
        if (!r.ok) throw new Error(body.detail || `Report request failed (${r.status})`)
        if (typeof body.html !== 'string' || !body.html.trim()) throw new Error('No HTML report content returned')
        return { html: body.html, title: body.title || 'Analysis report', warnings: body.warnings || [] }
      })
      .then(document => { if (!controller.signal.aborted) setLoaded({ key, document }) })
      .catch(e => { if (!controller.signal.aborted) setLoaded({ key, error: String(e) }) })
    return () => controller.abort()
  }, [url, key])

  const download = () => {
    if (!current?.document) return
    const objectUrl = URL.createObjectURL(new Blob([POLICY, current.document.html], { type: 'text/html;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = `${(path.split('/').pop() || 'report').replace(/\.html?$/i, '')}-preview.html`
    link.click()
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, padding: '5px 8px', borderBottom: '1px solid var(--apexa-border)' }}>
        <span title={path} style={{ flex: 1, minWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11 }}>
          {location === 'remote' ? 'Data host' : 'Local'} · {path.split('/').pop()}
        </span>
        <button className="apexa-gallery-button" onClick={() => setRevision(n => n + 1)}>{current?.error ? 'Retry' : 'Refresh'}</button>
        <button className="apexa-gallery-button" disabled={!current?.document} onClick={download} title="Download the static preview with available figures embedded">Save preview</button>
      </div>
      {current?.error ? <p role="alert" style={{ padding: 16 }}>Could not load report: {current.error}</p>
        : !current?.document ? <p role="status" style={{ padding: 16 }}>Loading report…</p>
          : <>
            {current.document.warnings.length > 0 && <details style={{ padding: '5px 10px', fontSize: 11 }}>
              <summary>{current.document.warnings.length} preview limitation(s)</summary>
              <ul>{current.document.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
            </details>}
            <iframe title={current.document.title} sandbox="" srcDoc={POLICY + current.document.html}
              style={{ border: 0, width: '100%', flex: 1, minHeight: 240, background: 'white' }} />
          </>}
    </div>
  )
}
