import { useEffect, useId, useRef, useState } from 'react'
import { getConnection, saveConnection, verifyConnection, type ConnectionConfig, type ConnectionSettings } from '@/api/connection'

const labels: Record<string, string> = {
  argo: 'ANL Argo', openai: 'OpenAI API', anthropic: 'Anthropic API', gemini: 'Gemini API',
  custom: 'Other compatible endpoint', ollama: 'Ollama on this machine', mag: 'AmSC gateway',
  'alcf-minerva': 'ALCF Minerva', 'alcf-sophia': 'ALCF Sophia',
}
const CUSTOM_MODEL = '__custom_model__'
const accessGroups = [
  { id: 'argo', label: 'ANL Argo (ANL network / VPN)', presets: ['argo'] },
  { id: 'api', label: 'Personal API key', presets: ['openai', 'anthropic', 'gemini'] },
  { id: 'local', label: 'Local / self-hosted models', presets: ['ollama', 'custom'] },
  { id: 'facility', label: 'Other university / facility access', presets: ['alcf-sophia', 'alcf-minerva', 'mag', 'custom'] },
]
function accessFor(preset: string) {
  // A custom URL does not record whether it is a campus or self-hosted service.
  if (preset === 'custom') return 'facility'
  return accessGroups.find(group => group.presets.includes(preset))?.id ?? 'argo'
}
export function ConnectionDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const title = useId()
  const modelList = useId()
  const [settings, setSettings] = useState<ConnectionSettings | null>(null)
  const [config, setConfig] = useState<ConnectionConfig | null>(null)
  const [access, setAccess] = useState('argo')
  const [manualModel, setManualModel] = useState(false)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const trigger = document.activeElement
    dialog.current?.showModal()
    return () => { if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus() }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    let live = true
    getConnection(controller.signal).then(data => {
      if (live) {
        const current = data.saved?.config ?? data.active
        setSettings(data); setConfig(current); setAccess(accessFor(current.preset)); setError(data.error ?? '')
      }
    }).catch(e => {
      if (live) setError(e instanceof Error ? e.message : 'Could not load connection settings. Check the APEXA server and retry.')
    }).finally(() => { if (live) setLoading(false) })
    return () => { live = false; controller.abort() }
  }, [loadAttempt])
  function update(next: Partial<ConnectionConfig>) {
    if (config) setConfig({ ...config, ...next })
    setMessage(''); setError('')
  }
  function selectProvider(id: string) {
    const preset = settings?.presets.find(p => p.id === id)
    if (preset && id !== config?.preset) {
      update({ preset: preset.id, base_url: preset.base_url, model: '', username: '' }); setKey(''); setManualModel(false)
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
  const selectedPreset = settings?.presets.find(p => p.id === config?.preset)
  const modelSuggestions = selectedPreset?.model_suggestions ?? []
  const modelChoices = selectedPreset?.model_choices ?? []
  const customModel = manualModel || (!!config?.model && !modelChoices.some(model => model.id === config.model))
  return <dialog ref={dialog} aria-labelledby={title} onCancel={e => { if (busy) e.preventDefault(); else onClose() }}
    className="m-auto w-[calc(100%_-_2rem)] max-w-lg max-h-[calc(100dvh_-_2rem)] overflow-y-auto rounded-xl border border-[var(--apexa-border)] bg-[var(--apexa-surface)] p-4 text-[var(--apexa-text)] shadow-2xl backdrop:bg-black/50">
    <div className="mb-3 flex items-center justify-between"><h2 id={title} className="text-sm font-semibold">LLM connection</h2>
      <button type="button" onClick={onClose} disabled={busy} aria-label="Close connection settings">×</button></div>
    {loading && <p role="status" className="text-xs text-[var(--apexa-text-muted)]">Loading connection settings…</p>}
    {error && <p role="alert" className="mb-3 text-xs text-red-500">{error}</p>}
    {!loading && !settings && <button type="button" className="apexa-dialog-button" onClick={() => {
      setError(''); setLoading(true); setLoadAttempt(attempt => attempt + 1)
    }}>Retry loading settings</button>}
    {config && settings && <form className="space-y-3" onSubmit={e => { e.preventDefault(); void act(true) }}>
      <p className="text-xs text-[var(--apexa-text-muted)]">ANL Argo is the default connection. You can also choose a personal API, local model or another facility.</p>
      <label className="block text-xs">Access type<select aria-label="Access type" className={input} value={access} disabled={busy} onChange={e => {
        const group = accessGroups.find(g => g.id === e.target.value)
        if (!group) return
        setAccess(group.id)
        if (!group.presets.includes(config.preset)) {
          const first = group.presets.find(id => settings.presets.some(p => p.id === id))
          if (first) selectProvider(first)
        }
      }}>{accessGroups.map(group => <option key={group.id} value={group.id}>{group.label}</option>)}</select></label>
      {access !== 'argo' && <label className="block text-xs">Provider / host<select aria-label="Provider / host" className={input} value={config.preset} disabled={busy} onChange={e => selectProvider(e.target.value)}>
        {settings.presets.filter(p => accessGroups.find(g => g.id === access)?.presets.includes(p.id)).map(p =>
          <option key={p.id} value={p.id}>{labels[p.id] ?? p.id}</option>)}
      </select></label>}
      {config.preset === 'argo' && <p className="text-xs text-[var(--apexa-text-muted)]">Use your ANL username on the ANL network or VPN. Argo does not require a personal API key.</p>}
      {config.preset === 'argo' ? <label className="block text-xs">ANL username<input className={input} value={config.username} required disabled={busy} onChange={e => update({ username: e.target.value })} /></label>
        : config.preset !== 'ollama' && <label className="block text-xs">API key (optional if already configured)<input className={input} type="password" autoComplete="off" value={key} maxLength={8192} disabled={busy} onChange={e => setKey(e.target.value)} />
          <span className="text-[var(--apexa-text-muted)]">Paste your provider’s API key here, or leave blank to keep a matching saved key. Saved keys use the server’s OS keyring.</span></label>}
      <label className="block text-xs">Endpoint URL<input className={input} value={config.base_url} required readOnly={config.preset !== 'custom'} disabled={busy}
        onChange={e => { update({ base_url: e.target.value }); setKey('') }} /></label>
      {modelChoices.length > 0 && <label className="block text-xs">Model<select aria-label="Model" className={input}
        value={customModel ? CUSTOM_MODEL : config.model} required disabled={busy} onChange={e => {
          const manual = e.target.value === CUSTOM_MODEL
          setManualModel(manual); update({ model: manual ? '' : e.target.value })
        }}>
        <option value="" disabled>Choose a model</option>
        {modelChoices.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
        <option value={CUSTOM_MODEL}>Other model (advanced)</option>
      </select></label>}
      {(modelChoices.length === 0 || customModel) && <label className="block text-xs">Model / deployment ID<input className={input} value={config.model} required maxLength={200} disabled={busy} list={modelSuggestions.length ? modelList : undefined}
        placeholder="Exact model ID supplied by your provider" onChange={e => update({ model: e.target.value })} /></label>}
      {modelChoices.length > 0 && <p className="text-xs text-[var(--apexa-text-muted)]">APEXA fills in the API ID for the model you select. Verify connection checks access for your account.</p>}
      {modelSuggestions.length > 0 && <datalist id={modelList}>{modelSuggestions.map(model => <option key={model} value={model} />)}</datalist>}
      {config.preset === 'argo' && <p className="text-xs text-[var(--apexa-text-muted)]">Suggestions are recorded Argo aliases, not a live availability check. You can enter any exact model ID.</p>}
      {config.preset === 'ollama' && <p className="text-xs text-[var(--apexa-text-muted)]">Use the exact installed tag from <code>ollama list</code>, for example a Gemma or GPT-OSS model with tool support. Setup does not download models.</p>}
      {config.preset.startsWith('alcf-') && <p className="text-xs text-[var(--apexa-text-muted)]">ALCF hosts the model at the facility. Recorded options include GPT-OSS and Gemma on Sophia, and Inkling on Minerva; verify the exact model ID and current availability.</p>}
      <p className="text-xs text-[var(--apexa-text-muted)]">Verify sends two small synthetic requests to the endpoint above and may use API credits. No conversation or experimental data is sent.</p>
      <div className="flex flex-wrap justify-end gap-2"><button type="button" className="apexa-dialog-button" disabled={busy || !config.model} onClick={() => void act(false)}>Verify connection</button>
        <button type="submit" className="apexa-dialog-button" disabled={busy}>Save for restart</button></div>
    </form>}
    {settings && <details className="mt-3 text-xs text-[var(--apexa-text-muted)]"><summary>What about ChatGPT / Claude subscriptions?</summary>
      <p className="mt-2">Subscription login is not available in APEXA’s WebUI. The separate tool integration lets Codex or Claude Code call APEXA analysis tools from their own chat interface. API accounts have separate billing.</p>
    </details>}
    {busy && <p role="status" className="mt-3 text-xs">Working…</p>}
    {message && <p role="status" className="mt-3 text-xs">{message}</p>}
  </dialog>
}
