import { useEffect, useId, useRef, useState } from 'react'
import { getConnection, saveConnection, verifyConnection, type ConnectionConfig, type ConnectionSettings } from '@/api/connection'

const labels: Record<string, string> = {
  argo: 'ANL Argo', openai: 'OpenAI API', anthropic: 'Anthropic API', gemini: 'Gemini API',
  custom: 'University / custom gateway', ollama: 'Local Ollama', mag: 'AmSC gateway',
  'alcf-minerva': 'ALCF Minerva', 'alcf-sophia': 'ALCF Sophia',
}
export function ConnectionDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const title = useId()
  const [settings, setSettings] = useState<ConnectionSettings | null>(null)
  const [config, setConfig] = useState<ConnectionConfig | null>(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const trigger = document.activeElement
    dialog.current?.showModal()
    let live = true
    getConnection().then(data => {
      if (live) { setSettings(data); setConfig(data.saved?.config ?? data.active); setError(data.error ?? '') }
    }).catch(() => { if (live) setError('Could not load connection settings. Check the server and reopen this dialog.') })
    return () => { live = false; if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus() }
  }, [])
  function update(next: Partial<ConnectionConfig>) {
    if (config) setConfig({ ...config, ...next })
    setMessage(''); setError('')
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
    <p className="mb-3 text-xs text-[var(--apexa-text-muted)]">Use your provider’s API account, an institutional gateway, or a local model. Chat subscriptions do not sign APEXA in.</p>
    {config && settings && <form className="space-y-3" onSubmit={e => { e.preventDefault(); void act(true) }}>
      <label className="block text-xs">Provider<select className={input} value={config.preset} disabled={busy} onChange={e => {
        const preset = settings.presets.find(p => p.id === e.target.value)
        if (preset) { update({ preset: preset.id, base_url: preset.base_url, model: '', username: '' }); setKey('') }
      }}>{settings.presets.map(p => <option key={p.id} value={p.id}>{labels[p.id] ?? p.id}</option>)}</select></label>
      <label className="block text-xs">Endpoint URL<input className={input} value={config.base_url} required readOnly={config.preset !== 'custom'} disabled={busy}
        onChange={e => { update({ base_url: e.target.value }); setKey('') }} /></label>
      <label className="block text-xs">Model / deployment ID<input className={input} value={config.model} required maxLength={200} disabled={busy}
        placeholder="Exact model ID supplied by your provider" onChange={e => update({ model: e.target.value })} /></label>
      {config.preset === 'argo' ? <label className="block text-xs">ANL username<input className={input} value={config.username} required disabled={busy} onChange={e => update({ username: e.target.value })} /></label>
        : config.preset !== 'ollama' && <label className="block text-xs">API key (optional if already configured)<input className={input} type="password" autoComplete="off" value={key} maxLength={8192} disabled={busy} onChange={e => setKey(e.target.value)} />
          <span className="text-[var(--apexa-text-muted)]">Saved keys use the server’s OS keyring. This field is never stored in browser storage.</span></label>}
      <p className="text-xs text-[var(--apexa-text-muted)]">Verify sends two small synthetic requests to the endpoint above and may use API credits. No conversation or experimental data is sent.</p>
      <div className="flex justify-end gap-2"><button type="button" className="apexa-compact-button" disabled={busy || !config.model} onClick={() => void act(false)}>Verify connection</button>
        <button type="submit" className="apexa-compact-button" disabled={busy}>Save for restart</button></div>
    </form>}
    {busy && <p role="status" className="mt-3 text-xs">Working…</p>}
    {message && <p role="status" className="mt-3 text-xs">{message}</p>}
    {error && <p role="alert" className="mt-3 text-xs text-red-500">{error}</p>}
  </dialog>
}
