import { useState } from 'react'
import { useVizStore } from '@/stores/vizStore'
import { useImageStore } from '@/stores/imageStore'
import { ArtifactBody } from './ArtifactBody'

/** A bounded visual overview. Opening a card restores the full scientific viewer. */
export function ResultGallery({ onOpen }: { onOpen: () => void }) {
  const { artifacts, reopen, compareIds, toggleCompare, showComparison } = useVizStore()
  const loadedImages = useImageStore(s => s.loadedImages)
  const [limit, setLimit] = useState(12)
  const visuals = [...artifacts].reverse().filter(a => ['plotly', 'image', 'diffraction', 'report', 'html-report', 'sequence', 'structure', 'refinement'].includes(a.type))
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 12 }}>
      <p style={{ fontSize: 12, color: 'var(--apexa-text-muted)', margin: '0 0 12px' }}>
        {visuals.length} visual results · newest first · select two to compare
      </p>
      {!visuals.length && <p>No plots or images yet. Tables and text are available in History.</p>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 12 }}>
        {visuals.slice(0, limit).map(a => {
          const diffraction = a.type === 'diffraction' ? a.data as { fileId?: string; preview?: string } : null
          const preview = diffraction ? loadedImages.get(diffraction.fileId ?? '')?.preview ?? diffraction.preview
            : a.type === 'sequence' ? (a.data as { preview?: string }).preview : null
          return (
            <article key={a.id} style={{ minWidth: 0, overflow: 'hidden', border: '1px solid var(--apexa-border)', borderRadius: 10, background: 'var(--apexa-surface)' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', padding: 10, borderBottom: '1px solid var(--apexa-border)' }}>
                <span title={a.title} style={{ flex: 1, minWidth: 100, fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.title}</span>
                <button aria-pressed={compareIds.includes(a.id)} onClick={() => toggleCompare(a.id)} className="apexa-gallery-button">
                  {compareIds.includes(a.id) ? 'Selected' : 'Compare'}
                </button>
                <button className="apexa-gallery-button" onClick={() => { reopen(a.id); onOpen() }}>Open</button>
              </div>
              <div style={{ height: 360, overflow: 'auto' }}>
                {preview ? (
                  <img src={preview.startsWith('data:') ? preview : `data:image/png;base64,${preview}`} alt={a.title}
                    style={{ width: '100%', height: '100%', objectFit: 'contain', padding: 12 }} />
                ) : a.type === 'sequence' || a.type === 'structure' || a.type === 'html-report' ? (
                  <p style={{ padding: 16, fontSize: 12 }}>Open to inspect {a.type === 'sequence' ? 'detector frames' : a.type === 'html-report' ? 'the report' : 'the crystal structure'}.</p>
                ) : <ArtifactBody artifact={a} />}
              </div>
            </article>
          )
        })}
      </div>
      {visuals.length > limit && <button className="apexa-gallery-button" style={{ marginTop: 12 }} onClick={() => setLimit(n => n + 12)}>Show more results</button>}
      {compareIds.length === 2 && <button className="apexa-gallery-button" style={{ position: 'sticky', bottom: 8, marginTop: 12, background: 'var(--apexa-accent)', color: 'white' }} onClick={() => { showComparison(); onOpen() }}>View comparison</button>}
    </div>
  )
}
