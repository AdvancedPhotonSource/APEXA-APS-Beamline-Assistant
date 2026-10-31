import { useEffect, useState } from 'react'
import { obj, str } from './refinement'
import type { Obj } from './refinement'

/** Poll durable reads directly; never use a chat turn to refresh a running fit. */
export function useRefinementLive(input: Obj) {
  const jobId = str(input.job_id)
  const location = str(input.location) === 'remote' ? 'remote' : 'local'
  const supported = /^(gsas2-[0-9a-f]{10}|[0-9a-f]{24})$/.test(jobId)
  const [snapshot, setSnapshot] = useState<Obj | null>(null)
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<number | null>(null)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    if (!supported) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    let failures = 0
    let controller: AbortController | undefined
    async function poll() {
      let terminal = false
      const request = new AbortController()
      controller = request
      const timeout = setTimeout(() => request.abort(), 15000)
      try {
        const response = await fetch(`/${location}/api/science/refinement/status?job_id=${encodeURIComponent(jobId)}`,
          { cache: 'no-store', signal: request.signal })
        const data = obj(await response.json().catch(() => null))
        if (!response.ok || !data) throw new Error(response.status === 404
          ? 'Live evidence is unavailable on this server. Restart APEXA after installing the canvas update; the saved job is retained.'
          : str(data?.detail) || `Could not read refinement status (${response.status})`)
        if (!active) return
        setSnapshot({ ...data, location })
        setUpdated(Date.now())
        setError('')
        failures = 0
        terminal = data.terminal === true && !['waiting', 'retrying'].includes(str(obj(data.publication)?.state))
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : String(e))
        failures += 1
      } finally {
        clearTimeout(timeout)
        if (active && !terminal) timer = setTimeout(poll, Math.min(30000, 3000 * 2 ** failures))
      }
    }
    void poll()
    return () => { active = false; controller?.abort(); clearTimeout(timer) }
  }, [jobId, location, supported, revision])
  return { data: snapshot && snapshot.job_id === jobId ? { ...input, ...snapshot } : input,
    supported, updated, error, refresh: () => { setError(''); setRevision(n => n + 1) } }
}
