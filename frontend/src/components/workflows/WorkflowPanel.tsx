import { useState } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { WORKFLOW_OPTIONS, workflowPrompt } from '@/lib/workflowCatalog'
import { ActiveWorkflows } from './ActiveWorkflows'

export function WorkflowPanel() {
  const sendMessage = useChatStore((s) => s.sendMessage)
  const [search, setSearch] = useState('')
  const matching = WORKFLOW_OPTIONS.filter(option =>
    `${option.name} ${option.description} ${option.group}`.toLowerCase().includes(search.toLowerCase()))
  const groups = [...new Set(matching.map(option => option.group))]

  return (
    <div className="p-3 space-y-4 overflow-y-auto h-full">
      <ActiveWorkflows />
      <p className="text-xs leading-relaxed" style={{ color: 'var(--apexa-text-muted)' }}>
        Choose an inspection or prepare an analysis plan. APEXA checks your inputs
        and executor availability before proposing a run.
      </p>
      <input type="search" aria-label="Find an analysis" placeholder="Find a technique or task…"
        value={search} onChange={e => setSearch(e.target.value)}
        className="w-full rounded-lg border px-3 py-2 text-xs"
        style={{ borderColor: 'var(--apexa-border)', background: 'var(--apexa-surface-2)' }} />
      {groups.map(group => (
        <section key={group} className="space-y-2">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: 'var(--apexa-text-muted)' }}>{group}</h3>
          {matching.filter(option => option.group === group).map(option => (
            <button key={option.id} type="button" onClick={() => sendMessage(workflowPrompt(option))}
              className="apexa-workflow-option w-full text-left p-3 rounded-lg border transition-colors"
              style={{ borderColor: 'var(--apexa-border)', background: 'var(--apexa-surface-2)' }}>
              <div className="text-sm font-medium" style={{ color: 'var(--apexa-text)' }}>{option.name}</div>
              <div className="text-xs mt-1 leading-relaxed" style={{ color: 'var(--apexa-text-muted)' }}>{option.description}</div>
              <div className="text-[10px] mt-2" style={{ color: 'var(--apexa-accent)' }}>{option.inspectOnly ? 'Inspect / review' : 'Prepare a plan'} →</div>
            </button>
          ))}
        </section>
      ))}
      {matching.length === 0 && <p className="text-xs" role="status">No matching task. Describe your technique in chat for a capability review.</p>}
      <p className="text-[11px] leading-relaxed" style={{ color: 'var(--apexa-text-muted)' }}>
        Availability depends on the data host and installed engines. DFXM,
        coherent 2D and pink-beam adapters have limited or simulation-only paths;
        use Check analysis readiness before planning real-data execution.
      </p>
    </div>
  )
}
