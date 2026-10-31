import { useMemo, useState } from 'react'
import { PlotlyChart } from './PlotlyChart'
import { GSAS2Workbench } from './GSAS2Workbench'
import { RefinementDecisions } from './RefinementDecisions'
import { useRefinementLive } from '@/lib/useRefinementLive'
import { useChatStore } from '@/stores/chatStore'

import { basename, executionState, fractionGroups, list, num, obj, outcomeCounts, residual, rowsFor, str } from '@/lib/refinement'
import type { Obj, RefinementRow as Row } from '@/lib/refinement'

function plotPaths(result: Obj): { path: string; title: string }[] {
  const found: { path: string; title: string }[] = []
  const add = (path: unknown, title: string) => {
    if (typeof path === 'string' && path.toLowerCase().endsWith('.png') && !found.some(x => x.path === path)) {
      found.push({ path, title })
    }
  }
  add(result.plot_path, 'Rietveld fit')
  for (const [index, path] of list(result.plots).entries()) add(path, `Histogram ${index + 1} fit`)
  const lebail = obj(result.lebail)
  if (lebail) add(lebail.plot, 'Le Bail profile comparator')
  return found
}

function badgeColor(status: string): string {
  const s = status.toLowerCase()
  if (['completed', 'complete', 'success', 'converged', 'passed', 'accepted'].includes(s)) return '#138a72'
  if (['running', 'queued', 'submitted'].includes(s)) return '#3478c8'
  if (['failed', 'error', 'blocked', 'cancelled', 'unqualified'].includes(s)) return '#b84b48'
  return '#ad7a2b'
}

function StatusBadge({ label, value }: { label: string; value: string }) {
  const color = badgeColor(value)
  return <span title={`${label}: ${value}`} style={{ display: 'inline-flex', gap: 5, alignItems: 'center', borderRadius: 999,
    padding: '3px 8px', border: `1px solid color-mix(in srgb, ${color} 38%, transparent)`,
    background: `color-mix(in srgb, ${color} 13%, transparent)`, color: 'var(--apexa-text)', fontSize: 11 }}>
    <b style={{ color }}>{label}</b> {value || 'unknown'}
  </span>
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div title={hint} style={{ border: '1px solid var(--apexa-border)', borderRadius: 9, padding: '10px 12px', background: 'var(--apexa-surface)' }}>
    <div style={{ color: 'var(--apexa-text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</div>
    <div style={{ color: 'var(--apexa-text)', fontSize: 16, fontWeight: 650, marginTop: 3 }}>{value}</div>
  </div>
}

function figure(title: string, xTitle: string, yTitle: string, traces: Obj[]) {
  return { data: traces, layout: { title: { text: title }, xaxis: { title: { text: xTitle } },
    yaxis: { title: { text: yTitle } }, height: 300, margin: { t: 44, r: 20, b: 55, l: 62 },
    legend: { orientation: 'h', y: -0.22 } } }
}

function scanAxis(rows: Row[]): { title: string; values: Map<string, number> } | null {
  if (!rows.length || rows.some(row => typeof row.axis !== 'number' || !Number.isFinite(row.axis))) return null
  const units = new Set(rows.map(row => row.axisUnits ?? ''))
  if (units.size !== 1) return null
  const unit = [...units][0]
  return { title: unit ? `Scan coordinate (${unit})` : 'Scan coordinate',
    values: new Map(rows.map(row => [row.id, row.axis!])) }
}

function refinementTrends(rows: Row[]) {
  if (rows.length < 2) return null
  const axis = scanAxis(rows)
  const completed = rows.map((row, index) => ({ row, index, rwp: residual(row) })).filter(x => x.rwp !== null)
  if (!completed.length) return null
  return figure(axis ? 'Fit residual by scan coordinate' : 'Fit residual by submitted job', axis?.title ?? 'Job index', 'Rwp (%)', [{
    x: completed.map(x => axis?.values.get(x.row.id) ?? x.index + 1), y: completed.map(x => x.rwp),
    text: completed.map(x => x.row.label), hovertemplate: '%{text}<br>x: %{x:.6g}<br>Rwp: %{y:.3f}%<extra></extra>',
    name: 'Rwp', type: 'scatter', mode: 'markers',
  }])
}

function phaseTrend(rows: Row[]) {
  if (rows.length < 2) return null
  const axis = scanAxis(rows)
  const traces = new Map<string, { x: number[]; y: number[]; text: string[] }>()
  rows.forEach((row, index) => {
    if (executionState(row) !== 'completed') return
    const groups = fractionGroups(row.result ?? {})
    groups.forEach(group => Object.entries(group.values).forEach(([phase, value]) => {
      const fraction = num(value)
      if (fraction === null) return
      const key = groups.length > 1 ? `${group.label} · ${phase}` : phase
      const trace = traces.get(key) ?? { x: [], y: [], text: [] }
      trace.x.push(axis?.values.get(row.id) ?? index + 1)
      trace.y.push(fraction * 100)
      trace.text.push(`${row.label} · ${group.label}`)
      traces.set(key, trace)
    }))
  })
  if (!traces.size) return null
  return figure('Modeled phase fractions', axis?.title ?? 'Job index', 'Mass fraction (%)',
    [...traces.entries()].map(([name, trace]) => ({ ...trace, name, type: 'scatter', mode: 'markers',
      hovertemplate: '%{text}<br>x: %{x:.6g}<br>%{y:.2f}%<extra>%{fullData.name}</extra>' })))
}

function EvidenceChecks({ coverage }: { coverage: unknown }) {
  const checks = Object.entries(obj(coverage) ?? {})
  if (!checks.length) return <p style={{ fontSize: 11 }}>Evidence coverage unavailable.</p>
  return <ul style={{ fontSize: 11, paddingLeft: 18 }}>
    {checks.map(([name, value]) => {
      const check = obj(value) ?? {}
      const reason = str(check.reason) || str(check.error) || list(check.findings).map(String).join('; ')
      return <li key={name} style={{ margin: '5px 0' }}>
        <StatusBadge label={name} value={str(check.status) || 'unavailable'} /> {reason}
      </li>
    })}
  </ul>
}

function ImagePanel({ path, title, location }: { path: string; title: string; location: string }) {
  const [failed, setFailed] = useState(false)
  const prefix = location === 'remote' ? '/remote' : '/local'
  const src = `${prefix}/api/science/image?path=${encodeURIComponent(path)}`
  return <figure style={{ minWidth: 0, margin: 0, border: '1px solid var(--apexa-border)', borderRadius: 9, overflow: 'hidden', background: 'var(--apexa-surface)' }}>
    <figcaption style={{ padding: '7px 10px', fontSize: 11, color: 'var(--apexa-text-2)', borderBottom: '1px solid var(--apexa-border)' }}>{title}</figcaption>
    {failed ? <div role="status" style={{ padding: 14, fontSize: 11, color: 'var(--apexa-text-muted)' }}>
      Plot file is unavailable from this APEXA host. <code>{basename(path)}</code>
    </div> : <a href={src} target="_blank" rel="noreferrer" title="Open full-size plot">
      <img src={src} alt={title} onError={() => setFailed(true)} loading="lazy"
        style={{ display: 'block', width: '100%', maxHeight: 520, objectFit: 'contain', padding: 8 }} />
    </a>}
  </figure>
}

function sectionStyle(): React.CSSProperties {
  return { marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--apexa-border)' }
}

export function RefinementView({ data: input }: { data: unknown }) {
  const live = useRefinementLive(obj(input) ?? {})
  const data = live.data
  const sendMessage = useChatStore(s => s.sendMessage)
  const rows = useMemo(() => rowsFor(data), [data])
  const location = str(data.location)
  const evidencePanels = list(data.panels).map(obj).filter((x): x is Obj => !!x)
  const proposals = list(data.proposals).map(obj).filter((x): x is Obj => !!x)
  const trials = [...list(data.trials).map(obj).filter((x): x is Obj => !!x),
    ...(typeof data.accepted === 'boolean' ? [data] : [])]
  const [selectedId, setSelectedId] = useState('')
  const selected = rows.length === 1 ? rows[0] : rows.find(row => row.id === selectedId)
  const summary = selected?.result
  const completedSummary = selected && executionState(selected) === 'completed' ? summary : undefined
  const counts = outcomeCounts(rows)
  const diagnostics = list(obj(summary?.diagnostics)?.findings ?? summary?.diagnostics)
  const pattern = refinementTrends(rows)
  const fractions = phaseTrend(rows)
  const jobIds = list(data.job_ids).filter((x): x is string => typeof x === 'string')
  const images = rows.flatMap(row => plotPaths(row.result ?? {}).map(plot => ({ ...plot, label: row.label })))
  const trace = list(summary?.decision_trace ?? summary?.history).map(obj).filter((x): x is Obj => !!x)
  const status = str(data.status) || (rows.length === 1 ? rows[0].state : '') || 'unknown'
  const sessionDir = str(data.session_dir)
  const improvement = obj(data.improvement) ?? {}
  const revisions = sessionDir ? list(data.history).map(obj).filter((x): x is Obj => !!x) : []
  const optimization = obj(summary?.optimization) ?? obj(selected?.progress?.optimization)
  const optimizationTrials = list(optimization?.trials).map(obj).filter((x): x is Obj => !!x)

  if (['tutorials', 'methods', 'research_capabilities', 'workflow_dir', 'comparison', 'stages'].some(key => key in data)
      || (typeof data.text === 'string' && typeof data.url === 'string')) {
    return <GSAS2Workbench data={data} />
  }

  return <div style={{ height: '100%', overflow: 'auto', padding: 'clamp(12px, 2vw, 22px)', color: 'var(--apexa-text)' }}>
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
      <div>
        <div style={{ fontSize: 10, letterSpacing: '.13em', textTransform: 'uppercase', color: 'var(--apexa-text-muted)' }}>Agentic powder refinement</div>
        <h2 style={{ margin: '4px 0 8px', fontSize: 19 }}>Fit evidence and refinement path</h2>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <StatusBadge label="Job" value={status} />
          {typeof completedSummary?.scientific_status === 'string' && <StatusBadge label={rows.length > 1 ? 'Selected job science' : 'Science'} value={completedSummary.scientific_status} />}
          {typeof summary?.numerical_status === 'string' && <StatusBadge label="Numerics" value={summary.numerical_status} />}
          {typeof summary?.execution_status === 'string' && <StatusBadge label="Execution" value={summary.execution_status} />}
        </div>
      </div>
      {(str(data.job_id) || rows.length > 1) && <code style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>
        {str(data.job_id) || `${rows.length} jobs`}
      </code>}
    </div>

    {sessionDir && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Refinement session · revision {String(obj(data.head)?.revision ?? data.session_revision ?? '—')}</h3>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
        <button className="apexa-gallery-button" onClick={() => sendMessage(`Refresh refinement session ${sessionDir} with powder_session_status and show its active result in the canvas.`)}>Refresh session</button>
        <button className="apexa-gallery-button" onClick={() => sendMessage(`Generate fresh evidence for refinement session ${sessionDir} using powder_session_evidence and show the plots in the canvas.`)}>Inspect residuals</button>
        <button className="apexa-gallery-button" onClick={() => sendMessage(`Continue refinement session ${sessionDir} with powder_improve, max_rounds=3, max_wall_seconds=900, auto_adopt=true. Poll its status and show adopted revisions and scientific status in the canvas.`)}>Continue refinement</button>
        {['running', 'queued'].includes(str(improvement.state)) && <button className="apexa-gallery-button" onClick={() => sendMessage(`Stop the improvement loop for refinement session ${sessionDir} using powder_session_control(action="stop").`)}>Stop improvement</button>}
      </div>
      {str(improvement.state) && <p style={{ fontSize: 12 }}>Improvement: {str(improvement.state)} · {str(improvement.stop_reason)} {str(improvement.error)}</p>}
      {revisions.length > 0 && <details style={{ marginTop: 10 }}><summary>Saved revisions</summary>
        <ul style={{ fontSize: 12 }}>{revisions.map((revision, i) => <li key={i}>
          Revision {String(revision.revision)} · {str(revision.action)}
          {num(revision.Rwp_after) !== null && ` · Rwp ${num(revision.Rwp_before)?.toFixed(3)} → ${num(revision.Rwp_after)?.toFixed(3)}`}
          {revision.revision !== obj(data.head)?.revision && !['running', 'queued'].includes(str(improvement.state)) && <button className="apexa-gallery-button" style={{ marginLeft: 8 }}
            onClick={() => sendMessage(`Restore refinement session ${sessionDir} to saved revision ${String(revision.revision)} using powder_session_control(action="restore", revision=${String(revision.revision)}, expected_revision=${String(obj(data.head)?.revision)}).`)}>Restore</button>}
        </li>)}</ul>
      </details>}
      {list(improvement.rounds).length > 0 && <details style={{ marginTop: 10 }}><summary>Diagnosis and trial history</summary>
        {list(improvement.rounds).map(obj).filter((r): r is Obj => !!r).map((round, i) => <div key={i} style={{ padding: '8px 0', fontSize: 12 }}>
          <b>Round {String(round.round)}</b> · {str(round.state)} {str(round.reason)}
          <ul>{list(round.trials).map(obj).filter((t): t is Obj => !!t).map((trial, j) => <li key={j}>
            {str(obj(trial.proposal)?.action)} · {trial.accepted ? 'accepted' : 'rejected'} · Rwp {String(trial.Rwp_before ?? '—')} → {String(trial.Rwp_after ?? '—')}
            {list(trial.reasons).length > 0 && ` · ${list(trial.reasons).join('; ')}`}
          </li>)}</ul>
        </div>)}
      </details>}
    </section>}

    {str(data.job_id) && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 10 }}>
      {['running', 'submitted', 'queued', 'partial', 'unknown'].includes(status.toLowerCase()) && <button className="apexa-gallery-button"
        onClick={() => live.supported ? live.refresh() : sendMessage(`Check powder refinement job ${str(data.job_id)} using refinement_status and update its canvas results.`)}>
        Refresh job status
      </button>}
      <button className="apexa-gallery-button"
        onClick={() => {
          const report = obj(obj(data.publication)?.report)
          sendMessage(report?.state === 'published' && str(report.path)
            ? `Open the saved powder refinement report in the canvas using open_science_view(kind="report", path=${JSON.stringify(report.path)}).`
            : `Generate the powder refinement report for job ${str(data.job_id)} with powder_report, then open it in the canvas.`)
        }}>
        {obj(obj(data.publication)?.report)?.state === 'published' ? 'Open saved report' : 'Generate report'}
      </button>
    </div>}

    {live.supported && <div style={{ marginTop: 8, fontSize: 11, color: 'var(--apexa-text-muted)' }} role="status">
      {live.error || (live.updated ? `Updated ${new Date(live.updated).toLocaleTimeString()}${data.terminal ? ' · job finished' : ' · refreshes automatically'}` : 'Connecting to recorded refinement evidence…')}
      {live.error && <button className="apexa-gallery-button" style={{ marginLeft: 8 }} onClick={live.refresh}>Retry</button>}
    </div>}
    {(str(data.output_dir) || str(obj(data.publication)?.output_dir)) && <p style={{ fontSize: 11, overflowWrap: 'anywhere' }}>
      Output folder: {str(obj(data.publication)?.output_dir) || str(data.output_dir)}
      {str(obj(data.publication)?.state) && ` · files ${str(obj(data.publication)?.state)}`}
      {str(obj(obj(data.publication)?.report)?.state) && ` · report ${str(obj(obj(data.publication)?.report)?.state)}`}
    </p>}

    {rows.length > 1 && <div style={{ marginTop: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(132px, 1fr))', gap: 8 }}>
        <Metric label="Completed jobs" value={`${counts.completed}/${rows.length}`} />
        <Metric label="Execution failures" value={String(counts.failed)} />
        <Metric label="Blocked by checks" value={String(counts.blocked)} />
        <Metric label="Provisional" value={String(counts.provisional)} />
      </div>
      <p style={{ fontSize: 11 }}>{counts.cancelled} cancelled · {counts.pending} pending · {counts.unknown} unknown · {counts.scienceUnavailable} completed with other / unavailable scientific status · {counts.missingResidual} completed without a finite residual</p>
      <label style={{ fontSize: 12 }}>Inspect a job{' '}
        <select value={selected?.id ?? ''} onChange={event => setSelectedId(event.target.value)}
          style={{ maxWidth: '100%', padding: 6, background: 'var(--apexa-surface)', color: 'var(--apexa-text)' }}>
          <option value="">Batch overview</option>
          {rows.map((row, i) => <option key={row.id} value={row.id}>{i + 1}. {row.label} ({row.state})</option>)}
        </select>
      </label>
    </div>}

    {completedSummary && selected && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(132px, 1fr))', gap: 8, marginTop: 14 }}>
      <Metric label="Rwp" value={residual(selected) === null ? '—' : `${residual(selected)!.toFixed(3)}%`} hint="Weighted profile residual; inspect scientific checks separately." />
      <Metric label="GOF" value={num(completedSummary.GOF) === null ? '—' : num(completedSummary.GOF)!.toFixed(3)} />
      <Metric label="Refinement steps" value={num(completedSummary.step_count ?? completedSummary.steps) === null ? '—' : String(completedSummary.step_count ?? completedSummary.steps)} />
      <Metric label="Stop reason" value={str(completedSummary.stop_reason || obj(completedSummary.stop_verdict)?.reason) || 'not reported'} />
      <Metric label="Elapsed" value={num(completedSummary.elapsed_seconds) === null ? '—' : `${num(completedSummary.elapsed_seconds)!.toFixed(1)} s`} />
    </div>}

    {completedSummary?.scientific_status === 'blocked' && <div role="status" style={{ marginTop: 12, padding: 12, borderRadius: 8, border: '1px solid #b84b48', fontSize: 12 }}>
      <b>Scientific checks blocked this result.</b>{' '}
      {Object.entries(obj(completedSummary.evidence_coverage) ?? {}).filter(([, v]) => obj(v)?.status === 'failed')
        .map(([k, v]) => `${k}: ${str(obj(v)?.reason) || list(obj(v)?.findings).join('; ') || 'failed'}`).join(' · ')}
    </div>}

    {list(completedSummary?.per_histogram_metrics).length > 0 && <div style={{ marginTop: 12, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 8 }}>
      {list(completedSummary?.per_histogram_metrics).map(obj).filter((h): h is Obj => !!h).map((h, i) =>
        <Metric key={i} label={str(h.name) || `Histogram ${i}`} value={`Rwp ${num(h.Rwp)?.toFixed(3) ?? '—'}%`} />)}
    </div>}

    {status === 'submitted' && str(data.job_id) && <div role="status" style={{ marginTop: 14, padding: 12, borderRadius: 8, background: 'var(--apexa-surface-2)', fontSize: 12 }}>
      Refinement was queued. Ask APEXA to check durable job <code>{str(data.job_id)}</code> for updated results.
      {jobIds.length > 0 && <div style={{ marginTop: 5 }}>Engine jobs: {jobIds.join(', ')}</div>}
    </div>}

    {selected && <RefinementDecisions key={selected.id} row={selected} location={location} />}

    {optimization && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 10px', fontSize: 14 }}>Numerical policy optimization</h3>
      <p style={{ fontSize: 12 }}>{str(optimization.status)} · {str(optimization.stop_reason)} · selected trial {String(optimization.selected_trial ?? 'none')}</p>
      <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead><tr>{['Trial', 'Proposal', 'State', 'Rwp', 'Score', 'Eligibility / error'].map(label => <th key={label} style={{ padding: 7, textAlign: 'left' }}>{label}</th>)}</tr></thead>
        <tbody>{optimizationTrials.map((trial, i) => <tr key={i}>
          <td style={{ padding: 7 }}>{String(trial.index)}</td>
          <td style={{ padding: 7 }}>{str(trial.proposal_method)}<details><summary>Policy</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(trial.policy, null, 2)}</pre></details></td>
          <td style={{ padding: 7 }}>{str(trial.state)}</td>
          <td style={{ padding: 7 }}>{num(trial.Rwp)?.toFixed(3) ?? '—'}</td>
          <td style={{ padding: 7 }}>{num(trial.selection_score)?.toFixed(2) ?? '—'}</td>
          <td style={{ padding: 7 }}>{str(trial.error) || str(trial.exclusion) || (trial.eligible ? 'eligible' : 'not established')}</td>
        </tr>)}</tbody>
      </table></div>
      <p style={{ fontSize: 11 }}>{str(optimization.score_scope)}</p>
    </section>}

    {rows.length > 0 && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 10px', fontSize: 14 }}>Patterns and job state</h3>
      <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead><tr>{['Observation / scan coordinate', 'Mode', 'Execution', 'Rwp', 'Scientific status', 'Evidence / errors'].map(x => <th key={x} style={{ padding: 7, textAlign: 'left', color: 'var(--apexa-text-muted)', borderBottom: '1px solid var(--apexa-border)' }}>{x}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}>
          <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{row.label}
            {row.axis !== undefined && <div style={{ marginTop: 2, color: 'var(--apexa-text-muted)' }}>{row.axis.toPrecision(6)} {row.axisUnits}</div>}
            {/^[0-9a-f]{24}$/.test(row.id) && <button className="apexa-gallery-button" style={{ marginLeft: 6 }}
              onClick={() => sendMessage(`Check powder job ${row.id} using refinement_status and update its canvas results.`)}>Refresh</button>}
          </td>
          <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{row.mode || '—'}</td>
          <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}><StatusBadge label="" value={executionState(row) === 'pending' ? row.state : executionState(row)} /></td>
          <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{residual(row) === null ? '—' : `${residual(row)!.toFixed(3)}%`}</td>
          <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{executionState(row) === 'completed' ? str(row.result?.scientific_status) || 'unavailable' : 'not assessed'}</td>
          <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{row.error && <div>{row.error}</div>}
            {row.result && <details><summary>Configured checks</summary><EvidenceChecks coverage={row.result.evidence_coverage} /></details>}
          </td>
        </tr>)}</tbody>
      </table></div>
    </section>}

    {pattern && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Profile residuals</h3>
      <p style={{ margin: '0 0 4px', fontSize: 11, color: 'var(--apexa-text-muted)' }}>Points include completed blocked and provisional jobs. Residual scales depend on instrument and uncertainty models; a low Rwp does not establish phase identity. Joint jobs may contain several histograms.</p>
      <div style={{ height: 320 }}><PlotlyChart data={pattern} /></div>
    </section>}

    {fractions && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Modeled phase fractions</h3>
      <p style={{ margin: '0 0 4px', fontSize: 11, color: 'var(--apexa-text-muted)' }}>Fractions are normalized over modeled crystalline phases; amorphous or missing phases are excluded.</p>
      <div style={{ height: 320 }}><PlotlyChart data={fractions} /></div>
    </section>}

    {completedSummary && rows.length === 1 && fractionGroups(completedSummary).some(g => Object.keys(g.values).length > 0) &&
      <div style={{ marginTop: 14, fontSize: 12 }}>
        <b>Modeled crystalline phases</b>
        {fractionGroups(completedSummary).map(group => <div key={group.label} style={{ marginTop: 4 }}>{group.label}: {' '}
          {Object.entries(group.values).filter(([, value]) => num(value) !== null).map(([name, value]) => `${name} ${(num(value)! * 100).toFixed(2)}%`).join(' · ')}
          {Object.keys(group.values).length === 1 && ' (one phase supplied; this does not establish phase purity)'}
        </div>)}
      </div>}

    {(images.length > 0 || evidencePanels.length > 0) && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Observed and calculated profiles</h3>
      <p style={{ margin: '0 0 10px', fontSize: 11, color: 'var(--apexa-text-muted)' }}>Fit overlays include calculated curves, difference, weighted residuals and phase reflection markers when the engine wrote them.</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 440px), 1fr))', gap: 10 }}>
        {images.map((image, index) => <ImagePanel key={`${image.path}-${index}`} path={image.path}
          title={`${image.label} · ${image.title}`} location={location} />)}
        {evidencePanels.map(panel => <ImagePanel key={str(panel.id)} path={str(panel.image_path)}
          title={`${str(panel.id)} · ${JSON.stringify(panel.range ?? [])}`} location={location} />)}
      </div>
    </section>}

    {completedSummary && images.length === 0 && evidencePanels.length === 0 && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Observed and calculated profiles</h3>
      <div role="status" style={{ padding: 12, borderRadius: 8, background: 'var(--apexa-surface-2)', fontSize: 12 }}>
        This result did not include a fit-plot image. Inspect the available metrics, decision history and evidence coverage.
      </div>
    </section>}

    {trace.length > 0 && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Agentic refinement decisions</h3>
      <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead><tr>{['Step', 'Ranked parameter', 'Requested release', 'Rwp before', 'Rwp after', 'Outcome', 'Reason'].map(x => <th key={x} style={{ padding: 7, textAlign: 'left', color: 'var(--apexa-text-muted)', borderBottom: '1px solid var(--apexa-border)' }}>{x}</th>)}</tr></thead>
        <tbody>{trace.map((step, index) => {
          const accepted = obj(step.accepted) ?? {}
          const param = str(step.param || accepted.raw_name || accepted.category || step.category) || '—'
          const before = num(step.Rwp_before) ?? num(step.rwp_before)
          const after = num(step.Rwp_after) ?? num(step.rwp_after)
          const rolledBack = Boolean(step.rolled_back)
          const outcome = rolledBack ? 'rolled back' : step.accepted === false ? 'rejected'
            : before !== null && after !== null ? `retained · ${after < before ? 'improved' : after > before ? 'worsened' : 'unchanged'}` : str(step.status) || 'recorded'
          return <tr key={index}>
            <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{index + 1}</td>
            <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{param}</td>
            <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{list(step.refined).map(String).join(', ') || 'unavailable'}</td>
            <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{before === null ? '—' : before.toFixed(3)}</td>
            <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{after === null ? '—' : after.toFixed(3)}</td>
            <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)', color: rolledBack || step.accepted === false ? '#b84b48' : 'var(--apexa-text)' }}>{outcome}</td>
            <td style={{ padding: 7, borderBottom: '1px solid var(--apexa-border)' }}>{str(step.reason) || '—'}</td>
          </tr>
        })}</tbody>
      </table></div>
    </section>}

    {(summary && (diagnostics.length > 0 || Array.isArray(summary.warnings) || Boolean(summary.trust) || Boolean(summary.qualification) || Boolean(summary.evidence_coverage))) && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Interpretation and qualifications</h3>
      {obj(summary?.trust) && <div style={{ marginBottom: 8, fontSize: 12 }}>
        Review: <b>{str(obj(summary?.trust)?.note) || 'Inspect the recorded checks'}</b>
        {list(obj(summary?.trust)?.flags).length > 0 && <ul>{list(obj(summary?.trust)?.flags).map((flag, i) => <li key={i}>{String(flag)}</li>)}</ul>}
      </div>}
      <EvidenceChecks coverage={summary?.evidence_coverage} />
      <p style={{ fontSize: 11 }}>Blocked means a configured check failed. Provisional may still include unavailable evidence. Neither status establishes scientific accuracy.</p>
      {diagnostics.length > 0 && <div style={{ fontSize: 12, marginBottom: 8 }}><b>Diagnostics</b><ul>{diagnostics.map((x, i) => <li key={i}>{obj(x) ? `${str(obj(x)?.what)} — ${str(obj(x)?.means)}` : String(x)}</li>)}</ul></div>}
      {Array.isArray(summary?.warnings) && list(summary?.warnings).length > 0 && <div style={{ fontSize: 12, marginBottom: 8 }}><b>Warnings</b><ul>{list(summary?.warnings).map((x, i) => <li key={i}>{String(x)}</li>)}</ul></div>}
      {Boolean(summary?.qualification) && <details><summary style={{ cursor: 'pointer', fontSize: 11 }}>Evidence coverage and qualification details</summary><pre style={{ whiteSpace: 'pre-wrap', fontSize: 10 }}>{JSON.stringify(summary?.qualification, null, 2)}</pre></details>}
      {(Boolean(summary?.effective_cells) || Boolean(summary?.cell_uncertainties)) && <details>
        <summary style={{ cursor: 'pointer', fontSize: 11 }}>Reference / effective cells and conditional uncertainties</summary>
        <p style={{ fontSize: 11 }}>HStrain can shift effective cells. Fixed or missing uncertainties do not represent measured zero uncertainty.</p>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: 10 }}>{JSON.stringify({ effective_cells: summary?.effective_cells, cell_uncertainties: summary?.cell_uncertainties }, null, 2)}</pre>
      </details>}
    </section>}

    {(proposals.length > 0 || trials.length > 0) && <section style={sectionStyle()}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Diagnostic proposals and trials</h3>
      <p style={{ margin: '0 0 10px', fontSize: 11, color: 'var(--apexa-text-muted)' }}>Trials preserve the baseline and leave a separate candidate. Acceptance requires lower combined loss over the cited window union; individual windows can worsen. Accepted candidates remain provisional.</p>
      <div style={{ display: 'grid', gap: 8 }}>
        {proposals.map((proposal, index) => <article key={index} style={{ padding: 10, border: '1px solid var(--apexa-border)', borderRadius: 8 }}>
          <b>{str(proposal.action) || 'Proposal'} · {str(proposal.rationale)}</b>
          {typeof proposal.predicted_improvement === 'string' && <p style={{ margin: '4px 0' }}>{proposal.predicted_improvement}</p>}
          <div style={{ color: 'var(--apexa-text-muted)', fontSize: 10 }}>Evidence: {list(proposal.evidence_ids).map(String).join(', ') || 'none cited'}</div>
        </article>)}
        {trials.map((trial, index) => <article key={`trial-${index}`} style={{ padding: 10, border: '1px solid var(--apexa-border)', borderRadius: 8 }}>
          <StatusBadge label="Decision" value={trial.accepted === true ? 'accepted' : trial.accepted === false ? 'rejected' : str(trial.status) || 'unavailable'} />
          {' '}<StatusBadge label="Candidate" value={trial.shadow === true ? 'shadow only' : 'not automatically adopted'} />
          {str(trial.scientific_status) && <> <StatusBadge label="Science" value={str(trial.scientific_status)} /></>}
          <span style={{ marginLeft: 10, fontSize: 11 }}>Rwp {String(trial.Rwp_before ?? '—')} → {String(trial.Rwp_after ?? '—')}</span>
          <div style={{ fontSize: 11, marginTop: 6 }}>Combined window loss: {num(trial.window_loss_before)?.toFixed(2) ?? '—'} → {num(trial.window_loss_after)?.toFixed(2) ?? '—'}</div>
          <EvidenceChecks coverage={trial.evidence_coverage} />
          {str(trial.error) && <p style={{ fontSize: 11 }}>{str(trial.error)}</p>}
          {list(trial.reasons).length > 0 && <ul style={{ fontSize: 11 }}>{list(trial.reasons).map((x, i) => <li key={i}>{String(x)}</li>)}</ul>}
        </article>)}
      </div>
    </section>}

    {!summary && !evidencePanels.length && !proposals.length && !trials.length && rows.length === 0 && <div role="status" style={{ marginTop: 16, padding: 12, background: 'var(--apexa-surface-2)', borderRadius: 8, fontSize: 12 }}>
      {str(data.error) || str(data.abstention_reason) || (status === 'waiting_for_completion' ? 'Waiting for the acquisition to finish writing this observation.' : str(data.job_id)
        ? 'No fit results yet. Ask APEXA to poll this job for results and evidence.'
        : 'No fit results or diagnostic proposals were returned.')}
    </div>}
  </div>
}
