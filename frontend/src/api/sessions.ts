import type { ChatSession, SessionWorkspace } from './types'

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
    throw new Error(typeof data.detail === 'string' ? data.detail : `Session request failed (${response.status})`)
  }
  return data as T
}

export const listServerSessions = () => request<{ sessions: ChatSession[]; last_active?: string }> ('')
export const readServerSession = (id: string) => request<ChatSession>(`/${encodeURIComponent(id)}`)
export const createServerSession = (id: string) => request<ChatSession>('', 'POST', { id })
export const importBrowserSession = (session: ChatSession) => request<ChatSession>('/import', 'POST', session)
export const renameServerSession = (id: string, title: string) => request<ChatSession>(`/${encodeURIComponent(id)}`, 'PATCH', { title })
export const archiveServerSession = (id: string) => request(`/${encodeURIComponent(id)}`, 'DELETE')
export const writeServerWorkspace = (id: string, workspace: SessionWorkspace, revision: number) =>
  request<{ workspaceRevision: number; revision: number }>(`/${encodeURIComponent(id)}/workspace`, 'PUT', { workspace, expected_revision: revision })
