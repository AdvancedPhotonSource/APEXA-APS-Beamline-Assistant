import { useEffect, useRef, useState, useCallback, type WheelEvent, type MouseEvent } from 'react'
import { useImageStore } from '@/stores/imageStore'

interface ViewerState {
  scale: number
  translateX: number
  translateY: number
  isDragging: boolean
  lastX: number
  lastY: number
}

export function DiffractionViewer({ fileId }: { fileId?: string }) {
  const { loadedImages, activeImageId } = useImageStore()
  const containerRef = useRef<HTMLDivElement>(null)
  const autoFit = useRef(true)
  const [cursorInfo, setCursorInfo] = useState<{ x: number; y: number; val?: number } | null>(null)

  const [view, setView] = useState<ViewerState>({
    scale: 1,
    translateX: 0,
    translateY: 0,
    isDragging: false,
    lastX: 0,
    lastY: 0,
  })

  const imageId = fileId ?? activeImageId
  const activeImage = imageId ? loadedImages.get(imageId) : null
  const imageWidth = activeImage?.stats.shape[1]
  const imageHeight = activeImage?.stats.shape[0]

  const fitToView = useCallback(() => {
    if (!imageWidth || !imageHeight || !containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    const scale = Math.min(rect.width / imageWidth, rect.height / imageHeight) * 0.95
    if (scale <= 0) return
    setView({ scale, translateX: (rect.width - imageWidth * scale) / 2,
      translateY: (rect.height - imageHeight * scale) / 2,
      isDragging: false, lastX: 0, lastY: 0 })
  }, [imageWidth, imageHeight])

  useEffect(() => {
    autoFit.current = true
    const host = containerRef.current
    if (!host) return
    const observer = new ResizeObserver(() => { if (autoFit.current) fitToView() })
    observer.observe(host)
    return () => observer.disconnect()
  }, [imageId, fitToView])

  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault()
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    autoFit.current = false

    const mouseX = e.clientX - rect.left
    const mouseY = e.clientY - rect.top
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    const newScale = Math.max(0.1, Math.min(50, view.scale * delta))

    // Zoom toward cursor (DIOPTAS-style)
    const scaleChange = newScale / view.scale
    const newTranslateX = mouseX - (mouseX - view.translateX) * scaleChange
    const newTranslateY = mouseY - (mouseY - view.translateY) * scaleChange

    setView((v) => ({
      ...v,
      scale: newScale,
      translateX: newTranslateX,
      translateY: newTranslateY,
    }))
  }, [view.scale, view.translateX, view.translateY])

  const handleMouseDown = useCallback((e: MouseEvent) => {
    if (e.button === 0) {
      autoFit.current = false
      setView((v) => ({ ...v, isDragging: true, lastX: e.clientX, lastY: e.clientY }))
    }
  }, [])

  const handleMouseMove = useCallback((e: MouseEvent) => {
    // Update cursor position info
    if (activeImage && containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect()
      const imgX = Math.floor((e.clientX - rect.left - view.translateX) / view.scale)
      const imgY = Math.floor((e.clientY - rect.top - view.translateY) / view.scale)
      if (imgX >= 0 && imgY >= 0 && imgX < activeImage.stats.shape[1] && imgY < activeImage.stats.shape[0]) {
        setCursorInfo({ x: imgX, y: imgY })
      } else {
        setCursorInfo(null)
      }
    }

    if (!view.isDragging) return
    const dx = e.clientX - view.lastX
    const dy = e.clientY - view.lastY
    setView((v) => ({
      ...v,
      translateX: v.translateX + dx,
      translateY: v.translateY + dy,
      lastX: e.clientX,
      lastY: e.clientY,
    }))
  }, [view.isDragging, view.lastX, view.lastY, view.translateX, view.translateY, view.scale, activeImage])

  const handleMouseUp = useCallback(() => {
    setView((v) => ({ ...v, isDragging: false }))
  }, [])

  const resetView = () => {
    autoFit.current = false
    setView({ scale: 1, translateX: 0, translateY: 0, isDragging: false, lastX: 0, lastY: 0 })
  }

  if (!activeImage) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
        No image loaded. Use the file browser to open a diffraction image.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-[280px] min-w-0">
      {/* Toolbar */}
      <div className="flex flex-wrap shrink-0 items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-900/50 text-xs">
        <span className="text-zinc-400 font-mono truncate max-w-[200px]" title={activeImage.filename}>
          {activeImage.filename}
        </span>
        <span className="text-zinc-600">|</span>
        <span className="text-zinc-500">
          {activeImage.stats.shape[1]}x{activeImage.stats.shape[0]}
        </span>
        <span className="text-zinc-600">|</span>
        <span className="text-zinc-500">{Math.round(view.scale * 100)}%</span>

        <div className="flex-1" />

        <button onClick={() => { autoFit.current = false; setView((v) => ({ ...v, scale: v.scale * 1.3 })) }}
          className="px-2 py-0.5 bg-zinc-800 hover:bg-zinc-700 rounded text-zinc-300 transition-colors">+</button>
        <button onClick={() => { autoFit.current = false; setView((v) => ({ ...v, scale: v.scale * 0.7 })) }}
          className="px-2 py-0.5 bg-zinc-800 hover:bg-zinc-700 rounded text-zinc-300 transition-colors">-</button>
        <button onClick={() => { autoFit.current = true; fitToView() }}
          className="px-2 py-0.5 bg-zinc-800 hover:bg-zinc-700 rounded text-zinc-300 transition-colors">Fit</button>
        <button onClick={resetView}
          className="px-2 py-0.5 bg-zinc-800 hover:bg-zinc-700 rounded text-zinc-300 transition-colors">1:1</button>
      </div>

      {/* Image canvas */}
      <div
        ref={containerRef}
        className="flex-1 min-h-0 overflow-hidden bg-black cursor-crosshair relative select-none"
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <img
          src={activeImage.preview.startsWith('data:') ? activeImage.preview : `data:image/png;base64,${activeImage.preview}`}
          alt={activeImage.filename}
          draggable={false}
          style={{
            width: imageWidth, height: imageHeight, maxWidth: 'none',
            transformOrigin: '0 0',
            transform: `translate(${view.translateX}px, ${view.translateY}px) scale(${view.scale})`,
            imageRendering: view.scale > 3 ? 'pixelated' : 'auto',
          }}
          className="absolute top-0 left-0"
        />
      </div>

      {/* Pixel info bar */}
      <div className="flex items-center justify-between px-3 py-1 border-t border-zinc-800 bg-zinc-900/80 text-xs text-zinc-500">
        {cursorInfo ? (
          <span className="font-mono">
            x: {cursorInfo.x}, y: {cursorInfo.y}
            {cursorInfo.val != null && ` | I: ${cursorInfo.val.toFixed(1)}`}
          </span>
        ) : (
          <span>Hover over image for coordinates</span>
        )}
        <span className="font-mono">
          min: {activeImage.stats.min.toFixed(0)} | max: {activeImage.stats.max.toFixed(0)} | mean: {activeImage.stats.mean.toFixed(1)}
        </span>
      </div>
    </div>
  )
}
