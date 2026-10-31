import type { ToolResult } from '@/api/types'
import { useMemo, useState } from 'react'
import { downloadJson } from '@/lib/boundedPreview'

// Match the literal terminal ESC byte without embedding control characters in
// a regex literal. Directory output can contain ANSI SGR color sequences.
const ansiColors = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, 'g')
function stripAnsi(str: string): string {
  return str.replace(ansiColors, '')
}

function classifyEntry(line: string): { name: string; type: 'dir' | 'symlink' | 'file'; ext: string } {
  const clean = stripAnsi(line).trim()
  if (!clean) return { name: '', type: 'file', ext: '' }
  if (clean.includes('->')) {
    const [before] = clean.split('->')
    return { name: before.trim(), type: 'symlink', ext: '' }
  }
  if (clean.endsWith('/')) return { name: clean, type: 'dir', ext: '' }
  const ext = clean.includes('.') ? '.' + clean.split('.').pop()!.toLowerCase() : ''
  return { name: clean, type: 'file', ext }
}

const extColors: Record<string, string> = {
  '.py': '#22c55e', '.sh': '#22c55e', '.js': '#22c55e', '.ts': '#22c55e', '.tsx': '#22c55e',
  '.csv': '#eab308', '.dat': '#eab308', '.xy': '#eab308', '.txt': '#a3a3a3',
  '.tif': '#a855f7', '.tiff': '#a855f7', '.ge': '#a855f7', '.ge2': '#a855f7',
  '.ge3': '#a855f7', '.ge5': '#a855f7', '.h5': '#a855f7', '.hdf': '#a855f7',
  '.md': '#737373', '.log': '#737373', '.lock': '#737373', '.bin': '#737373',
  '.json': '#06b6d4', '.toml': '#06b6d4', '.yaml': '#06b6d4', '.yml': '#06b6d4',
  '.pdf': '#ef4444',
}

export function FileListCard({ result }: { result: ToolResult }) {
  const [page, setPage] = useState(0)
  const listing = result.data.listing ?? result.data.content ?? result.data.entries
  const { entries, dirPath } = useMemo(() => {
    if (Array.isArray(listing)) return { entries: listing, dirPath: String(result.data.path ?? '') }
    const lines = stripAnsi(typeof listing === 'string' ? listing : '').split('\n')
    return { dirPath: lines[0]?.trim() || '', entries: lines.slice(1).map(l => l.trim()).filter(l => l && !l.startsWith('(') && !/^\d+ director/.test(l)) }
  }, [listing, result.data.path])
  const currentPage = Math.min(page, Math.max(0, Math.ceil(entries.length / 100) - 1))
  const classified = entries.slice(currentPage * 100, (currentPage + 1) * 100).map(entry => {
    if (entry && typeof entry === 'object') return classifyEntry(String(entry.name ?? entry.path ?? '') + (entry.is_dir ? '/' : ''))
    return classifyEntry(String(entry))
  }).filter(e => e.name)
  const dirs = classified.filter(e => e.type === 'dir')
  const symlinks = classified.filter(e => e.type === 'symlink')
  const files = classified.filter(e => e.type === 'file')

  return (
    <div style={{
      borderRadius: 12,
      border: '1px solid var(--apexa-border)',
      background: 'var(--apexa-surface)',
      overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 12px',
        borderBottom: '1px solid var(--apexa-border)',
        background: 'var(--apexa-surface-2)',
      }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
        <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--apexa-text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {dirPath}
        </span>
      </div>

      <div style={{ maxHeight: 400, overflowY: 'auto', padding: '4px 4px' }}>
        {dirs.map((e, i) => (
          <div key={`d-${i}`} style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '3px 8px', borderRadius: 6, cursor: 'default',
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="#3b82f6" style={{ flexShrink: 0 }}>
              <path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z" />
            </svg>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#3b82f6' }}>{e.name}</span>
          </div>
        ))}
        {symlinks.map((e, i) => (
          <div key={`s-${i}`} style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '3px 8px', borderRadius: 6,
          }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="2" style={{ flexShrink: 0 }}>
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
            </svg>
            <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: '#06b6d4' }}>{e.name}</span>
          </div>
        ))}
        {files.map((e, i) => (
          <div key={`f-${i}`} style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '3px 8px', borderRadius: 6,
          }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--apexa-text-muted)" strokeWidth="2" style={{ flexShrink: 0 }}>
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
            </svg>
            <span style={{
              fontSize: 12, fontFamily: 'var(--font-mono)', flex: 1,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              color: extColors[e.ext] || 'var(--apexa-text)',
            }}>{e.name}</span>
          </div>
        ))}
      </div>

      <div style={{
        padding: '6px 12px',
        borderTop: '1px solid var(--apexa-border)',
        fontSize: 10, color: 'var(--apexa-text-muted)',
      }}>
        {entries.length ? `${currentPage * 100 + 1}–${Math.min((currentPage + 1) * 100, entries.length)} of ${entries.length} entries` : 'No entries'}
        {entries.length > 100 && <span className="inline-flex gap-2 ml-2">
          <button className="apexa-gallery-button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous entries</button>
          <button className="apexa-gallery-button" disabled={(currentPage + 1) * 100 >= entries.length} onClick={() => setPage(currentPage + 1)}>Next entries</button>
          <button className="apexa-gallery-button" onClick={() => downloadJson(result.data, 'directory-result')}>Download full listing</button>
        </span>}
      </div>
    </div>
  )
}
