import type { VizArtifact } from '@/api/types'
import { cn } from '@/lib/cn'
import { useEffect, useRef } from 'react'

interface VizTabsProps {
  artifacts: VizArtifact[]
  activeId: string | null
  onSelect: (id: string) => void
  onClose: (id: string) => void
}

export function VizTabs({ artifacts, activeId, onSelect, onClose }: VizTabsProps) {
  const tabs = useRef(new Map<string, HTMLButtonElement>())
  useEffect(() => {
    // Reveal the chosen tab without stealing focus from chat or viewer controls.
    if (activeId) tabs.current.get(activeId)?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activeId, artifacts.length])
  if (artifacts.length === 0) return null

  const close = (id: string, restoreFocus: boolean) => {
    const index = artifacts.findIndex(artifact => artifact.id === id)
    const neighbor = artifacts[index + 1] ?? artifacts[index - 1]
    onClose(id)
    if (restoreFocus && neighbor) tabs.current.get(neighbor.id)?.focus()
  }

  return (
    <div role="tablist" aria-label="Open results" className="flex shrink-0 items-center gap-0.5 px-2 pt-2 overflow-x-auto border-b"
      style={{ borderColor: 'var(--apexa-border)', background: 'var(--apexa-surface-2)' }}>
      {artifacts.map((a, index) => (
        <div
          key={a.id}
          className={cn(
            'group shrink-0 flex items-center gap-1 px-1 rounded-t-lg text-xs transition-colors',
            a.id === activeId ? 'border-b-2' : 'opacity-60 hover:opacity-100'
          )}
          style={{
            background: a.id === activeId ? 'var(--apexa-surface)' : 'transparent',
            borderBottomColor: 'var(--apexa-accent)',
            color: a.id === activeId ? 'var(--apexa-accent)' : 'var(--apexa-text-muted)',
          }}
          role="presentation"
        >
          <button type="button" ref={node => { if (node) tabs.current.set(a.id, node); else tabs.current.delete(a.id) }}
            className="flex min-w-0 items-center gap-1.5 rounded px-2 py-1.5" title={a.title}
            role="tab" id={`canvas-tab-${encodeURIComponent(a.id)}`} aria-controls="canvas-active-view"
            aria-selected={a.id === activeId} tabIndex={a.id === activeId || (!activeId && index === 0) ? 0 : -1}
            onClick={() => onSelect(a.id)} onKeyDown={event => {
              const target = event.key === 'ArrowRight' ? (index + 1) % artifacts.length
                : event.key === 'ArrowLeft' ? (index - 1 + artifacts.length) % artifacts.length
                  : event.key === 'Home' ? 0 : event.key === 'End' ? artifacts.length - 1 : null
              if (target != null) {
                event.preventDefault()
                onSelect(artifacts[target].id)
                tabs.current.get(artifacts[target].id)?.focus()
              } else if (event.key === 'Delete') {
                event.preventDefault()
                close(a.id, true)
              }
            }}>
            <TypeIcon type={a.type} />
            <span className="max-w-[120px] truncate">{a.title}</span>
          </button>
          <button
            type="button"
            aria-label={`Close ${a.title}`}
            title={`Close ${a.title}; keep it in History`}
            onClick={event => close(a.id, event.detail === 0)}
            className="rounded px-1.5 py-1 opacity-60 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"
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
    case 'refinement':
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
          <path d="M3 17l5-5 4 3 8-9" /><path d="M15 6h5v5" /><path d="M3 21h18" />
        </svg>
      )
    case 'diffraction':
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
          <circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" />
        </svg>
      )
    case 'html-report':
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
