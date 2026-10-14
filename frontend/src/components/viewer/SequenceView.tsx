import { useEffect, useState } from 'react'
import type { VizArtifact } from '@/api/types'
import { useVizStore } from '@/stores/vizStore'
import { PlotlyChart } from '../viz/PlotlyChart'

interface Manifest { sequence_id: string; frames: number; shape: number[]; ordering: string }
interface Frame {
  preview: string; shape: number[]; preview_shape?: number[]; vmin: number; vmax: number; filename: string; analysis_key: string
  stats: { min: number; max: number; mean: number; excluded_pixels: number }
  context: { sequence_frame: number; file_frame: number; settings: { center_x: number; center_y: number }; [key: string]: unknown }
  profile?: { radius_px: number[]; intensity: number[] }
}
interface Rings { rings: { hkl: number[]; radius_px: number; d_angstrom: number; two_theta_deg: number }[]; model: string; signature: string }
const DEFAULT_SETTINGS = { dark_path: '', dark_dataset: '', dark_frame: 0, mask_path: '', mask_dataset: '', corrected: true, log: false, colormap: 'viridis', vmin: '', vmax: '', center_x: '', center_y: '', preview_max_side: 1024 }
interface SavedView {
  settings: typeof DEFAULT_SETTINGS; index: number; source: { pattern: string; dataset: string; path: string }
  geometry: { path: string; energy_kev: string; distance_mm: string; pixel_um: string; location: string }; rings: Rings | null
}

async function post<T>(endpoint: string, data: unknown, signal?: AbortSignal, local = false): Promise<T> {
  const r = await fetch(`${local ? '/local' : ''}/api/science/${endpoint}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data), signal })
  const result = await r.json()
  if (!r.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail))
  return result
}

export function SequenceView({ artifact }: { artifact: VizArtifact }) {
  const input = artifact.data as { path: string; dataset?: string; pattern?: string; location?: string; viewerState?: SavedView }
  const localData = input.location === 'local'
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [frame, setFrame] = useState<Frame | null>(null)
  const [index, setIndex] = useState(input.viewerState?.index ?? 0)
  const [playing, setPlaying] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [source, setSource] = useState(input.viewerState?.source ?? { pattern: input.pattern ?? '*', dataset: input.dataset ?? '', path: input.path })
  const [reload, setReload] = useState(0)
  const [showProfile, setShowProfile] = useState(true)
  const [settings, setSettings] = useState(input.viewerState?.settings ?? DEFAULT_SETTINGS)
  const [geometry, setGeometry] = useState(() => input.viewerState?.geometry ?? ({
    path: (useVizStore.getState().artifacts.findLast(a => a.type === 'structure')?.data as { path?: string })?.path ?? '',
    energy_kev: '', distance_mm: '', pixel_um: '', location: 'local',
  }))
  const [rings, setRings] = useState<Rings | null>(input.viewerState?.rings ?? null)
  const geometrySignature = JSON.stringify(geometry)
  const currentRings = rings?.signature === geometrySignature ? rings : null
  const reportId = `inspection-report-${artifact.id}`

  useEffect(() => {
    const store = useVizStore.getState()
    const current = store.artifacts.find(a => a.id === artifact.id)
    if (current) store.updateArtifact(artifact.id, { data: { ...(current.data as object), viewerState: { settings, source, geometry, rings, index } } })
  }, [artifact.id, settings, source, geometry, rings, index])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setManifest(null); setFrame(null); setPlaying(false)
    post<Manifest>('sequence', source, controller.signal, localData)
      .then(result => { setManifest(result); setIndex(i => Math.min(i, result.frames - 1)) })
      .catch(e => { if (e.name !== 'AbortError') { setError(String(e)); setLoading(false) } })
    return () => controller.abort()
    // Source fields are committed with Reload, so typing never scans a directory.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input.path, reload, localData])

  useEffect(() => {
    if (!manifest) return
    const controller = new AbortController()
    setLoading(true); setError('')
    const timer = setTimeout(() => {
      post<Frame>('frame', { ...settings, sequence_id: manifest.sequence_id, index,
        vmin: settings.vmin === '' ? null : Number(settings.vmin), vmax: settings.vmax === '' ? null : Number(settings.vmax),
        center_x: settings.center_x === '' ? (manifest.shape[1] - 1) / 2 : Number(settings.center_x),
        center_y: settings.center_y === '' ? (manifest.shape[0] - 1) / 2 : Number(settings.center_y),
      }, controller.signal, localData).then(result => { setFrame(result); setLoading(false) })
        .catch(e => { if (e.name !== 'AbortError') { setError(String(e)); setLoading(false); setPlaying(false) } })
    }, 150)
    return () => { clearTimeout(timer); controller.abort() }
  }, [manifest, index, settings, localData])

  useEffect(() => {
    if (!playing || loading || !manifest || !frame || error) return
    const timer = setTimeout(() => setIndex(i => (i + 1) % manifest.frames), 350)
    return () => clearTimeout(timer)
  }, [playing, loading, manifest, frame, error])

  const makeReport = () => ({
    title: `Detector inspection · ${artifact.title}`, eyebrow: 'SYNCHRONIZED VIEWER REPORT',
    subtitle: 'Inspection and display corrections; this is not a reconstruction acceptance verdict.',
    header_pairs: [['Source', frame?.filename ?? input.path], ['Frame (1-based)', String((frame?.context.sequence_frame ?? 0) + 1)], ['Analysis key', frame?.analysis_key ?? 'pending']],
    tiles: frame ? Object.entries(frame.stats).map(([key, value]) => ({ key, value: String(value) })) : [],
    plates: frame ? [{ key: 'detector', title: 'Detector frame (ring guides documented below)', subtitle: 'Corrections and display limits are recorded in the provenance below.', png_b64: frame.preview.split(',')[1] }] : [],
    findings: [
      { title: 'Correction and display provenance', body: JSON.stringify(frame?.context ?? {}, null, 2), severity: 'solid', tag: 'Recorded' },
      { title: 'Ring model', body: currentRings ? `${currentRings.model}. Geometry: ${geometrySignature}. ${currentRings.rings.length} unique rings within the configured reflection-index limit.` : 'No current ring simulation. Supply a CIF and measured geometry.', severity: 'warn', tag: 'Model scope' },
    ],
    verdict: 'unknown', verdict_note: loading ? 'Updating view; figures describe the last completed frame.' : 'Viewer inspection only; scientific acceptance checks are separate.',
    generated_at: new Date().toISOString(), linked_source_id: artifact.id,
    linked_analysis_key: frame?.analysis_key,
  })

  useEffect(() => {
    if (!frame) return
    const store = useVizStore.getState()
    const existing = store.artifacts.find(a => a.id === artifact.id)
    if (existing) store.updateArtifact(artifact.id, { data: { ...(existing.data as object), analysisKey: frame.analysis_key, appliedContext: frame.context, preview: frame.preview } })
    if (store.artifacts.some(a => a.id === reportId)) store.updateArtifact(reportId, {
      data: makeReport(),
      provenance: { inputs: [frame.filename], tool: 'detector inspection', timestamp: Date.now() },
    })
    // Reports follow accepted frames and the applied ring model, never draft input values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, rings, geometrySignature, loading])

  const openReport = () => {
    if (!frame) return
    const store = useVizStore.getState()
    store.addArtifact({ id: reportId, type: 'report', title: `Inspection · ${artifact.title}`, data: makeReport(), sourceMessageId: artifact.sourceMessageId,
      provenance: { inputs: [frame.filename], tool: 'detector inspection', timestamp: Date.now() } })
    store.clearCompare(); store.toggleCompare(artifact.id); store.toggleCompare(reportId)
  }
  const simulate = async () => {
    if (!manifest) return
    setPlaying(false); setError('')
    try {
      const result = await post<Omit<Rings, 'signature'>>('rings', { path: geometry.path, energy_kev: Number(geometry.energy_kev), distance_mm: Number(geometry.distance_mm), pixel_um: Number(geometry.pixel_um), max_radius: Math.hypot(...manifest.shape) }, undefined, geometry.location === 'local')
      setRings({ ...result, signature: geometrySignature })
    } catch (e) { setError(String(e)) }
  }
  const field = (label: string, value: string | number, change: (v: string) => void, type = 'text') => (
    <label style={{ display: 'grid', gap: 3, minWidth: 0, fontSize: 11 }}>{label}
      <input type={type} value={value} onChange={e => change(e.target.value)} style={{ width: '100%', minWidth: 0, padding: 5, border: '1px solid var(--apexa-border)', borderRadius: 4, background: 'var(--apexa-surface)' }} />
    </label>
  )
  return (
    <div style={{ minHeight: '100%', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', fontSize: 12 }}>
        <button className="apexa-gallery-button" disabled={!manifest || manifest.frames < 2} onClick={() => setPlaying(p => !p)}>{playing ? 'Pause' : 'Play'}</button>
        <button className="apexa-gallery-button" disabled={!index} onClick={() => { setPlaying(false); setIndex(i => i - 1) }}>Previous</button>
        <button className="apexa-gallery-button" disabled={!manifest || index >= manifest.frames - 1} onClick={() => { setPlaying(false); setIndex(i => i + 1) }}>Next</button>
        <span>Frame {index + 1} / {manifest?.frames ?? '…'}</span>
        <button className="apexa-gallery-button" disabled={!frame || loading} onClick={openReport}>Report alongside</button>
        <span role="status">{loading ? 'Loading updated view…' : ''}</span>
      </div>
      {manifest && <input aria-label="Frame index" type="range" min={0} max={manifest.frames - 1} value={index} onChange={e => { setPlaying(false); setIndex(Number(e.target.value)) }} />}
      {error && <div role="alert" style={{ color: '#ef6464', overflowWrap: 'anywhere' }}>{error}</div>}
      <details><summary>Source, dark and mask</summary>
        <div className="apexa-viewer-fields">
          {field(`Detector file or directory (${localData ? 'APEXA host' : 'data host'})`, source.path, v => setSource(s => ({ ...s, path: v })))}
          {field('Sample file pattern (directory)', source.pattern, v => setSource(s => ({ ...s, pattern: v })))}
          {field('HDF5 detector dataset', source.dataset, v => setSource(s => ({ ...s, dataset: v })))}
          <button className="apexa-gallery-button" onClick={() => setReload(n => n + 1)}>Reload sequence</button>
          {field('Dark file path', settings.dark_path, v => setSettings(s => ({ ...s, dark_path: v })))}
          {field('Dark HDF5 dataset', settings.dark_dataset, v => setSettings(s => ({ ...s, dark_dataset: v })))}
          {field('Dark frame index (0-based)', settings.dark_frame, v => setSettings(s => ({ ...s, dark_frame: Number(v) })), 'number')}
          {field('Mask file path', settings.mask_path, v => setSettings(s => ({ ...s, mask_path: v })))}
          {field('Mask HDF5 dataset', settings.mask_dataset, v => setSettings(s => ({ ...s, mask_dataset: v })))}
        </div>
        <p style={{ fontSize: 11 }}>Nonzero mask = excluded (gray). Dark subtraction uses the selected dark frame without rescaling. Raw files remain unchanged. Choose a sample-only pattern to exclude darks and masks from the sequence.</p>
      </details>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 12 }}>
        <label><input type="checkbox" checked={settings.corrected} onChange={e => setSettings(s => ({ ...s, corrected: e.target.checked }))} /> Corrected view</label>
        <label><input type="checkbox" checked={settings.log} onChange={e => setSettings(s => ({ ...s, log: e.target.checked }))} /> Log display</label>
        <label>Colormap <select value={settings.colormap} onChange={e => setSettings(s => ({ ...s, colormap: e.target.value }))}>{['viridis', 'gray', 'inferno', 'magma'].map(c => <option key={c}>{c}</option>)}</select></label>
        <label>Preview <select value={settings.preview_max_side ?? 1024} onChange={e => setSettings(s => ({ ...s, preview_max_side: Number(e.target.value) }))}>{[512, 1024, 2048, 4096].map(n => <option key={n} value={n}>{n} px</option>)}</select></label>
        <button className="apexa-gallery-button" disabled={!frame} onClick={() => frame && setSettings(s => ({ ...s, vmin: String(frame.vmin), vmax: String(frame.vmax) }))}>Lock contrast</button>
        <button className="apexa-gallery-button" onClick={() => setSettings(s => ({ ...s, vmin: '', vmax: '' }))}>Auto contrast</button>
      </div>
      <div className="apexa-viewer-fields">
        {field('Minimum (blank = auto)', settings.vmin, v => setSettings(s => ({ ...s, vmin: v })), 'number')}
        {field('Maximum (blank = auto)', settings.vmax, v => setSettings(s => ({ ...s, vmax: v })), 'number')}
      </div>
      {frame && <>
        <div style={{ height: 'clamp(280px, 48vh, 620px)', background: '#08090b', borderRadius: 8, overflow: 'hidden' }}>
          <svg role="img" aria-label={`Detector frame ${frame.context.sequence_frame + 1} with optional predicted rings`} viewBox={`0 0 ${frame.shape[1]} ${frame.shape[0]}`} width="100%" height="100%">
            <image href={frame.preview} width={frame.shape[1]} height={frame.shape[0]} />
            {currentRings?.rings.map((r, i) => <circle key={i} cx={frame.context.settings.center_x} cy={frame.context.settings.center_y} r={r.radius_px} fill="none" stroke="#22d3ee" strokeWidth={1.5} vectorEffect="non-scaling-stroke"><title>{`hkl ${r.hkl.join(' ')} · d ${r.d_angstrom.toFixed(4)} Å · 2θ ${r.two_theta_deg.toFixed(3)}°`}</title></circle>)}
          </svg>
        </div>
        <div style={{ fontSize: 11, color: 'var(--apexa-text-muted)', overflowWrap: 'anywhere' }}>{frame.filename} · file frame {frame.context.file_frame} · mean {frame.stats.mean.toFixed(2)} · excluded {frame.stats.excluded_pixels} pixels · preview {frame.preview_shape?.join(' × ') ?? frame.shape.join(' × ')} (nearest pixel); statistics/profile use full data</div>
      </>}
      <details><summary>Inspect and simulate rings</summary>
        <div className="apexa-viewer-fields">
          {field('Beam center X (px; blank = midpoint)', settings.center_x, v => setSettings(s => ({ ...s, center_x: v })), 'number')}
          {field('Beam center Y (px; blank = midpoint)', settings.center_y, v => setSettings(s => ({ ...s, center_y: v })), 'number')}
          {field('Structure CIF path', geometry.path, v => setGeometry(s => ({ ...s, path: v })))}
          <label style={{ fontSize: 11 }}>CIF location <select value={geometry.location} onChange={e => setGeometry(s => ({ ...s, location: e.target.value }))}><option value="local">APEXA host (MP/COD downloads)</option><option value="data">Detector data host</option></select></label>
          {field('Energy (keV)', geometry.energy_kev, v => setGeometry(s => ({ ...s, energy_kev: v })), 'number')}
          {field('Detector distance (mm)', geometry.distance_mm, v => setGeometry(s => ({ ...s, distance_mm: v })), 'number')}
          {field('Pixel size (µm)', geometry.pixel_um, v => setGeometry(s => ({ ...s, pixel_um: v })), 'number')}
        </div>
        <button className="apexa-gallery-button" disabled={!manifest} onClick={simulate}>Simulate ideal rings</button>
        <button className="apexa-gallery-button" onClick={() => setRings(null)}>Clear rings</button>
        <p style={{ fontSize: 11 }}>Ideal, untilted detector only; no distortion or intensity prediction. Reflection indices are bounded to ±8. Detector midpoint is an initial profile center, not a measured beam center.</p>
        {currentRings && <div style={{ maxHeight: 180, overflow: 'auto', fontSize: 11 }}>{currentRings.rings.map((r, i) => <div key={i}>{r.hkl.join(' ')} · {r.d_angstrom.toFixed(4)} Å · {r.two_theta_deg.toFixed(3)}° · {r.radius_px.toFixed(1)} px</div>)}</div>}
      </details>
      <label style={{ fontSize: 12 }}><input type="checkbox" checked={showProfile} onChange={e => setShowProfile(e.target.checked)} /> Radial profile of displayed data</label>
      {showProfile && frame?.profile && <div style={{ height: 320 }}><PlotlyChart data={{ data: [{ type: 'scatter', mode: 'lines', x: frame.profile.radius_px, y: frame.profile.intensity }], layout: { title: 'Observed radial profile', xaxis: { title: 'Radius (pixels)' }, yaxis: { title: 'Mean intensity (detector units)' } } }} /></div>}
      <div style={{ fontSize: 10, color: 'var(--apexa-text-muted)' }}>{manifest?.ordering}</div>
    </div>
  )
}
