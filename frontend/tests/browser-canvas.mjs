/** Optional synthetic browser interactions; uses the same external Playwright/Vite setup as browser-workflows.mjs. */
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.APEXA_PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.APEXA_CHROME || undefined })
const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } })
page.setDefaultTimeout(15000)
const errors = [], dialogs = [], passed = []
page.on('pageerror', error => errors.push(String(error)))
page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.dismiss() })
const imageData = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="cyan"/></svg>')
const artifact = id => ({ id, title: `Image ${id}`, type: 'image', data: imageData, sourceMessageId: '' })
let uploadCount = 0, holdUpload = false, delayedUpload, delayedViewer, delayedCsv
const send = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const waitFor = async check => { for (let i = 0; i < 100 && !check(); i++) await new Promise(resolve => setTimeout(resolve, 10)); assert.ok(check()) }
await page.route(url => /^\/(?:api|local\/api|remote\/api)\//.test(url.pathname), async route => {
  const url = new URL(route.request().url())
  if (url.pathname === '/api/upload') {
    uploadCount++
    if (holdUpload) { delayedUpload = route; return }
    return send(route, { file_id: `upload-${uploadCount}`, filename: `report-${uploadCount}.html`, saved_path: `/synthetic/report-${uploadCount}.html` })
  }
  if (url.pathname === '/local/api/science/report') return send(route, { html: '<html><body>Synthetic report</body></html>', title: 'Synthetic report', warnings: [] })
  if (url.pathname === '/api/viz/discover') return send(route, { lineout: [{ file: '/synthetic/lineout.csv' }], calibrant: [], caked: [], peaks_h5: [], grains: [], spots: [], microstructure: [] })
  if (url.pathname === '/api/viz/lineout') { delayedViewer = route; return }
  if (url.pathname === '/api/browse') return send(route, { path: '/synthetic', parent: '/', entries: [{ name: 'late.csv', path: '/synthetic/late.csv', is_dir: false, ext: '.csv', size: 10, modified: 1 }], offset: 0, total: 1, has_more: false, next_offset: null })
  if (url.pathname === '/api/data/csv') { delayedCsv = route; return }
  if (url.pathname === '/api/status') return send(route, { status: 'synthetic', mcp_client_connected: false, connected_servers: [], active_connections: 0 })
  return send(route, { detail: 'External services disabled in canvas fixture' }, 503)
})
const state = () => page.evaluate(() => {
  const { artifacts, openIds, activeId, compareIds, pinned } = window.apexaHarness.useVizStore.getState()
  return { ids: artifacts.map(item => item.id), openIds, activeId, compareIds, pinned }
})
try {
  await page.goto(`${process.env.APEXA_VITE_URL || 'http://127.0.0.1:5179'}/tests/browser-fixture.html`)
  await page.waitForFunction(() => !!window.apexaHarness)
  await page.evaluate(items => {
    const h = window.apexaHarness
    h.useChatStore.setState({ currentSessionId: 'canvas-a', messages: [], _pendingToolResults: [] })
    h.useVizStore.getState().clear()
    h.useVizStore.getState().addArtifacts(items)
    h.renderCanvas()
  }, ['A', 'B', 'C'].map(artifact))
  assert.equal(await page.getByRole('tab', { name: 'Image C', exact: true }).getAttribute('aria-selected'), 'true')
  await page.getByRole('tab', { name: 'Image A', exact: true }).click()
  await page.getByRole('button', { name: /^⧉ compare/ }).click()
  await page.getByRole('tab', { name: 'Image B', exact: true }).click()
  await page.getByRole('button', { name: /^⧉ compare/ }).click()
  assert.deepEqual((await state()).compareIds, ['A', 'B'])
  await page.locator('.apexa-compare-grid').waitFor()
  passed.push('comparison survives selecting the second tab')

  await page.getByRole('button', { name: 'Gallery', exact: true }).click()
  await page.locator('article').filter({ hasText: 'Image C' }).getByRole('button', { name: 'Open', exact: true }).click()
  assert.deepEqual((await state()).compareIds, [])
  assert.equal(await page.locator('.apexa-compare-grid').count(), 0)
  assert.equal(await page.getByRole('tab', { name: 'Image C', exact: true }).getAttribute('aria-selected'), 'true')
  passed.push('Gallery Open exits an unrelated comparison')

  await page.getByRole('button', { name: /^☆ pin/ }).click()
  const closeC = page.getByRole('button', { name: 'Close Image C', exact: true })
  await closeC.focus(); await closeC.press('Enter')
  assert.equal((await state()).activeId, 'B')
  assert.equal(await page.getByRole('tab', { name: 'Image C', exact: true }).count(), 0)
  await page.getByRole('button', { name: /^History \(/ }).click()
  await page.getByRole('checkbox', { name: /^Favorites only/ }).check()
  assert.equal(await page.getByRole('button', { name: /^Open Image .* in Canvas$/ }).count(), 1)
  const reopenC = page.getByRole('button', { name: 'Open Image C in Canvas', exact: true })
  await reopenC.focus(); await reopenC.press('Enter')
  assert.equal((await state()).activeId, 'C')
  passed.push('keyboard close keeps a favorite available through History')

  const tabC = page.getByRole('tab', { name: 'Image C', exact: true })
  await tabC.focus(); await tabC.press('Delete')
  await page.getByRole('tab', { name: 'Image B', exact: true }).press('Home')
  assert.equal((await state()).activeId, 'A')
  await page.getByRole('tab', { name: 'Image A', exact: true }).press('ArrowRight')
  assert.equal((await state()).activeId, 'B')
  passed.push('Delete and arrow/Home keyboard tab navigation')

  await page.getByRole('button', { name: 'Gallery', exact: true }).click()
  for (const id of ['A', 'C']) await page.locator('article').filter({ hasText: `Image ${id}` }).getByRole('button', { name: 'Compare', exact: true }).click()
  await page.getByRole('button', { name: 'View comparison', exact: true }).click()
  assert.deepEqual((await state()).compareIds, ['A', 'C'])
  assert.ok((await state()).openIds.includes('C'))
  await page.locator('.apexa-compare-grid').waitFor()
  passed.push('View comparison reopens both selected results')

  await page.getByRole('button', { name: /^History \(/ }).click()
  await page.evaluate(item => window.apexaHarness.useVizStore.getState().addArtifacts([item]), artifact('new'))
  await page.getByRole('tab', { name: 'Image new', exact: true }).waitFor()
  assert.equal((await state()).activeId, 'new')
  await page.getByRole('button', { name: 'Close Image new', exact: true }).click()
  const selectedBeforeRepeat = (await state()).activeId
  await page.evaluate(item => window.apexaHarness.useVizStore.getState().addArtifacts([item]), artifact('new'))
  assert.equal((await state()).activeId, selectedBeforeRepeat)
  assert.equal((await state()).openIds.includes('new'), false)
  passed.push('new results reveal Active; repeated deliveries do not reopen closed tabs')

  await page.evaluate(items => window.apexaHarness.useVizStore.getState().addArtifacts(items), Array.from({ length: 30 }, (_, i) => artifact(`wide-${i}`)))
  const selected = page.getByRole('tab', { name: 'Image wide-29', exact: true })
  await selected.waitFor()
  const stripBox = await page.getByRole('tablist', { name: 'Open results' }).boundingBox()
  const tabBox = await selected.boundingBox()
  assert.ok(tabBox.x >= stripBox.x && tabBox.x + tabBox.width <= stripBox.x + stripBox.width + 1)
  passed.push('new active tab scrolls into the visible strip')

  await page.evaluate(() => window.apexaHarness.renderToolCard())
  const cardToggle = page.getByRole('button', { name: /fixture_summary/ })
  await cardToggle.click()
  await page.getByRole('button', { name: /open in Canvas/ }).click()
  const firstGeneric = (await state()).activeId
  await cardToggle.click(); await cardToggle.click()
  await page.getByRole('button', { name: /open in Canvas/ }).click()
  assert.equal((await state()).activeId, firstGeneric)
  assert.equal((await state()).ids.filter(id => id === firstGeneric).length, 1)
  passed.push('generic tool card remounts reuse the same explicit result tab')

  await page.evaluate(() => window.apexaHarness.renderShell())
  const layouts = page.getByRole('group', { name: 'Workspace layout' })
  await layouts.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.evaluate(item => window.apexaHarness.useVizStore.getState().addArtifacts([item]), artifact('revealed'))
  await page.getByRole('tab', { name: 'Image revealed', exact: true }).waitFor()
  assert.equal(await layouts.getByRole('button', { name: 'Split', exact: true }).getAttribute('aria-pressed'), 'true')
  await layouts.getByRole('button', { name: 'Chat', exact: true }).click()
  await page.evaluate(item => window.apexaHarness.useVizStore.getState().addArtifacts([item]), artifact('revealed'))
  assert.equal(await layouts.getByRole('button', { name: 'Chat', exact: true }).getAttribute('aria-pressed'), 'true')
  passed.push('new result reveals hidden canvas; duplicate respects chosen Chat layout')

  await page.evaluate(() => { window.apexaHarness.useVizStore.getState().clear(); window.apexaHarness.renderCanvas() })
  await page.locator('input[type="file"]').setInputFiles(['one', 'two'].map(name => ({ name: `${name}.html`, mimeType: 'text/html', buffer: Buffer.from('<html>Synthetic</html>') })))
  await page.waitForFunction(() => window.apexaHarness.useVizStore.getState().artifacts.length === 2)
  passed.push('Dashboard multi-file upload survives its first-result unmount')

  await page.evaluate(() => { window.apexaHarness.useVizStore.getState().clear(); window.apexaHarness.renderCanvas() })
  holdUpload = true
  await page.locator('input[type="file"]').setInputFiles({ name: 'late.html', mimeType: 'text/html', buffer: Buffer.from('<html>Synthetic</html>') })
  await waitFor(() => delayedUpload)
  await page.evaluate(() => {
    const h = window.apexaHarness
    h.useChatStore.setState({ currentSessionId: 'canvas-b' }); h.useVizStore.getState().clear()
    h.useChatStore.setState({ currentSessionId: 'canvas-a' })
  })
  await send(delayedUpload, { file_id: 'late', saved_path: '/synthetic/late.html' })
  await page.waitForTimeout(100)
  assert.deepEqual((await state()).ids, [])
  passed.push('Dashboard ignores delayed upload after switching away and back')

  await page.evaluate(() => window.apexaHarness.renderLauncher())
  await page.getByPlaceholder('/path/to/analysis').fill('/synthetic')
  await page.getByRole('button', { name: 'Scan', exact: true }).click()
  await page.getByRole('button', { name: 'lineout.csv', exact: true }).click()
  await waitFor(() => delayedViewer)
  const viewerResult = { title: 'Synthetic lineout', plotly: { data: [{ x: [1, 2], y: [3, 4] }] }, tables: [] }
  await send(delayedViewer, viewerResult)
  await page.waitForFunction(() => window.apexaHarness.useVizStore.getState().artifacts.length === 1)
  const originalViewer = (await state()).activeId
  delayedViewer = null
  await page.getByRole('button', { name: 'lineout.csv', exact: true }).click()
  await waitFor(() => delayedViewer)
  await send(delayedViewer, viewerResult)
  await page.waitForTimeout(100)
  assert.deepEqual((await state()).ids, [originalViewer])
  passed.push('reopening unchanged folder visualization reuses its result tab')
  await page.evaluate(() => window.apexaHarness.useVizStore.getState().clear())
  delayedViewer = null
  await page.getByRole('button', { name: 'lineout.csv', exact: true }).click()
  await waitFor(() => delayedViewer)
  await page.evaluate(() => window.apexaHarness.useChatStore.setState({ currentSessionId: 'canvas-b' }))
  await send(delayedViewer, { title: 'Late lineout', plotly: { data: [] }, tables: [] }).catch(() => {})
  await page.waitForTimeout(100)
  assert.deepEqual((await state()).ids, [])
  passed.push('folder viewer ignores a result from the previous session')

  await page.evaluate(() => window.apexaHarness.renderFiles())
  await page.getByRole('button', { name: 'late.csv', exact: false }).click()
  await waitFor(() => delayedCsv)
  await page.evaluate(() => { window.apexaHarness.useChatStore.setState({ currentSessionId: 'canvas-c' }); window.apexaHarness.useVizStore.getState().clear() })
  await send(delayedCsv, { columns: ['x', 'y'], data: { x: [1], y: [2] } })
  await page.waitForTimeout(100)
  assert.deepEqual((await state()).ids, [])
  assert.deepEqual(dialogs, [])
  passed.push('Data file browser ignores delayed CSV completion after session switch')
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ browser: browser.version(), passed }, null, 2))
} finally { await browser.close() }
