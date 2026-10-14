import type { RailView } from './IconRail'
import { lazy, Suspense } from 'react'
const FileBrowser = lazy(() => import('@/components/files/FileBrowser').then(m => ({ default: m.FileBrowser })))
const WorkflowPanel = lazy(() => import('@/components/workflows/WorkflowPanel').then(m => ({ default: m.WorkflowPanel })))
const MotorDashboard = lazy(() => import('@/components/motors/MotorDashboard').then(m => ({ default: m.MotorDashboard })))
const ResultsPanel = lazy(() => import('@/components/viz/ResultsPanel').then(m => ({ default: m.ResultsPanel })))
const ChatHistory = lazy(() => import('@/components/chat/ChatHistory').then(m => ({ default: m.ChatHistory })))

interface SidePanelProps {
  view: RailView
  onClose?: () => void
}

const TITLES: Record<string, string> = {
  history: 'Chat History',
  files: 'Data browser',
  workflows: 'Analysis setup',
  motors: 'Motor Control',
  viewers: 'Results & reports',
}

export function SidePanel({ view, onClose }: SidePanelProps) {
  if (!view) return null

  return (
    <div id="apexa-side-panel" className="h-full border-r overflow-hidden flex flex-col"
      style={{ background: 'var(--apexa-surface)', borderColor: 'var(--apexa-border)', boxShadow: 'var(--apexa-elev-1)' }}>
      <div className="px-3 py-2.5 border-b flex items-center gap-2"
        style={{
          borderColor: 'var(--apexa-border)',
          background: 'linear-gradient(180deg, var(--apexa-surface-2), transparent)',
        }}>
        <span style={{ width: 3, height: 12, borderRadius: 2, background: 'var(--apexa-accent-grad)', boxShadow: 'var(--apexa-glow)' }} />
        <h2 className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--apexa-text-2)' }}>
          {TITLES[view] ?? ''}
        </h2>
        {onClose && <button type="button" onClick={onClose} aria-label="Close sidebar" title="Close sidebar"
          className="apexa-gallery-button ml-auto">×</button>}
      </div>
      <div className="flex-1 overflow-hidden">
        <Suspense fallback={<p style={{ padding: 12, fontSize: 12 }}>Loading panel…</p>}>
        {view === 'history' && <ChatHistory />}
        {view === 'files' && <FileBrowser />}
        {view === 'workflows' && <WorkflowPanel />}
        {view === 'motors' && <MotorDashboard />}
        {view === 'viewers' && <ResultsPanel />}
        </Suspense>
      </div>
    </div>
  )
}
