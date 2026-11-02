import type { ToolResult } from '../api/types'

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function text(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  const trimmed = value.trim()
  return trimmed.length > 1200 ? `${trimmed.slice(0, 1200)}…` : trimmed
}
function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

/** Read recorded planner fields; never infer a launch, utilization or GPU speedup. */
export function computeDecision(result: ToolResult) {
  const compute = object(result.data.compute)
  if (!compute) return null
  const allocation = object(compute.allocation) ?? compute
  const fields: { label: string; value: string }[] = []
  const host = text(allocation.host)
  if (host || allocation.host === '') fields.push({ label: 'Host', value: host ?? 'Local host' })
  const device = text(allocation.device) ?? text(compute.device)
  if (device) fields.push({ label: 'Device', value: device === 'cpu' ? 'CPU' : device })
  const target = text(compute.target)
  if (target && !host && allocation.host !== '') fields.push({ label: 'Target', value: target })
  for (const [key, label] of [['n_workers', 'Workers'], ['threads_per_worker', 'Threads / worker'], ['cpu_budget', 'CPU thread budget']] as const) {
    const value = number(allocation[key])
    if (value !== null && Number.isSafeInteger(value)) fields.push({ label, value: String(value) })
  }
  for (const [key, label] of [['worker_rss_gb', 'Estimated RAM / worker'], ['worker_vram_gb', 'Estimated GPU RAM / worker'], ['memory_budget_gb', 'RAM budget'], ['gpu_budget_gb', 'GPU RAM budget']] as const) {
    const value = number(allocation[key])
    if (value !== null && value > 0) fields.push({ label, value: `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} GiB` })
  }
  const reason = text(allocation.reason) ?? text(compute.reason)
  if (!fields.length && !reason) return null
  const scope = allocation.admission_scope === 'controller_host_only'
    ? 'Remote resources require a separate host or scheduler allocation.'
    : allocation.admission_scope === 'same_host_registry'
      ? 'Shared reservations cover cooperating APEXA processes on this host.' : null
  const notice = result.data.nothing_was_run === true ? 'No processing started.'
    : result.data.status === 'dispatch_recommended' ? 'Dispatch recommendation.' : null
  return { fields, reason, scope, notice, resources: text(allocation.resources) }
}
