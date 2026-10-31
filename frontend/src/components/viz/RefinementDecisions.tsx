import { useEffect, useState } from 'react'
import { PlotlyChart } from './PlotlyChart'
import { executionState, list, num, obj, str } from '@/lib/refinement'
import type { Obj, RefinementRow } from '@/lib/refinement'

const objects = (v: unknown): Obj[] => list(v).map(obj).filter((x): x is Obj => !!x)
const number = (v: unknown, digits = 3) => num(v)?.toFixed(digits) ?? '—'
const cell = { padding: '7px 8px', textAlign: 'left' as const, borderBottom: '1px solid var(--apexa-border)', verticalAlign: 'top' as const }
const box = { border: '1px solid var(--apexa-border)', borderRadius: 9, padding: 12, minWidth: 0 }
const categoryName = (v: unknown) => ({
  instprm: 'Instrument profile', sample_disp: 'Sample displacement', hstrain: 'Histogram lattice offsets',
  lattice: 'Unit cell', cell: 'Unit cell', background: 'Background', bkg: 'Background',
  scale: 'Intensity scale', atom_xyz: 'Atomic positions', atom_uiso: 'Atomic displacement',
  size: 'Crystallite size', strain: 'Microstrain', wavelength: 'Wavelength',
} as Record<string, string>)[str(v)] || str(v).replaceAll('_', ' ') || 'Comparing candidates'
const delta = (before: unknown, after: unknown) => {
  const b = num(before), a = num(after)
  return a === null || b === null ? '—' : `${a > b ? '+' : ''}${(a - b).toFixed(3)} pp`
}

function outcome(step: Obj) {
  if (step.rolled_back === true) return 'Rolled back'
  const before = num(step.Rwp_before), after = num(step.Rwp_after)
  if (before === null || after === null) return 'Recorded; outcome unavailable'
  return after < before ? 'Retained · residual improved' : after > before ? 'Retained · residual worsened' : 'Retained · residual unchanged'
}

function useEvidence(jobId: string, location: string, snapshotId: string) {
  const [value, setValue] = useState<{ id: string; data?: Obj; error?: string }>({ id: '' })
  useEffect(() => {
    if (!snapshotId) return
    const controller = new AbortController()
    let active = true
    const timeout = setTimeout(() => controller.abort(), 15000)
    void fetch(`/${location === 'remote' ? 'remote' : 'local'}/api/science/refinement/evidence?job_id=${encodeURIComponent(jobId)}&snapshot=${encodeURIComponent(snapshotId)}`,
      { cache: 'no-store', signal: controller.signal }).then(async response => {
      const data = obj(await response.json())
      if (!response.ok || !data) throw new Error(str(data?.detail) || 'Recorded fit snapshot could not be loaded')
      if (active) setValue({ id: snapshotId, data })
    }).catch(e => { if (active) setValue({ id: snapshotId, error: String(e) }) })
      .finally(() => clearTimeout(timeout))
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [jobId, location, snapshotId])
  return value.id === snapshotId ? value : { id: snapshotId }
}

function profileFigure(histogram: Obj, window?: Obj, comparison?: Obj) {
  const p = obj(window?.profile) ?? obj(histogram.profile) ?? {}
  const traces: Obj[] = [
    { x: p.x, y: p.observed, name: 'Observed', type: 'scatter', mode: 'lines', line: { color: '#63758c', width: 1 } },
    { x: p.x, y: p.calculated, name: comparison ? 'Calculated before' : 'Calculated', type: 'scatter', mode: 'lines', line: { color: '#3679d3', width: 1.4 } },
    { x: p.x, y: p.weighted_residual, name: comparison ? 'Residual before' : 'Weighted residual', type: 'scatter', mode: 'lines', yaxis: 'y2', line: { color: '#ae50a4', width: 1 } },
  ]
  if (comparison) {
    const after = obj(comparison.profile) ?? {}
    traces.push({ x: after.x, y: after.calculated, name: 'Calculated trial', type: 'scatter', mode: 'lines', line: { color: '#d18528', width: 1.4 } },
      { x: after.x, y: after.weighted_residual, name: 'Residual trial', type: 'scatter', mode: 'lines', yaxis: 'y2', line: { color: '#198971', width: 1 } })
  } else if (list(p.background).length) traces.push({ x: p.x, y: p.background, name: 'Background', type: 'scatter', mode: 'lines', line: { color: '#b19a71', width: 1 } })
  const reflections = objects(window?.reflections ?? histogram.reflections)
  if (reflections.length) traces.push({ x: reflections.map(r => r.position ?? r.native_position), y: reflections.map(() => 0),
    text: reflections.map(r => `${str(r.phase)} (${list(r.hkl).join(' ')})`), name: 'Reflections', type: 'scatter', mode: 'markers', yaxis: 'y2',
    marker: { symbol: 'line-ns', size: 8, color: '#8e8b72' }, hovertemplate: '%{text}<br>%{x}<extra></extra>' })
  return { data: traces, layout: { margin: { t: 15, r: 20, b: 65, l: 65 },
    xaxis: { title: { text: str(histogram.axis) }, anchor: 'y2' },
    yaxis: { title: { text: 'Intensity' }, domain: [0.36, 1] },
    yaxis2: { title: { text: 'Weighted residual' }, domain: [0, 0.23], zeroline: true },
    legend: { orientation: 'h', y: -0.2 },
    shapes: window ? [] : objects(histogram.windows).slice(0, 6).map(w => ({ type: 'rect', xref: 'x', yref: 'paper',
      x0: list(w.range)[0], x1: list(w.range)[1], y0: 0, y1: 1, fillcolor: 'rgba(211,133,40,.06)', line: { width: 0 } })) } }
}

export function RefinementDecisions({ row, location }: { row: RefinementRow; location: string }) {
  const liveEvents = objects(row.decision?.events)
  const savedSteps: Obj[] = objects(row.result?.history).map((s, i) => ({ ...s, event: 'step_result', driver_step: i + 1 }))
  const events: Obj[] = liveEvents.length ? liveEvents : savedSteps.length ? savedSteps
    : num(row.progress?.driver_step) !== null ? [{ ...row.progress, event: 'step_result' }] : []
  const steps = events.filter(e => e.event === 'step_result')
  const latestStep = Math.max(0, ...events.map(e => num(e.driver_step) ?? 0))
  const [chosenStep, setChosenStep] = useState<number | null>(null)
  const currentStep = chosenStep ?? latestStep
  const current = events.filter(e => e.driver_step === currentStep)
  const step = current.find(e => e.event === 'step_result')
  const trial = [...current].reverse().find(e => e.event === 'trial_result')
  const ranking = current.find(e => e.event === 'candidate_ranking')
  const selected = [...current].reverse().find(e => e.event === 'candidate_selected')
  const detail = trial ?? step ?? selected ?? {}
  const checks = obj(detail.checks) ?? {}
  const exclusions = objects([...current].reverse().find(e => e.event === 'recipe_exclusions')?.withheld
    ?? objects(row.result?.candidate_log).find(c => c.step_idx === currentStep - 1)?.withheld)
  const recipe = obj(row.result?.recipe) ?? obj(events.find(e => e.event === 'recipe')?.recipe)
  const candidates = objects(ranking?.candidates ?? objects(row.result?.candidate_log).find(c => c.step_idx === currentStep - 1)?.ranked)
  const beforeId = str(step?.before_snapshot) || str(ranking?.snapshot)
  const afterId = str(step?.trial_snapshot) || str(trial?.snapshot)
  const shownId = beforeId || (chosenStep === null ? str(row.decision?.snapshot) || str(row.inspection?.snapshot) : '')
  const before = useEvidence(row.id, location, shownId)
  const after = useEvidence(row.id, location, afterId)
  const histograms = objects(before.data?.histograms)
  const [histIndex, setHistIndex] = useState(0)
  const histogram = histograms.find(h => h.index === histIndex) ?? histograms[0]
  const afterHistogram = objects(after.data?.histograms).find(h => h.index === histogram?.index)
  const [windowId, setWindowId] = useState('')
  const windows = objects(histogram?.windows)
  const window = windows.find(w => w.id === windowId) ?? windows[0]
  const afterWindow = objects(afterHistogram?.comparison_windows).find(w => w.id === window?.id)
  const running = executionState(row) === 'pending'
  // The overview follows the kept fit independently of the step being inspected.
  // A trial snapshot must not become the current fit before its disposition.
  const lastStep = steps.at(-1)
  const finalEvent = [...events].reverse().find(e => e.event === 'finished')
  const firstRanking = events.find(e => e.event === 'candidate_ranking')
  const retainedId = str(finalEvent?.snapshot) || str(lastStep?.retained_snapshot) || str(firstRanking?.snapshot) || str(row.inspection?.snapshot)
  const retained = useEvidence(row.id, location, retainedId)
  const initial = useEvidence(row.id, location, str(firstRanking?.snapshot))
  const retainedHistograms = objects(retained.data?.histograms)
  const initialHistograms = objects(initial.data?.histograms)
  const completed = executionState(row) === 'completed'
  const currentRwp = (completed ? num(row.result?.Rwp) : null) ?? num(retained.data?.Rwp)
    ?? num(lastStep?.rolled_back ? lastStep?.Rwp_before : lastStep?.Rwp_after)
  const initialRwp = num(initial.data?.Rwp) ?? num(steps[0]?.Rwp_before)
  const metrics: Obj[] = completed && objects(row.result?.per_histogram_metrics).length
    ? objects(row.result?.per_histogram_metrics) : retainedHistograms.map(h => ({ ...obj(h.metrics), index: h.index, name: h.name }))
  const kept = steps.filter(s => s.rolled_back !== true)
  const retainedValues = steps.map(s => ({ step: s.driver_step, value: num(s.rolled_back ? s.Rwp_before : s.Rwp_after) }))
    .filter((s): s is { step: unknown; value: number } => s.value !== null)
  const best = [...(initialRwp === null ? [] : [{ step: 'start', value: initialRwp }]), ...retainedValues]
    .sort((a, b) => a.value - b.value)[0]
  const worsened = kept.filter(s => num(s.Rwp_before) !== null && num(s.Rwp_after) !== null && num(s.Rwp_after)! > num(s.Rwp_before)!)
  const latestEvent = obj(row.decision?.latest) ?? {}
  const trend = { data: [
    { x: [0, ...steps.map(s => s.driver_step)], y: [initialRwp, ...steps.map(s => s.rolled_back ? s.Rwp_before : s.Rwp_after)],
      name: 'Retained fit', type: 'scatter', mode: 'lines+markers', line: { color: '#3679d3' } },
    { x: steps.filter(s => s.rolled_back).map(s => s.driver_step), y: steps.filter(s => s.rolled_back).map(s => s.Rwp_after),
      name: 'Rolled-back trial', type: 'scatter', mode: 'markers', marker: { color: '#bf5347', symbol: 'x', size: 9 } },
  ], layout: { margin: { t: 15, r: 20, b: 55, l: 60 }, xaxis: { title: { text: 'Decision step' }, dtick: 1 },
    yaxis: { title: { text: 'Combined Rwp (%)' } }, legend: { orientation: 'h', y: -0.25 } } }

  return <section style={{ marginTop: 18, paddingTop: 15, borderTop: '1px solid var(--apexa-border)' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
      <h3 style={{ margin: 0, fontSize: 16 }}>Refinement overview</h3>
    </div>
    <div style={{ ...box, marginTop: 12, background: 'var(--apexa-surface)' }}>
      <b style={{ fontSize: 13 }}>{running ? `Now · step ${latestStep || '—'}` : 'Run finished'}</b>
      <p style={{ fontSize: 12, margin: '6px 0 12px' }}>{running
        ? latestEvent.event === 'evaluating' ? 'Comparing which parameters could improve the fit.'
          : latestEvent.event === 'finished' ? 'Refinement decisions finished; preparing final results.'
          : str(latestEvent.stage) || `${categoryName(latestEvent.category)} · ${str(latestEvent.event).replaceAll('_', ' ') || 'waiting for recorded evidence'}`
        : `Stop reason: ${str(row.result?.stop_reason) || row.state}. Scientific status: ${str(row.result?.scientific_status) || 'unavailable'}.`}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 12, fontSize: 12 }}>
        <div>{completed ? 'Final joint Rwp' : 'Latest kept joint Rwp'}<br /><b style={{ fontSize: 19 }}>{number(currentRwp)}%</b></div>
        <div>Change from start<br /><b>{number(initialRwp)}% → {number(currentRwp)}%</b><br />{delta(initialRwp, currentRwp)} · lower is better</div>
        <div>Best recorded kept fit<br /><b>{number(best?.value)}%{best && ` · ${best.step === 'start' ? 'start' : `step ${String(best.step)}`}`}</b>
          {best && currentRwp !== null && currentRwp > best.value && <div style={{ color: '#b84b48', marginTop: 3 }}>Current fit is {delta(best.value, currentRwp)} above this value</div>}</div>
        <div>Decisions so far<br /><b>{steps.length} finished · {kept.length} kept · {steps.length - kept.length} rolled back</b>
          {worsened.length > 0 && <div style={{ color: '#b84b48', marginTop: 3 }}>{worsened.length} kept despite a higher residual</div>}</div>
      </div>
      {metrics.length > 0 && <div style={{ overflowX: 'auto', marginTop: 12 }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead><tr>{['Dataset', 'Starting Rwp', completed ? 'Final Rwp' : 'Latest kept Rwp', 'Change'].map(label => <th key={label} style={cell}>{label}</th>)}</tr></thead>
        <tbody>{metrics.map((h, i) => {
          const start = obj(initialHistograms.find(v => v.index === h.index)?.metrics)?.Rwp
          return <tr key={String(h.index ?? i)}><td style={cell}>{str(h.name) || `Histogram ${i}`}</td><td style={cell}>{number(start)}%</td>
            <td style={cell}>{number(h.Rwp)}%</td><td style={cell}>{delta(start, h.Rwp)}</td></tr>
        })}</tbody>
      </table></div>}
      <p style={{ fontSize: 11, marginBottom: 0, color: 'var(--apexa-text-muted)' }}>Rwp measures profile agreement. Scientific checks determine which conclusions are supported.
        {' '}Starting values refer to the first recorded decision baseline.
        {running && row.heartbeatAge !== undefined && ` Worker heartbeat: ${row.heartbeatAge.toFixed(0)} s ago.`}</p>
    </div>
    {steps.length > 0 && <div style={{ marginTop: 14 }}>
      <h4 style={{ margin: '0 0 6px' }}>Is the fit improving?</h4>
      <div style={{ height: 250 }}><PlotlyChart data={trend} /></div>
      <h4 style={{ margin: '12px 0 6px' }}>What changed during this run?</h4>
      <p style={{ fontSize: 11 }}>All recorded decisions stay here. Select a step to inspect its fit and peak evidence.</p>
      <div style={{ overflow: 'auto', maxHeight: 330 }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead><tr>{['Step', 'Change tried', 'Joint Rwp before → trial', 'Decision / reason'].map(label => <th key={label} style={cell}>{label}</th>)}</tr></thead>
        <tbody>{steps.map((s, i) => {
          const related = events.filter(e => e.driver_step === s.driver_step)
          const t = [...related].reverse().find(e => e.event === 'trial_result') ?? s
          return <tr key={`${s.driver_step}-${i}`} style={{ background: chosenStep === s.driver_step ? 'var(--apexa-surface-2)' : undefined }}>
            <td style={cell}><button className="apexa-gallery-button" onClick={() => { setChosenStep(num(s.driver_step)); setWindowId('') }}>Step {String(s.driver_step)}</button></td>
            <td style={cell}>{categoryName(t.category ?? s.category)}<div style={{ fontSize: 11 }}>{list(t.refined).map(String).join(', ') || 'Release not recorded'}</div></td>
            <td style={cell}>{number(s.Rwp_before)}% → {number(s.Rwp_after)}%</td>
            <td style={cell}>{outcome(s)}{str(t.reason || s.reason) && <div style={{ fontSize: 11 }}>{str(t.reason || s.reason)}</div>}</td>
          </tr>
        })}
        {running && !steps.some(s => s.driver_step === latestStep) && <tr><td style={cell}>Step {latestStep}</td><td style={cell} colSpan={3}>In progress · {str(latestEvent.stage) || 'evaluating the next change'}</td></tr>}
        </tbody>
      </table></div>
    </div>}
    {retainedHistograms.some(h => objects(h.windows).length > 0) && <div style={{ marginTop: 16 }}>
      <h4 style={{ margin: '0 0 6px' }}>Which regions still disagree?</h4>
      <p style={{ fontSize: 11 }}>Largest recorded residual regions in the latest kept snapshot{num(retained.data?.timestamp) !== null && ` · ${new Date(num(retained.data?.timestamp)! * 1000).toLocaleTimeString()}`}.
        {' '}Regions can overlap. These are diagnostic priorities; they do not establish a cause.</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 250px), 1fr))', gap: 10 }}>
        {retainedHistograms.map(h => <div key={String(h.index)} style={{ ...box, fontSize: 12 }}><b>{str(h.name)}</b>
          {objects(h.windows).slice(0, 2).map(w => <p key={str(w.id)} style={{ margin: '7px 0 0' }}>
            {list(w.range).map(v => number(v, 2)).join('–')} {str(h.axis)} · {number(num(w.loss_fraction) === null ? null : num(w.loss_fraction)! * 100, 1)}% of this dataset’s weighted loss
          </p>)}
        </div>)}
      </div>
    </div>}
    {retained.error && <p role="status" style={{ fontSize: 12 }}>Latest kept fit evidence: {retained.error}</p>}
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginTop: 24, paddingTop: 14, borderTop: '1px solid var(--apexa-border)' }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>Inspect a decision</h3>
      {latestStep > 0 && <label style={{ fontSize: 12 }}>Inspect step{' '}
        <select value={chosenStep ?? 'latest'} onChange={e => { setChosenStep(e.target.value === 'latest' ? null : Number(e.target.value)); setWindowId('') }}>
          <option value="latest">{running ? 'Follow latest' : 'Latest decision'}</option>
          {[...new Set(events.map(e => num(e.driver_step)).filter((n): n is number => n !== null && n > 0))].map(n => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>}
    </div>
    {chosenStep !== null && <p style={{ fontSize: 12 }}>Inspecting step {chosenStep}. The overview above continues to follow the latest kept fit.{' '}
      <button className="apexa-gallery-button" onClick={() => { setChosenStep(null); setWindowId('') }}>Follow latest</button></p>}
    {!liveEvents.length && <p role="status" style={{ fontSize: 12, color: 'var(--apexa-text-muted)' }}>
      {running ? 'No detailed decision evidence has arrived yet. Available progress is shown below.'
        : 'This run did not record live peak snapshots or candidate rankings. Available saved decisions and final fit plots are shown; before/after peak evidence is unavailable.'}
    </p>}
    {currentStep > 0 && <div style={{ ...box, marginTop: 12, background: 'var(--apexa-surface)' }}>
      <b>Step {currentStep} · {str(detail.category) || 'Assessing candidates'}</b>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginTop: 10, fontSize: 12 }}>
        <div>Ranked parameter<br /><b>{str(detail.picked || detail.param) || '—'}</b></div>
        <div>{Array.isArray(checks.newly_varied) ? 'New independent variables' : 'Requested release'}<br /><b>{list(detail.refined).map(String).join(', ') || 'Not yet recorded'}</b></div>
        <div>Combined Rwp<br /><b>{number(detail.Rwp_before)} → {number(detail.Rwp_after)}%</b></div>
        <div>Decision<br /><b>{step ? outcome(step) : trial ? 'Trial evaluated; disposition pending' : 'Considering candidates'}</b></div>
      </div>
      <p style={{ fontSize: 12, marginBottom: 0 }}>{str(detail.reason) || (step ? 'No rollback reason recorded; retention does not establish scientific validity.' : '')}</p>
      {obj(checks.covariance) && <p style={{ fontSize: 12 }}>Covariance: <b>{str(obj(checks.covariance)?.status)}</b> · Convergence: <b>{str(obj(checks.convergence)?.status)}</b></p>}
      {str(checks.diagnostic_action) && <p style={{ fontSize: 12 }}>{str(checks.diagnostic_action)}</p>}
      {after.data && <details style={{ fontSize: 11, marginTop: 8 }}><summary>Parameters actually varying in the trial fit</summary>
        {list(after.data.varying_parameters).map(String).join(', ') || 'Unavailable'}
      </details>}
    </div>}
    {(before.error || after.error) && <p role="alert" style={{ fontSize: 12 }}>{before.error || after.error}</p>}
    {shownId && !before.data && !before.error && <p role="status">Loading recorded fit evidence…</p>}
    {histograms.length > 0 && <div style={{ marginTop: 16 }}>
      <label style={{ fontSize: 12 }}>Histogram{' '}<select value={String(histogram?.index)} onChange={e => { setHistIndex(Number(e.target.value)); setWindowId('') }} style={{ maxWidth: '100%' }}>
        {histograms.map(h => <option key={String(h.index)} value={String(h.index)}>{str(h.name) || `Histogram ${h.index}`} · Rwp {number(obj(h.metrics)?.Rwp)}%</option>)}
      </select></label>
      {after.data && <div style={{ overflowX: 'auto', marginTop: 10 }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead><tr>{['Histogram', 'Rwp before', 'Rwp trial', 'Change'].map(s => <th key={s} style={cell}>{s}</th>)}</tr></thead>
        <tbody>{histograms.map(h => {
          const b = num(obj(h.metrics)?.Rwp)
          const a = num(obj(objects(after.data?.histograms).find(v => v.index === h.index)?.metrics)?.Rwp)
          return <tr key={String(h.index)}><td style={cell}>{str(h.name)}</td><td style={cell}>{number(b)}%</td><td style={cell}>{number(a)}%</td>
            <td style={cell}>{a === null || b === null ? 'unavailable' : a > b ? 'worsened' : a < b ? 'improved' : 'unchanged'}</td></tr>
        })}</tbody>
      </table></div>}
      {histogram?.error ? <p role="status">{str(histogram.error)}</p> : histogram && <>
        <p style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>{shownId === 'inspection-final' ? 'Final-fit inspection generated after completion; not historical decision evidence' : beforeId ? 'Recorded before the selected decision' : 'Latest recorded driver snapshot'} · {String(histogram.points)} fitted points · {String(histogram.display_points)} overview points.
          {' '}{obj(histogram.classifier)?.used_for_priority_bias ? 'Residual-shape scores from this histogram influenced candidate ranking.' : 'This histogram is shown for diagnosis; its residual-shape scores did not influence candidate ranking.'}</p>
        <div style={{ height: 390 }}><PlotlyChart data={profileFigure(histogram)} /></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 350px), 1fr))', gap: 12, marginTop: 12 }}>
          <div style={box}>
            <h4 style={{ margin: '0 0 8px' }}>Peak and residual regions</h4>
            <div style={{ maxHeight: 300, overflow: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead><tr>{['Window', 'Residual shape', 'Fit loss', 'Used in ranking'].map(s => <th key={s} style={cell}>{s}</th>)}</tr></thead>
              <tbody>{windows.map(w => <tr key={str(w.id)} style={{ background: w.id === window?.id ? 'var(--apexa-surface-2)' : undefined }}>
                <td style={cell}><button className="apexa-gallery-button" onClick={() => setWindowId(str(w.id))}>{list(w.range).map(v => number(v, 2)).join('–')}</button></td>
                <td style={cell}>{str(w.category) || (w.source === 'residual_maximum' ? 'Residual maximum' : 'No clear signature')}</td>
                <td style={cell}>{number((num(w.loss_fraction) ?? 0) * 100, 1)}%</td>
                <td style={cell}>{w.used_for_priority_bias ? 'Yes' : 'No'}</td>
              </tr>)}</tbody></table></div>
            <p style={{ fontSize: 11 }}>Shape labels are heuristic hypotheses. Windows can overlap; their loss percentages must not be summed.</p>
          </div>
          <div style={box}>
            <h4 style={{ margin: '0 0 8px' }}>{shownId === 'inspection-final' ? 'Selected region · final fit' : 'Selected region · before and trial'}</h4>
            {window && <><div style={{ height: 350 }}><PlotlyChart data={profileFigure(histogram, window, afterWindow)} /></div>
              <p style={{ fontSize: 11 }}>Window weighted loss: {number(window.weighted_loss, 1)}{afterWindow && ` → ${number(afterWindow.weighted_loss, 1)}`}.
                {step?.rolled_back === true && ' The trial was rolled back.'}
                {!afterWindow && ' A matching trial window is not available.'}</p></>}
          </div>
        </div>
      </>}
    </div>}
    {recipe && <details style={{ marginTop: 14 }}><summary>Scientific recipe and assumptions</summary>
      <p style={{ fontSize: 12 }}>{str(recipe.selection)}. {str(recipe.acceptance)}.</p>
      {objects(recipe.experiments).map((e, i) => <p key={i} style={{ fontSize: 12 }}>
        Histogram {String(e.histogram)}: {str(obj(e.context)?.radiation)} · {str(obj(e.context)?.geometry)} · profile {obj(e.context)?.instrument_profile_known ? 'declared calibrated; held' : 'calibration unverified'}.
      </p>)}
      {Object.entries(obj(obj(recipe.policy)?.justifications) ?? {}).map(([key, value]) => <p key={key} style={{ fontSize: 12 }}><b>{categoryName(key)}</b>: {String(value)}</p>)}
      {str(obj(recipe.policy)?.shared_structure_justification) && <p style={{ fontSize: 12 }}>Shared structure: {str(obj(recipe.policy)?.shared_structure_justification)}</p>}
      <p style={{ fontSize: 12 }}>Requires expert input: {list(recipe.expert_branches).map(String).join('; ')}.</p>
      <p style={{ fontSize: 11 }}>{str(recipe.validation)}</p>
    </details>}
    {exclusions.length > 0 && <details style={{ marginTop: 14 }}><summary>Parameters withheld by the scientific recipe ({exclusions.length})</summary>
      <ul style={{ fontSize: 12 }}>{exclusions.map((e, i) => <li key={i}><b>{str(e.name)}</b>: {str(e.reason)}</li>)}</ul>
    </details>}
    {candidates.length > 0 && <details style={{ marginTop: 16 }}><summary style={{ fontSize: 14, fontWeight: 600 }}>Why this parameter was considered</summary>
      <p style={{ fontSize: 11 }}>{str(ranking?.ranking_rule) || 'Recorded ranking scores'}. These scores are not probabilities or predicted percentage improvements.</p>
      {num(ranking?.candidate_count) !== null && <p style={{ fontSize: 11 }}>Showing {candidates.length} of {String(ranking?.candidate_count)} eligible candidates.</p>}
      <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead><tr>{['Rank', 'Parameter', 'Category', 'Sensitivity', 'Bias', 'Sign bonus', 'Saturation', 'Score'].map(s => <th style={cell} key={s}>{s}</th>)}</tr></thead>
        <tbody>{candidates.map((c, i) => <tr key={i} style={{ background: c.name === detail.picked ? 'var(--apexa-surface-2)' : undefined }}>
          {[c.rank, c.name, c.category, number(c.leverage), number(c.priority_bias), number(c.sign_bonus, 0), number(c.saturation), number(c.score)].map((v, j) => <td style={cell} key={j}>{String(v ?? '—')}</td>)}
        </tr>)}</tbody>
      </table></div>
    </details>}
    {current.some(e => e.event === 'candidate_skipped') && <details style={{ marginTop: 12 }}><summary>Parameters blocked or skipped</summary>
      <ul style={{ fontSize: 12 }}>{current.filter(e => e.event === 'candidate_skipped').map((e, i) => <li key={i}><b>{str(e.picked)}</b>: {str(e.reason)}</li>)}</ul>
    </details>}
    {objects(ranking?.excluded).length > 0 && <details style={{ marginTop: 10 }}><summary>Previously excluded or already enabled parameters</summary>
      <ul style={{ fontSize: 12 }}>{objects(ranking?.excluded).map((e, i) => <li key={i}><b>{str(e.name)}</b>: {str(e.reason)}</li>)}</ul>
    </details>}
  </section>
}
