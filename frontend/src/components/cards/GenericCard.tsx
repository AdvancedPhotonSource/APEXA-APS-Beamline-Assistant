import { useState } from 'react'
import type { ToolResult } from '@/api/types'
import { boundedPreview, downloadJson } from '@/lib/boundedPreview'

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(6)
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'string') return v.length > 1500 ? v.slice(0, 1500) + '…' : v
  return boundedPreview(v)
}

export function GenericCard({ result }: { result: ToolResult }) {
  const { tool, data } = result
  const [expanded, setExpanded] = useState(false)

  const entries = Object.entries(data as Record<string, unknown>).filter(
    ([k]) => !['tool', 'status', 'success'].includes(k)
  )
  const simple = entries.filter(([, v]) => typeof v !== 'object' || v === null)
  const complex = entries.filter(([, v]) => typeof v === 'object' && v !== null)

  return (
    <div className="mt-3 rounded-xl border border-zinc-700/50 bg-zinc-900/50 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-700/30 bg-zinc-800/40">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-zinc-400" />
          <span className="text-[11px] font-medium text-zinc-400 uppercase tracking-wide font-mono">{tool}</span>
        </div>
        <span className={`text-[10px] font-medium ${['success', 'completed'].includes(result.status) ? 'text-emerald-400/80' : 'text-amber-400'}`}>{result.status}</span>
      </div>
      {simple.length > 0 && (
        <div className="px-3 py-2 space-y-1">
          {simple.slice(0, 40).map(([k, v]) => (
            <div key={k} className="flex justify-between items-baseline gap-4 text-xs">
              <span className="text-zinc-500 shrink-0">{k.replace(/_/g, ' ')}</span>
              <span className="text-zinc-200 font-mono text-[11px] truncate text-right">{formatValue(v)}</span>
            </div>
          ))}
        </div>
      )}
      {complex.length > 0 && (
        <div className="border-t border-zinc-700/30">
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-full px-3 py-1.5 text-[10px] text-zinc-500 hover:text-zinc-300 bg-transparent border-none cursor-pointer text-left flex items-center gap-1 transition-colors"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 150ms' }}>
              <polyline points="9 18 15 12 9 6" />
            </svg>
            {expanded ? 'Collapse' : 'Show'} details ({complex.length})
          </button>
          {expanded && (
            <pre className="px-3 pb-2 text-[11px] text-zinc-400 font-mono whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto">
              {boundedPreview(Object.fromEntries(complex.slice(0, 40)))}
            </pre>
          )}
        </div>
      )}
      <button className="apexa-gallery-button m-2" onClick={() => downloadJson(data, tool)}>Download full result</button>
      {(expanded || simple.length > 40) && <p className="px-3 pb-2 text-[10px] text-zinc-500">Preview is bounded. Download includes the complete recorded result.</p>}
    </div>
  )
}
