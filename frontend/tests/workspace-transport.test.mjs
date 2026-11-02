import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { readServerSession, resetWorkspaceTransport, writeServerWorkspace } from '../src/api/sessions.ts'

beforeEach(resetWorkspaceTransport)
const makeWorkspace = artifacts => ({ artifacts, openIds: artifacts.map(a => a.id), activeId: artifacts[0]?.id ?? null, pinned: [], compareIds: [], draft: '' })
const makePlot = (id, y = [2, 3]) => ({ id, type: 'plotly', data: { data: [{ x: [0, 1], y }] }, title: id, sourceMessageId: '' })

function fakeServer(context) {
  const calls = []
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    const body = JSON.parse(options.body)
    calls.push({ url, method: options.method, body, bytes: Buffer.byteLength(options.body) })
    return { ok: true, json: async () => ({ workspaceRevision: body.expected_revision + 1, revision: body.expected_revision + 1 }) }
  })
  return calls
}

test('draft-only saves do not serialize or upload an acknowledged scientific array', async context => {
  const calls = fakeServer(context)
  let serializations = 0
  const values = Array.from({ length: 250000 }, (_, i) => i * 0.25)
  const plot = makePlot('large')
  plot.data = { toJSON() { serializations++; return { data: [{ y: values }] } } }
  const workspace = makeWorkspace([plot])
  await writeServerWorkspace('large', workspace, 0)
  await writeServerWorkspace('large', { ...workspace, artifacts: [plot], draft: 'Continue analysis' }, 1)
  assert.equal(serializations, 1)
  assert.deepEqual(calls[1].body.artifact_slots, [{ reuse: 0 }])
  assert.equal(calls[1].body.workspace.draft, 'Continue analysis')
  assert.ok(calls[0].bytes > 1_000_000)
  assert.ok(calls[1].bytes < 500)
})

test('reordered artifacts reuse their original slots while changed data with the same ID is uploaded', async context => {
  const calls = fakeServer(context)
  const one = makePlot('one'), two = makePlot('two')
  await writeServerWorkspace('reorder', makeWorkspace([one, two]), 0)
  const changed = makePlot('one', [99, 100])
  await writeServerWorkspace('reorder', makeWorkspace([two, changed]), 1)
  assert.deepEqual(calls[1].body.artifact_slots, [{ reuse: 1 }, { value: changed }])
  await writeServerWorkspace('reorder', makeWorkspace([]), 2)
  assert.deepEqual(calls[2].body.artifact_slots, [])
})

test('references never cross sessions or different revisions', async context => {
  const calls = fakeServer(context)
  const workspace = makeWorkspace([makePlot('same')])
  await writeServerWorkspace('a', workspace, 0)
  await writeServerWorkspace('b', workspace, 1)
  await writeServerWorkspace('a', workspace, 5)
  assert.ok(calls.slice(1).every(c => 'value' in c.body.artifact_slots[0]))
})

test('a server without PATCH gets full snapshots including every artifact', async context => {
  const calls = []
  context.mock.method(globalThis, 'fetch', async (_url, options) => {
    const body = JSON.parse(options.body)
    calls.push({ method: options.method, body })
    return options.method === 'PATCH'
      ? { ok: false, status: 405, json: async () => ({ detail: 'Method Not Allowed' }) }
      : { ok: true, json: async () => ({ workspaceRevision: body.expected_revision + 1, revision: 2 }) }
  })
  const workspace = makeWorkspace([makePlot('legacy')])
  await writeServerWorkspace('old-server', workspace, 0)
  await writeServerWorkspace('old-server', { ...workspace, draft: 'Another edit' }, 1)
  assert.deepEqual(calls.map(c => c.method), ['PATCH', 'PUT', 'PUT'])
  assert.deepEqual(calls[1].body.workspace.artifacts, workspace.artifacts)
  assert.deepEqual(calls[2].body.workspace.artifacts, workspace.artifacts)
})

test('conflicts and uncertain writes are not replayed as a full overwrite', async context => {
  const calls = []
  let response = { ok: false, status: 409, json: async () => ({ detail: 'Workspace changed in another window' }) }
  context.mock.method(globalThis, 'fetch', async (_url, options) => { calls.push(options.method); if (response instanceof Error) throw response; return response })
  const workspace = makeWorkspace([makePlot('stale')])
  await assert.rejects(writeServerWorkspace('conflict', workspace, 0), /another window/)
  response = new TypeError('connection lost')
  await assert.rejects(writeServerWorkspace('uncertain', workspace, 0), /connection lost/)
  assert.deepEqual(calls, ['PATCH', 'PATCH'])
})

test('malformed receipts and a runtime reset cannot authorize artifact reuse', async context => {
  const calls = []
  let result = { ok: true }
  let finish
  context.mock.method(globalThis, 'fetch', async (_url, options) => {
    const body = JSON.parse(options.body)
    calls.push(body)
    if (result === 'delayed') return new Promise(resolve => { finish = resolve })
    return { ok: true, json: async () => result }
  })
  const workspace = makeWorkspace([makePlot('retained')])
  await assert.rejects(writeServerWorkspace('session', workspace, 0), /did not confirm/)
  result = 'delayed'
  const pending = writeServerWorkspace('session', workspace, 0)
  resetWorkspaceTransport()
  finish({ ok: true, json: async () => ({ workspaceRevision: 1, revision: 2 }) })
  await pending
  result = { workspaceRevision: 2, revision: 3 }
  await writeServerWorkspace('session', workspace, 1)
  assert.ok(calls.every(c => 'value' in c.artifact_slots[0]))
})

test('reading a fresh session drops the previous artifact optimization cache', async context => {
  const calls = fakeServer(context)
  const workspace = makeWorkspace([makePlot('read-again')])
  await writeServerWorkspace('reload', workspace, 0)
  const original = globalThis.fetch
  context.mock.method(globalThis, 'fetch', async (url, options) => options.method === 'GET'
    ? { ok: true, json: async () => ({ id: 'reload', workspaceRevision: 1, workspace }) }
    : original(url, options))
  await readServerSession('reload')
  await writeServerWorkspace('reload', workspace, 1)
  assert.ok('value' in calls[1].body.artifact_slots[0])
})
