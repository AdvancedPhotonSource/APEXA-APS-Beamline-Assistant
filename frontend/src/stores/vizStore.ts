import { create } from 'zustand'
import type { VizArtifact } from '@/api/types'

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
  addArtifacts: (artifacts: VizArtifact[]) => void
  updateArtifact: (id: string, patch: Partial<VizArtifact>) => void
  setActive: (id: string) => void
  showCanvas: () => void
  closeTab: (id: string) => void          // hide from tab strip, keep in history
  reopen: (id: string) => void            // re-open a history item as a tab
  removeArtifact: (id: string) => void    // hard delete from history
  togglePin: (id: string) => void
  toggleCompare: (id: string) => void
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

  addArtifact: (artifact) =>
    set((s) => {
      const a = stamp(artifact)
      const reveal = ['structure', 'sequence', 'report', 'html-report'].includes(a.type)
      return {
        revealRevision: s.revealRevision + (reveal ? 1 : 0),
        compareIds: reveal ? [] : s.compareIds,
        artifacts: s.artifacts.some(item => item.id === a.id)
          ? s.artifacts.map(item => item.id === a.id ? a : item) : [...s.artifacts, a],
        openIds: s.openIds.includes(a.id) ? s.openIds : [...s.openIds, a.id],
        activeId: a.id,
      }
    }),

  addArtifacts: (artifacts) =>
    set((s) => {
      const stamped = artifacts.map(stamp)
      const merged = new Map(s.artifacts.map(a => [a.id, a]))
      stamped.forEach(a => merged.set(a.id, a))
      const newIds = stamped.map((a) => a.id).filter((id) => !s.openIds.includes(id))
      const current = s.artifacts.find(a => a.id === s.activeId)
      const reveal = stamped.some(a => ['structure', 'sequence', 'report', 'html-report'].includes(a.type))
      return {
        revealRevision: s.revealRevision + (reveal ? 1 : 0),
        compareIds: reveal ? [] : s.compareIds,
        artifacts: [...merged.values()],
        openIds: [...new Set([...s.openIds, ...newIds])],
        // Prefer the scientific visual over a trailing parameter table.
        activeId: [...stamped].reverse().find(a => ['plotly', 'diffraction', 'image', 'html-report', 'report', 'structure', 'sequence'].includes(a.type))?.id
          ?? (current && ['plotly', 'diffraction', 'image', 'html-report', 'report', 'structure', 'sequence'].includes(current.type) ? current.id : null)
          ?? stamped[stamped.length - 1]?.id ?? s.activeId,
      }
    }),

  updateArtifact: (id, patch) => set(s => ({
    artifacts: s.artifacts.map(a => a.id === id ? { ...a, ...patch, id: a.id } : a),
  })),

  setActive: (id) => set(s => s.artifacts.some(a => a.id === id) ? {
    activeId: id,
    openIds: s.openIds.includes(id) ? s.openIds : [...s.openIds, id],
    compareIds: [],
    revealRevision: s.revealRevision + 1,
    canvasRevision: s.canvasRevision + 1,
  } : {}),

  showCanvas: () => set(s => ({
    compareIds: [],
    revealRevision: s.revealRevision + 1,
    canvasRevision: s.canvasRevision + 1,
  })),

  closeTab: (id) =>
    set((s) => {
      const openIds = s.openIds.filter((o) => o !== id)
      return {
        openIds,
        activeId: s.activeId === id ? (openIds[openIds.length - 1] ?? null) : s.activeId,
        compareIds: s.compareIds.filter((c) => c !== id),
      }
    }),

  reopen: (id) =>
    set((s) => ({
      openIds: s.openIds.includes(id) ? s.openIds : [...s.openIds, id],
      activeId: id,
      revealRevision: s.revealRevision + 1,
      canvasRevision: s.canvasRevision + 1,
    })),

  removeArtifact: (id) =>
    set((s) => {
      const filtered = s.artifacts.filter((a) => a.id !== id)
      const openIds = s.openIds.filter((o) => o !== id)
      return {
        artifacts: filtered,
        openIds,
        activeId: s.activeId === id ? (openIds[openIds.length - 1] ?? null) : s.activeId,
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
      if (s.compareIds.includes(id)) {
        return { compareIds: s.compareIds.filter((c) => c !== id) }
      }
      const next = [...s.compareIds, id]
      return { compareIds: next.slice(-MAX_COMPARE) }
    }),

  clearCompare: () => set({ compareIds: [] }),

  clear: () => set({ artifacts: [], openIds: [], activeId: null, pinned: [], compareIds: [] }),
}))
