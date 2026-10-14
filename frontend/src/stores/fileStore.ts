import { create } from 'zustand'
import type { BrowseEntry } from '@/api/types'
import { browseDirectory } from '@/api/endpoints'

interface FileState {
  currentPath: string
  parentPath: string | null
  entries: BrowseEntry[]
  isLoading: boolean
  error: string | null

  browse: (path?: string) => Promise<void>
  restorePath: (path: string) => void
  goUp: () => Promise<void>
}

let browseController: AbortController | null = null
export const useFileStore = create<FileState>((set, get) => ({
  currentPath: '.',
  parentPath: null,
  entries: [],
  isLoading: false,
  error: null,

  restorePath: path => {
    browseController?.abort()
    set({ currentPath: path, parentPath: null, entries: [], isLoading: false, error: null })
  },

  browse: async (path?: string) => {
    browseController?.abort()
    const controller = new AbortController()
    browseController = controller
    set({ isLoading: true, error: null })
    try {
      const result = await browseDirectory(path ?? get().currentPath, controller.signal)
      if (controller.signal.aborted) return
      set({
        currentPath: result.path,
        parentPath: result.parent,
        entries: result.entries,
        isLoading: false,
      })
    } catch (e) {
      if (controller.signal.aborted) return
      set({ error: String(e), isLoading: false })
    }
  },

  goUp: async () => {
    const { parentPath } = get()
    if (parentPath) {
      await get().browse(parentPath)
    }
  },
}))
