import { useEffect, useState } from 'react'
import { useImageStore } from '@/stores/imageStore'
import { useVizStore } from '@/stores/vizStore'
import { VizTabs } from './VizTabs'
import { Dashboard } from './Dashboard'
import { CanvasHistory } from './CanvasHistory'
import { ResultGallery } from './ResultGallery'
import { ArtifactBody } from './ArtifactBody'
import { ProvenanceBar } from './ProvenanceBar'
import { ViewerControls } from '@/components/viewer/ViewerControls'
import { ImageActions } from '@/components/viewer/ImageActions'
import { deriveProvenance } from '@/lib/provenance'
import type { VizArtifact } from '@/api/types'

/**
 * VizPanel = the Artifact Canvas: the "evidence" zone of the chat-drives /
 * canvas-holds / rail-watches layout. It holds the scientific objects APEXA
 * produces (plot, image, table) as stable, addressable things you can pin,
 * compare side-by-side, and trace back to what produced them (provenance footer).
 * A persistent header states that role and toggles between the open artifact
 * (Active) and the full session record (Results) for reproducibility.
 */
export function VizPanel({ onFocus, focused = false }: { onFocus?: () => void; focused?: boolean }) {
  const { artifacts, openIds, activeId, setActive, closeTab, pinned, togglePin, compareIds, toggleCompare, clearCompare, revealRevision } =
    useVizStore()
  type CanvasView = 'active' | 'gallery' | 'results'
  const [choice, setChoice] = useState<{ view: CanvasView; revision: number }>({ view: 'active', revision: revealRevision })
  // Derive from the store revision so requests made while this canvas is hidden
  // still open Active when React resumes its effects and viewers.
  const view = choice.revision === revealRevision ? choice.view : 'active'
  const setView = (next: CanvasView) => setChoice({ view: next, revision: revealRevision })

  // Nothing produced yet → the empty state explains the panel's purpose itself.
  if (artifacts.length === 0) return <Dashboard />

  const openArtifacts = openIds
    .map((id) => artifacts.find((a) => a.id === id))
    .filter((a): a is VizArtifact => !!a)
  const active = openArtifacts.find((a) => a.id === activeId) ?? openArtifacts[openArtifacts.length - 1]
  const compareArtifacts = compareIds
    .map((id) => artifacts.find((a) => a.id === id))
    .filter((a): a is VizArtifact => !!a)
  const inCompare = compareArtifacts.length === 2

  return (
    <div className="apexa-canvas" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, minWidth: 0, background: 'var(--apexa-surface)' }}>
      {/* Canvas identity + Active/Results segmented control (always visible so the
          panel's role is legible). */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', flexShrink: 0, alignItems: 'center', gap: 8,
        padding: '7px 12px', borderBottom: '1px solid var(--apexa-border)',
        background: 'linear-gradient(180deg, var(--apexa-surface-2), transparent)',
      }}>
        <span style={{ width: 3, height: 12, borderRadius: 2, background: 'var(--apexa-accent-grad)', boxShadow: 'var(--apexa-glow)' }} />
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase', color: 'var(--apexa-text-2)' }}>
          Canvas
        </span>
        <span style={{ flex: 1 }} />
        {onFocus && <ToolbarBtn active={focused} onClick={onFocus} title="Expand the canvas for inspection or presentation">
          {focused ? 'Restore layout' : 'Expand canvas'}
        </ToolbarBtn>}
        <div style={{ display: 'flex', borderRadius: 7, overflow: 'hidden', border: '1px solid var(--apexa-border)' }}>
          <SegBtn active={view === 'active'} onClick={() => setView('active')}>Active</SegBtn>
          <SegBtn active={view === 'gallery'} onClick={() => setView('gallery')}>Gallery</SegBtn>
          <SegBtn active={view === 'results'} onClick={() => setView('results')}>Results ({artifacts.length})</SegBtn>
        </div>
      </div>

      {view === 'gallery' ? (
        <ResultGallery onOpen={() => setView('active')} />
      ) : view === 'results' ? (
        <CanvasHistory onOpen={() => setView('active')} />
      ) : !active ? (
        <div style={{ padding: 24, color: 'var(--apexa-text-muted)', fontSize: 13 }}>
          All tabs closed. Switch to <b>Results ({artifacts.length})</b> to re-open a result.
        </div>
      ) : (
      <ActiveView
        active={active}
        openArtifacts={openArtifacts}
        setActive={setActive}
        closeTab={closeTab}
        pinned={pinned}
        togglePin={togglePin}
        compareIds={compareIds}
        toggleCompare={toggleCompare}
        clearCompare={clearCompare}
        compareArtifacts={compareArtifacts}
        inCompare={inCompare}
      />
      )}
    </div>
  )
}

function SegBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '3px 10px', border: 'none', fontSize: 11, cursor: 'pointer',
        background: active ? 'var(--apexa-accent-grad)' : 'var(--apexa-surface)',
        color: active ? '#fff' : 'var(--apexa-text-2)',
        boxShadow: active ? 'var(--apexa-glow)' : 'none',
      }}
    >{children}</button>
  )
}

function ActiveView({
  active, openArtifacts, setActive, closeTab, pinned, togglePin,
  compareIds, toggleCompare, clearCompare, compareArtifacts, inCompare,
}: {
  active: VizArtifact
  openArtifacts: VizArtifact[]
  setActive: (id: string) => void
  closeTab: (id: string) => void
  pinned: string[]
  togglePin: (id: string) => void
  compareIds: string[]
  toggleCompare: (id: string) => void
  clearCompare: () => void
  compareArtifacts: VizArtifact[]
  inCompare: boolean
}) {
  const isPinned = pinned.includes(active.id)
  const isComparing = compareIds.includes(active.id)
  const [controlsOpen, setControlsOpen] = useState(false)
  const setActiveImage = useImageStore(s => s.setActiveImage)
  const fileId = active.type === 'diffraction' ? (active.data as { fileId?: string }).fileId : undefined
  useEffect(() => {
    if (fileId) setActiveImage(fileId)
  }, [fileId, setActiveImage])

  return (
    <>
      <VizTabs artifacts={openArtifacts} activeId={active.id} onSelect={setActive} onClose={closeTab} />

      {/* Canvas toolbar — pin / compare actions on the active artifact */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap', flexShrink: 0,
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          borderBottom: '1px solid var(--apexa-border)',
          background: 'var(--apexa-surface-2)',
          fontSize: 12,
        }}
      >
        <span style={{ fontWeight: 600, color: 'var(--apexa-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {active.title}
        </span>
        <span style={{ flex: 1 }} />
        {active.type === 'diffraction' && fileId && !inCompare && (
          <ToolbarBtn active={false} onClick={() => {
            const image = useImageStore.getState().loadedImages.get(fileId ?? '')
            if (image) useVizStore.getState().addArtifact({ id: `sequence-${image.path}`, type: 'sequence', title: image.filename, data: { path: image.path }, sourceMessageId: active.sourceMessageId })
          }} title="Browse frames, apply dark/mask, inspect and simulate rings">Analyze frames</ToolbarBtn>
        )}
        {active.type === 'diffraction' && fileId && !inCompare && (
          <ToolbarBtn active={controlsOpen} onClick={() => setControlsOpen(v => !v)} title="Show image contrast and analysis controls">
            {controlsOpen ? 'Hide controls' : 'Image controls'}
          </ToolbarBtn>
        )}
        <ToolbarBtn active={isPinned} onClick={() => togglePin(active.id)} title="Pin this artifact">
          {isPinned ? '★ pinned' : '☆ pin'}
        </ToolbarBtn>
        <ToolbarBtn active={isComparing} onClick={() => toggleCompare(active.id)} title="Add to side-by-side compare (max 2)">
          ⧉ compare{compareIds.length ? ` (${compareIds.length}/2)` : ''}
        </ToolbarBtn>
        {inCompare && (
          <ToolbarBtn active={false} onClick={clearCompare} title="Exit compare view">
            ✕ exit compare
          </ToolbarBtn>
        )}
      </div>

      {/* Body — single active artifact, or two-up compare */}
      <div className="apexa-canvas-body" style={{ flex: 1, minHeight: 0, minWidth: 0, overflow: 'hidden', display: 'flex' }}>
        {inCompare ? (
          <div className="apexa-compare-grid">
            {compareArtifacts.map((a, i) => (
              <div
                key={a.id}
                style={{
                  minWidth: 0, minHeight: 320,
                  display: 'flex',
                  flexDirection: 'column',
                  overflow: 'hidden',
                  borderLeft: i === 1 ? '1px solid var(--apexa-border)' : undefined,
                }}
              >
                <div style={{ padding: '4px 10px', fontSize: 11, color: 'var(--apexa-text-muted)', borderBottom: '1px solid var(--apexa-border)' }}>
                  {a.title}
                </div>
                <div style={{ flex: 1, minHeight: 0, minWidth: 0, overflow: 'auto' }}>
                  <ArtifactBody artifact={a} />
                </div>
                <ProvenanceBar prov={deriveProvenance(a)} />
              </div>
            ))}
          </div>
        ) : (
          <>
            <div style={{ flex: 1, minHeight: 0, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ flex: 1, minHeight: 0, minWidth: 0, overflow: 'auto' }}>
                <ArtifactBody artifact={active} />
              </div>
              <ProvenanceBar prov={deriveProvenance(active)} />
            </div>

            {active.type === 'diffraction' && fileId && controlsOpen && (
              <div
                className="apexa-image-controls"
                style={{
                  width: 270,
                  flexShrink: 0,
                  borderLeft: '1px solid var(--apexa-border)',
                  background: 'var(--apexa-surface-2)',
                  overflowY: 'auto',
                }}
              >
                {/* Grounded asks + MIDAS-viewer launcher for the active image */}
                <ImageActions />
                {/* Contrast / colormap / radial profile */}
                <ViewerControls />
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}

function ToolbarBtn({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean
  onClick: () => void
  title: string
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        padding: '3px 10px',
        borderRadius: 6,
        border: active ? '1px solid transparent' : '1px solid var(--apexa-border)',
        background: active ? 'var(--apexa-accent-grad)' : 'var(--apexa-surface)',
        color: active ? '#fff' : 'var(--apexa-text)',
        boxShadow: active ? 'var(--apexa-glow)' : 'none',
        fontSize: 11,
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </button>
  )
}
