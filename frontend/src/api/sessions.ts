import type { ChatSession, SessionWorkspace, VizArtifact } from './types'

class SessionRequestError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

export class SessionApiUnavailable extends Error {
  constructor() {
    super('This APEXA server does not expose the session API. Chat can continue with this browser’s local history. Restart APEXA with ./start_web_viewer.sh from the current checkout to restore shared sessions.')
    this.name = 'SessionApiUnavailable'
  }
}

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/sessions${path}`, {
      method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15000), cache: 'no-store',
    })
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    throw new Error(`Could not reach the APEXA session API: ${reason}`)
  }
  // Reverse proxies and old server versions may return an HTML 404 page.
  // Keep that response actionable instead of leaking a JSON parse exception.
  const data: Record<string, unknown> = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 404 && (path === '' || path === '/import')) throw new SessionApiUnavailable()
    throw new SessionRequestError(response.status, typeof data.detail === 'string' ? data.detail : `Session request failed (${response.status})`)
  }
  return data as T
}

export const listServerSessions = () => request<{ sessions: ChatSession[]; last_active?: string }> ('')
export const readServerSession = (id: string) => {
  confirmedWorkspaces.delete(id)
  return request<ChatSession>(`/${encodeURIComponent(id)}?lazy_artifacts=true`)
}
export async function readServerArtifact(ref: { sessionId: string; digest: string }, signal: AbortSignal): Promise<VizArtifact> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(ref.sessionId)}/artifacts/${encodeURIComponent(ref.digest)}`, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load this saved artifact. Reconnect to its APEXA server or reload the session.')
  return response.json()
}
export const createServerSession = (id: string) => request<ChatSession>('', 'POST', { id })
export const importBrowserSession = (session: ChatSession) => request<ChatSession>('/import', 'POST', session)
export const renameServerSession = (id: string, title: string) => request<ChatSession>(`/${encodeURIComponent(id)}`, 'PATCH', { title })
export const archiveServerSession = async (id: string) => {
  const result = await request(`/${encodeURIComponent(id)}`, 'DELETE')
  confirmedWorkspaces.delete(id)
  return result
}

type WorkspaceReceipt = { workspaceRevision: number; revision: number }
const confirmedWorkspaces = new Map<string, { revision: number; artifacts: VizArtifact[] }>()
export function releaseWorkspaceCache(id: string) { confirmedWorkspaces.delete(id) }
let patchSupported = true
let transportEpoch = 0

export function resetWorkspaceTransport() {
  confirmedWorkspaces.clear()
  patchSupported = true
  transportEpoch++
}

export async function writeServerWorkspace(id: string, workspace: SessionWorkspace, revision: number): Promise<WorkspaceReceipt> {
  const epoch = transportEpoch
  const path = `/${encodeURIComponent(id)}/workspace`
  let result: WorkspaceReceipt
  if (patchSupported) {
    const previous = confirmedWorkspaces.get(id)
    // Store updates are immutable. Compare object identity, never stringify or
    // hash a scientific array just to find out whether a draft edit changed it.
    const reusable = new Map<VizArtifact, number>(previous?.revision === revision ? previous.artifacts.map((artifact, index) => [artifact, index]) : [])
    const { artifacts, ...settings } = workspace
    const slots = artifacts.map(artifact => {
      const index = reusable.get(artifact)
      return index === undefined ? { value: artifact } : { reuse: index }
    })
    try {
      result = await request<WorkspaceReceipt>(path, 'PATCH', { workspace: settings, artifact_slots: slots, expected_revision: revision })
    } catch (error) {
      // A running older backend can still serve freshly rebuilt frontend files.
      // Only an explicit unsupported method permits a full-snapshot fallback.
      // Never replay an uncertain write or bypass a revision conflict.
      if (!(error instanceof SessionRequestError) || error.status !== 405) throw error
      if (epoch === transportEpoch) patchSupported = false
      result = await request<WorkspaceReceipt>(path, 'PUT', { workspace, expected_revision: revision })
    }
  } else {
    result = await request<WorkspaceReceipt>(path, 'PUT', { workspace, expected_revision: revision })
  }
  if (!Number.isSafeInteger(result.workspaceRevision) || result.workspaceRevision !== revision + 1) {
    throw new Error('The session server did not confirm the expected workspace revision. Reload saved state before retrying.')
  }
  if (epoch === transportEpoch) {
    confirmedWorkspaces.delete(id)
    confirmedWorkspaces.set(id, { revision: result.workspaceRevision, artifacts: workspace.artifacts })
    // This is an optimization cache, not another unbounded session store.
    while (confirmedWorkspaces.size > 32) confirmedWorkspaces.delete(confirmedWorkspaces.keys().next().value!)
  }
  return result
}
