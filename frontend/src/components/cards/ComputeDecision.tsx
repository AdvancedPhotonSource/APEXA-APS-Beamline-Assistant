import type { ToolResult } from '@/api/types'
import { computeDecision } from '@/lib/computeDecision'

export function ComputeDecision({ result }: { result: ToolResult }) {
  const decision = computeDecision(result)
  if (!decision) return null
  return (
    <section aria-label="Compute plan" className="my-2 rounded-lg border border-[var(--apexa-border)] bg-[var(--apexa-surface)] p-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h3 className="font-semibold text-[var(--apexa-text)]">Recorded compute plan</h3>
        {decision.notice && <span className="text-[var(--apexa-text-2)]">{decision.notice}</span>}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        {decision.fields.map(field => <div key={field.label} className="min-w-0">
          <dt className="text-[var(--apexa-text-muted)]">{field.label}</dt>
          <dd className="text-[var(--apexa-text)] font-mono break-words">{field.value}</dd>
        </div>)}
      </dl>
      {decision.reason && <p className="mt-2 text-[var(--apexa-text-2)] break-words">{decision.reason}</p>}
      {(decision.resources || decision.scope) && <details className="mt-2 text-[var(--apexa-text-muted)]">
        <summary className="cursor-pointer">Inventory and reservation scope</summary>
        {decision.resources && <p className="mt-1 break-words">{decision.resources}</p>}
        {decision.scope && <p className="mt-1">{decision.scope}</p>}
      </details>}
    </section>
  )
}
