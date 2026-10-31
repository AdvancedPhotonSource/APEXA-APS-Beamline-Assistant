export type WorkflowDecisionKind = 'approve' | 'reject'
export type ScientificVerdict = 'unassessed' | 'provisional' | 'rejected' | 'accepted'
export type JobExecution = 'unknown' | 'pending' | 'running' | 'completed' | 'failed' | 'denied' | 'cancelled'

export interface WorkflowStep {
  id: string
  tool: string
  arguments: Record<string, unknown>
  bindings: Record<string, { step: string; path: string[] }>
  review_after: boolean
}

export interface WorkflowSnapshot {
  workflow_id: string
  session_id: string
  revision: number
  orchestration_state: 'awaiting_review' | 'running' | 'waiting_job' | 'blocked' | 'completed' | 'cancel_requested' | 'cancelled' | 'rejected'
  current_step: string | null
  plan: {
    schema_version: number
    recipe: string
    recipe_version: string
    dataset_id: string
    plan_hash: string
    recipe_source_hash: string
    tool_schema_hash: string
    steps: WorkflowStep[]
  }
  durability: 'sqlite'
  gate: null | {
    gate_id: string
    kind: 'plan_review' | 'result_review'
    step_id: string | null
    decisions: WorkflowDecisionKind[]
    recovery: boolean
    prompt: string
  }
  stages: {
    id: string
    tool: string
    state: 'pending' | 'queued' | 'claimed' | 'running' | 'submitted' | 'completed' | 'failed' | 'refused' | 'cancelled' | 'uncertain'
    run_id: string | null
    outcome: null | { execution: JobExecution; scientific_verdict: ScientificVerdict; error?: string | null }
    cancellation_status: null | 'not_requested' | 'requested' | 'acknowledged' | 'stopped'
  }[]
  job_execution: JobExecution
  scientific_verdict: ScientificVerdict
  error: string | null
}

export interface WorkflowRecipe {
  recipe: string
  recipe_version: string
  description: string
  stages: { id: string; tool: string; required: boolean; review_after: boolean }[]
  initial_review_required: boolean
  parameters_inferred: boolean
  hardware_execution: false | 'not_a_hardware_recipe'
}

/** Durable workflow actions have their own identity/revision, separate from chat. */
export class WorkflowApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'WorkflowApiError'
    this.status = status
  }
}

export interface WorkflowDecision {
  expected_revision: number
  gate_id: string
  decision: WorkflowDecisionKind
  request_id: string
  reason?: string
}

export interface WorkflowCancellation {
  expected_revision: number
  request_id: string
}

async function request<T>(path: string, signal: AbortSignal, body?: WorkflowDecision | WorkflowCancellation): Promise<T> {
  const response = await fetch(`/api/workflows${path}`, {
    method: body ? 'POST' : 'GET', cache: 'no-store', signal,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  })
  const payload = await response.json().catch(() => {
    if (response.ok) throw new Error('Workflow service returned an invalid response')
    return {}
  })
  if (!response.ok) throw new WorkflowApiError(typeof payload.detail === 'string' ? payload.detail
    : typeof payload.error === 'string' ? payload.error : `Workflow request failed (${response.status})`, response.status)
  return payload as T
}

export function workflowRequestId(): string {
  return typeof crypto.randomUUID === 'function' ? crypto.randomUUID()
    : [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, '0')).join('')
}

export const advanceWorkflow = (id: string, decision: WorkflowDecision, signal: AbortSignal) =>
  request<unknown>(`/${encodeURIComponent(id)}/advance`, signal, decision)

export const cancelWorkflow = (id: string, cancellation: WorkflowCancellation, signal: AbortSignal) =>
  request<unknown>(`/${encodeURIComponent(id)}/cancel`, signal, cancellation)

export async function listWorkflows(sessionId: string, signal: AbortSignal): Promise<WorkflowSnapshot[]> {
  const result = await request<{ workflows: WorkflowSnapshot[] }>(`?${new URLSearchParams({ session_id: sessionId, limit: '100' })}`, signal)
  if (!Array.isArray(result.workflows)) throw new Error('Workflow service returned an invalid list')
  // Keep the UI session scoped even if a server response contains other sessions.
  return result.workflows.filter(run => run.session_id === sessionId)
}

export const readWorkflow = (id: string, signal: AbortSignal) =>
  request<WorkflowSnapshot>(`/${encodeURIComponent(id)}`, signal)

export const workflowCatalog = (signal: AbortSignal) => request<{ recipes: WorkflowRecipe[] }>('/catalog', signal)

export function canCancelWorkflow(run: WorkflowSnapshot): boolean {
  return !['completed', 'rejected', 'cancelled', 'cancel_requested'].includes(run.orchestration_state)
}

export function workflowNeedsPolling(run: WorkflowSnapshot): boolean {
  return ['running', 'waiting_job', 'cancel_requested'].includes(run.orchestration_state)
    || run.stages.some(stage => ['claimed', 'running', 'submitted', 'uncertain'].includes(stage.state))
}
