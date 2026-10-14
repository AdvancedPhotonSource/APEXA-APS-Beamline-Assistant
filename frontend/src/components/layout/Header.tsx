import type { ReactNode } from 'react'
import { useConnectionStore } from '@/stores/connectionStore'
import { useThemeStore, type ThemeMode } from '@/stores/themeStore'

export function Header({ children }: { children?: ReactNode }) {
  const { connected, selectedModel, availableModels, setModel } = useConnectionStore()
  const { mode, setMode } = useThemeStore()
  const allModels = Object.values(availableModels).flatMap(models =>
    Object.entries(models).map(([id, name]) => ({ id, name: String(name) })))
  const themes: { value: ThemeMode; label: string; icon: string }[] = [
    { value: 'light', label: 'Light', icon: '☀' },
    { value: 'dark', label: 'Dark', icon: '◑' },
    { value: 'midnight', label: 'Midnight', icon: '☾' },
  ]
  const theme = themes.find(t => t.value === mode) ?? themes[0]
  const nextTheme = themes[(themes.indexOf(theme) + 1) % themes.length]

  return (
    <header className="apexa-topbar">
      <span className="apexa-brand" title="Advanced Photon EXperiment Assistant">APEXA</span>
      {children}
      <div className="apexa-header-controls">
        {allModels.length > 0 && <select className="apexa-model-select" aria-label="Analysis assistant model"
          title={selectedModel} value={selectedModel} onChange={e => setModel(e.target.value)}>
          {allModels.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>}
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
    </header>
  )
}
