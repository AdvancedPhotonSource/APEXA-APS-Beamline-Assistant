import { useEffect, useState } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { list, num, obj, str } from '@/lib/refinement'
import type { Obj } from '@/lib/refinement'
import { PlotlyChart } from './PlotlyChart'

const records = (value: unknown): Obj[] => list(value).map(obj).filter((v): v is Obj => !!v)
const section = { marginTop: 20, paddingTop: 14, borderTop: '1px solid var(--apexa-border)' }

export function GSAS2Workbench({ data }: { data: Obj }) {
  const sendMessage = useChatStore(s => s.sendMessage)
  const [query, setQuery] = useState('')
  const [instruction, setInstruction] = useState('')
  const [figureResponse, setFigureResponse] = useState<{ url: string; figures: unknown[]; error?: string }>({ url: '', figures: [] })
  const path = str(data.figures_path)
  const prefix = data.location === 'remote' ? '/remote' : '/local'
  const figuresUrl = path ? `${prefix}/api/science/gsas2/figures?path=${encodeURIComponent(path)}` : ''
  const figures = figureResponse.url === figuresUrl ? figureResponse.figures : []
  const error = figureResponse.url === figuresUrl ? figureResponse.error : undefined
  useEffect(() => {
    if (!figuresUrl) return
    const controller = new AbortController()
    fetch(figuresUrl, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`Could not load numerical plots (${response.status})`)
        return response.json()
      })
      .then(value => { if (!controller.signal.aborted) setFigureResponse({ url: figuresUrl, figures: list(value.figures) }) })
      .catch(reason => { if (!controller.signal.aborted) setFigureResponse({ url: figuresUrl, figures: [], error: String(reason) }) })
    return () => controller.abort()
  }, [figuresUrl])
  const tutorials = records(data.tutorials).filter(t => `${str(t.title)} ${str(t.category)}`.toLowerCase().includes(query.toLowerCase()))
  const methods = [...records(data.methods), ...records(data.functions)]
  const capabilities = records(data.research_capabilities)
  const operations = records(data.operations)
  const analyses = records(data.analyses)
  const stages = records(data.stages)
  const runtimes = obj(data.external_runtimes)
  const comparison = records(data.comparison)
  const status = str(data.execution_status) || str(data.status)

  return <div style={{ height: '100%', overflow: 'auto', padding: 'clamp(12px, 2vw, 22px)', color: 'var(--apexa-text)' }}>
    <h2 style={{ margin: '0 0 8px' }}>GSAS-II workbench</h2>
    <div style={{ fontSize: 12, color: 'var(--apexa-text-muted)' }}>{status} · scientific status: {str(data.scientific_status) || 'not evaluated'}</div>
    {str(data.error) && <p role="alert">{str(data.error)}</p>}
    {list(data.artifact_warnings).map((warning, i) => <p role="status" key={i}>{String(warning)}</p>)}

    {str(data.workflow_dir) && <section style={section}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="apexa-gallery-button" onClick={() => sendMessage(`Use gsas2_workflow_status for ${str(data.workflow_dir)} and update the canvas.`)}>Refresh progress</button>
        {['queued', 'running'].includes(status) && <button className="apexa-gallery-button" onClick={() => sendMessage(`Cancel GSAS-II workflow ${str(data.workflow_dir)} using gsas2_workflow_cancel.`)}>Stop workflow</button>}
      </div>
      {str(data.gpx_path) && <form style={{ display: 'flex', gap: 8, marginTop: 12 }} onSubmit={event => {
        event.preventDefault()
        if (!instruction.trim()) return
        sendMessage(`Continue from GSAS-II project ${str(data.gpx_path)} in a new output directory. ${instruction.trim()} Show progress and results in the canvas.`)
        setInstruction('')
      }}>
        <input aria-label="Requested analysis or refinement change" value={instruction} onChange={e => setInstruction(e.target.value)}
          placeholder="Describe the next analysis or refinement change…" style={{ minWidth: 0, flex: 1, padding: 9, background: 'var(--apexa-surface)', color: 'inherit', border: '1px solid var(--apexa-border)', borderRadius: 6 }} />
        <button className="apexa-gallery-button" type="submit" disabled={!instruction.trim()}>Apply through APEXA</button>
      </form>}
      {operations.length > 0 && <ol style={{ paddingLeft: 22 }}>{operations.map((operation, i) => {
        const request = obj(operation.request) ?? {}
        return <li key={i} style={{ padding: '6px 0', fontSize: 12 }}>
          <b>{str(request.method) || str(request.function) || str(request.name) || str(request.kind)}</b> · {str(operation.state)}
          {str(operation.error) && <div role="alert">{str(operation.error)}</div>}
          {records(operation.histograms).map((hist, j) => <div key={j} style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>
            {str(hist.name)} · Rwp {num(hist.Rwp)?.toFixed(3) ?? 'unavailable'}
          </div>)}
          <details><summary>Parameters and result</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 11 }}>{JSON.stringify({ parameters: request, result: operation.result, result_path: operation.result_path }, null, 2)}</pre></details>
        </li>
      })}</ol>}
      {str(data.gpx_path) && <p style={{ fontSize: 11, overflowWrap: 'anywhere' }}>Saved project: {str(data.gpx_path)}</p>}
    </section>}

    {error && <p role="alert">{error}</p>}
    {stages.length > 0 && <section style={section}>
      <h3>Proposed refinement stages</h3>
      <p style={{ fontSize: 12 }}>{str(data.strategy)} · {str(data.strategy_why)}</p>
      {stages.map((stage, i) => <article key={i} style={{ padding: '9px 0', borderBottom: '1px solid var(--apexa-border)' }}>
        <b>{i + 1}. {str(stage.stage)}</b>
        <p style={{ margin: '5px 0', fontSize: 12 }}>{str(stage.why)}</p>
        <div style={{ fontSize: 12 }}>Parameters: {list(stage.parameters ?? stage.categories).join(', ')}</div>
        {Boolean(stage.blocked_by || stage.withheld_reason) && <p style={{ fontSize: 12 }}>{str(stage.blocked_by) || str(stage.withheld_reason)}</p>}
        <p style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>{str(stage.citation)}</p>
      </article>)}
      {[...list(data.blockers), ...list(data.blocking_questions)].map((item, i) => <p key={i} role="status" style={{ fontSize: 12 }}>
        {typeof item === 'string' ? item : JSON.stringify(item)}
      </p>)}
    </section>}
    {figures.map((figure, i) => <section key={`${path}-${i}`} style={{ ...section, height: 420 }}><PlotlyChart data={figure} /></section>)}

    {Array.isArray(data.tutorials) && <section style={section}>
      <h3 style={{ marginTop: 0 }}>Tutorials ({tutorials.length} shown / {String(data.total_tutorials ?? records(data.tutorials).length)})</h3>
      <input aria-label="Filter tutorials" placeholder="Filter by topic…" value={query} onChange={e => setQuery(e.target.value)}
        style={{ padding: 8, width: '100%', background: 'var(--apexa-surface)', color: 'inherit', border: '1px solid var(--apexa-border)', borderRadius: 6 }} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,280px),1fr))', gap: 10, marginTop: 12 }}>
        {tutorials.map(t => { const coverage = obj(t.coverage) ?? {}; return <article key={str(t.id)} style={{ padding: 12, border: '1px solid var(--apexa-border)', borderRadius: 8 }}>
          <div style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>{str(t.category)}</div>
          <h4 style={{ margin: '5px 0', fontSize: 13 }}>{str(t.title)}</h4>
          <p style={{ fontSize: 11 }}>{str(coverage.coverage) || str(coverage.scope)}{str(coverage.gap) && ` · ${str(coverage.gap)}`}</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <a href={str(t.url)} target="_blank" rel="noreferrer">Read source</a>
            <button className="apexa-gallery-button" onClick={() => sendMessage(`Read GSAS-II tutorial ${JSON.stringify(t.id)} with gsas2_tutorials, inspect its capability coverage and the selected canvas inputs, then carry out the requested applicable steps using the available tools. Ask for any required scientific inputs that are missing. Display progress and results in the canvas.`)}>Use this workflow</button>
          </div>
        </article> })}
      </div>
    </section>}

    {str(data.text) && <section style={section}>
      <h3>{str(data.title)}</h3>
      <a href={str(data.url)} target="_blank" rel="noreferrer">Official tutorial</a>
      <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65, fontSize: 13 }}>{str(data.text)}</p>
      {num(data.next_offset) !== null && <button className="apexa-gallery-button" onClick={() => sendMessage(`Read the next part of GSAS-II tutorial ${JSON.stringify(data.id)} with offset=${String(data.next_offset)} and show it in the canvas.`)}>Read next part</button>}
    </section>}

    {capabilities.length > 0 && <section style={section}>
      <h3>Capabilities discussed in the paper</h3>
      <p style={{ fontSize: 12 }}>Implementation coverage and scientific performance are separate. Each entry records the current limitation.</p>
      {capabilities.map(c => <article key={str(c.reference)} style={{ padding: '10px 0', borderBottom: '1px solid var(--apexa-border)' }}>
        <b>{str(c.reference)}</b> · {str(c.implementation)}
        <p style={{ margin: '4px 0', fontSize: 12 }}>{str(c.capability)}</p>
        <p style={{ margin: '4px 0', fontSize: 11, color: 'var(--apexa-text-muted)' }}>{str(c.limitation)}</p>
      </article>)}
    </section>}

    {methods.length > 0 && <section style={section}>
      <h3>Installed GSAS-II operations</h3>
      <p style={{ fontSize: 12 }}>APEXA calls these operations from your prompt. Availability indicates a callable API, not a validated tutorial result.</p>
      {methods.map((m, i) => <details key={i} style={{ padding: '5px 0', fontSize: 12 }}>
        <summary>{str(m.target)} {str(m.method) || str(m.function)} · {m.available ? 'available' : 'unavailable'}</summary>
        <code style={{ overflowWrap: 'anywhere' }}>{str(m.signature)}</code>
        <p style={{ whiteSpace: 'pre-wrap' }}>{str(m.description) || str(m.reason)}</p>
      </details>)}
    </section>}

    {analyses.length > 0 && <section style={section}>
      <h3>Advanced tutorial operations</h3>
      {analyses.map((item, i) => <details key={i} style={{ padding: '6px 0', fontSize: 12 }}>
        <summary>{str(item.name) || str(item.kind)}</summary>
        <code style={{ overflowWrap: 'anywhere' }}>{str(item.signature)}</code>
        <p style={{ whiteSpace: 'pre-wrap' }}>{str(item.description)}</p>
      </details>)}
    </section>}
    {runtimes && <section style={section}>
      <h3>External modeling software</h3>
      {Object.entries(runtimes).map(([name, value]) => { const runtime = obj(value) ?? {}; return <p key={name} style={{ fontSize: 12 }}>
        <b>{name}</b> · {runtime.available ? 'runtime found; execution unverified' : 'runtime not configured'}
        {str(runtime.reason) && ` · ${str(runtime.reason)}`}
      </p> })}
    </section>}

    {comparison.length > 0 && <section style={section}>
      <h3>Alternative refinement policies</h3>
      <p style={{ fontSize: 12 }}>Selection: {str(data.selection)} · {str(data.qualification)}</p>
      {comparison.map(c => <article key={str(c.job_id)} style={{ padding: 10, borderBottom: '1px solid var(--apexa-border)' }}>
        <b>{str(c.job_id)}</b> · {str(c.execution_status)} · {str(c.scientific_status)}
        <div style={{ fontSize: 12 }}>Rwp: {num(c.Rwp)?.toFixed(3) ?? 'unavailable'} · model score: {num(c.selection_score)?.toFixed(2) ?? 'unavailable'}</div>
        {str(c.exclusion) && <div>{str(c.exclusion)}</div>}
        <details><summary>Policy</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(c.policy, null, 2)}</pre></details>
      </article>)}
    </section>}
  </div>
}
