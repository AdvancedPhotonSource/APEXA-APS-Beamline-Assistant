import { useEffect, useState, useSyncExternalStore } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { WorkflowSession } from '@/lib/workflowSession'
import { WorkflowRunCard } from './WorkflowRunCard'

const PAGE_SIZE = 8

function SessionWorkflows({ sessionId }: { sessionId: string }) {
  const [controller] = useState(() => new WorkflowSession(sessionId))
  const [page, setPage] = useState(0)
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => {
    controller.start()
    const unsubscribe = useChatStore.subscribe((next, previous) => {
      if (next.currentSessionId === sessionId && (next.messages !== previous.messages
        || next._pendingToolResults !== previous._pendingToolResults)) void controller.refresh()
    })
    return () => { unsubscribe(); controller.stop() }
  }, [controller, sessionId])
  const lastPage = Math.max(0, Math.ceil(state.workflows.length / PAGE_SIZE) - 1)
  const selectedPage = Math.min(page, lastPage)
  return <section className="space-y-2" aria-label="Session workflows">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold">Session workflows ({state.workflows.length})</h3>
      <button className="apexa-gallery-button" onClick={() => void controller.refresh()} disabled={state.loading}>Refresh</button>
    </div>
    {state.loading && <p className="text-[11px]" role="status">Refreshing workflow state…</p>}
    {state.error && <p className="text-xs text-red-400" role="alert">{state.error}</p>}
    {state.notice && <p className="text-xs" role="status">{state.notice}</p>}
    {!state.loading && !state.error && !state.workflows.length && <p className="text-[11px]" style={{ color: 'var(--apexa-text-muted)' }}>
      Prepared plans appear here for review before execution.
    </p>}
    {state.workflows.slice(selectedPage * PAGE_SIZE, (selectedPage + 1) * PAGE_SIZE).map(run => <WorkflowRunCard
      key={`${run.workflow_id}:${run.gate?.gate_id ?? 'no-gate'}`} run={run}
      busy={state.busyIds.includes(run.workflow_id) || state.loading || !!state.error} uncertain={state.uncertainIds.includes(run.workflow_id)}
      onDecide={(decision, reason) => { void controller.decide(run, decision, reason) }}
      onCancel={() => { void controller.cancel(run) }} onRetry={() => { void controller.retry(run.workflow_id) }} />)}
    {lastPage > 0 && <nav aria-label="Workflow pages" className="flex items-center justify-between gap-2 text-[11px]">
      <button className="apexa-gallery-button" disabled={selectedPage === 0} onClick={() => setPage(selectedPage - 1)}>Previous workflows</button>
      <span>{selectedPage + 1} / {lastPage + 1}</span>
      <button className="apexa-gallery-button" disabled={selectedPage === lastPage} onClick={() => setPage(selectedPage + 1)}>Next workflows</button>
    </nav>}
    {state.workflows.length === 100 && <p className="text-[10px]">Showing the 100 most recent workflows in this session.</p>}
  </section>
}

export function ActiveWorkflows() {
  const sessionId = useChatStore(state => state.currentSessionId)
  return sessionId ? <SessionWorkflows key={sessionId} sessionId={sessionId} /> : null
}
