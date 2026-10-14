import type { ToolResult } from '@/api/types'

export function WorkflowCard({ result }: { result: ToolResult }) {
  const data = result.data as Record<string, unknown>
  const workflow = String(data.workflow ?? data.tool ?? result.tool)
  const status = result.status
  const output = data.output as Record<string, unknown> | undefined

  const statusConfig: Record<string, { color: string; bg: string; icon: string }> = {
    completed: { color: 'text-emerald-400', bg: 'bg-emerald-500/15', icon: 'M22 11.08V12a10 10 0 1 1-5.93-9.14M22 4 12 14.01 9 11.01' },
    success: { color: 'text-emerald-400', bg: 'bg-emerald-500/15', icon: 'M22 11.08V12a10 10 0 1 1-5.93-9.14M22 4 12 14.01 9 11.01' },
    error: { color: 'text-red-400', bg: 'bg-red-500/15', icon: 'M12 2 2 22h20L12 2zM12 9v4M12 17h.01' },
    failed: { color: 'text-red-400', bg: 'bg-red-500/15', icon: 'M12 2 2 22h20L12 2zM12 9v4M12 17h.01' },
  }
  const sc = statusConfig[status] ?? { color: 'text-zinc-400', bg: 'bg-zinc-500/15', icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20' }

  return (
    <div className="mt-3 rounded-xl border border-purple-500/30 bg-purple-950/20 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-purple-500/20">
        <div className="flex items-center gap-2.5">
          <div className={`w-6 h-6 rounded-full ${sc.bg} flex items-center justify-center`}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={sc.color}>
              <path d={sc.icon} />
            </svg>
          </div>
          <span className="text-[11px] font-semibold text-purple-300 uppercase tracking-wide">{workflow}</span>
        </div>
        <span className={`text-[10px] font-medium ${sc.color}`}>{status}</span>
      </div>
      {(data.total_grains_found ?? data.total_grains) != null && (
        <div className="px-3 py-2 flex items-baseline justify-between border-b border-zinc-800/50">
          <span className="text-xs text-zinc-400">Grains found</span>
          <span className="text-sm font-bold text-purple-300 font-mono">{String(data.total_grains_found ?? data.total_grains)}</span>
        </div>
      )}
      {data.total_voxels != null && <div className="px-3 py-2 text-xs">Voxels: {String(data.total_voxels)}</div>}
      {!!data.run_note && <p className="px-3 py-2 text-xs text-amber-300">{String(data.run_note)}</p>}
      {output && (
        <details>
          <summary className="px-3 py-1.5 text-[10px] text-zinc-500 cursor-pointer hover:text-zinc-300 transition-colors">
            Workflow output
          </summary>
          <pre className="px-3 pb-2 text-[11px] text-zinc-400 font-mono whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto">
            {JSON.stringify(output, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}
