/** Display the engine's outcomes without deriving scientific qualification. */
export type Obj = Record<string, unknown>
export type RefinementRow = {
  id: string
  state: string
  label: string
  mode?: string
  error?: string
  axis?: number
  axisUnits?: string
  result?: Obj
  progress?: Obj
  decision?: Obj
  heartbeatAge?: number
  inspection?: Obj
}
export const obj = (value: unknown): Obj | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Obj : null
export const list = (value: unknown): unknown[] => Array.isArray(value) ? value : []
export const str = (value: unknown): string => typeof value === 'string' ? value : ''
export const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null
export const basename = (path: string): string => path.split(/[\\/]/).filter(Boolean).pop() ?? path

export function executionState(row: RefinementRow): string {
  const states = [row.state, str(row.result?.execution_status), str(row.result?.status)]
  if (states.includes('cancelled')) return 'cancelled'
  if (states.some(x => ['failed', 'error', 'execution_error'].includes(x)) || row.result?.error) return 'failed'
  if (['queued', 'submitted', 'running', 'pending'].includes(row.state)) return 'pending'
  if (states.some(x => ['completed', 'complete', 'success'].includes(x))) return 'completed'
  return 'unknown'
}

export function residual(row: RefinementRow): number | null {
  return executionState(row) === 'completed' ? num(row.result?.Rwp) ?? num(row.result?.joint_Rwp_pct) : null
}

export function rowsFor(data: Obj): RefinementRow[] {
  const liveJobs = obj(data.jobs)
  if (liveJobs) return Object.entries(liveJobs).filter(([, id]) => typeof id === 'string').map(([lane, id]) => ({
    id: str(id), state: 'submitted', label: `${lane} job`, mode: lane,
  }))
  if (Array.isArray(data.jobs)) return data.jobs.map(obj).filter((x): x is Obj => !!x).map((job, index) => {
    const payload = obj(job.payload) ?? {}
    const observations = list(payload.observations).map(obj).filter((x): x is Obj => !!x)
    const observation = observations[0] ?? {}
    const scan = obj(obj(observation.conditions)?.scan_coordinate) ?? {}
    const result = obj(job.result) ?? undefined
    const label = str(observation.frame_id) || basename(str(observation.data_path)) || `Job ${index + 1}`
    return { id: str(job.id) || `job-${index}`, state: str(job.state) || 'unknown',
      label: observations.length > 1 ? `${label} (+${observations.length - 1} observations)` : label,
      mode: str(payload.mode), error: str(job.error) || str(result?.error),
      axis: num(scan.value) ?? undefined, axisUnits: str(scan.units) || undefined, result,
      progress: obj(job.progress) ?? undefined, decision: obj(job.decision) ?? undefined,
      inspection: obj(job.inspection) ?? undefined,
      heartbeatAge: num(job.heartbeat_age_seconds) ?? undefined }
  })
  if (Array.isArray(data.results)) return data.results.map(obj).filter((x): x is Obj => !!x).map((result, i) => ({
    id: str(result.job_id) || `legacy-${i}`, label: basename(str(result.data_file)) || `Pattern ${i + 1}`,
    state: str(result.execution_status) || str(result.status) || 'completed', error: str(result.error), result,
  }))
  if (Array.isArray(data.job_ids)) return data.job_ids.filter((id): id is string => typeof id === 'string').map(id => ({
    id, state: str(data.status) || 'submitted', label: id, mode: str(data.mode),
  }))
  if ('accepted' in data || 'panels' in data || 'proposals' in data) return []
  const result = obj(data.result) ?? data
  const hasResult = ['Rwp', 'joint_Rwp_pct', 'gpx_path', 'weight_fractions', 'history',
    'decision_trace', 'scientific_status', 'execution_status'].some(key => key in result)
  if (!hasResult) return []
  return [{ id: str(data.job_id) || str(result.observation_id) || 'refinement',
    state: str(data.state) || str(data.status) || str(result.execution_status) || 'unknown',
    label: basename(str(result.data_file) || str(result.gpx_path)) || 'Refinement',
    mode: str(data.mode), error: str(data.error) || str(result.error), result }]
}

export function outcomeCounts(rows: RefinementRow[]) {
  const completed = rows.filter(row => executionState(row) === 'completed')
  return {
    completed: completed.length,
    failed: rows.filter(row => executionState(row) === 'failed').length,
    cancelled: rows.filter(row => executionState(row) === 'cancelled').length,
    pending: rows.filter(row => executionState(row) === 'pending').length,
    unknown: rows.filter(row => executionState(row) === 'unknown').length,
    blocked: completed.filter(row => row.result?.scientific_status === 'blocked').length,
    provisional: completed.filter(row => row.result?.scientific_status === 'provisional').length,
    scienceUnavailable: completed.filter(row => !['blocked', 'provisional'].includes(str(row.result?.scientific_status))).length,
    missingResidual: completed.filter(row => residual(row) === null).length,
  }
}

export function fractionGroups(result: Obj): { label: string; values: Obj }[] {
  const reports = list(result.phase_fraction_reports).map(obj).filter((x): x is Obj => !!x)
  if (reports.length) return reports.map((report, i) => ({
    label: `Histogram ${String(report.histogram ?? i)}`, values: obj(report.values) ?? {},
  }))
  return [{ label: 'Modeled phases', values: obj(result.weight_fractions) ?? {} }]
}
