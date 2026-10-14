import { Activity, useState, useEffect, useCallback, useRef } from 'react'
import { Header } from './Header'
import { IconRail, type RailView } from './IconRail'
import { SidePanel } from './SidePanel'
import { ChatPanel } from '@/components/chat/ChatPanel'
import { VizPanel } from '@/components/viz/VizPanel'
import { FacilityStatus } from './FacilityStatus'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { useThemeStore } from '@/stores/themeStore'
import { useVizStore } from '@/stores/vizStore'
import { useChatStore } from '@/stores/chatStore'
import { workspaceLayout, parseWorkspaceView, WORKSPACE_LAYOUT_KEY, MIN_CHAT_WIDTH, MIN_CANVAS_WIDTH, DIVIDER_WIDTH, type WorkspaceView } from '@/lib/workspaceLayout'

function DragHandle({ onDrag, onToggle, label, value, min, max, className = '' }: {
  onDrag: (deltaX: number) => void
  onToggle: () => void
  label: string
  value: number
  min: number
  max: number
  className?: string
}) {
  const lastX = useRef<number | null>(null)
  const [active, setActive] = useState(false)

  return (
    <div
      className={`apexa-pane-divider ${className}`}
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={Math.round(max)}
      tabIndex={0}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        e.preventDefault()
        e.currentTarget.focus()
        e.currentTarget.setPointerCapture(e.pointerId)
        lastX.current = e.clientX
        setActive(true)
      }}
      onPointerMove={(e) => {
        if (lastX.current === null) return
        const dx = e.clientX - lastX.current
        lastX.current = e.clientX
        onDrag(dx)
      }}
      onPointerUp={(e) => e.currentTarget.releasePointerCapture(e.pointerId)}
      onLostPointerCapture={() => { lastX.current = null; setActive(false) }}
      onDoubleClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault()
          onDrag((e.key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 50 : 20))
        } else if (e.key === 'Enter') {
          e.preventDefault()
          onToggle()
        }
      }}
      title={`${label} · drag or use arrow keys · double-click or Enter to collapse`}
      data-dragging={active}
    >
      <span aria-hidden="true">⋮</span>
    </div>
  )
}

export function AppShell() {
  // First launch exposes both conversation and scientific canvas. Full-width
  // choices are explicit and remembered; rail panels open on demand.
  const [activeView, setActiveView] = useState<RailView>(null)
  const mode = useThemeStore((s) => s.mode)
  const artifactCount = useVizStore((s) => s.artifacts.length)
  const [sideW, setSideW] = useState(260)
  const [chatRatio, setChatRatio] = useState(0.5)
  const [layoutChoice, setLayoutChoice] = useState<WorkspaceView | null>(() => {
    try { return parseWorkspaceView(localStorage.getItem(WORKSPACE_LAYOUT_KEY)) } catch { return null }
  })
  const [workspaceWidth, setWorkspaceWidth] = useState(window.innerWidth - 68)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const { canSplit, view } = workspaceLayout(layoutChoice, workspaceWidth)

  useEffect(() => {
    const workspace = workspaceRef.current
    if (!workspace) return
    const observer = new ResizeObserver(([entry]) => setWorkspaceWidth(entry.contentRect.width))
    observer.observe(workspace)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', mode)
  }, [mode])

  // Explicit "View in panel" / file-open actions reveal results even after the
  // user chose Chat. Background results respect that full-width writing choice.
  useEffect(() => useVizStore.subscribe((state, previous) => {
    if (state.canvasRevision !== previous.canvasRevision) {
      setLayoutChoice(current => current === 'canvas' || !canSplit ? 'canvas' : 'split')
      if (!canSplit || window.innerWidth <= 900) setActiveView(null)
    }
  }), [canSplit])

  // Tasks launched from the canvas/sidebar must make their conversation visible.
  useEffect(() => useChatStore.subscribe((state, previous) => {
    const message = state.messages.at(-1)
    if (message?.role === 'user' && message.id !== previous.messages.at(-1)?.id) {
      setLayoutChoice(current => current === 'canvas' ? (canSplit ? 'split' : 'chat') : current)
      if (!canSplit || window.innerWidth <= 900) setActiveView(null)
    }
  }), [canSplit])

  const chooseView = (next: WorkspaceView) => {
    setLayoutChoice(next)
    try { localStorage.setItem(WORKSPACE_LAYOUT_KEY, next) } catch { /* Private browsing may disallow storage. */ }
    if (next !== 'split') setActiveView(null)
  }

  const handleSideDrag = useCallback((dx: number) => {
    setSideW(w => Math.max(200, Math.min(420, window.innerWidth - 68 - MIN_CHAT_WIDTH, w + dx)))
  }, [])
  const availableWidth = Math.max(1, workspaceWidth - DIVIDER_WIDTH)
  const maxChatWidth = Math.max(MIN_CHAT_WIDTH, availableWidth - MIN_CANVAS_WIDTH)
  const visibleChatWidth = Math.max(MIN_CHAT_WIDTH, Math.min(maxChatWidth, availableWidth * chatRatio))
  const handleChatDrag = (dx: number) => {
    setChatRatio(ratio => {
      const currentWidth = Math.max(MIN_CHAT_WIDTH, Math.min(maxChatWidth, availableWidth * ratio))
      return Math.max(MIN_CHAT_WIDTH, Math.min(maxChatWidth, currentWidth + dx)) / availableWidth
    })
  }

  return (
    <div className="apexa-app-shell" style={{ width: '100%', display: 'flex', flexDirection: 'column', background: 'transparent', color: 'var(--apexa-text)' }}>
      <Header>
        <div role="group" aria-label="Workspace layout" className="apexa-layout-switch">
          {(['chat', 'split', 'canvas'] as const).map(option => (
            <button key={option} type="button" aria-pressed={view === option}
              disabled={option === 'split' && !canSplit}
              onClick={() => chooseView(option)}
              title={option === 'chat' ? 'Full-width conversation'
                : option === 'canvas' ? 'Full-width scientific canvas'
                : canSplit ? 'Conversation and results side by side' : 'Widen the workspace to show chat and canvas side by side'}>
              {option === 'chat' ? 'Chat' : option === 'split' ? 'Split' : 'Canvas'}
              {option === 'canvas' && artifactCount > 0 && <span className="apexa-result-count">{artifactCount}</span>}
            </button>
          ))}
        </div>
      </Header>
      <div style={{ position: 'relative', display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <IconRail activeView={activeView} onSelect={setActiveView} />

        {activeView && (
          <>
            <div className="apexa-side-pane" style={{ width: sideW, flexShrink: 0, minHeight: 0, overflow: 'hidden', background: 'var(--apexa-surface)' }}>
              <SidePanel view={activeView} onClose={() => setActiveView(null)} />
            </div>
            <DragHandle className="apexa-sidebar-divider" label="Resize sidebar" value={sideW} min={200} max={420}
              onDrag={handleSideDrag} onToggle={() => setActiveView(null)} />
          </>
        )}

        <div ref={workspaceRef} className="apexa-workspace" style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
          <div className="apexa-analysis-workspace" style={{ display: 'flex', flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
            {/* Keep the same ChatPanel mounted in every layout to retain drafts,
                attachments and scroll position while inspecting results. */}
            <div key="chat" className="apexa-conversation-pane" style={{
              display: view === 'canvas' ? 'none' : 'flex',
              flexDirection: 'column', flex: view === 'split' ? '0 0 auto' : 1,
              width: view === 'split' ? visibleChatWidth : undefined,
              minWidth: 0, minHeight: 0, overflow: 'hidden',
            }}>
              <ChatPanel />
            </div>
            {view === 'split' && (
              <DragHandle key="divider" label="Resize conversation" value={visibleChatWidth}
                min={MIN_CHAT_WIDTH} max={maxChatWidth}
                onDrag={handleChatDrag} onToggle={() => chooseView('chat')} />
            )}
            <div key="canvas" style={{ display: view === 'chat' ? 'none' : 'block', flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
              <Activity mode={view === 'chat' ? 'hidden' : 'visible'}>
                <VizPanel focused={view === 'canvas'} onFocus={() => chooseView(view === 'canvas' ? (canSplit ? 'split' : 'chat') : 'canvas')} />
              </Activity>
            </div>
          </div>
        </div>
      </div>

      <FacilityStatus />
      <ConfirmModal />
    </div>
  )
}
