import { useState, useEffect, useRef } from 'react'
import { useImageStore } from '@/stores/imageStore'
import { fetchColormaps } from '@/api/endpoints'

const DEFAULT_COLORMAPS = ['viridis', 'plasma', 'inferno', 'magma', 'gray', 'hot', 'jet', 'bone']

export function ViewerControls() {
  const { activeImageId, loadedImages, settings, updateSettings, applySettings, computeRadialProfile, isLoading } = useImageStore()
  const [colormaps, setColormaps] = useState<string[]>(DEFAULT_COLORMAPS)
  const applyTimer = useRef<number | null>(null)

  const activeImage = activeImageId ? loadedImages.get(activeImageId) : null

  useEffect(() => {
    fetchColormaps().then((r) => setColormaps(r.colormaps)).catch(() => {})
  }, [])

  useEffect(() => () => {
    if (applyTimer.current) clearTimeout(applyTimer.current)
  }, [activeImageId])

  // Live update: change a slider/colormap → push to the backend (debounced) so the
  // image updates immediately instead of needing an explicit Apply click.
  const live = (partial: Parameters<typeof updateSettings>[0]) => {
    updateSettings(partial)
    if (applyTimer.current) clearTimeout(applyTimer.current)
    applyTimer.current = window.setTimeout(() => { applySettings() }, 180)
  }

  if (!activeImage) {
    return (
      <div className="p-3 text-xs text-zinc-600">
        Load an image to adjust settings.
      </div>
    )
  }

  const { stats } = activeImage

  return (
    <div className="p-3 space-y-4 text-xs">
      <div>
        <div className="text-zinc-400 font-medium mb-2 uppercase tracking-wider">Contrast</div>

        <label className="flex items-center justify-between text-zinc-500 mb-1">
          <span>Min</span>
          <span className="font-mono">{settings.vmin.toFixed(0)}</span>
        </label>
        <input
          type="range"
          min={stats.min}
          max={stats.max}
          step={(stats.max - stats.min) / 1000}
          value={settings.vmin}
          onChange={(e) => live({ vmin: parseFloat(e.target.value) })}
          className="w-full h-1 bg-zinc-700 rounded-full appearance-none cursor-pointer accent-blue-500"
        />

        <label className="flex items-center justify-between text-zinc-500 mb-1 mt-2">
          <span>Max</span>
          <span className="font-mono">{settings.vmax.toFixed(0)}</span>
        </label>
        <input
          type="range"
          min={stats.min}
          max={stats.max}
          step={(stats.max - stats.min) / 1000}
          value={settings.vmax}
          onChange={(e) => live({ vmax: parseFloat(e.target.value) })}
          className="w-full h-1 bg-zinc-700 rounded-full appearance-none cursor-pointer accent-blue-500"
        />

        <label className="flex items-center justify-between text-zinc-500 mb-1 mt-2">
          <span>Gamma</span>
          <span className="font-mono">{settings.gamma.toFixed(2)}</span>
        </label>
        <input
          type="range"
          min={0.1}
          max={5.0}
          step={0.05}
          value={settings.gamma}
          onChange={(e) => live({ gamma: parseFloat(e.target.value) })}
          className="w-full h-1 bg-zinc-700 rounded-full appearance-none cursor-pointer accent-blue-500"
        />
      </div>

      <div>
        <div className="text-zinc-400 font-medium mb-2 uppercase tracking-wider">Colormap</div>
        <select
          value={settings.colormap}
          onChange={(e) => live({ colormap: e.target.value })}
          className="w-full bg-zinc-800 text-zinc-300 rounded-md px-2 py-1.5 border border-zinc-700 focus:outline-none focus:border-blue-500"
        >
          {colormaps.map((cm) => (
            <option key={cm} value={cm}>{cm}</option>
          ))}
        </select>
      </div>

      <button
        onClick={applySettings}
        disabled={isLoading}
        className="w-full py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 text-white rounded-lg transition-colors font-medium"
      >
        {isLoading ? 'Applying...' : 'Apply'}
      </button>

      <div className="border-t border-zinc-800 pt-3">
        <div className="text-zinc-400 font-medium mb-2 uppercase tracking-wider">Analysis</div>
        <button
          onClick={computeRadialProfile}
          className="w-full py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors"
        >
          Radial Profile
        </button>
      </div>

      <div className="border-t border-zinc-800 pt-3">
        <div className="text-zinc-400 font-medium mb-2 uppercase tracking-wider">Image Info</div>
        <div className="space-y-1 text-zinc-500 font-mono">
          <div>Size: {stats.shape[1]} x {stats.shape[0]}</div>
          <div>Type: {stats.dtype}</div>
          <div>Range: [{stats.min.toFixed(0)}, {stats.max.toFixed(0)}]</div>
          <div>Mean: {stats.mean.toFixed(1)}</div>
          <div>Std: {stats.std.toFixed(1)}</div>
        </div>
      </div>
    </div>
  )
}
