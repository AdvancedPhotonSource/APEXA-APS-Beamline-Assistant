export type RailView = 'history' | 'files' | 'workflows' | 'motors' | 'viewers' | null

interface IconRailProps {
  activeView: RailView
  onSelect: (view: RailView) => void
}

export function IconRail({ activeView, onSelect }: IconRailProps) {
  const toggle = (view: RailView) => {
    onSelect(activeView === view ? null : view)
  }

  return (
    <nav aria-label="Workspace sections" style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      width: 68,
      flexShrink: 0,
      background: 'var(--apexa-rail-bg)',
      borderRight: '1px solid var(--apexa-border)',
      paddingTop: 10,
      paddingBottom: 10,
      gap: 2,
    }}>
      <RailButton active={activeView === 'files'} onClick={() => toggle('files')} label="Data">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
      </RailButton>

      <RailButton active={activeView === 'workflows'} onClick={() => toggle('workflows')} label="Analyze">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <polyline points="16 3 21 3 21 8" />
          <line x1="4" y1="20" x2="21" y2="3" />
          <polyline points="21 16 21 21 16 21" />
          <line x1="15" y1="15" x2="21" y2="21" />
          <line x1="4" y1="4" x2="9" y2="9" />
        </svg>
      </RailButton>

      <RailButton active={activeView === 'viewers'} onClick={() => toggle('viewers')} label="Results">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M3 9h18" />
          <path d="M9 21V9" />
          <path d="M13 13l2 2 4-4" />
        </svg>
      </RailButton>

      <RailButton active={activeView === 'history'} onClick={() => toggle('history')} label="Chats">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </RailButton>

      <div style={{ flex: 1 }} />

      <RailButton active={activeView === 'motors'} onClick={() => toggle('motors')} label="Motors">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </RailButton>

      {/* APEXA logo mark */}
      <ApexaLogo size={30} />
    </nav>
  )
}

function RailButton({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean
  onClick: () => void
  label: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-expanded={active}
      aria-controls={active ? 'apexa-side-panel' : undefined}
      onClick={onClick}
      title={label}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 3,
        width: 58,
        height: 56,
        borderRadius: 10,
        border: active ? '1px solid rgba(59,130,246,0.35)' : '1px solid transparent',
        cursor: 'pointer',
        transition: 'all 150ms var(--apexa-ease)',
        background: active
          ? 'linear-gradient(135deg, var(--apexa-accent-soft), transparent)'
          : 'transparent',
        color: active ? 'var(--apexa-accent)' : 'var(--apexa-text-muted)',
        boxShadow: active ? 'inset 2px 0 0 var(--apexa-accent), 0 0 16px rgba(59,130,246,0.16)' : 'none',
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.background = 'var(--apexa-rail-active)'
          e.currentTarget.style.color = 'var(--apexa-text-2)'
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = 'var(--apexa-text-muted)'
        }
      }}
    >
      {children}
      <span style={{ fontSize: 10, fontWeight: 500, letterSpacing: 0.3 }}>{label}</span>
    </button>
  )
}

export function ApexaLogo({ size = 28 }: { size?: number }) {
  return (
    <div style={{
      width: size,
      height: size,
      borderRadius: '50%',
      background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      boxShadow: '0 0 10px rgba(59,130,246,0.3)',
      flexShrink: 0,
    }}>
      {/* Stylized diffraction ring icon */}
      <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.8">
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="1.5" fill="white" stroke="none" />
      </svg>
    </div>
  )
}
