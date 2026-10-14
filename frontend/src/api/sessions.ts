import type { ChatSession, SessionWorkspace } from './types'

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/sessions${path}`, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15000), cache: 'no-store',
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Session request failed (${response.status})`)
  return data
}

export const listServerSessions = () => request<{ sessions: ChatSession[]; last_active?: string }> ('')
export const readServerSession = (id: string) => request<ChatSession>(`/${encodeURIComponent(id)}`)
export const createServerSession = (id: string) => request<ChatSession>('', 'POST', { id })
export const importBrowserSession = (session: ChatSession) => request<ChatSession>('/import', 'POST', session)
export const renameServerSession = (id: string, title: string) => request<ChatSession>(`/${encodeURIComponent(id)}`, 'PATCH', { title })
export const archiveServerSession = (id: string) => request(`/${encodeURIComponent(id)}`, 'DELETE')
export const writeServerWorkspace = (id: string, workspace: SessionWorkspace, revision: number) =>
  request<{ workspaceRevision: number; revision: number }>(`/${encodeURIComponent(id)}/workspace`, 'PUT', { workspace, expected_revision: revision })
