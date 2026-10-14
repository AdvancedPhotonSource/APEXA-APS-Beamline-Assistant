import { memo } from 'react'
import Plot from './LazyPlot'
import { useThemeStore } from '@/stores/themeStore'

interface PlotlyChartProps {
  data: unknown
}

function normalizeLayout(layout: Record<string, unknown>): Partial<Plotly.Layout> {
  const result = { ...layout }
  if (typeof result.title === 'string') {
    result.title = { text: result.title }
  }
  if (typeof (result.xaxis as Record<string, unknown>)?.title === 'string') {
    result.xaxis = { ...(result.xaxis as object), title: { text: (result.xaxis as Record<string, unknown>).title } }
  }
  if (typeof (result.yaxis as Record<string, unknown>)?.title === 'string') {
    result.yaxis = { ...(result.yaxis as object), title: { text: (result.yaxis as Record<string, unknown>).title } }
  }
  return result as Partial<Plotly.Layout>
}

const THEME_LAYOUTS: Record<string, Partial<Plotly.Layout>> = {
  dark: {
    paper_bgcolor: 'transparent',
    plot_bgcolor: '#18181b',
    font: { color: '#a1a1aa', family: 'Inter, system-ui, sans-serif', size: 12 },
    xaxis: { gridcolor: '#27272a', zerolinecolor: '#3f3f46' },
    yaxis: { gridcolor: '#27272a', zerolinecolor: '#3f3f46' },
  },
  light: {
    paper_bgcolor: 'transparent',
    plot_bgcolor: '#ffffff',
    font: { color: '#555770', family: 'Inter, system-ui, sans-serif', size: 12 },
    xaxis: { gridcolor: '#e2e4e9', zerolinecolor: '#c8cad0' },
    yaxis: { gridcolor: '#e2e4e9', zerolinecolor: '#c8cad0' },
  },
  midnight: {
    paper_bgcolor: 'transparent',
    plot_bgcolor: '#0f1729',
    font: { color: '#8899b8', family: 'Inter, system-ui, sans-serif', size: 12 },
    xaxis: { gridcolor: '#1c2a48', zerolinecolor: '#243358' },
    yaxis: { gridcolor: '#1c2a48', zerolinecolor: '#243358' },
  },
}

export const PlotlyChart = memo(function PlotlyChart({ data }: PlotlyChartProps) {
  const mode = useThemeStore((s) => s.mode)
  const plotData = data as { data?: Plotly.Data[]; layout?: Record<string, unknown> }

  const themeLayout = THEME_LAYOUTS[mode] ?? THEME_LAYOUTS.dark
  const defaultLayout: Partial<Plotly.Layout> = {
    ...themeLayout,
    margin: { t: 40, r: 20, b: 50, l: 60 },
    autosize: true,
  }

  const userLayout = plotData.layout ? normalizeLayout(plotData.layout) : {}

  return (
    <div className="w-full h-full min-h-[300px]">
      <Plot
        data={plotData.data ?? []}
        layout={{ ...defaultLayout, ...userLayout,
          xaxis: { ...themeLayout.xaxis, automargin: true, ...userLayout.xaxis },
          yaxis: { ...themeLayout.yaxis, automargin: true, ...userLayout.yaxis },
          uirevision: 'preserve-interaction',
        }}
        config={{
          responsive: true,
          displayModeBar: true,
          modeBarButtonsToRemove: ['lasso2d', 'select2d'] as Plotly.ModeBarDefaultButtons[],
          displaylogo: false,
        }}
        useResizeHandler
        style={{ width: '100%', height: '100%' }}
      />
    </div>
  )
})
