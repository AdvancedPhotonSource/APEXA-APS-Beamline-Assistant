import { create } from 'zustand'
import type { BrowseEntry } from '@/api/types'
import { browseDirectory } from '@/api/endpoints'

interface FileState {
  currentPath: string
  parentPath: string | null
  entries: BrowseEntry[]
  isLoading: boolean
  error: string | null
  offset: number
  total: number
  nextOffset: number | null

  browse: (path?: string) => Promise<void>
  restorePath: (path: string) => void
  goUp: () => Promise<void>
  page: (offset: number) => Promise<void>
  cancel: () => void
}

let browseController: AbortController | null = null
export const useFileStore = create<FileState>((set, get) => ({
  currentPath: '.',
  parentPath: null,
  entries: [],
  isLoading: false,
  error: null,
  offset: 0,
  total: 0,
  nextOffset: null,

  restorePath: path => {
    browseController?.abort()
    set({ currentPath: path, parentPath: null, entries: [], isLoading: false, error: null, offset: 0, total: 0, nextOffset: null })
  },

  browse: async (path?: string) => {
    if (path && path !== get().currentPath) get().restorePath(path)
    await get().page(0)
  },

  page: async (offset: number) => {
    browseController?.abort()
    const controller = new AbortController()
    browseController = controller
    set({ isLoading: true, error: null })
    try {
      const result = await browseDirectory(get().currentPath, controller.signal, offset)
      if (controller.signal.aborted || controller !== browseController) return
      set({
        currentPath: result.path,
        parentPath: result.parent,
        entries: result.entries,
        isLoading: false,
        offset: result.offset ?? 0,
        total: result.total ?? result.entries.length,
        nextOffset: result.has_more ? result.next_offset ?? null : null,
      })
    } catch (e) {
      if (controller.signal.aborted || controller !== browseController) return
      set({ error: String(e), isLoading: false })
    }
  },

  cancel: () => { browseController?.abort(); set({ isLoading: false }) },

  goUp: async () => {
    const { parentPath } = get()
    if (parentPath) {
      await get().browse(parentPath)
    }
  },
}))
