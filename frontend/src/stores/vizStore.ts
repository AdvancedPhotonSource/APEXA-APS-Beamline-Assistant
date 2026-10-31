import { create } from 'zustand'
import type { VizArtifact } from '@/api/types'
import { canonicalFileArtifact } from '../lib/canvasIdentity.ts'

interface VizState {
  // `artifacts` is the full session HISTORY — nothing is dropped when a tab is
  // closed (reproducibility: you can always answer "how was this made?"). `openIds`
  // is the subset currently shown as tabs. Closing a tab hides it from the strip
  // but keeps it in history; removeArtifact() is a hard delete used by the history
  // list only.
  artifacts: VizArtifact[]
  openIds: string[]             // artifact ids currently open as tabs
  activeId: string | null
  pinned: string[]              // artifact ids the user wants to keep handy
  compareIds: string[]          // up to 2 ids shown side-by-side
  revealRevision: number       // explicit native-view requests reveal the Active pane
  canvasRevision: number       // user actions reveal a hidden workspace canvas

  addArtifact: (artifact: VizArtifact) => void
  addArtifacts: (artifacts: VizArtifact[], options?: { activate?: boolean }) => void
  updateArtifact: (id: string, patch: Partial<VizArtifact>) => void
  setActive: (id: string) => void
  showCanvas: () => void
  closeTab: (id: string) => void          // hide from tab strip, keep in history
  reopen: (id: string) => void            // re-open a history item as a tab
  removeArtifact: (id: string) => void    // hard delete from history
  togglePin: (id: string) => void
  toggleCompare: (id: string) => void
  showComparison: () => void
  clearCompare: () => void
  clear: () => void
}

const MAX_COMPARE = 2

const stamp = (a: VizArtifact): VizArtifact =>
  a.createdAt ? a : { ...a, createdAt: Date.now() }

export const useVizStore = create<VizState>((set) => ({
  artifacts: [],
  openIds: [],
  activeId: null,
  pinned: [],
  compareIds: [],
  revealRevision: 0,
  canvasRevision: 0,

  // Explicit opens always select the requested result. Background delivery only
  // opens new identities; repeated delivery never resurrects a closed tab.
  addArtifact: (artifact) => useVizStore.getState().addArtifacts([artifact], { activate: true }),

  addArtifacts: (artifacts, options = {}) =>
    set((s) => {
      if (!artifacts.length) return {}
      const merged = new Map(s.artifacts.map(a => [a.id, a]))
      const incoming = new Map<string, VizArtifact>()
      for (const artifact of artifacts.map(canonicalFileArtifact)) {
        const previous = merged.get(artifact.id)
        const a = stamp({ ...artifact, createdAt: previous?.createdAt ?? artifact.createdAt,
          sourceMessageId: artifact.sourceMessageId || previous?.sourceMessageId || '' })
        // Delivery of the original file request must not erase an edited viewer.
        if (previous && ['structure', 'sequence'].includes(a.type) && a.data && typeof a.data === 'object') {
          const old = previous.data && typeof previous.data === 'object' ? previous.data as Record<string, unknown> : {}
          a.data = { ...a.data as Record<string, unknown>,
            ...Object.fromEntries(['viewerState', 'appliedContext', 'preview'].filter(key => key in old).map(key => [key, old[key]])) }
        }
        merged.set(a.id, a)
        incoming.set(a.id, a)
      }
      const known = new Set(s.artifacts.map(a => a.id))
      const opened = [...incoming.values()].filter(a => options.activate || !known.has(a.id))
      const selected = [...opened].reverse().find(a => ['plotly', 'diffraction', 'image', 'html-report', 'report', 'structure', 'sequence', 'refinement'].includes(a.type))
        ?? [...opened].reverse().find(a => a.type !== 'text')
        ?? (options.activate || !s.activeId ? opened.at(-1) : undefined)
      return {
        artifacts: [...merged.values()],
        openIds: [...new Set([...s.openIds, ...opened.map(a => a.id)])],
        activeId: selected?.id ?? s.activeId,
        // A new result consistently reveals Active, regardless of renderer type.
        revealRevision: s.revealRevision + (selected ? 1 : 0),
        canvasRevision: s.canvasRevision + (selected ? 1 : 0),
        compareIds: selected && s.compareIds.length === MAX_COMPARE ? [] : s.compareIds,
      }
    }),

  updateArtifact: (id, patch) => set(s => ({
    artifacts: s.artifacts.map(a => a.id === id ? { ...a, ...patch, id: a.id } : a),
  })),

  setActive: (id) => set(s => s.artifacts.some(a => a.id === id) ? {
    activeId: id,
    openIds: s.openIds.includes(id) ? s.openIds : [...s.openIds, id],
    compareIds: s.compareIds.length === MAX_COMPARE ? [] : s.compareIds,
    revealRevision: s.revealRevision + 1,
    canvasRevision: s.canvasRevision + 1,
  } : {}),

  showCanvas: () => set(s => ({
    compareIds: s.compareIds.length === MAX_COMPARE ? [] : s.compareIds,
    revealRevision: s.revealRevision + 1,
    canvasRevision: s.canvasRevision + 1,
  })),

  closeTab: (id) =>
    set((s) => {
      const openIds = s.openIds.filter((o) => o !== id)
      return {
        openIds,
        activeId: s.activeId === id ? (openIds[Math.min(s.openIds.indexOf(id), openIds.length - 1)] ?? null) : s.activeId,
        compareIds: s.compareIds.filter((c) => c !== id),
      }
    }),

  reopen: (id) => useVizStore.getState().setActive(id),

  removeArtifact: (id) =>
    set((s) => {
      const filtered = s.artifacts.filter((a) => a.id !== id)
      const openIds = s.openIds.filter((o) => o !== id)
      return {
        artifacts: filtered,
        openIds,
        activeId: s.activeId === id ? (openIds[Math.min(s.openIds.indexOf(id), openIds.length - 1)] ?? null) : s.activeId,
        pinned: s.pinned.filter((p) => p !== id),
        compareIds: s.compareIds.filter((c) => c !== id),
      }
    }),

  togglePin: (id) =>
    set((s) => ({
      pinned: s.pinned.includes(id)
        ? s.pinned.filter((p) => p !== id)
        : [...s.pinned, id],
    })),

  // Toggle an artifact into the compare set (FIFO, capped at MAX_COMPARE).
  toggleCompare: (id) =>
    set((s) => {
      if (!s.artifacts.some(a => a.id === id)) return {}
      if (s.compareIds.includes(id)) {
        return { compareIds: s.compareIds.filter((c) => c !== id) }
      }
      const next = [...s.compareIds, id]
      return { compareIds: next.slice(-MAX_COMPARE) }
    }),

  showComparison: () => set(s => {
    const ids = s.compareIds.filter(id => s.artifacts.some(a => a.id === id))
    if (ids.length !== MAX_COMPARE) return {}
    return { openIds: [...new Set([...s.openIds, ...ids])], activeId: ids[0],
      revealRevision: s.revealRevision + 1, canvasRevision: s.canvasRevision + 1 }
  }),

  clearCompare: () => set({ compareIds: [] }),

  clear: () => set({ artifacts: [], openIds: [], activeId: null, pinned: [], compareIds: [] }),
}))
