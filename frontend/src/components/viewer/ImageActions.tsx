import { useState } from 'react'
import { useImageStore } from '@/stores/imageStore'
import { useChatStore } from '@/stores/chatStore'

/**
 * Contextual actions for the image currently loaded in the Canvas. Every action
 * is grounded on the real file path so the agent operates on *this* image (no
 * guessed filenames). Covers the common "ask about this image" operations plus a
 * launcher for all MIDAS viewers.
 */

// Grounded quick-asks — composed into chat prompts that reference the active path.
const ASKS: { label: string; icon: string; prompt: (p: string) => string }[] = [
  { label: 'Recommend a workflow', icon: '💡', prompt: (p) => `Inspect ${p} and recommend a workflow: what should I do with this, my options, and the parameters — before running anything.` },
  { label: 'Plan detector calibration', icon: '🎯', prompt: (p) => `Prepare calibration for ${p}. Inspect file contents and acquisition metadata; confirm calibrant, measured energy/wavelength, pixel size, geometry and corrections. Treat filenames only as hints. Report missing inputs and a plan before running calibration.` },
  { label: 'Overlay calibrant rings', icon: '◎', prompt: (p) => `Overlay the calibrant (CeO2/LaB6) ring positions on ${p} using the refined calibration parameters, and show me how well they line up.` },
  { label: 'Integrate to 1D', icon: '📈', prompt: (p) => `Integrate ${p} from 2D to a 1D lineout using the calibrated parameters.` },
  { label: 'Find beam center', icon: '✛', prompt: (p) => `Estimate the beam center and detector distance from the diffraction rings in ${p}.` },
  { label: 'Enumerate Bragg rings', icon: '≣', prompt: (p) => `Enumerate the expected Bragg rings (hkl, d-spacing, 2θ, radius) for the geometry used for ${p}.` },
  { label: 'Inspect dataset', icon: '🔍', prompt: (p) => `Inspect ${p} and report its real dimensions, frame count, and any embedded geometry.` },
]

// All MIDAS viewers (run_midas_viewer). In-app ones render in the Canvas; the
// others open a dedicated window. The agent's run_midas_viewer resolves the right
// input (image, lineout, or result folder) from the path/context.
const VIEWERS: { label: string; viewer: string; note?: string }[] = [
  { label: 'Raw image (ff_asym_qt)', viewer: 'ff_asym_qt' },
  { label: 'Lineout', viewer: 'plot_lineout_results' },
  { label: 'Lineout vs rings', viewer: 'plot_lineout_comparison' },
  { label: 'Caked 2D (web)', viewer: 'viz_caking' },
  { label: 'Caked peaks', viewer: 'plot_caked_peaks' },
  { label: 'Integrator peaks', viewer: 'plot_integrator_peaks' },
  { label: 'Calibrant QC', viewer: 'plot_calibrant_results' },
  { label: 'Phase-ID results', viewer: 'plot_phase_id_results' },
  { label: 'FF grains 3D', viewer: 'plotGrains3d', note: 'result folder' },
  { label: 'FF spots 3D', viewer: 'plotFFSpots3d', note: 'result folder' },
  { label: 'FF spots by grain', viewer: 'plotFFSpots3dGrains', note: 'result folder' },
  { label: 'FF interactive', viewer: 'interactiveFFplotting', note: 'result folder' },
  { label: 'NF microstructure', viewer: 'nf_qt', note: '.mic' },
  { label: 'FF+NF overlay', viewer: 'PlotFFNF', note: 'result folder' },
  { label: 'PF sinogram', viewer: 'pfIntensityViewer', note: 'PF params' },
  { label: 'Peak σ statistics', viewer: 'peak_sigma_statistics', note: 'results dir' },
]

export function ImageActions() {
  const activeImageId = useImageStore((s) => s.activeImageId)
  const loadedImages = useImageStore((s) => s.loadedImages)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const [ask, setAsk] = useState('')
  const [showViewers, setShowViewers] = useState(false)

  const img = activeImageId ? loadedImages.get(activeImageId) : null
  if (!img) return null
  const path = img.path

  const send = (prompt: string) => sendMessage(prompt)
  const launchViewer = (v: string) =>
    sendMessage(`Open the MIDAS "${v}" viewer for ${path} (use the appropriate file or result folder near it).`)

  const handleAsk = () => {
    const q = ask.trim()
    if (!q) return
    sendMessage(`${q}\n\n(about the image: ${path})`)
    setAsk('')
  }

  return (
    <div className="p-3 space-y-3 text-xs" style={{ borderBottom: '1px solid var(--apexa-border)' }}>
      <div>
        <div className="font-semibold uppercase tracking-wider" style={{ color: 'var(--apexa-text-2)', fontSize: 10 }}>
          Active image
        </div>
        <div style={{ color: 'var(--apexa-text)', fontWeight: 600, wordBreak: 'break-all', marginTop: 2 }} title={path}>
          {img.filename}
        </div>
      </div>

      {/* Grounded quick-asks */}
      <div className="space-y-1.5">
        {ASKS.map((a) => (
          <button
            key={a.label}
            onClick={() => send(a.prompt(path))}
            className="w-full text-left rounded-lg transition-colors flex items-center gap-2"
            style={{
              padding: '7px 9px',
              border: '1px solid var(--apexa-border)',
              background: 'var(--apexa-surface)',
              color: 'var(--apexa-text)',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--apexa-accent, #3b82f6)')}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--apexa-border)')}
          >
            <span style={{ width: 16, textAlign: 'center' }}>{a.icon}</span>
            <span>{a.label}</span>
          </button>
        ))}
      </div>

      {/* Free-form ask grounded on the image */}
      <div style={{ display: 'flex', gap: 4 }}>
        <input
          value={ask}
          onChange={(e) => setAsk(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAsk()}
          placeholder="Ask about this image…"
          style={{
            flex: 1, padding: '6px 8px', fontSize: 12, borderRadius: 6,
            border: '1px solid var(--apexa-border)', background: 'var(--apexa-surface)',
            color: 'var(--apexa-text)', outline: 'none',
          }}
        />
        <button
          onClick={handleAsk}
          disabled={!ask.trim()}
          style={{
            padding: '6px 10px', borderRadius: 6, border: 'none',
            background: 'var(--apexa-accent, #3b82f6)', color: '#fff', fontSize: 12,
            cursor: ask.trim() ? 'pointer' : 'default', opacity: ask.trim() ? 1 : 0.4,
          }}
        >
          Ask
        </button>
      </div>

      {/* All MIDAS viewers launcher */}
      <div>
        <button
          onClick={() => setShowViewers((v) => !v)}
          className="w-full text-left font-semibold uppercase tracking-wider flex items-center justify-between"
          style={{ color: 'var(--apexa-text-2)', fontSize: 10, background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0' }}
        >
          <span>MIDAS viewers ({VIEWERS.length})</span>
          <span>{showViewers ? '▾' : '▸'}</span>
        </button>
        {showViewers && (
          <div className="grid grid-cols-1 gap-1 mt-1.5">
            {VIEWERS.map((v) => (
              <button
                key={v.viewer}
                onClick={() => launchViewer(v.viewer)}
                className="w-full text-left rounded-md transition-colors flex items-center justify-between"
                style={{
                  padding: '5px 8px', border: '1px solid var(--apexa-border)',
                  background: 'var(--apexa-surface)', color: 'var(--apexa-text)', fontSize: 11,
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--apexa-surface-3)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--apexa-surface)')}
                title={`run_midas_viewer: ${v.viewer}${v.note ? ` (${v.note})` : ''}`}
              >
                <span>{v.label}</span>
                {v.note && <span style={{ color: 'var(--apexa-text-muted)', fontSize: 9 }}>{v.note}</span>}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
