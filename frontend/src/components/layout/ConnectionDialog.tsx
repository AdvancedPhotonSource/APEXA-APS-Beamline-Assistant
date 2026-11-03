import { useEffect, useId, useRef, useState } from 'react'
import { getConnection, saveConnection, verifyConnection, type ConnectionConfig, type ConnectionSettings } from '@/api/connection'

const labels: Record<string, string> = {
  argo: 'ANL Argo', openai: 'OpenAI API', anthropic: 'Anthropic API', gemini: 'Gemini API',
  custom: 'Other compatible endpoint', ollama: 'Ollama on this machine', mag: 'AmSC gateway',
  'alcf-minerva': 'ALCF Minerva', 'alcf-sophia': 'ALCF Sophia',
}
const accessGroups = [
  { id: 'facility', label: 'University / facility access', presets: ['argo', 'alcf-sophia', 'alcf-minerva', 'mag', 'custom'] },
  { id: 'api', label: 'Personal API key', presets: ['openai', 'anthropic', 'gemini'] },
  { id: 'local', label: 'Local / self-hosted models', presets: ['ollama', 'custom'] },
]
function accessFor(preset: string) {
  return accessGroups.find(group => group.presets.includes(preset))?.id ?? 'facility'
}
export function ConnectionDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const title = useId()
  const [settings, setSettings] = useState<ConnectionSettings | null>(null)
  const [config, setConfig] = useState<ConnectionConfig | null>(null)
  const [access, setAccess] = useState('facility')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const trigger = document.activeElement
    dialog.current?.showModal()
    let live = true
    getConnection().then(data => {
      if (live) {
        const current = data.saved?.config ?? data.active
        setSettings(data); setConfig(current); setAccess(accessFor(current.preset)); setError(data.error ?? '')
      }
    }).catch(() => { if (live) setError('Could not load connection settings. Check the server and reopen this dialog.') })
    return () => { live = false; if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus() }
  }, [])
  function update(next: Partial<ConnectionConfig>) {
    if (config) setConfig({ ...config, ...next })
    setMessage(''); setError('')
  }
  function selectProvider(id: string) {
    const preset = settings?.presets.find(p => p.id === id)
    if (preset && id !== config?.preset) {
      update({ preset: preset.id, base_url: preset.base_url, model: '', username: '' }); setKey('')
    }
  }
  async function act(save: boolean) {
    if (!config || busy) return
    setBusy(true); setMessage(''); setError('')
    try {
      if (save) {
        const result = await saveConnection(config, key, settings?.saved?.revision ?? null)
        setKey('')
        setSettings(s => s ? { ...s, saved: { revision: result.revision, config }, restart_required: true } : s)
        setMessage(result.environment_override
          ? 'Saved. Explicit server environment settings still take precedence. Update those settings and restart APEXA to use this connection.'
          : 'Saved. Restart APEXA to use this connection. Running work keeps its current connection.')
      } else {
        const result = await verifyConnection(config, key)
        if (result.ok) setMessage('Verified structured tool calling and tool results. Scientific engines were not tested.')
        else setError(result.detail ?? 'Connection verification failed.')
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Connection request failed.') }
    finally { setBusy(false) }
  }
  const input = 'w-full rounded border border-[var(--apexa-border)] bg-[var(--apexa-surface-2)] p-2 text-xs text-[var(--apexa-text)]'
  return <dialog ref={dialog} aria-labelledby={title} onCancel={e => { if (busy) e.preventDefault(); else onClose() }}
    className="m-auto w-[calc(100%_-_2rem)] max-w-lg max-h-[calc(100dvh_-_2rem)] overflow-y-auto rounded-xl border border-[var(--apexa-border)] bg-[var(--apexa-surface)] p-4 text-[var(--apexa-text)] shadow-2xl backdrop:bg-black/50">
    <div className="mb-3 flex items-center justify-between"><h2 id={title} className="text-sm font-semibold">LLM connection</h2>
      <button type="button" onClick={onClose} disabled={busy} aria-label="Close connection settings">×</button></div>
    <p className="mb-3 text-xs text-[var(--apexa-text-muted)]">Choose how APEXA’s WebUI and CLI access a model, then select the provider or host.</p>
    {config && settings && <form className="space-y-3" onSubmit={e => { e.preventDefault(); void act(true) }}>
      <label className="block text-xs">Access type<select aria-label="Access type" className={input} value={access} disabled={busy} onChange={e => {
        const group = accessGroups.find(g => g.id === e.target.value)
        if (!group) return
        setAccess(group.id)
        if (!group.presets.includes(config.preset)) {
          const first = group.presets.find(id => settings.presets.some(p => p.id === id))
          if (first) selectProvider(first)
        }
      }}>{accessGroups.map(group => <option key={group.id} value={group.id}>{group.label}</option>)}</select></label>
      <label className="block text-xs">Provider / host<select aria-label="Provider / host" className={input} value={config.preset} disabled={busy} onChange={e => selectProvider(e.target.value)}>
        {settings.presets.filter(p => accessGroups.find(g => g.id === access)?.presets.includes(p.id)).map(p =>
          <option key={p.id} value={p.id}>{labels[p.id] ?? p.id}</option>)}
      </select></label>
      <label className="block text-xs">Endpoint URL<input className={input} value={config.base_url} required readOnly={config.preset !== 'custom'} disabled={busy}
        onChange={e => { update({ base_url: e.target.value }); setKey('') }} /></label>
      <label className="block text-xs">Model / deployment ID<input className={input} value={config.model} required maxLength={200} disabled={busy}
        placeholder="Exact model ID supplied by your provider" onChange={e => update({ model: e.target.value })} /></label>
      {config.preset === 'ollama' && <p className="text-xs text-[var(--apexa-text-muted)]">Use the exact installed tag from <code>ollama list</code>, for example a Gemma or GPT-OSS model with tool support. Setup does not download models.</p>}
      {config.preset.startsWith('alcf-') && <p className="text-xs text-[var(--apexa-text-muted)]">ALCF hosts the model at the facility. Recorded options include GPT-OSS and Gemma on Sophia, and Inkling on Minerva; verify the exact model ID and current availability.</p>}
      {config.preset === 'argo' ? <label className="block text-xs">ANL username<input className={input} value={config.username} required disabled={busy} onChange={e => update({ username: e.target.value })} /></label>
        : config.preset !== 'ollama' && <label className="block text-xs">API key (optional if already configured)<input className={input} type="password" autoComplete="off" value={key} maxLength={8192} disabled={busy} onChange={e => setKey(e.target.value)} />
          <span className="text-[var(--apexa-text-muted)]">Saved keys use the server’s OS keyring. This field is never stored in browser storage.</span></label>}
      <p className="text-xs text-[var(--apexa-text-muted)]">Verify sends two small synthetic requests to the endpoint above and may use API credits. No conversation or experimental data is sent.</p>
      <div className="flex justify-end gap-2"><button type="button" className="apexa-compact-button" disabled={busy || !config.model} onClick={() => void act(false)}>Verify connection</button>
        <button type="submit" className="apexa-compact-button" disabled={busy}>Save for restart</button></div>
    </form>}
    <details className="mt-3 text-xs text-[var(--apexa-text-muted)]"><summary>What about ChatGPT / Claude subscriptions?</summary>
      <p className="mt-2">Subscription login is not available in APEXA’s WebUI. The separate tool integration lets Codex or Claude Code call APEXA analysis tools from their own chat interface. API accounts have separate billing.</p>
    </details>
    {busy && <p role="status" className="mt-3 text-xs">Working…</p>}
    {message && <p role="status" className="mt-3 text-xs">{message}</p>}
    {error && <p role="alert" className="mt-3 text-xs text-red-500">{error}</p>}
  </dialog>
}
