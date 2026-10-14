import { useState } from 'react'
import type { Provenance } from '@/api/types'
import { Quantity } from './Quantity'

/**
 * ProvenanceBar — the trust + reproducibility footer for an artifact.
 *
 * Every scientific result should answer "what produced this?" without leaving the
 * view: which inputs, which parameters, which tool+version, when, and the exact
 * command to reproduce it. This is the single biggest differentiator between a
 * chatbot and an instrument a scientist will trust during beamtime.
 */

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 8px',
        borderRadius: 6,
        background: 'var(--apexa-surface-2)',
        border: '1px solid var(--apexa-border)',
        fontSize: 11,
        color: 'var(--apexa-text-muted)',
        maxWidth: 320,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

function fmtTime(ts?: number): string {
  if (!ts) return ''
  try {
    return new Date(ts).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
  } catch {
    return ''
  }
}

export function ProvenanceBar({ prov }: { prov?: Provenance }) {
  const [copied, setCopied] = useState(false)
  if (!prov) return null

  const params = prov.params ?? {}
  const paramKeys = Object.keys(params)

  const copyCommand = async () => {
    if (!prov.command) return
    try {
      await navigator.clipboard.writeText(prov.command)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div
      style={{
        borderTop: '1px solid var(--apexa-border)',
        flexShrink: 0, maxHeight: 160, overflow: 'auto',
        background: 'var(--apexa-surface-2)',
        padding: '8px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        fontSize: 11,
      }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        <span style={{ color: 'var(--apexa-text-muted)', fontWeight: 600, letterSpacing: 0.3 }}>
          PROVENANCE
        </span>
        {prov.tool && (
          <Chip>
            🔧 {prov.tool}
            {prov.version ? ` v${prov.version}` : ''}
          </Chip>
        )}
        {prov.inputs?.map((f) => (
          <Chip key={f}>📄 {f.split('/').pop()}</Chip>
        ))}
        {prov.timestamp && <Chip>🕑 {fmtTime(prov.timestamp)}</Chip>}
      </div>

      {paramKeys.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'baseline' }}>
          {paramKeys.map((k) => {
            const v = params[k]
            return typeof v === 'number' ? (
              <Quantity key={k} label={k} value={v} />
            ) : (
              <span key={k} style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>
                <b style={{ color: 'var(--apexa-text)' }}>{k}</b> {String(v)}
              </span>
            )
          })}
        </div>
      )}

      {prov.command && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <code
            style={{
              flex: 1,
              fontFamily: 'var(--apexa-mono, ui-monospace, monospace)',
              fontSize: 11,
              color: 'var(--apexa-text)',
              background: 'var(--apexa-surface)',
              border: '1px solid var(--apexa-border)',
              borderRadius: 6,
              padding: '4px 8px',
              overflow: 'auto',
              whiteSpace: 'nowrap',
            }}
          >
            {prov.command}
          </code>
          <button
            onClick={copyCommand}
            style={{
              flexShrink: 0,
              padding: '4px 10px',
              borderRadius: 6,
              border: '1px solid var(--apexa-border)',
              background: 'var(--apexa-surface)',
              color: copied ? '#22c55e' : 'var(--apexa-text)',
              fontSize: 11,
              cursor: 'pointer',
            }}
            title="Copy the exact command to reproduce this result"
          >
            {copied ? '✓ copied' : '⤓ reproduce'}
          </button>
        </div>
      )}
    </div>
  )
}
