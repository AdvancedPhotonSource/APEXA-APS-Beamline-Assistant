import type { VizArtifact } from '@/api/types'
import { cn } from '@/lib/cn'

interface VizTabsProps {
  artifacts: VizArtifact[]
  activeId: string | null
  onSelect: (id: string) => void
  onClose: (id: string) => void
}

export function VizTabs({ artifacts, activeId, onSelect, onClose }: VizTabsProps) {
  if (artifacts.length === 0) return null

  return (
    <div className="flex items-center gap-0.5 px-2 pt-2 overflow-x-auto border-b"
      style={{ borderColor: 'var(--apexa-border)', background: 'var(--apexa-surface-2)' }}>
      {artifacts.map((a) => (
        <div
          key={a.id}
          className={cn(
            'group flex items-center gap-1.5 px-3 py-1.5 rounded-t-lg text-xs cursor-pointer transition-colors',
            a.id === activeId ? 'border-b-2 border-blue-500' : 'opacity-60 hover:opacity-100'
          )}
          style={{
            background: a.id === activeId ? 'var(--apexa-surface)' : 'transparent',
            color: a.id === activeId ? 'var(--apexa-text)' : 'var(--apexa-text-muted)',
          }}
          onClick={() => onSelect(a.id)}
        >
          <TypeIcon type={a.type} />
          <span className="max-w-[120px] truncate">{a.title}</span>
          <button
            onClick={(e) => { e.stopPropagation(); onClose(a.id) }}
            className="ml-1 opacity-0 group-hover:opacity-100 transition-opacity hover:text-red-400"
          >
            x
          </button>
        </div>
      ))}
    </div>
  )
}

function TypeIcon({ type }: { type: string }) {
  const className = 'w-3 h-3'
  switch (type) {
    case 'plotly':
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      )
    case 'diffraction':
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
          <circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" />
        </svg>
      )
    case 'report':
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="13" y2="17" />
        </svg>
      )
    case 'image':
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <polyline points="21 15 16 10 5 21" />
        </svg>
      )
    default:
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        </svg>
      )
  }
}
