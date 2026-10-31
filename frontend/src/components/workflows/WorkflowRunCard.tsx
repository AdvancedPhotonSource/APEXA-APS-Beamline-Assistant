import { useState } from 'react'
import { boundedPreview } from '@/lib/boundedPreview'
import { canCancelWorkflow } from '@/api/workflows'
import type { WorkflowDecisionKind, WorkflowSnapshot } from '@/api/workflows'

function label(value: string) { return value.replaceAll('_', ' ') }

function StateBadge({ name, value }: { name: string; value: string }) {
  const color = ['failed', 'denied', 'rejected', 'blocked'].includes(value) ? '#f87171'
    : ['provisional', 'awaiting_review', 'uncertain', 'cancel_requested'].includes(value) ? '#fbbf24'
      : name === 'Science' && value === 'accepted' ? '#4ade80' : 'var(--apexa-text-2)'
  return <span className="rounded border px-1.5 py-0.5 text-[10px]" style={{ borderColor: 'var(--apexa-border)', color }}>
    {name}: {label(value)}
  </span>
}

export function WorkflowRunCard({ run, busy, uncertain, onDecide, onCancel, onRetry }: {
  run: WorkflowSnapshot
  busy: boolean
  uncertain: boolean
  onDecide: (decision: WorkflowDecisionKind, reason: string) => void
  onCancel: () => void
  onRetry: () => void
}) {
  const [reason, setReason] = useState('')
  const disabled = busy || uncertain
  return <article className="space-y-2 rounded-lg border p-3" aria-label={`Workflow: ${label(run.plan.recipe)}`}
    style={{ borderColor: 'var(--apexa-border)', background: 'var(--apexa-surface-2)' }}>
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <h4 className="text-xs font-semibold capitalize">{label(run.plan.recipe)}</h4>
        <p className="truncate text-[10px]" style={{ color: 'var(--apexa-text-muted)' }} title={run.workflow_id}>{run.workflow_id}</p>
      </div>
      <span className="shrink-0 text-[10px]" style={{ color: 'var(--apexa-text-muted)' }}>Revision {run.revision}</span>
    </div>
    <div className="flex flex-wrap gap-1" aria-label="Workflow outcomes">
      <StateBadge name="Workflow" value={run.orchestration_state} />
      <StateBadge name="Execution" value={run.job_execution} />
      <StateBadge name="Science" value={run.scientific_verdict} />
    </div>
    <p className="break-all text-[11px]">Dataset: {run.plan.dataset_id || 'Unspecified'}</p>
    {run.error && <p className="text-xs text-red-400">{run.error}</p>}
    <a className="inline-block text-[11px] underline" href={`/api/workflows/${encodeURIComponent(run.workflow_id)}`} target="_blank" rel="noreferrer">Open full plan and recorded state</a>
    <details className="text-xs" open={run.gate?.kind === 'plan_review'}>
      <summary className="cursor-pointer">Stages and evidence ({run.plan.steps.length})</summary>
      <ol className="mt-2 space-y-2">
        {run.plan.steps.map(step => {
          const stage = run.stages.find(item => item.id === step.id)
          return <li key={step.id} className="rounded border p-2" style={{ borderColor: 'var(--apexa-border)' }}>
            <div className="font-medium capitalize">{label(step.id)}{run.current_step === step.id ? ' · current stage' : ''}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              <StateBadge name="Step" value={stage?.state ?? 'unknown'} />
              <StateBadge name="Execution" value={stage?.outcome?.execution ?? 'unknown'} />
              <StateBadge name="Science" value={stage?.outcome?.scientific_verdict ?? 'unassessed'} />
            </div>
            <p className="mt-1 break-all" style={{ color: 'var(--apexa-text-muted)' }}>{step.tool}</p>
            <details className="mt-1"><summary className="cursor-pointer">Parameter preview</summary>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px]">{boundedPreview(step.arguments)}</pre>
              <p className="text-[10px]">Open the full plan above to inspect every value.</p>
            </details>
            {Object.entries(step.bindings).map(([argument, binding]) => <p key={argument} className="mt-1 break-all text-[11px]">
              {argument} uses {binding.step} → {binding.path.join('.')} after review.
            </p>)}
            {stage?.outcome?.error && <p className="mt-1 text-red-400">{stage.outcome.error}</p>}
            {stage?.run_id && <a className="mt-1 inline-block break-all text-[11px] underline" target="_blank" rel="noreferrer"
              href={`/api/runs/${encodeURIComponent(stage.run_id)}`}>Open recorded job evidence ({stage.run_id})</a>}
            {stage?.cancellation_status && stage.cancellation_status !== 'not_requested'
              && <p className="mt-1 text-[11px]">Job cancellation: {label(stage.cancellation_status)}</p>}
          </li>
        })}
      </ol>
    </details>
    {run.gate && <section className="space-y-2 rounded border p-2" aria-label="Workflow decision" style={{ borderColor: '#b45309' }}>
      <h5 className="text-xs font-semibold">{run.gate.recovery ? 'Review recovery' : run.gate.kind === 'plan_review' ? 'Review plan' : 'Review stage result'}</h5>
      <p className="whitespace-pre-wrap text-xs leading-relaxed">{run.gate.prompt}</p>
      <label className="block text-[11px]">Decision note (optional)
        <textarea value={reason} onChange={event => setReason(event.target.value)} rows={2} maxLength={2000} disabled={disabled}
          className="mt-1 w-full rounded border p-1.5 text-xs" style={{ borderColor: 'var(--apexa-border)', background: 'var(--apexa-surface)' }} />
      </label>
      <div className="flex flex-wrap gap-2">
        {run.gate.decisions.includes('approve') && <button className="apexa-gallery-button" disabled={disabled} onClick={() => onDecide('approve', reason.trim())}>Approve &amp; continue</button>}
        {run.gate.decisions.includes('reject') && <button className="apexa-gallery-button" disabled={disabled} onClick={() => onDecide('reject', reason.trim())}>{run.gate.kind === 'plan_review' ? 'Reject plan' : 'Stop workflow'}</button>}
      </div>
      {!run.gate.decisions.includes('approve') && <p className="text-[11px]">Continuation is unavailable for this recorded result.</p>}
      {run.gate.kind === 'result_review' && <p className="text-[10px]">Continuing or stopping changes workflow progression. The scientific verdict comes from the recorded evidence.</p>}
    </section>}
    {uncertain && <div className="space-y-1" role="status">
      <p className="text-xs">The previous action has no confirmed response. Refresh or retry the same request.</p>
      <button className="apexa-gallery-button" disabled={busy} onClick={onRetry}>Retry same request</button>
    </div>}
    {(canCancelWorkflow(run) || run.orchestration_state === 'cancel_requested') && <div className="space-y-1 border-t pt-2" style={{ borderColor: 'var(--apexa-border)' }}>
      {canCancelWorkflow(run) && <button className="apexa-gallery-button" disabled={disabled} onClick={onCancel}>Request cancellation</button>}
      <p className="text-[10px] leading-relaxed" style={{ color: 'var(--apexa-text-muted)' }}>External jobs may still be active until their recorded status confirms they stopped.</p>
    </div>}
  </article>
}
