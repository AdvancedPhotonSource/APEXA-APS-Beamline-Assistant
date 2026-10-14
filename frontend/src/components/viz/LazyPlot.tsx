import { lazy, Suspense, useEffect, useRef, useState, type ComponentProps } from 'react'

// The ONE place plotly.js is referenced. It is dynamically imported so the ~4.5 MB
// library ships as its own chunk, fetched only when the first plot renders. Any
// eager `import Plot from '@/lib/plotly'` would pull it back into the main bundle —
// so all Plotly consumers must import THIS instead.
const RawPlot = lazy(() => import('@/lib/plotly'))

type PlotProps = ComponentProps<typeof RawPlot>

export default function LazyPlot(props: PlotProps) {
  const host = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    if (!host.current) return
    let frame = 0
    const observer = new ResizeObserver(([entry]) => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const width = Math.floor(entry.contentRect.width)
        const height = Math.floor(entry.contentRect.height)
        if (width > 0 && height > 0) {
          setSize(previous => previous.width === width && previous.height === height
            ? previous : { width, height })
        }
      })
    })
    observer.observe(host.current)
    return () => { observer.disconnect(); cancelAnimationFrame(frame) }
  }, [])
  return (
    <div ref={host} style={{ ...props.style, width: '100%', height: '100%', minWidth: 0, minHeight: 0 }}>
    <Suspense
      fallback={
        <div
          className="w-full h-full min-h-[200px] flex items-center justify-center text-xs"
          style={{ color: 'var(--apexa-text-muted)' }}
        >
          loading chart…
        </div>
      }
    >
      <RawPlot {...props}
        layout={{ ...props.layout, autosize: true, ...(size.width ? size : {}) }}
        style={{ width: '100%', height: '100%' }}
      />
    </Suspense>
    </div>
  )
}
