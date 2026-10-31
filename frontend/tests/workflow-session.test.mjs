import { test } from 'node:test'
import assert from 'node:assert/strict'
import { WorkflowSession } from '../src/lib/workflowSession.ts'
import { workflowFixture } from './workflow-fixture.mjs'

const response = (payload, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(payload) })
const settle = async () => { for (let i = 0; i < 16; i++) await Promise.resolve() }

test('late read cannot overwrite a refreshed gate, even when transport ignores abort', async () => {
  const reads = []
  globalThis.fetch = (url, options) => new Promise(resolve => reads.push({ url, ...options, resolve }))
  const session = new WorkflowSession('session-a')
  try {
    session.start()
    const latest = session.refresh()
    assert.equal(reads[0].signal.aborted, true)
    reads[1].resolve(response({ workflows: [workflowFixture({ revision: 5 })] }))
    await latest
    reads[0].resolve(response({ workflows: [workflowFixture({ revision: 4 })] }))
    await settle()
    assert.equal(session.getSnapshot().workflows[0].revision, 5)
  } finally { session.stop() }
})

test('unmount aborts requests and a switched session ignores previous-session data', async () => {
  let oldRead
  globalThis.fetch = (url, options) => {
    const id = new URL(url, 'http://fixture').searchParams.get('session_id')
    if (id === 'session-a') return new Promise(resolve => { oldRead = { ...options, resolve } })
    return Promise.resolve(response({ workflows: [workflowFixture(), workflowFixture({ workflow_id: 'workflow-b', session_id: 'session-b' })] }))
  }
  const first = new WorkflowSession('session-a')
  const second = new WorkflowSession('session-b')
  first.start()
  first.stop()
  try {
    second.start()
    await settle()
    oldRead.resolve(response({ workflows: [workflowFixture()] }))
    await settle()
    assert.equal(oldRead.signal.aborted, true)
    assert.deepEqual(first.getSnapshot().workflows, [])
    assert.deepEqual(second.getSnapshot().workflows.map(run => run.workflow_id), ['workflow-b'])
  } finally { second.stop() }
})

test('duplicate clicks dispatch once; a 409 refreshes the gate without replaying approval', async () => {
  let current = workflowFixture()
  let resolveAction
  const posts = []
  globalThis.fetch = (url, options) => options.method === 'POST'
    ? new Promise(resolve => { posts.push({ url, ...JSON.parse(options.body) }); resolveAction = resolve })
    : Promise.resolve(response({ workflows: [current] }))
  const session = new WorkflowSession('session-a')
  try {
    session.start()
    await settle()
    const displayed = session.getSnapshot().workflows[0]
    const decision = session.decide(displayed, 'approve', 'Reviewed overlay')
    await session.decide(displayed, 'approve', 'Reviewed overlay')
    assert.equal(posts.length, 1)
    assert.equal(posts[0].expected_revision, 4)
    assert.equal(posts[0].gate_id, 'gate-plan')
    assert.ok(posts[0].request_id)
    current = workflowFixture({ revision: 6, gate: { ...current.gate, gate_id: 'gate-result', kind: 'result_review' }, scientific_verdict: 'provisional' })
    resolveAction(response({ detail: 'Gate changed' }, 409))
    await decision
    assert.equal(session.getSnapshot().workflows[0].revision, 6)
    assert.match(session.getSnapshot().notice, /changed before the decision/)
    await session.decide(displayed, 'approve', 'Old view')
    assert.equal(posts.length, 1, 'old painted gate must never approve a newly loaded gate')
    assert.equal(session.getSnapshot().workflows[0].scientific_verdict, 'provisional')
  } finally { session.stop() }
})

test('lost action response permits only an explicit retry with the same request identity', async () => {
  let current = workflowFixture()
  const posts = []
  globalThis.fetch = async (url, options) => {
    if (options.method !== 'POST') return response({ workflows: [current] })
    posts.push({ url, ...JSON.parse(options.body) })
    if (posts.length === 1) throw new TypeError('Network response lost')
    current = workflowFixture({ revision: 5, scientific_verdict: 'provisional' })
    return response({ accepted: true }, 202)
  }
  const session = new WorkflowSession('session-a')
  try {
    session.start()
    await settle()
    const run = session.getSnapshot().workflows[0]
    await session.decide(run, 'approve', 'Reviewed')
    assert.deepEqual(session.getSnapshot().uncertainIds, [run.workflow_id])
    await session.decide(run, 'approve', 'New action')
    await session.cancel(run)
    assert.equal(posts.length, 1)
    await session.retry(run.workflow_id)
    assert.deepEqual(posts[1], posts[0])
    assert.deepEqual(session.getSnapshot().uncertainIds, [])
    assert.equal(session.getSnapshot().workflows[0].scientific_verdict, 'provisional')
  } finally { session.stop() }
})

test('server allowed decisions govern dispatch and cancellation preserves unresolved status', async () => {
  let current = workflowFixture({ orchestration_state: 'waiting_job', gate: { gate_id: 'result-gate', kind: 'result_review',
    step_id: 'calibrate', decisions: ['reject'], recovery: false, prompt: 'Job not yet complete' }, job_execution: 'running' })
  const posts = []
  globalThis.fetch = async (url, options) => {
    if (options.method !== 'POST') return response({ workflows: [current] })
    posts.push({ url, ...JSON.parse(options.body) })
    current = workflowFixture({ revision: 5, orchestration_state: 'cancel_requested', gate: null, job_execution: 'running' })
    return response(current)
  }
  const session = new WorkflowSession('session-a')
  try {
    session.start()
    await settle()
    await session.decide(session.getSnapshot().workflows[0], 'approve', '')
    assert.equal(posts.length, 0)
    await session.cancel(session.getSnapshot().workflows[0])
    assert.equal(posts[0].url, '/api/workflows/workflow-fixture/cancel')
    assert.equal(posts[0].expected_revision, 4)
    assert.equal(session.getSnapshot().workflows[0].job_execution, 'running')
    assert.match(session.getSnapshot().notice, /must confirm/)
    await session.cancel(session.getSnapshot().workflows[0])
    assert.equal(posts.length, 1)
  } finally { session.stop() }
})

test('polling follows active work and stops when a new review gate is reached', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  let current = workflowFixture({ orchestration_state: 'running', gate: null })
  let reads = 0
  globalThis.fetch = async () => { reads++; return response({ workflows: [current] }) }
  const session = new WorkflowSession('session-a', 5000)
  try {
    session.start()
    await settle()
    assert.equal(reads, 1)
    current = workflowFixture({ revision: 5 })
    context.mock.timers.tick(5000)
    await settle()
    assert.equal(reads, 2)
    context.mock.timers.tick(60000)
    await settle()
    assert.equal(reads, 2)
  } finally { session.stop(); context.mock.timers.reset() }
})

test('unavailable workflow API reports failure rather than presenting an empty successful list', async () => {
  globalThis.fetch = async () => response({ detail: 'Workflow routes unavailable' }, 404)
  const session = new WorkflowSession('session-a')
  try {
    session.start()
    await settle()
    assert.equal(session.getSnapshot().loading, false)
    assert.match(session.getSnapshot().error, /Workflow routes unavailable/)
  } finally { session.stop() }
})
