/** Optional real-browser workflow interactions with synthetic API responses.
 * APEXA_PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs
 * APEXA_CHROME=/absolute/path/to/Chrome node tests/browser-workflows.mjs
 */
import assert from 'node:assert/strict'
import { workflowFixture } from './workflow-fixture.mjs'
const { chromium } = await import(process.env.APEXA_PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.APEXA_CHROME || undefined })
const page = await browser.newPage({ viewport: { width: 1100, height: 1000 } })
page.setDefaultTimeout(10000)
const errors = []
const posts = []
page.on('pageerror', error => errors.push(String(error)))
let current = workflowFixture()
let second = []
let delayedRead = null
let holdFirstSession = false
let unavailable = false
const send = (route, payload, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) })
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const request = route.request()
  const url = new URL(request.url())
  if (request.method() === 'POST') {
    const body = request.postDataJSON()
    posts.push({ path: url.pathname, ...body })
    if (body.expected_revision !== current.revision) return send(route, { detail: 'Gate changed' }, 409)
    if (url.pathname.endsWith('/cancel')) {
      current = { ...current, revision: current.revision + 1, orchestration_state: 'cancel_requested', gate: null, job_execution: 'running' }
    } else {
      current = { ...current, revision: 5, scientific_verdict: 'provisional', job_execution: 'completed',
        gate: { ...current.gate, gate_id: 'gate-result', kind: 'result_review', step_id: 'calibrate',
          prompt: 'Review recorded calibration evidence. Approval continues the plan without changing the scientific verdict.' },
        stages: [{ ...current.stages[0], state: 'completed', run_id: 'synthetic-job', outcome: { execution: 'completed', scientific_verdict: 'provisional' } }, current.stages[1]] }
    }
    return send(route, { accepted: true }, 202)
  }
  if (url.pathname === '/api/workflows') {
    if (unavailable) return send(route, { detail: 'Synthetic workflow service unavailable' }, 503)
    if (url.searchParams.get('session_id') === 'session-a') {
      if (holdFirstSession) { delayedRead = route; return }
      return send(route, { workflows: [current] })
    }
    return send(route, { workflows: second })
  }
  return send(route, { detail: 'External APIs disabled for synthetic workflow interactions' }, 503)
})
try {
  await page.goto(`${process.env.APEXA_VITE_URL || 'http://127.0.0.1:5179'}/tests/browser-fixture.html`)
  await page.waitForFunction(() => !!window.apexaHarness)
  await page.evaluate(() => window.apexaHarness.renderWorkflows())
  const workflows = page.getByRole('region', { name: 'Session workflows' })
  await workflows.getByRole('heading', { name: 'Review plan', exact: true }).waitFor()
  await workflows.getByRole('textbox', { name: 'Decision note (optional)' }).fill('Reviewed synthetic plan')
  await workflows.getByRole('button', { name: 'Approve & continue', exact: true }).click()
  await workflows.getByRole('heading', { name: 'Review stage result', exact: true }).waitFor()
  assert.equal(posts.length, 1)
  assert.equal(posts[0].path, '/api/workflows/workflow-fixture/advance')
  assert.equal(posts[0].expected_revision, 4)
  assert.equal(posts[0].gate_id, 'gate-plan')
  assert.equal(posts[0].reason, 'Reviewed synthetic plan')
  assert.ok(posts[0].request_id)
  assert.equal(await workflows.getByRole('textbox', { name: 'Decision note (optional)' }).inputValue(), '')
  assert.match(await workflows.locator('[aria-label="Workflow outcomes"]').innerText(), /Science: provisional/)
  await workflows.getByText('Stages and evidence (2)', { exact: true }).click()
  assert.equal(await workflows.getByRole('link', { name: /Open recorded job evidence/ }).getAttribute('href'), '/api/runs/synthetic-job')

  // Another client changes the gate after it was painted in this tab.
  current = { ...current, revision: 6, orchestration_state: 'blocked', gate: { ...current.gate, gate_id: 'gate-rejected', decisions: ['reject'] } }
  await workflows.getByRole('button', { name: 'Approve & continue', exact: true }).click()
  await workflows.getByText('Continuation is unavailable for this recorded result.').waitFor()
  assert.equal(posts.length, 2)
  assert.equal(posts[1].expected_revision, 5)
  assert.match(await workflows.innerText(), /changed before the decision/)
  assert.equal(await workflows.getByRole('button', { name: 'Approve & continue', exact: true }).count(), 0)
  assert.equal(await workflows.getByRole('button', { name: 'Stop workflow', exact: true }).count(), 1)

  await workflows.getByRole('button', { name: 'Request cancellation', exact: true }).click()
  await workflows.getByText('Workflow: cancel requested', { exact: true }).waitFor()
  assert.equal(posts.length, 3)
  assert.equal(posts[2].path, '/api/workflows/workflow-fixture/cancel')
  assert.equal(posts[2].expected_revision, 6)
  assert.equal(await workflows.getByRole('button', { name: 'Request cancellation', exact: true }).count(), 0)
  assert.match(await workflows.innerText(), /External jobs may still be active/)
  assert.match(await workflows.locator('[aria-label="Workflow outcomes"]').innerText(), /Execution: running/)

  holdFirstSession = true
  await workflows.getByRole('button', { name: 'Refresh', exact: true }).click()
  for (let i = 0; !delayedRead && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10))
  assert.ok(delayedRead)
  await page.evaluate(() => window.apexaHarness.useChatStore.setState({ currentSessionId: 'session-b', messages: [], _pendingToolResults: [] }))
  await workflows.getByText('Prepared plans appear here for review before execution.').waitFor()
  await send(delayedRead, { workflows: [current] }).catch(() => {})
  assert.equal(await workflows.getByRole('article').count(), 0)

  // A prepared workflow arriving in a chat tool result refreshes the active session.
  second = [workflowFixture({ workflow_id: 'workflow-b', session_id: 'session-b' })]
  await page.evaluate(() => window.apexaHarness.useChatStore.setState({ _pendingToolResults: [{ tool: 'workflow_prepare', status: 'completed', data: { workflow_id: 'workflow-b' } }] }))
  await workflows.getByText('workflow-b', { exact: true }).waitFor()
  assert.equal(await workflows.getByText('workflow-fixture', { exact: true }).count(), 0)
  unavailable = true
  await workflows.getByRole('button', { name: 'Refresh', exact: true }).click()
  await workflows.getByRole('alert').waitFor()
  assert.match(await workflows.getByRole('alert').innerText(), /Synthetic workflow service unavailable/)
  assert.equal(await workflows.getByRole('button', { name: 'Approve & continue', exact: true }).isDisabled(), true)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ browser: browser.version(), passed: ['explicit revision/gate/request approval', 'decision note resets per gate',
    'provisional scientific verdict preserved', 'job evidence link', '409 refresh without replay', 'reject-only gate',
    'cancellation remains unresolved', 'session switch ignores delayed read', 'chat tool result refresh', 'API failure disables stale decisions'], posts: posts.length }, null, 2))
} finally { await browser.close() }
