import { useState } from 'react'
import type { ToolResult } from '@/api/types'
import { useVizStore } from '@/stores/vizStore'
import { provenanceFromToolResult } from '@/lib/provenance'
import { extractArtifacts, resultIdentity } from '@/lib/parseToolResult'

/**
 * Shared chrome for every chat-side tool card. Adds the two things that make a
 * tool result a *scientific* result rather than a chat bubble:
 *   1. "open in Canvas" — promote the result to a pinnable/comparable artifact.
 *   2. a compact provenance strip — tool+version, timestamp, and a copyable
 *      reproduce command, so what produced the numbers is always one glance away.
 */
export function ToolCardChrome({
  result,
  children,
}: {
  result: ToolResult
  children: React.ReactNode
}) {
  const addArtifact = useVizStore((s) => s.addArtifact)
  const [copied, setCopied] = useState(false)
  const prov = provenanceFromToolResult(result)

  const openInCanvas = () => {
    const visuals = extractArtifacts([result], '')
    if (visuals.length) {
      useVizStore.getState().addArtifacts(visuals, { activate: true })
      return
    }
    addArtifact({
      id: `tr-${resultIdentity(result)}`,
      type: 'table',
      title: result.tool,
      data: result.data,
      sourceMessageId: '',
      provenance: prov,
    })
  }

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

  const ts = prov.timestamp
    ? new Date(prov.timestamp).toISOString().slice(11, 19) + 'Z'
    : ''

  return (
    <div>
      {typeof result.data.mode === 'string' && result.data.mode !== 'real' && (
        <div style={{ fontSize: 12, padding: '6px 8px', color: 'var(--apexa-text-2)', background: 'var(--apexa-surface-2)' }}>
          {result.data.mode === 'synthetic' ? 'Simulation using generated inputs'
            : result.data.mode === 'input-inspection' ? 'Input inspection only — inversion has not run'
            : result.data.mode === 'deferred' ? 'Real-data workflow is not implemented in this adapter'
            : result.data.mode}
        </div>
      )}
      {children}
      <div
        className="flex items-center gap-2 flex-wrap"
        style={{
          marginTop: 4,
          padding: '4px 6px 0',
          fontSize: 11,
          color: 'var(--apexa-text-muted)',
        }}
      >
        <button
          onClick={openInCanvas}
          title="Open this result as a pinnable / comparable artifact in the Canvas"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 8px',
            borderRadius: 6,
            border: '1px solid var(--apexa-border)',
            background: 'var(--apexa-surface)',
            color: 'var(--apexa-text)',
            cursor: 'pointer',
            fontSize: 11,
          }}
        >
          ⧉ open in Canvas
        </button>

        {prov.command && (
          <button
            onClick={copyCommand}
            title="Copy the command to reproduce this result"
            style={{
              padding: '2px 8px',
              borderRadius: 6,
              border: '1px solid var(--apexa-border)',
              background: 'var(--apexa-surface)',
              color: copied ? '#22c55e' : 'var(--apexa-text-muted)',
              cursor: 'pointer',
              fontSize: 11,
            }}
          >
            {copied ? '✓ copied' : '⤓ reproduce'}
          </button>
        )}

        <span style={{ flex: 1 }} />
        {prov.tool && prov.tool !== result.tool && <span>via {prov.tool}</span>}
        {ts && <span style={{ fontFamily: 'var(--apexa-mono, monospace)' }}>{ts}</span>}
      </div>
    </div>
  )
}
