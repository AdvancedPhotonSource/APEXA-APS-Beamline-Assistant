import { resultIdentity } from '../lib/resultIdentity.ts'
import { create } from 'zustand'
import type { ImageStats, VizArtifact } from '@/api/types'
import { loadImageByPath, adjustImage, fetchRadialProfile } from '@/api/endpoints'
import { useVizStore } from './vizStore'

interface LoadedImage {
  fileId: string
  filename: string
  stats: ImageStats
  preview: string
  path: string
  settings?: ViewerSettings
}

interface ViewerSettings {
  vmin: number
  vmax: number
  gamma: number
  colormap: string
}

interface ImageState {
  loadedImages: Map<string, LoadedImage>
  activeImageId: string | null
  settings: ViewerSettings
  isLoading: boolean

  loadImage: (path: string) => Promise<void>
  restoreImage: (artifact: VizArtifact, isCurrent: () => boolean) => Promise<void>
  resetSession: () => void
  setActiveImage: (id: string) => void
  updateSettings: (partial: Partial<ViewerSettings>) => void
  applySettings: () => Promise<void>
  computeRadialProfile: () => Promise<void>
}

let adjustmentController: AbortController | null = null
let imageSession = 0
export const useImageStore = create<ImageState>((set, get) => ({
  loadedImages: new Map(),
  activeImageId: null,
  settings: { vmin: 0, vmax: 100, gamma: 1.0, colormap: 'viridis' },
  isLoading: false,

  resetSession: () => {
    imageSession += 1
    adjustmentController?.abort()
    set({ loadedImages: new Map(), activeImageId: null, isLoading: false })
  },

  restoreImage: async (artifact, isCurrent) => {
    const epoch = imageSession
    const data = artifact.data as { path?: string; settings?: ViewerSettings }
    if (!data.path) throw new Error('This saved image has no source path. Reopen it from Data.')
    try {
      const result = await loadImageByPath(data.path)
      if (!isCurrent() || epoch !== imageSession) return
      if (!result.success) throw new Error('Could not reopen the saved image. Check that its source file is available.')
      const settings = data.settings ?? { vmin: result.stats.min, vmax: result.stats.max, gamma: 1, colormap: 'viridis' }
      let preview = result.preview
      if (data.settings) {
        const adjusted = await adjustImage(result.file_id, settings)
        if (!isCurrent() || epoch !== imageSession) return
        if (!adjusted.success) throw new Error('Could not restore the saved image display settings.')
        preview = adjusted.image
      }
      if (!isCurrent() || epoch !== imageSession) return
      set(s => ({ loadedImages: new Map(s.loadedImages).set(result.file_id, {
        fileId: result.file_id, filename: result.filename, stats: result.stats, preview, path: data.path!, settings,
      }) }))
      useVizStore.getState().updateArtifact(artifact.id, { data: { ...data, fileId: result.file_id, preview, stats: result.stats } })
      if (useVizStore.getState().activeId === artifact.id) get().setActiveImage(result.file_id)
    } catch (error) {
      if (!isCurrent() || epoch !== imageSession) return
      throw error
    }
  },

  loadImage: async (path: string) => {
    const epoch = imageSession
    set({ isLoading: true })
    try {
      const result = await loadImageByPath(path)
      if (epoch !== imageSession) return
      if (!result.success) throw new Error(`Could not load ${path}`)
      if (result.success) {
        const img: LoadedImage = {
          fileId: result.file_id,
          filename: result.filename,
          stats: result.stats,
          preview: result.preview,
          path,
        }
        set((s) => {
          const newMap = new Map(s.loadedImages)
          newMap.set(result.file_id, img)
          return {
            loadedImages: newMap,
            activeImageId: result.file_id,
            settings: {
              vmin: result.stats.min,
              vmax: result.stats.max,
              gamma: 1.0,
              colormap: 'viridis',
            },
          }
        })

        // Push to viz panel as artifact
        useVizStore.getState().addArtifact({
          id: `img-${result.file_id}`,
          type: 'diffraction',
          title: result.filename,
          data: {
            fileId: result.file_id,
            path,
            preview: result.preview,
            stats: result.stats,
          },
          sourceMessageId: '',
        })
      }
    } catch (e) {
      if (epoch !== imageSession) return
      throw new Error(`Failed to load image: ${String(e)}`)
    } finally {
      if (epoch === imageSession) set({ isLoading: false })
    }
  },

  setActiveImage: (id: string) => {
    if (id === get().activeImageId) return
    adjustmentController?.abort()
    const image = get().loadedImages.get(id)
    if (!image) return
    set({ activeImageId: id, isLoading: false, settings: image.settings ?? {
      vmin: image.stats.min, vmax: image.stats.max, gamma: 1, colormap: 'viridis',
    } })
  },

  updateSettings: (partial) =>
    set((s) => ({ settings: { ...s.settings, ...partial } })),

  applySettings: async () => {
    const { activeImageId, settings } = get()
    if (!activeImageId) return
    adjustmentController?.abort()
    const controller = new AbortController()
    adjustmentController = controller
    set({ isLoading: true })
    try {
      const result = await adjustImage(activeImageId, settings, controller.signal)
      if (controller.signal.aborted) return
      if (result.success) {
        set((s) => {
          const newMap = new Map(s.loadedImages)
          const existing = newMap.get(activeImageId)
          if (existing) {
            newMap.set(activeImageId, { ...existing, preview: result.image, settings })
          }
          return { loadedImages: newMap }
        })

        // Update viz artifact too
        const vizStore = useVizStore.getState()
        const existing = vizStore.artifacts.find(a => a.type === 'diffraction' && (a.data as { fileId?: string }).fileId === activeImageId)
        if (existing) {
          vizStore.updateArtifact(existing.id, {
            data: {
              ...(existing.data as Record<string, unknown>),
              preview: result.image,
              settings,
            },
          })
        }
      }
    } catch (e) {
      if (controller.signal.aborted) return
      console.error('Failed to adjust image:', e)
    } finally {
      if (!controller.signal.aborted) set({ isLoading: false })
    }
  },

  computeRadialProfile: async () => {
    const epoch = imageSession
    const { activeImageId, loadedImages } = get()
    if (!activeImageId) return
    const img = loadedImages.get(activeImageId)
    if (!img) return

    try {
      const centerX = Math.floor(img.stats.shape[1] / 2)
      const centerY = Math.floor(img.stats.shape[0] / 2)
      const result = await fetchRadialProfile(activeImageId, centerX, centerY)
      if (epoch !== imageSession) return

      if (result.success) {
        useVizStore.getState().addArtifact({
          id: `radial-${resultIdentity({ tool: 'radial-profile', status: 'completed', data: { path: img.path, centerX, centerY, radii: result.radii, intensity: result.intensity ?? result.intensities } })}`,
          type: 'plotly',
          title: `Radial Profile - ${img.filename}`,
          data: {
            data: [
              {
                x: result.radii,
                // backend returns `intensity` (singular); tolerate either.
                y: result.intensity ?? result.intensities,
                type: 'scatter',
                mode: 'lines',
                name: 'Radial Intensity',
                line: { color: '#3b82f6', width: 1.5 },
              },
            ],
            layout: {
              title: `Radial Profile: ${img.filename}`,
              xaxis: { title: 'Radius (pixels)' },
              yaxis: { title: 'Mean Intensity' },
            },
          },
          sourceMessageId: '',
        })
      }
    } catch (e) {
      if (epoch !== imageSession) return
      console.error('Failed to compute radial profile:', e)
    }
  },
}))
