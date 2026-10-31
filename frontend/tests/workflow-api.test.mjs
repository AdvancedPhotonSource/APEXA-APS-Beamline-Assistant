import { test } from 'node:test'
import assert from 'node:assert/strict'
import { advanceWorkflow, cancelWorkflow, WorkflowApiError } from '../src/api/workflows.ts'

test('workflow approval sends explicit gate identity, revision and retry identity outside chat', async () => {
  let call
  globalThis.fetch = async (url, options) => { call = { url, ...options }; return { ok: true, json: async () => ({ accepted: true }) } }
  const controller = new AbortController()
  const decision = { expected_revision: 4, gate_id: 'review-calibration', decision: 'approve', request_id: 'request-fixed', reason: 'Reviewed overlay' }
  await advanceWorkflow('run/with spaces', decision, controller.signal)
  assert.equal(call.url, '/api/workflows/run%2Fwith%20spaces/advance')
  assert.equal(call.method, 'POST')
  assert.deepEqual(JSON.parse(call.body), decision)
  assert.equal(call.signal, controller.signal)
  assert.equal(call.cache, 'no-store')
})

test('cancellation is a separate revision-checked action and never becomes a chat command', async () => {
  let call
  globalThis.fetch = async (url, options) => { call = { url, ...options }; return { ok: true, json: async () => ({ accepted: true }) } }
  await cancelWorkflow('run-1', { expected_revision: 7, request_id: 'cancel-fixed' }, new AbortController().signal)
  assert.equal(call.url, '/api/workflows/run-1/cancel')
  assert.deepEqual(JSON.parse(call.body), { expected_revision: 7, request_id: 'cancel-fixed' })
})

test('stale gate conflicts preserve HTTP status and server explanation', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: 'Gate already advanced in another window' }) })
  await assert.rejects(advanceWorkflow('run-1', { expected_revision: 1, gate_id: 'old', decision: 'approve', request_id: 'request-1' }, new AbortController().signal),
    error => error instanceof WorkflowApiError && error.status === 409 && /another window/.test(error.message))
})
