import { useChatStore } from '@/stores/chatStore'
import { ApexaLogo } from '@/components/layout/IconRail'

export function TypingIndicator() {
  const { isLoading, progress, activity } = useChatStore()

  if (!isLoading) return null

  return (
    <div style={{ display: 'flex', gap: 12, padding: '14px 20px' }}>
      <div style={{ marginTop: 4, flexShrink: 0 }}>
        <ApexaLogo size={32} />
      </div>
      <div className="rounded-2xl shadow-sm animate-fade-in" style={{
        padding: '12px 16px',
        maxWidth: '80%',
        background: 'var(--apexa-chat-assistant)',
        border: '1px solid var(--apexa-border)',
      }}>
        {progress ? (
          <div>
            <div className="text-[13px] text-[var(--apexa-text-2)] mb-2 font-medium">{progress.step}</div>
            <div className="w-48 h-1.5 rounded-full bg-[var(--apexa-surface-3)] overflow-hidden">
              <div className="h-full rounded-full transition-all duration-500 ease-out" style={{
                width: `${progress.percent}%`,
                background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)',
              }} />
            </div>
            <div className="text-[11px] text-[var(--apexa-text-muted)] mt-1.5 tabular-nums">{progress.percent}%</div>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <span className="text-[13px] text-[var(--apexa-text-2)]">
              {activity ? (
                <span className="font-mono text-[12px] text-[var(--apexa-text)]">{activity}</span>
              ) : 'Thinking'}
            </span>
            <span className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <span key={i} className="w-1.5 h-1.5 rounded-full bg-blue-500" style={{
                  animation: 'bounce 1.4s infinite',
                  animationDelay: `${i * 160}ms`,
                  opacity: 0.7,
                }} />
              ))}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
