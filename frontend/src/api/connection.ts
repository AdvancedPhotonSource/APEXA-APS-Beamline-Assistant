export interface ConnectionConfig {
  preset: string
  base_url: string
  model: string
  username: string
}
export interface ConnectionSettings {
  active: ConnectionConfig
  saved: { revision: string; config: ConnectionConfig } | null
  environment_override: boolean
  restart_required: boolean
  error: string | null
  presets: { id: string; base_url: string; wire: string; model_suggestions?: string[] }[]
}
async function request(path: string, init?: RequestInit) {
  const res = await fetch(`/api/connection${path}`, { ...init, cache: 'no-store', signal: AbortSignal.timeout(50000) })
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Connection settings request failed')
  return data
}
export function getConnection(): Promise<ConnectionSettings> { return request('') }
export function saveConnection(config: ConnectionConfig, apiKey: string, revision: string | null): Promise<{ revision: string; restart_required: boolean; environment_override: boolean }> {
  return request('', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config, api_key: apiKey, expected_revision: revision }) })
}
export function verifyConnection(config: ConnectionConfig, apiKey: string): Promise<{ ok: boolean; detail?: string; scope?: string }> {
  return request('/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config, api_key: apiKey }) })
}
