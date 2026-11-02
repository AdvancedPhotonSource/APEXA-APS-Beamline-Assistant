import { test } from 'node:test'
import assert from 'node:assert/strict'
import { computeDecision } from '../src/lib/computeDecision.ts'

const result = data => ({ tool: 'midas_integrate_series', status: 'completed', data })

test('integration shows its actual allocation rather than the initial target hint', () => {
  const decision = computeDecision(result({ compute: { target: 'local-gpu', device: 'cuda', reason: 'Initial hint', allocation: {
    host: '', device: 'cpu', n_workers: 4, threads_per_worker: 2, worker_rss_gb: 7,
    reason: 'Qualified CPU backend; data is local', admission_scope: 'same_host_registry',
  } } }))
  assert.equal(decision.fields.find(f => f.label === 'Device').value, 'CPU')
  assert.equal(decision.fields.find(f => f.label === 'Workers').value, '4')
  assert.equal(decision.reason, 'Qualified CPU backend; data is local')
  assert.equal(decision.notice, null)
})

test('capacity refusal keeps zero workers and the reason visible without claiming execution', () => {
  const decision = computeDecision(result({ nothing_was_run: true, compute: {
    n_workers: 0, device: 'cpu', reason: 'Insufficient available RAM', worker_rss_gb: 7,
  } }))
  assert.equal(decision.fields.find(f => f.label === 'Workers').value, '0')
  assert.equal(decision.notice, 'No processing started.')
  assert.match(decision.reason, /Insufficient/)
})

test('a remote recommendation never fabricates a worker allocation or measured usage', () => {
  const decision = computeDecision(result({ status: 'dispatch_recommended', nothing_was_run: true,
    compute: { target: 'remote', reason: 'Process on the data host' } }))
  assert.deepEqual(decision.fields, [{ label: 'Target', value: 'remote' }])
  assert.equal(decision.notice, 'No processing started.')
  assert.equal(decision.resources, null)
})

test('unknown or malformed planner fields do not become resource measurements', () => {
  for (const compute of [null, [], 'gpu', {}, { n_workers: true, cpu_budget: -1, worker_rss_gb: Infinity, threads_per_worker: '8' }]) {
    assert.equal(computeDecision(result({ compute })), null)
  }
  const decision = computeDecision(result({ compute: { device: 'cuda:1', admission_scope: 'controller_host_only', worker_vram_gb: 4 } }))
  assert.match(decision.scope, /separate host or scheduler/)
  assert.equal(decision.fields.find(f => f.label === 'Estimated GPU RAM / worker').value, '4 GiB')
})
