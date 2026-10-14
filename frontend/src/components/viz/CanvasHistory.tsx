import { useVizStore } from '@/stores/vizStore'
import { deriveProvenance } from '@/lib/provenance'
import type { VizArtifact } from '@/api/types'

/**
 * Results / History — the full session record of every artifact APEXA produced,
 * browsable even after its tab is closed. This is the reproducibility surface:
 * each row shows what it is, the tool that made it, and when, and lets you
 * re-open, pin, or delete it. Newest first.
 */
export function CanvasHistory({ onOpen }: { onOpen: () => void }) {
  const { artifacts, openIds, pinned, reopen, togglePin, removeArtifact } = useVizStore()

  if (artifacts.length === 0) {
    return (
      <div style={{ padding: 24, color: 'var(--apexa-text-muted)', fontSize: 13 }}>
        No results yet. Ask APEXA in chat to calibrate, integrate, or plot — every
        result it produces is recorded here.
      </div>
    )
  }

  const rows = [...artifacts].reverse()   // newest first

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto', background: 'var(--apexa-panel-bg)' }}>
      <div style={{ padding: '10px 14px', fontSize: 11, color: 'var(--apexa-text-muted)' }}>
        {artifacts.length} result{artifacts.length === 1 ? '' : 's'} this session · newest first
      </div>
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {rows.map((a) => {
          const prov = deriveProvenance(a)
          const isOpen = openIds.includes(a.id)
          const isPinned = pinned.includes(a.id)
          return (
            <div
              key={a.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '9px 14px', borderTop: '1px solid var(--apexa-border)',
                cursor: 'pointer', background: 'transparent',
              }}
              onClick={() => { reopen(a.id); onOpen() }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--apexa-surface-2)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              title="Open in Canvas"
            >
              <TypeGlyph type={a.type} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 13, fontWeight: 600, color: 'var(--apexa-text)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {a.title}
                  {isOpen && <span style={{ marginLeft: 8, fontSize: 9, color: 'var(--apexa-text-muted)', fontWeight: 400 }}>· open</span>}
                </div>
                <div style={{
                  fontSize: 11, color: 'var(--apexa-text-muted)', marginTop: 1,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {a.type}{prov.tool ? ` · ${prov.tool}` : ''}{a.createdAt ? ` · ${fmtTime(a.createdAt)}` : ''}
                </div>
              </div>
              <RowBtn
                active={isPinned}
                title={isPinned ? 'Unpin' : 'Pin'}
                onClick={(e) => { e.stopPropagation(); togglePin(a.id) }}
              >{isPinned ? '★' : '☆'}</RowBtn>
              <RowBtn
                active={false}
                title="Delete from history"
                onClick={(e) => { e.stopPropagation(); removeArtifact(a.id) }}
              >✕</RowBtn>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function fmtTime(ms: number): string {
  const d = new Date(ms)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function RowBtn({ active, title, onClick, children }: {
  active: boolean; title: string; onClick: (e: React.MouseEvent) => void; children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        flexShrink: 0, width: 26, height: 26, borderRadius: 6,
        border: '1px solid var(--apexa-border)',
        background: active ? 'var(--apexa-accent, #3b82f6)' : 'var(--apexa-surface)',
        color: active ? '#fff' : 'var(--apexa-text-muted)',
        cursor: 'pointer', fontSize: 12, lineHeight: 1,
      }}
    >{children}</button>
  )
}

function TypeGlyph({ type }: { type: VizArtifact['type'] }) {
  const stroke = 'var(--apexa-text-muted)'
  const common = { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke, strokeWidth: 2 } as const
  if (type === 'plotly') return <svg {...common}><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></svg>
  if (type === 'diffraction') return <svg {...common}><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" /></svg>
  if (type === 'report' || type === 'html-report') return <svg {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" /></svg>
  if (type === 'image') return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" /></svg>
  return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="2" /></svg>
}
