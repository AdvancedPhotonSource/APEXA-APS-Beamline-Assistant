import type { ToolResult } from '@/api/types'
import { executionState, fractionGroups, list, num, outcomeCounts, residual, rowsFor, str } from '@/lib/refinement'

const numeric = (value: unknown): string => num(value)?.toFixed(3) ?? '—'

export function RefinementCard({ result }: { result: ToolResult }) {
  const data = result.data
  const rows = rowsFor(data)
  const counts = outcomeCounts(rows)
  const single = rows.length === 1 && executionState(rows[0]) === 'completed' ? rows[0] : undefined
  const direct = single?.result
  const groups = direct ? fractionGroups(direct) : []
  const tool = result.tool.split(':').pop() ?? result.tool
  const detail = Array.isArray(data.tutorials) ? `${data.tutorials.length} GSAS-II tutorials · open the canvas to explore`
    : Array.isArray(data.stages) ? `${data.stages.length} proposed refinement stages · inspect the plan in the canvas`
    : Array.isArray(data.methods) ? `${data.methods.length} installed-operation entries · capability coverage in the canvas`
    : typeof data.workflow_dir === 'string' ? `${list(data.operations).length} recorded operations · ${str(data.execution_status) || str(data.status)}`
    : typeof data.session_dir === 'string' ? 'Versioned refinement session · active result and history in the canvas'
    : tool === 'powder_evidence' ? `${list(data.panels).length} fit and residual panels`
    : tool === 'powder_advise' ? `${list(data.proposals).length} evidence-bound proposals`
    : tool === 'powder_trial' ? `Trial ${data.accepted === true ? 'accepted' : 'not accepted'} · ${data.shadow === true ? 'shadow candidate' : 'not automatically adopted'}`
    : rows.length ? `${counts.completed}/${rows.length} completed · ${counts.failed} failed · ${counts.cancelled} cancelled`
    : typeof data.job_id === 'string' ? `Job ${data.job_id}` : 'Powder refinement'

  return <div style={{ marginTop: 10, border: '1px solid rgba(59,130,246,.25)', borderRadius: 10, overflow: 'hidden', background: 'var(--apexa-surface)' }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '9px 12px', borderBottom: '1px solid var(--apexa-border)' }}>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase' }}>Agentic refinement</span>
      <span style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>{String(data.status ?? result.status)}</span>
    </div>
    <div style={{ padding: '9px 12px', fontSize: 12, color: 'var(--apexa-text)' }}>
      <div>{detail}</div>
      {str(data.error) && <div role="status" style={{ marginTop: 6 }}>{str(data.error)}</div>}
      {rows.length > 0 && <div style={{ marginTop: 6, fontSize: 11 }}>
        Completed results: {counts.blocked} blocked by checks · {counts.provisional} provisional
        {counts.scienceUnavailable > 0 && ` · ${counts.scienceUnavailable} other / unavailable`}
        {counts.missingResidual > 0 && ` · ${counts.missingResidual} without a finite residual`}
      </div>}
      {single && direct && <div style={{ display: 'flex', gap: 14, marginTop: 7, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 11 }}>
        <span>Rwp <b>{numeric(residual(single))}</b>%</span><span>GOF <b>{numeric(direct.GOF)}</b></span>
        {typeof direct.stop_reason === 'string' && <span>Stop: {direct.stop_reason}</span>}
      </div>}
      {groups.map(group => {
        const values = Object.entries(group.values).filter(([, value]) => num(value) !== null)
        return values.length > 0 && <div key={group.label} style={{ marginTop: 7, color: 'var(--apexa-text-2)', fontSize: 11 }}>
          {group.label}: {values.map(([name, value]) => `${name} ${(num(value)! * 100).toFixed(1)}%`).join(' · ')}
        </div>
      })}
      <div style={{ marginTop: 7, color: 'var(--apexa-text-muted)', fontSize: 10 }}>
        Completion and low Rwp do not establish scientific validity. Inspect fit evidence and missing checks in the canvas.
      </div>
    </div>
  </div>
}
