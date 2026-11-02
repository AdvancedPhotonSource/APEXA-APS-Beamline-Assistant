/** Synthetic feature-request interactions. See browser-canvas.mjs for the Vite/Playwright setup. */
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.APEXA_PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.APEXA_CHROME || undefined })
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
page.setDefaultTimeout(15000)
const errors = [], requests = [], passed = []
let contextReads = 0, pendingSave, outcome = 'hold'
page.on('pageerror', error => errors.push(String(error)))
const send = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
await page.route(url => /^\/(?:api|local\/api|remote\/api)\//.test(url.pathname), async route => {
  const path = new URL(route.request().url()).pathname
  if (path === '/api/feedback') {
    requests.push(route.request().postDataJSON())
    if (outcome === 'hold') { pendingSave = route; return }
    if (outcome === 'offline') return route.abort('failed')
    return send(route, { ok: true, id: 'request-fixture-123' })
  }
  if (path === '/api/feedback/context') {
    contextReads++
    return send(route, { model: 'synthetic', tools: [] })
  }
  if (path === '/api/status') return send(route, { status: 'synthetic', mcp_client_connected: false, connected_servers: [], active_connections: 0 })
  return send(route, { detail: 'External services disabled in feedback fixture' }, 503)
})
try {
  await page.goto(`${process.env.APEXA_VITE_URL || 'http://127.0.0.1:5179'}/tests/browser-fixture.html`)
  await page.waitForFunction(() => !!window.apexaHarness)
  await page.evaluate(() => window.apexaHarness.renderShell())
  const open = page.getByRole('button', { name: 'Request a feature', exact: true })
  await open.click()
  const dialog = page.getByRole('dialog', { name: 'Request a feature' })
  const description = dialog.getByLabel('What would you like APEXA to do?')
  await description.waitFor()
  assert.equal(await description.evaluate(element => element === document.activeElement), true)
  await dialog.getByRole('button', { name: 'Save request', exact: true }).click()
  await dialog.getByRole('alert').filter({ hasText: 'Please describe the feature' }).waitFor()
  assert.equal(requests.length, 0)
  assert.equal(contextReads, 0)
  passed.push('standalone form is available before chat and rejects empty requests')

  const text = 'Compare reconstructed grains from multiple scans'
  await description.fill(text)
  await dialog.getByRole('button', { name: 'Save request', exact: true }).click()
  await dialog.getByRole('button', { name: 'Saving…', exact: true }).waitFor()
  assert.equal(await description.isDisabled(), true)
  assert.equal(await dialog.getByRole('button', { name: 'Saving…', exact: true }).isDisabled(), true)
  await page.keyboard.press('Escape')
  assert.equal(await dialog.isVisible(), true)
  // The intercepted request may reach the handler after the button updates.
  for (let i = 0; i < 100 && !pendingSave; i++) await new Promise(resolve => setTimeout(resolve, 10))
  assert.ok(pendingSave)
  assert.equal(requests.length, 1)
  await send(pendingSave, { ok: false, error: 'Storage unavailable' }, 503)
  await dialog.getByRole('alert').filter({ hasText: 'Storage unavailable' }).waitFor()
  assert.equal(await description.inputValue(), text)
  assert.equal(await description.isEnabled(), true)
  passed.push('pending submission prevents duplicates; storage failure preserves the draft')

  outcome = 'offline'
  await dialog.getByRole('button', { name: 'Save request', exact: true }).click()
  await dialog.getByRole('alert').filter({ hasText: 'Could not confirm the save' }).waitFor()
  assert.equal(await description.inputValue(), text)
  assert.equal(await dialog.getByRole('status').count(), 0)
  passed.push('network failure stays recoverable without a false success')

  outcome = 'success'
  await dialog.getByRole('button', { name: 'Save request', exact: true }).click()
  await dialog.getByRole('status').filter({ hasText: 'request-fixture-123' }).waitFor()
  assert.deepEqual(requests.at(-1), { kind: 'report', category: 'feature_request', comment: text, message_id: '', context: {} })
  assert.equal(contextReads, 0)
  await dialog.getByRole('button', { name: 'Done', exact: true }).click()
  await dialog.waitFor({ state: 'detached' })
  assert.equal(await open.evaluate(element => element === document.activeElement), true)
  passed.push('successful save shows a receipt and returns focus to the feature button')

  await open.click()
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'detached' })
  passed.push('Escape dismisses an idle dialog')

  await page.setViewportSize({ width: 390, height: 844 })
  await open.click()
  await dialog.waitFor()
  const bounds = await dialog.boundingBox()
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390 && bounds.height <= 844)
  assert.equal(await dialog.evaluate(element => getComputedStyle(element).backgroundColor), 'rgb(255, 255, 255)')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  passed.push('mobile dialog fits the viewport and retains the Classic light palette')

  await page.evaluate(() => window.apexaHarness.renderMessage({ id: 'answer-1', role: 'assistant', content: 'Synthetic answer', timestamp: 1 }))
  await page.getByRole('button', { name: 'Report', exact: true }).click()
  const report = page.getByRole('dialog', { name: 'Report an issue', exact: true })
  await report.getByLabel('What happened, and what did you expect?').fill('The expected result was missing')
  // Wait for the displayed diagnostics, so this tests the existing report path.
  await report.getByRole('button', { name: 'show what is attached' }).click()
  await report.locator('pre').filter({ hasText: 'synthetic' }).waitFor()
  await report.getByRole('button', { name: 'Send report', exact: true }).click()
  await report.getByRole('status').waitFor()
  assert.equal(requests.at(-1).category, 'wrong_result')
  assert.equal(requests.at(-1).message_id, 'answer-1')
  assert.equal(requests.at(-1).context.model, 'synthetic')
  passed.push('answer reports still retain their message and optional diagnostic context')

  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ browser: browser.version(), passed }, null, 2))
} finally {
  await browser.close()
}
