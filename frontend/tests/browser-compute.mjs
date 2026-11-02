/** Synthetic browser checks for recorded compute plans; no actual compute probes. */
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.APEXA_PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.APEXA_CHROME || undefined })
const page = await browser.newPage({ viewport: { width: 900, height: 900 } })
const errors = [], passed = []
page.on('pageerror', error => errors.push(String(error)))
await page.route(url => /^\/(?:api|local\/api|remote\/api)\//.test(url.pathname), route => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }))
try {
  await page.goto(`${process.env.APEXA_VITE_URL || 'http://127.0.0.1:5179'}/tests/browser-fixture.html`)
  await page.waitForFunction(() => !!window.apexaHarness)
  const result = { tool: 'midas_integrate_series', status: 'completed', data: { tool: 'midas_integrate_series', status: 'completed',
    compute: { target: 'local-cpu', allocation: { host: '', device: 'cpu', n_workers: 4, threads_per_worker: 2, worker_rss_gb: 3,
      reason: '4 workers fit the available memory', admission_scope: 'same_host_registry', resources: 'Synthetic inventory: 16 cores' } } } }
  await page.evaluate(item => window.apexaHarness.renderToolCard(item), result)
  await page.getByRole('button', { name: /midas_integrate_series/ }).click()
  const plan = page.getByRole('region', { name: 'Compute plan' })
  await plan.getByText('4 workers fit the available memory').waitFor()
  assert.equal(await plan.locator('dd').filter({ hasText: /^CPU$/ }).count(), 1)
  await plan.getByText('Inventory and reservation scope').click()
  await plan.getByText('Synthetic inventory: 16 cores').waitFor()
  passed.push('expanded tool card exposes recorded allocation and inventory scope')

  await page.getByRole('button', { name: /open in Canvas/ }).click()
  await page.evaluate(() => window.apexaHarness.renderCanvas())
  await page.getByRole('region', { name: 'Compute plan' }).getByText('4 workers fit the available memory').waitFor()
  passed.push('opening the result in Canvas retains the compute explanation')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => window.apexaHarness.renderToolCard({ tool: 'midas_calibrate_series', status: 'failed', data: {
    error: 'Not enough memory', nothing_was_run: true, compute: { device: 'cpu', host: '', n_workers: 0, reason: 'No worker fits available RAM' },
  } }))
  await page.getByRole('button', { name: /midas_calibrate_series/ }).click()
  await plan.getByText('No processing started.').waitFor()
  await page.getByText('Not enough memory', { exact: true }).waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
  passed.push('failed admission shows its reason and no-execution notice on mobile')

  await page.evaluate(() => window.apexaHarness.renderToolCard({ tool: 'fixture_summary', status: 'completed', data: { mean: 1 } }))
  await page.getByRole('button', { name: /fixture_summary/ }).click()
  assert.equal(await plan.count(), 0)
  passed.push('tools without a compute decision do not invent one')
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ browser: browser.version(), passed }, null, 2))
} finally { await browser.close() }
