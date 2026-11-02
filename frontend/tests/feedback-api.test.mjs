import { test } from 'node:test'
import assert from 'node:assert/strict'
import { sendFeedback } from '../src/api/endpoints.ts'

const request = { kind: 'report', category: 'feature_request', comment: 'Compare scans', context: {} }

test('feedback confirms saving only with both a successful HTTP response and a receipt', async context => {
  let response
  context.mock.method(globalThis, 'fetch', async () => response)
  for (const [ok, data] of [[false, { ok: true, id: 'false-success' }], [true, { ok: true }], [true, null]]) {
    response = { ok, json: async () => data }
    assert.equal((await sendFeedback(request)).ok, false)
  }
  response = { ok: true, json: async () => ({ ok: true, id: 'saved-id' }) }
  assert.deepEqual(await sendFeedback(request), { ok: true, id: 'saved-id' })
})

test('feedback returns recoverable errors for unavailable servers and non-JSON replies', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Network offline') })
  assert.equal((await sendFeedback(request)).ok, false)
  fetch.mock.mockImplementation(async () => ({ ok: false, json: async () => { throw new SyntaxError('Proxy HTML') } }))
  assert.equal((await sendFeedback(request)).ok, false)
  fetch.mock.mockImplementation(async () => ({ ok: false, json: async () => ({ ok: false, error: 'Storage unavailable' }) }))
  assert.deepEqual(await sendFeedback(request), { ok: false, error: 'Storage unavailable' })
})
