import type { ToolResult } from '@/api/types'

function count(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '—'
}

function fixed(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toPrecision(4) : '—'
}

export function DynamicsCard({ result }: { result: ToolResult }) {
  const data = result.data
  const tracks = Array.isArray(data.tracks_preview)
    ? data.tracks_preview as Record<string, unknown>[]
    : []
  const unusable = Array.isArray(data.unusable_patterns) ? data.unusable_patterns.length : 0

  return <div style={{ marginTop: 10, border: '1px solid rgba(34,211,238,.25)', borderRadius: 10, overflow: 'hidden', background: 'var(--apexa-surface)' }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '9px 12px', borderBottom: '1px solid var(--apexa-border)' }}>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase' }}>Diffraction feature tracking</span>
      <span style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>{String(data.status ?? result.status)}</span>
    </div>
    <div style={{ padding: '9px 12px', fontSize: 12, color: 'var(--apexa-text)' }}>
      <div>{count(data.usable_patterns)} patterns · {count(data.candidate_peaks)} peak candidates · {count(data.tracks)} tracks</div>
      <div style={{ marginTop: 5, color: 'var(--apexa-text-2)' }}>
        Axis: {String(data.axis_label ?? 'frame')} · link radius: {fixed(data.link_radius)} diffraction-axis units
      </div>
      {typeof data.trajectory_csv === 'string' && <div style={{ marginTop: 5, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 10, overflowWrap: 'anywhere' }}>
        Full table: {data.trajectory_csv}
      </div>}
      {tracks.length > 0 && <div style={{ marginTop: 8, display: 'grid', gap: 4 }}>
        {tracks.slice(0, 6).map((track) => <div key={String(track.track_id)} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 10, color: 'var(--apexa-text-2)' }}>
          <span>Feature {String(track.track_id)} · {count(track.observations)} observations</span>
          <span>{fixed(track.position_start)} → {fixed(track.position_end)}</span>
        </div>)}
      </div>}
      {unusable > 0 && <div style={{ marginTop: 6, fontSize: 10, color: 'var(--apexa-text-muted)' }}>
        {unusable} patterns could not be analyzed; see the manifest for details.
      </div>}
      <div style={{ marginTop: 8, color: 'var(--apexa-text-muted)', fontSize: 10 }}>
        Feature IDs are continuity tracks, not phase labels. Position-fit uncertainty omits calibration, background, overlap and model errors.
      </div>
    </div>
  </div>
}
