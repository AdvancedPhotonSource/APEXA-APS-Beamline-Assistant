import { useState, useCallback, useRef } from 'react'
import { useVizStore } from '@/stores/vizStore'
import { useChatStore } from '@/stores/chatStore'
import { fetchCsvData, uploadFile } from '@/api/endpoints'
import { WORKFLOW_OPTIONS, workflowPrompt } from '@/lib/workflowCatalog'
import { isHtmlReport, reportFileArtifact } from '@/lib/reportArtifacts'

/**
 * Canvas empty state. States the panel's purpose plainly (no fake demo data),
 * accepts dropped files, lists what renders here, and offers grounded starter
 * actions that all route through chat — so the Canvas reads as the "evidence"
 * half of chat-drives / canvas-holds. Nothing here is decorative: every control
 * either loads a real file or sends a real command to APEXA.
 */
export function Dashboard() {
  const addArtifact = useVizStore((s) => s.addArtifact)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const [isDragOver, setIsDragOver] = useState(false)
  const [status, setStatus] = useState<{ msg: string; type: 'info' | 'error' } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const processFiles = useCallback(async (files: File[]) => {
    for (const file of files) {
      const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
      if (isHtmlReport(file.name) || ['cif','tif','tiff','ge','ge1','ge2','ge3','ge4','ge5','h5','hdf5','hdf','nxs','cbf','edf'].includes(ext)) {
        setStatus({ msg: `Loading ${file.name}...`, type: 'info' })
        try {
          const data = await uploadFile(file)
          addArtifact(isHtmlReport(file.name) ? reportFileArtifact(data.path, '', 'local', file.name)
            : { id: `upload-${data.file_id}`, type: ext === 'cif' ? 'structure' : 'sequence',
              title: file.name, data: { path: data.path, location: 'local', source: 'Uploaded file' }, sourceMessageId: '' })
          setStatus(null)
        } catch (err) {
          setStatus({ msg: `Failed to load ${file.name}: ${err}`, type: 'error' })
        }
      } else if (['csv','dat','xy','txt'].includes(ext)) {
        setStatus({ msg: `Loading ${file.name}...`, type: 'info' })
        try {
          const data = await uploadFile(file)
          if (data.path) {
            const csv = await fetchCsvData(data.path)
            const cols = csv.columns
            addArtifact({
              id: `upload-${data.file_id}`, type: 'plotly', title: file.name,
              data: {
                data: [{ x: csv.data[cols[0]], y: csv.data[cols.length > 1 ? cols[1] : cols[0]], type: 'scatter', mode: 'lines', line: { color: '#3b82f6' } }],
                layout: { title: file.name, xaxis: { title: cols[0] }, yaxis: { title: cols.length > 1 ? cols[1] : 'Value' } },
              },
              sourceMessageId: '',
            })
            setStatus(null)
          }
        } catch (err) {
          setStatus({ msg: `Failed to load ${file.name}: ${err}`, type: 'error' })
        }
      } else {
        setStatus({ msg: `Unsupported file type: .${ext}`, type: 'error' })
      }
    }
  }, [addArtifact])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setIsDragOver(false)
    processFiles(Array.from(e.dataTransfer.files))
  }, [processFiles])

  const card: React.CSSProperties = {
    borderRadius: 12, border: '1px solid var(--apexa-border)',
    background: 'var(--apexa-surface)', overflow: 'hidden',
    boxShadow: 'var(--apexa-elev-1)',
  }

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 20, background: 'transparent' }}>
      {/* Purpose — what this panel is for */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase', color: 'var(--apexa-text-2)' }}>Canvas</div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--apexa-text)', margin: '4px 0 4px' }}>
          Results appear here
        </h2>
        <p style={{ fontSize: 12.5, color: 'var(--apexa-text-muted)', lineHeight: 1.5, maxWidth: 560 }}>
          Structures, detector sequences, plots and reports open here as tabs — each
          one you can pin, compare side-by-side, and trace back to the exact tool and
          inputs that made it (the provenance footer). Ask in <b>chat</b>, drop a file
          below, or pick one in the <b>Data</b> panel and hit <b>Recommend</b>.
        </p>
      </div>

      {status && (
        <div style={{
          marginBottom: 12, padding: '8px 14px', borderRadius: 8, fontSize: 12,
          background: status.type === 'error' ? 'rgba(239,68,68,0.1)' : 'rgba(59,130,246,0.1)',
          color: status.type === 'error' ? '#ef4444' : '#3b82f6',
          border: `1px solid ${status.type === 'error' ? 'rgba(239,68,68,0.2)' : 'rgba(59,130,246,0.2)'}`,
        }}>{status.msg}</div>
      )}

      <input
        ref={fileRef} type="file" multiple
        accept=".html,.htm,.cif,.tif,.tiff,.ge,.ge1,.ge2,.ge3,.ge4,.ge5,.h5,.hdf5,.hdf,.nxs,.cbf,.edf,.csv,.dat,.xy,.txt"
        onChange={(e) => { if (e.target.files) processFiles(Array.from(e.target.files)); e.target.value = '' }}
        style={{ display: 'none' }}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 }}>
        {/* Drop zone */}
        <div
          role="button"
          tabIndex={0}
          aria-label="Upload a structure, detector file, diffraction pattern or HTML report"
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileRef.current?.click() } }}
          onDragOver={(e) => { e.preventDefault(); setIsDragOver(true) }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
          style={{
            gridColumn: '1 / -1', borderRadius: 12, minHeight: 130,
            border: isDragOver ? '2px dashed #3b82f6' : '2px dashed var(--apexa-border)',
            background: isDragOver ? 'rgba(59,130,246,0.05)' : 'var(--apexa-surface)',
            padding: 24, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', textAlign: 'center',
            cursor: 'pointer', transition: 'all 200ms',
          }}
        >
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke={isDragOver ? '#3b82f6' : 'var(--apexa-text-muted)'} strokeWidth="1.5" style={{ marginBottom: 8 }}>
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--apexa-text)', marginBottom: 4 }}>Drop a file or click to browse</div>
          <div style={{ fontSize: 11, color: 'var(--apexa-text-muted)' }}>.cif · TIFF / HDF5 / NeXus sequences · .ge .cbf .edf · .csv .dat .xy · HTML reports</div>
          <div style={{ fontSize: 11, color: 'var(--apexa-text-muted)', marginTop: 6 }}>For large beamline datasets, open an existing server path in Data.</div>
        </div>

        {/* What renders here — a legend, not fake data */}
        <div className="apexa-hoverable" style={{ ...card, padding: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--apexa-text)', marginBottom: 10 }}>What shows up here</div>
          {[
            ['1D diffraction patterns', '.xy .dat .csv'],
            ['Crystal structures', '.cif'],
            ['Detector sequences', 'frames, darks, masks'],
            ['Caked heatmaps', '2θ × η'],
            ['Grain maps (FF-HEDM)', 'Grains.csv'],
            ['Calibration QC', 'residuals'],
            ['Analysis reports', 'results and provenance'],
          ].map(([label, desc]) => (
            <div key={label} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontSize: 12 }}>
              <span style={{ color: 'var(--apexa-text)' }}>{label}</span>
              <span style={{ color: 'var(--apexa-text-muted)', fontSize: 10 }}>{desc}</span>
            </div>
          ))}
        </div>

        {/* Grounded starter actions — every one sends a real command to chat */}
        <div className="apexa-hoverable" style={{ ...card, padding: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--apexa-text)', marginBottom: 4 }}>Not sure where to start?</div>
          <div style={{ fontSize: 11, color: 'var(--apexa-text-muted)', marginBottom: 10 }}>
            APEXA can inspect your data and recommend the next step.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <StarterBtn primary onClick={() => sendMessage(workflowPrompt(WORKFLOW_OPTIONS.find(o => o.id === 'readiness')!))}>
              Check analysis readiness
            </StarterBtn>
            <StarterBtn onClick={() => sendMessage(workflowPrompt(WORKFLOW_OPTIONS.find(o => o.id === 'inspect')!))}>
              Recommend a workflow for my data
            </StarterBtn>
          </div>
        </div>

        {/* Common commands — send to chat, don't just decorate */}
        <div className="apexa-hoverable" style={{ ...card, gridColumn: '1 / -1', padding: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--apexa-text)', marginBottom: 8 }}>Prepare an analysis</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {WORKFLOW_OPTIONS.filter(o => ['ff', 'nf', 'pf', 'refine', 'pdf'].includes(o.id)).map((option) => (
              <button
                key={option.id}
                onClick={() => sendMessage(workflowPrompt(option))}
                style={{
                  fontSize: 11, padding: '5px 11px', borderRadius: 16, cursor: 'pointer',
                  background: 'var(--apexa-surface-2)', border: '1px solid var(--apexa-border)', color: 'var(--apexa-text-2)',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--apexa-accent, #3b82f6)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--apexa-border)')}
              >{option.name}</button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function StarterBtn({ primary, onClick, children }: { primary?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={primary ? 'apexa-sheen' : undefined}
      style={{
        textAlign: 'left', padding: '8px 11px', borderRadius: 8, fontSize: 12.5, cursor: 'pointer',
        border: primary ? 'none' : '1px solid var(--apexa-border)',
        background: primary ? 'var(--apexa-accent-grad)' : 'var(--apexa-surface)',
        color: primary ? '#fff' : 'var(--apexa-text)', fontWeight: primary ? 600 : 500,
        boxShadow: primary ? 'var(--apexa-glow)' : 'none',
      }}
    >{children}</button>
  )
}
