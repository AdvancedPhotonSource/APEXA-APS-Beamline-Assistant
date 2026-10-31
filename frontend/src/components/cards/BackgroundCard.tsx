import type { ToolResult } from '@/api/types'

function number(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '—'
}

export function BackgroundCard({ result }: { result: ToolResult }) {
  const data = result.data
  const shape = Array.isArray(data.input_shape) ? data.input_shape.join(' × ') : '—'
  return <div style={{ marginTop: 10, border: '1px solid rgba(34,211,238,.25)', borderRadius: 10, overflow: 'hidden', background: 'var(--apexa-surface)' }}>
    <div style={{ padding: '9px 12px', borderBottom: '1px solid var(--apexa-border)', fontSize: 11, fontWeight: 700, textTransform: 'uppercase' }}>
      Detector background combine
    </div>
    <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--apexa-text)' }}>
      {number(data.input_count)} frames · {String(data.method ?? '—')} · {shape} pixels · {String(data.input_dtype ?? '—')} → {String(data.output_dtype ?? '—')}
      {typeof data.acquisition_context === 'string' && <div style={{ marginTop: 6 }}>Declared acquisition group: {data.acquisition_context}</div>}
      {typeof data.output_file === 'string' && <div style={{ marginTop: 6, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 10, overflowWrap: 'anywhere' }}>
        Output: {data.output_file}
      </div>}
      <div role="note" style={{ marginTop: 9, padding: 8, borderRadius: 7, background: 'var(--apexa-surface-2)', color: 'var(--apexa-text-muted)', fontSize: 10 }}>
        Pixel averaging does not verify that exposure, attenuation, detector settings, or the experiment's subtraction method match the sample. Review the manifest and acquisition record before using this image as a correction.
      </div>
    </div>
  </div>
}
