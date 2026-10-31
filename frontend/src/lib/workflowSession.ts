import { advanceWorkflow, cancelWorkflow, canCancelWorkflow, listWorkflows, workflowNeedsPolling, workflowRequestId, WorkflowApiError } from '../api/workflows.ts'
import type { WorkflowCancellation, WorkflowDecision, WorkflowDecisionKind, WorkflowSnapshot } from '../api/workflows.ts'

interface WorkflowState {
  workflows: WorkflowSnapshot[]
  loading: boolean
  busyIds: string[]
  uncertainIds: string[]
  error: string | null
  notice: string | null
}

type Action = { id: string; kind: 'advance'; body: WorkflowDecision } | { id: string; kind: 'cancel'; body: WorkflowCancellation }

/** One mounted session owns requests, polling and idempotent action receipts. */
export class WorkflowSession {
  private state: WorkflowState = { workflows: [], loading: true, busyIds: [], uncertainIds: [], error: null, notice: null }
  private listeners = new Set<() => void>()
  private active = false
  private epoch = 0
  private read: AbortController | null = null
  private writes = new Map<string, AbortController>()
  private uncertain = new Map<string, Action>()
  private timer: ReturnType<typeof setTimeout> | undefined
  private sessionId: string
  private pollMs: number

  constructor(sessionId: string, pollMs = 5000) {
    this.sessionId = sessionId
    this.pollMs = pollMs
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private set(patch: Partial<WorkflowState>) {
    if (!this.active) return
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }

  start() {
    this.active = true
    this.epoch++
    void this.refresh()
  }

  stop() {
    this.active = false
    this.epoch++
    this.read?.abort()
    this.writes.forEach(controller => controller.abort())
    this.writes.clear()
    clearTimeout(this.timer)
  }

  refresh = async () => {
    if (!this.active) return
    clearTimeout(this.timer)
    this.read?.abort()
    const controller = new AbortController()
    this.read = controller
    this.set({ loading: true })
    try {
      const workflows = await listWorkflows(this.sessionId, controller.signal)
      if (!this.active || this.read !== controller || controller.signal.aborted) return
      for (const [id, action] of this.uncertain) {
        const run = workflows.find(item => item.workflow_id === id)
        if (run && (run.revision !== action.body.expected_revision
          || (action.kind === 'advance' && run.gate?.gate_id !== action.body.gate_id))) this.uncertain.delete(id)
      }
      this.set({ workflows, error: null, uncertainIds: [...this.uncertain.keys()] })
    } catch (error) {
      if (!this.active || this.read !== controller || controller.signal.aborted) return
      this.set({ error: `Could not load workflows: ${error instanceof Error ? error.message : String(error)}. Use Refresh to try again.` })
    } finally {
      if (this.active && this.read === controller && !controller.signal.aborted) {
        this.set({ loading: false })
        if (this.state.workflows.some(workflowNeedsPolling)) this.timer = setTimeout(() => { void this.refresh() }, this.pollMs)
      }
    }
  }

  decide = (run: WorkflowSnapshot, decision: WorkflowDecisionKind, reason: string) => {
    if (!run.gate?.decisions.includes(decision) || this.uncertain.has(run.workflow_id)) return Promise.resolve()
    return this.perform({ id: run.workflow_id, kind: 'advance', body: { expected_revision: run.revision, gate_id: run.gate.gate_id,
      decision, request_id: workflowRequestId(), ...(reason ? { reason } : {}) } })
  }

  cancel = (run: WorkflowSnapshot) => {
    if (!canCancelWorkflow(run) || this.uncertain.has(run.workflow_id)) return Promise.resolve()
    return this.perform({ id: run.workflow_id, kind: 'cancel', body: { expected_revision: run.revision, request_id: workflowRequestId() } })
  }

  retry = (id: string) => {
    const action = this.uncertain.get(id)
    return action ? this.perform(action) : Promise.resolve()
  }

  private async perform(action: Action) {
    if (!this.active || this.writes.has(action.id)) return
    const current = this.state.workflows.find(run => run.workflow_id === action.id)
    if (!current || current.session_id !== this.sessionId || current.revision !== action.body.expected_revision
      || (action.kind === 'advance' && (current.gate?.gate_id !== action.body.gate_id || !current.gate.decisions.includes(action.body.decision)))) {
      this.set({ notice: 'This workflow changed. Review the refreshed state before deciding again.' })
      await this.refresh()
      return
    }
    const epoch = this.epoch
    const controller = new AbortController()
    this.writes.set(action.id, controller)
    this.set({ busyIds: [...this.writes.keys()], notice: null })
    try {
      if (action.kind === 'advance') await advanceWorkflow(action.id, action.body, controller.signal)
      else await cancelWorkflow(action.id, action.body, controller.signal)
      if (!this.active || epoch !== this.epoch) return
      this.uncertain.delete(action.id)
      this.set({ uncertainIds: [...this.uncertain.keys()], notice: action.kind === 'cancel'
        ? 'Cancellation requested. Recorded job status must confirm whether external work has stopped.'
        : 'Decision submitted. The recorded scientific verdict is unchanged by approval.' })
    } catch (error) {
      if (!this.active || epoch !== this.epoch) return
      if (error instanceof WorkflowApiError && error.status >= 400 && error.status < 500) {
        this.uncertain.delete(action.id)
        this.set({ uncertainIds: [...this.uncertain.keys()], notice: error.status === 409
          ? 'This workflow changed before the decision was applied. Review the refreshed state before deciding again.'
          : `The action was not accepted: ${error.message}` })
      } else {
        // A lost response can follow a committed mutation. Keep its identity for an explicit retry.
        this.uncertain.set(action.id, action)
        this.set({ uncertainIds: [...this.uncertain.keys()], notice: 'The action response was lost. Refreshing recorded state; any retry will reuse the same request ID.' })
      }
    } finally {
      if (this.active && epoch === this.epoch) {
        await this.refresh()
        if (this.active && epoch === this.epoch) {
          this.writes.delete(action.id)
          this.set({ busyIds: [...this.writes.keys()] })
        }
      }
    }
  }
}
