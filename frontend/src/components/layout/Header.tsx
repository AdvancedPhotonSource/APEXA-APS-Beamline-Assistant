import { useState, type ReactNode } from 'react'
import { useConnectionStore } from '@/stores/connectionStore'
import { useThemeStore, type ThemeMode } from '@/stores/themeStore'
import { ApexaLogo } from '@/components/ui/ApexaLogo'
import { FeedbackDialog } from '@/components/feedback/FeedbackDialog'
import { ConnectionDialog } from './ConnectionDialog'

export function Header({ children }: { children?: ReactNode }) {
  const [requestingFeature, setRequestingFeature] = useState(false)
  const [configuring, setConfiguring] = useState(false)
  const { connected, selectedModel, availableModels, setModel } = useConnectionStore()
  const { mode, setMode } = useThemeStore()
  const allModels = Object.values(availableModels).flatMap(models =>
    Object.entries(models).map(([id, name]) => ({ id, name: String(name) })))
  const themes: { value: ThemeMode; label: string; icon: string }[] = [
    { value: 'light', label: 'Classic light', icon: '☀' },
    { value: 'dark', label: 'Dark', icon: '◑' },
    { value: 'midnight', label: 'Midnight', icon: '☾' },
  ]
  const theme = themes.find(t => t.value === mode) ?? themes[0]
  const nextTheme = themes[(themes.indexOf(theme) + 1) % themes.length]

  return (
    <header className="apexa-topbar">
      <span className="flex shrink-0 items-center gap-1.5" title="Advanced Photon EXperiment Assistant">
        <ApexaLogo decorative /><span className="apexa-brand">APEXA</span>
      </span>
      {children}
      <div className="apexa-header-controls">
        <button type="button" className="apexa-compact-button" onClick={() => setConfiguring(true)}
          aria-label="LLM connection settings" title="LLM connection settings" aria-haspopup="dialog">⚙</button>
        {allModels.length > 0 && <select className="apexa-model-select" aria-label="Analysis assistant model"
          title={selectedModel} value={selectedModel} onChange={e => setModel(e.target.value)}>
          {allModels.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>}
        <button type="button" className="apexa-compact-button apexa-feature-button"
          onClick={() => setRequestingFeature(true)} aria-haspopup="dialog"
          aria-label="Request a feature" title="Request a feature">
          <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M9 18h6m-5 3h4M8 14a6 6 0 1 1 8 0c-1 .8-1 1.8-1 4H9c0-2.2 0-3.2-1-4Z" />
          </svg>
        </button>
        <button type="button" className="apexa-compact-button" onClick={() => setMode(nextTheme.value)}
          aria-label={`Theme: ${theme.label}. Switch to ${nextTheme.label}`} title={`Theme: ${theme.label} · switch to ${nextTheme.label}`}>
          {theme.icon}
        </button>
        <span className="apexa-connection-status" role="status" title={connected ? 'WebSocket connected' : 'WebSocket disconnected'}>
          <span aria-hidden="true" style={{ width: 7, height: 7, flexShrink: 0, borderRadius: '50%', background: connected ? '#10b981' : '#ef4444' }} />
          <span className="apexa-connection-label">{connected ? 'Connected' : 'Disconnected'}</span>
          <span className="sr-only">{connected ? 'WebSocket connected' : 'WebSocket disconnected'}</span>
        </span>
      </div>
      {requestingFeature && <FeedbackDialog mode="feature" onClose={() => setRequestingFeature(false)} />}
      {configuring && <ConnectionDialog onClose={() => setConfiguring(false)} />}
    </header>
  )
}
