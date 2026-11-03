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
  presets: { id: string; base_url: string; wire: string; model_suggestions?: string[];
    model_choices?: { id: string; label: string }[] }[]
}
async function request(path: string, init?: RequestInit) {
  const timeout = AbortSignal.timeout(init?.method ? 50000 : 10000)
  const signal = init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout
  let res: Response
  try {
    res = await fetch(`/api/connection${path}`, { ...init, cache: 'no-store', signal })
  } catch (error) {
    if (init?.signal?.aborted) throw error
    throw new Error(timeout.aborted
      ? 'The connection-settings request timed out. Check that APEXA is responding, then retry.'
      : 'Could not reach the APEXA server. Check that it is running, then retry.')
  }
  if (res.status === 404 || res.status === 405) {
    throw new Error(`This server does not provide the connection-settings API required by this WebUI (HTTP ${res.status}). Stop and restart APEXA from the updated checkout, then retry. Refreshing the browser alone does not update the server.`)
  }
  if (res.status === 401 || res.status === 403) {
    throw new Error(`Access to connection settings was denied (HTTP ${res.status}). Check the server access configuration and reopen APEXA using its authorized URL.`)
  }
  let data
  try { data = await res.json() } catch {
    throw new Error(`The server returned an unreadable connection-settings response (HTTP ${res.status}). Check the APEXA server log and any reverse proxy, then retry.`)
  }
  if (!res.ok) throw new Error(typeof data?.detail === 'string' ? `${data.detail} (HTTP ${res.status})` : `Connection settings request failed (HTTP ${res.status}). Check the APEXA server log, then retry.`)
  return data
}

function isConfig(value: unknown): value is ConnectionConfig {
  if (!value || typeof value !== 'object') return false
  const config = value as Record<string, unknown>
  return ['preset', 'base_url', 'model', 'username'].every(key => typeof config[key] === 'string')
}
export async function getConnection(signal?: AbortSignal): Promise<ConnectionSettings> {
  const data = await request('', { signal })
  if (!data || !isConfig(data.active)
      || !(data.saved === null || (typeof data.saved?.revision === 'string' && isConfig(data.saved.config)))
      || typeof data.environment_override !== 'boolean' || typeof data.restart_required !== 'boolean'
      || !(data.error === null || typeof data.error === 'string')
      || !Array.isArray(data.presets) || !data.presets.length
      || !data.presets.every((preset: Record<string, unknown> | null) => preset
        && ['id', 'base_url', 'wire'].every(key => typeof preset[key] === 'string')
        && (preset.model_suggestions === undefined || (Array.isArray(preset.model_suggestions)
          && preset.model_suggestions.every(model => typeof model === 'string')))
        && (preset.model_choices === undefined || (Array.isArray(preset.model_choices)
          && preset.model_choices.every(model => model && typeof model.id === 'string' && typeof model.label === 'string'))))) {
    throw new Error('The server returned incompatible connection settings. Restart APEXA from the updated checkout and reload this page.')
  }
  return data
}
export function saveConnection(config: ConnectionConfig, apiKey: string, revision: string | null): Promise<{ revision: string; restart_required: boolean; environment_override: boolean }> {
  return request('', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config, api_key: apiKey, expected_revision: revision }) })
}
export function verifyConnection(config: ConnectionConfig, apiKey: string): Promise<{ ok: boolean; detail?: string; scope?: string }> {
  return request('/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config, api_key: apiKey }) })
}
