import { useState } from 'react'
import type { ToolResult } from '@/api/types'
import { ToolResultCard } from './ToolResultCard'
import { boundedPreview } from '@/lib/boundedPreview'

/**
 * Claude-Code-style collapsible tool-call box: a compact one-line header
 * (▸ tool · status) that expands to show the call inputs ("in:") and the
 * rendered output ("out:"). Collapsed by default so a long tool sequence stays
 * scannable; click to enlarge any single call.
 */

const STATUS_COLOR: Record<string, string> = {
  success: '#22c55e', completed: '#22c55e',
  error: '#ef4444', failed: '#ef4444', warning: '#f59e0b',
  rejected: '#ef4444', denied: '#ef4444', provisional: '#f59e0b',
}

function oneLineSummary(r: ToolResult): string {
  const d = r.data || {}
  // Prefer the most informative field the tool returned.
  for (const k of ['message', 'output_file', 'output_root', 'result_folder', 'lineout_xy', 'param_file', 'n_tracks', 'status']) {
    const v = (d as Record<string, unknown>)[k]
    if (typeof v === 'string' && v) return v.length > 90 ? v.slice(0, 90) + '…' : v
  }
  if (typeof (d as Record<string, unknown>).succeeded === 'number')
    return `${(d as Record<string, unknown>).succeeded} succeeded`
  if (typeof (d as Record<string, unknown>).tracks === 'number')
    return String((d as Record<string, unknown>).tracks) + ' tracks'
  return ''
}

export function ToolCallBox({ result }: { result: ToolResult }) {
  const [open, setOpen] = useState(false)
  const color = STATUS_COLOR[result.status] ?? 'var(--apexa-text-muted)'
  const summary = oneLineSummary(result)

  return (
    <div
      style={{
        marginTop: 6,
        border: '1px solid var(--apexa-border)',
        borderRadius: 8,
        overflow: 'hidden',
        background: 'var(--apexa-surface-2)',
        boxShadow: 'var(--apexa-elev-1)',
      }}
    >
      {/* Header — click to expand/collapse */}
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, width: '100%',
          padding: '6px 10px', border: 'none', background: 'transparent',
          cursor: 'pointer', textAlign: 'left', color: 'var(--apexa-text)',
        }}
      >
        <span style={{ color: 'var(--apexa-text-muted)', fontSize: 10, width: 8 }}>{open ? '▾' : '▸'}</span>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0 }} />
        <span style={{ fontFamily: 'var(--apexa-mono, ui-monospace, monospace)', fontSize: 12, fontWeight: 600 }}>
          {result.tool}
        </span>
        {summary && !open && (
          <span style={{ color: 'var(--apexa-text-muted)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {summary}
          </span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ color, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4 }}>{result.status}</span>
      </button>

      {open && (
        <div style={{ borderTop: '1px solid var(--apexa-border)', padding: '8px 10px' }}>
          {result.args && Object.keys(result.args).length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 10, color: 'var(--apexa-text-muted)', fontWeight: 600, marginBottom: 2 }}>IN</div>
              <pre style={preStyle}>{boundedPreview(result.args)}</pre>
            </div>
          )}
          <div>
            <div style={{ fontSize: 10, color: 'var(--apexa-text-muted)', fontWeight: 600, marginBottom: 2 }}>OUT</div>
            {/* Rich domain card when we recognize the tool, else raw JSON */}
            <ToolResultCard result={result} />
          </div>
        </div>
      )}
    </div>
  )
}

const preStyle: React.CSSProperties = {
  margin: 0, padding: '6px 8px', borderRadius: 6,
  background: 'var(--apexa-surface)', border: '1px solid var(--apexa-border)',
  fontFamily: 'var(--apexa-mono, ui-monospace, monospace)', fontSize: 11,
  color: 'var(--apexa-text)', overflow: 'auto', maxHeight: 240, whiteSpace: 'pre-wrap',
}
