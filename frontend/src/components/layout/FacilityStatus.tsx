import { useEffect, useState } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { useVizStore } from '@/stores/vizStore'

interface Status {
  status: string
  mcp_client_connected: boolean
  connected_servers: string[]
  active_connections: number
  network_tier?: string
  science_location?: string
  available_models?: string[]
}

/**
 * Always-visible facility status bar — the "facility rail watches" zone, condensed
 * into a persistent IDE-style strip. Shows live backend/session state so an operator
 * always knows the system is connected and whether a job is running.
 */
export function FacilityStatus() {
  const [status, setStatus] = useState<Status | null>(null)
  const [reachable, setReachable] = useState(true)
  const isLoading = useChatStore((s) => s.isLoading)
  const progress = useChatStore((s) => s.progress)
  const artifactCount = useVizStore((s) => s.artifacts.length)

  useEffect(() => {
    let alive = true
    let inFlight: AbortController | null = null
    const poll = async () => {
      // Don't poll a backgrounded tab — avoids flooding /api/status in the logs.
      if (typeof document !== 'undefined' && document.hidden) return
      if (inFlight) return
      const controller = new AbortController()
      inFlight = controller
      const timeout = setTimeout(() => controller.abort(), 5000)
      try {
        const r = await fetch('/api/status', { signal: controller.signal })
        if (!r.ok) throw new Error(`Status unavailable: ${r.status}`)
        const j = (await r.json()) as Status
        if (alive) {
          setStatus(j)
          setReachable(true)
        }
      } catch {
        if (alive) setReachable(false)
      } finally {
        clearTimeout(timeout)
        inFlight = null
      }
    }
    poll()
    const t = setInterval(poll, 15000) // 15s — status changes slowly
    return () => {
      alive = false
      clearInterval(t)
      inFlight?.abort()
    }
  }, [])

  const online = reachable && !!status?.mcp_client_connected
  const dot = isLoading ? '#f59e0b' : online ? '#22c55e' : '#ef4444'
  const jobLabel = isLoading
    ? progress?.step
      ? `running · ${progress.step}${progress.percent ? ` ${Math.round(progress.percent)}%` : ''}`
      : 'running…'
    : 'idle'

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        height: 24,
        padding: '0 12px',
        borderTop: '1px solid var(--apexa-border)',
        background: 'color-mix(in srgb, var(--apexa-surface-2) 78%, transparent)',
        backdropFilter: 'blur(10px) saturate(1.2)',
        WebkitBackdropFilter: 'blur(10px) saturate(1.2)',
        boxShadow: '0 -1px 14px rgba(59,130,246,0.05)',
        color: 'var(--apexa-text-muted)',
        fontSize: 11,
        flexShrink: 0,
        fontFamily: 'var(--apexa-mono, ui-monospace, monospace)',
        overflow: 'hidden',
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: dot, boxShadow: `0 0 6px ${dot}` }} />
        {isLoading ? 'busy' : online ? 'connected' : reachable ? 'no MCP' : 'offline'}
      </span>

      <Sep />
      <span>job: {jobLabel}</span>
      {status?.network_tier && <span>network: {status.network_tier} · data: {status.science_location}</span>}

      {status?.connected_servers && status.connected_servers.length > 0 && (
        <>
          <Sep />
          <span>servers: {status.connected_servers.join(', ')}</span>
        </>
      )}

      <Sep />
      <span>artifacts: {artifactCount}</span>

      <span style={{ flex: 1 }} />
      <span>APEXA</span>
    </div>
  )
}

function Sep() {
  return <span style={{ opacity: 0.4 }}>│</span>
}
