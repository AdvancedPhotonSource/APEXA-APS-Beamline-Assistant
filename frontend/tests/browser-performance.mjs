/** Optional real-browser checks. Requires a running local Vite and Playwright.
 * APEXA_PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs
 * APEXA_CHROME=/absolute/path/to/Chrome node tests/browser-performance.mjs
 */
import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
const { chromium } = await import(process.env.APEXA_PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.APEXA_CHROME || undefined })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', error => errors.push(String(error)))
await page.route(url => url.pathname.startsWith('/api/'), route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Synthetic benchmark: external APIs disabled"}' }))
const url = `${process.env.APEXA_VITE_URL || 'http://127.0.0.1:5179'}/tests/browser-fixture.html`
const report = { browser: browser.version(), viewport: '1440x900', fixture: '1000 Markdown messages with tables and two collapsed tools each', samples: [] }
try {
  await page.goto(url)
  await page.waitForFunction(() => !!window.apexaHarness)
  // Warm module and browser caches; each sample then mounts the same fixture.
  await page.evaluate(() => window.apexaHarness.render(60))
  for (let repetition = 0; repetition < 3; repetition++) {
    for (const unbounded of [true, false]) {
      const result = await page.evaluate(unbounded => window.apexaHarness.render(1000, unbounded), unbounded)
      assert.equal(result.renderedMessages, unbounded ? 1000 : 60)
      report.samples.push({ mode: unbounded ? 'all messages (previous rendering strategy)' : 'bounded messages', ...result })
    }
  }
  await page.getByRole('button', { name: 'Older', exact: true }).click()
  assert.equal(await page.locator('[data-message-id]').count(), 60)
  const first = await page.locator('[data-message-id]').first().getAttribute('data-message-id')
  await page.evaluate(() => {
    const store = window.apexaHarness.useChatStore
    store.setState(state => ({ messages: [...state.messages, { id: 'late-answer', role: 'assistant', content: 'New answer', timestamp: 1001 }] }))
  })
  assert.equal(await page.locator('[data-message-id]').first().getAttribute('data-message-id'), first)
  await page.getByRole('button', { name: 'Latest', exact: true }).click()
  await page.locator('[data-message-id="late-answer"]').waitFor()
  report.paging = 'Older page remains anchored on incoming answer; Latest reaches new answer; at most 60 messages'

  await page.evaluate(() => {
    window.apexaHarness.useVizStore.setState({ artifacts: Array.from({ length: 150 }, (_, i) => ({ id: `plot-${i}`, type: 'plotly', title: `Synthetic plot ${i}`, data: {}, sourceMessageId: '' })) })
    window.apexaHarness.renderResults()
  })
  assert.equal(await page.locator('button[title^="Open Synthetic plot"]').count(), 60)
  await page.getByRole('button', { name: 'Next', exact: true }).click()
  assert.equal(await page.locator('button[title^="Open Synthetic plot"]').count(), 60)
  await page.getByRole('searchbox', { name: 'Search session results' }).fill('Synthetic plot 149')
  await page.waitForFunction(() => document.querySelectorAll('button[title^="Open Synthetic plot"]').length === 1)
  report.resultGallery = '150 results: at most 60 mounted; search resets to matching page'

  await page.route(url => url.pathname === '/api/browse', route => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'))
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ path: '/synthetic', parent: '/', offset, limit: 100,
      total: 205, has_more: offset < 200, next_offset: offset < 200 ? offset + 100 : null,
      entries: Array.from({ length: Math.min(100, 205 - offset) }, (_, i) => ({ name: `file-${offset + i}.tif`, path: `/synthetic/file-${offset + i}.tif`, is_dir: false, size: 1000, modified: 1, ext: '.tif' })) }) })
  })
  await page.evaluate(() => window.apexaHarness.renderFiles())
  await page.waitForFunction(() => document.querySelectorAll('.apexa-file-row').length === 100)
  await page.getByRole('button', { name: 'Next', exact: true }).click()
  await page.getByRole('button', { name: 'file-100.tif', exact: false }).waitFor()
  assert.equal(await page.locator('.apexa-file-row').count(), 100)
  await page.getByRole('button', { name: 'Next', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.apexa-file-row').length === 5)
  assert.equal(await page.getByRole('button', { name: 'Next', exact: true }).isDisabled(), true)
  report.filePaging = '205 stubbed files: 100/100/5 mounted rows; final Next disabled'

  report.persistence = await page.evaluate(async () => {
    const { BrowserSessionCache } = window.apexaHarness
    const data = { data: [{ x: Array.from({ length: 250000 }, (_, i) => i), y: Array.from({ length: 250000 }, (_, i) => Math.sin(i)) }] }
    const artifact = { id: 'large', type: 'plotly', title: 'Synthetic 250k-point plot', data, sourceMessageId: '' }
    const session = { id: 'migration', title: 'Migration fixture', messages: [], createdAt: 1, updatedAt: 1,
      workspace: { artifacts: [artifact], openIds: ['large'], activeId: 'large', pinned: [], compareIds: [], draft: 'initial' } }
    localStorage.clear()
    const legacy = { ...session, workspace: { ...session.workspace,
      artifacts: [{ ...artifact, data: { data: [{ x: [1, 2], y: [3, 4] }] } }] } }
    localStorage.setItem('apexa.chat.sessions', JSON.stringify([legacy]))
    const cache = new BrowserSessionCache()
    await cache.save(await cache.read(JSON.parse(localStorage.getItem('apexa.chat.sessions'))))
    const loaded = [session]
    const start = performance.now()
    await cache.save(loaded)
    const initialCommitMs = performance.now() - start
    const legacyRemoved = localStorage.getItem('apexa.chat.sessions') == null
    let payloadWrites = 0
    const originalPut = IDBObjectStore.prototype.put
    IDBObjectStore.prototype.put = function (...args) {
      if (this.name === 'payloads') payloadWrites++
      return originalPut.apply(this, args)
    }
    const changed = { ...loaded[0], workspace: { ...loaded[0].workspace, draft: 'latest' } }
    const editStart = performance.now()
    const olderSave = cache.save([{ ...changed, workspace: { ...changed.workspace, draft: 'older' } }])
    const latestSave = cache.save([changed])
    await Promise.all([olderSave, latestSave])
    const editCommitMs = performance.now() - editStart
    IDBObjectStore.prototype.put = originalPut
    cache.close()
    const restoredCache = new BrowserSessionCache()
    const restored = await restoredCache.read([])
    restoredCache.close()
    return { initialCommitMs, editCommitMs, payloadWrites, legacyRemoved,
      journalBytes: localStorage.getItem('apexa.chat.recovery.v1').length,
      restoredDraft: restored[0].workspace.draft, restoredPoints: restored[0].workspace.artifacts[0].data.data[0].x.length }
  })
  assert.equal(report.persistence.legacyRemoved, true)
  assert.equal(report.persistence.payloadWrites, 0, 'draft changes must reuse committed large payloads')
  assert.equal(report.persistence.restoredDraft, 'latest')
  assert.equal(report.persistence.restoredPoints, 250000)
  assert.ok(report.persistence.journalBytes < 2000)

  report.migrationFailure = await page.evaluate(async () => {
    const { BrowserSessionCache } = window.apexaHarness
    localStorage.setItem('apexa.chat.sessions', 'legacy retained')
    const cache = new BrowserSessionCache()
    await cache.read([])
    let rejected = false
    try { await cache.save([{ id: 'invalid', title: 'Uncloneable', messages: [], createdAt: 1, updatedAt: 1,
      workspace: { artifacts: [{ id: 'bad', type: 'plotly', data: { invalid() {} } }], openIds: [], pinned: [], compareIds: [], activeId: null } }]) }
    catch { rejected = true }
    cache.close()
    const reader = new BrowserSessionCache()
    const restored = await reader.read([])
    reader.close()
    // The last complete snapshot survives an aborted structured-clone transaction.
    const previous = restored.find(row => row.id === 'migration')
    return { rejected, legacyRetained: localStorage.getItem('apexa.chat.sessions') === 'legacy retained',
      previousPoints: previous.workspace.artifacts[0].data.data[0].x.length }
  })
  assert.equal(report.migrationFailure.rejected, true)
  assert.equal(report.migrationFailure.legacyRetained, true)
  assert.equal(report.migrationFailure.previousPoints, 250000)

  report.cacheConflict = await page.evaluate(async () => {
    localStorage.removeItem('apexa.chat.recovery.v1')
    const { BrowserSessionCache } = window.apexaHarness
    const first = new BrowserSessionCache(), second = new BrowserSessionCache()
    const left = await first.read([]), right = await second.read([])
    await first.save(left)
    let rejected = false
    try { await second.save(right) } catch { rejected = true }
    first.close(); second.close()
    const reader = new BrowserSessionCache()
    const restored = await reader.read([])
    reader.close()
    return { rejected, points: restored[0].workspace.artifacts[0].data.data[0].x.length }
  })
  assert.equal(report.cacheConflict.rejected, true)
  assert.equal(report.cacheConflict.points, 250000)

  const savedSessions = await page.evaluate(async () => {
    await new Promise((resolve, reject) => {
      const request = indexedDB.deleteDatabase('apexa.browser-sessions.v1')
      request.onsuccess = resolve; request.onerror = () => reject(request.error)
    })
    localStorage.clear()
    const { useChatStore, useVizStore } = window.apexaHarness
    window.disposeApexaFixture = useChatStore.getState().init({ sync: false })
    await useChatStore.getState().saveWorkspace()
    const first = useChatStore.getState().currentSessionId
    useChatStore.getState().setDraft('First session draft')
    useVizStore.getState().addArtifact({ id: 'restore-plot', type: 'plotly', title: 'Restore', sourceMessageId: '', data: { data: [{ x: [1, 2], y: [3, 4] }] } })
    useChatStore.getState().newChat()
    const second = useChatStore.getState().currentSessionId
    useChatStore.getState().setDraft('Second session draft')
    await useChatStore.getState().saveWorkspace()
    if (useChatStore.getState().browserCacheError) throw new Error(useChatStore.getState().browserCacheError)
    return { first, second }
  })
  await page.reload()
  await page.waitForFunction(() => !!window.apexaHarness)
  report.storeRestore = await page.evaluate(async ({ first, second }) => {
    const { useChatStore, useVizStore } = window.apexaHarness
    window.disposeApexaFixture = useChatStore.getState().init({ sync: false })
    await useChatStore.getState().saveWorkspace()
    const selected = useChatStore.getState().currentSessionId
    const secondDraft = useChatStore.getState().draft
    useChatStore.getState().switchSession(first)
    const firstDraft = useChatStore.getState().draft
    const points = useVizStore.getState().artifacts[0]?.data?.data?.[0]?.x
    await useChatStore.getState().saveWorkspace()
    return { selected, expected: second, firstDraft, secondDraft, points, error: useChatStore.getState().browserCacheError }
  }, savedSessions)
  assert.equal(report.storeRestore.selected, savedSessions.second)
  assert.equal(report.storeRestore.firstDraft, 'First session draft')
  assert.equal(report.storeRestore.secondDraft, 'Second session draft')
  assert.deepEqual(report.storeRestore.points, [1, 2])
  assert.equal(report.storeRestore.error, null)

  report.startupRace = await page.evaluate(async () => {
    const { BrowserSessionCache, useChatStore } = window.apexaHarness
    window.disposeApexaFixture()
    const original = BrowserSessionCache.prototype.read
    let release
    const gate = new Promise(resolve => { release = resolve })
    BrowserSessionCache.prototype.read = async function (...args) { await gate; return original.apply(this, args) }
    window.disposeApexaFixture = useChatStore.getState().init({ sync: false })
    useChatStore.getState().setDraft('Typed while cache was loading')
    release()
    await useChatStore.getState().saveWorkspace()
    BrowserSessionCache.prototype.read = original
    return { draft: useChatStore.getState().draft, sessions: useChatStore.getState().sessions.length,
      error: useChatStore.getState().browserCacheError }
  })
  assert.equal(report.startupRace.draft, 'Typed while cache was loading')
  assert.ok(report.startupRace.sessions >= 3)
  assert.equal(report.startupRace.error, null)
  assert.deepEqual(errors, [])
  report.note = 'Local headless Chrome, Vite development build; synthetic rendering/persistence only. No production, network, LLM or scientific-engine speedup is implied.'
  if (process.env.APEXA_PERF_OUTPUT) await writeFile(process.env.APEXA_PERF_OUTPUT, JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report, null, 2))
} finally { await browser.close() }
