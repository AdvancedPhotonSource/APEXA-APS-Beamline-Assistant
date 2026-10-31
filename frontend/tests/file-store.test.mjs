import { test } from 'node:test'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
registerHooks({ resolve(specifier, context, next) {
  const candidate = specifier.startsWith('@/') ? new URL(`../src/${specifier.slice(2)}.ts`, import.meta.url)
    : specifier.startsWith('.') && !/\.[a-z]+$/.test(specifier) && context.parentURL ? new URL(`${specifier}.ts`, context.parentURL) : null
  return next(candidate && existsSync(fileURLToPath(candidate)) ? candidate.href : specifier, context)
} })
const { useFileStore } = await import('../src/stores/fileStore.ts')
const result = (path, offset = 0) => ({ path, parent: '/', offset, limit: 100, total: 201,
  has_more: offset < 200, next_offset: offset < 200 ? offset + 100 : null,
  entries: [{ path: `${path}/file-${offset}`, name: `file-${offset}`, is_dir: false, size: 1, modified: 1 }] })
const response = data => ({ ok: true, json: async () => data })

test('file pages send bounded offsets and replace visible rows', async () => {
  const calls = []
  globalThis.fetch = async url => { const query = new URL(url, 'http://localhost').searchParams; calls.push(query); return response(result(query.get('path'), Number(query.get('offset')))) }
  const store = useFileStore.getState()
  store.restorePath('/data')
  await store.browse()
  await store.page(100)
  assert.equal(calls[1].get('limit'), '100')
  assert.equal(calls[1].get('offset'), '100')
  assert.deepEqual(useFileStore.getState().entries.map(e => e.name), ['file-100'])
  assert.equal(useFileStore.getState().nextOffset, 200)
  await store.page(200)
  assert.equal(useFileStore.getState().nextOffset, null)
})

test('switching folders aborts previous page and ignores its late success', async () => {
  const requests = []
  globalThis.fetch = (url, options) => new Promise(resolve => requests.push({ url, ...options, resolve }))
  const store = useFileStore.getState()
  store.restorePath('/old')
  const old = store.page(100)
  const newer = store.browse('/new')
  assert.equal(requests[0].signal.aborted, true)
  requests[1].resolve(response(result('/new')))
  await newer
  requests[0].resolve(response(result('/old', 100)))
  await old
  assert.equal(useFileStore.getState().currentPath, '/new')
  assert.equal(useFileStore.getState().entries[0].path, '/new/file-0')
})

test('cancel and session restoration ignore stale failures without clearing the next session', async () => {
  let reject
  let signal
  globalThis.fetch = (_url, options) => { signal = options.signal; return new Promise((_r, failure) => { reject = failure }) }
  const store = useFileStore.getState()
  const pending = store.browse('/old')
  store.cancel()
  assert.equal(signal.aborted, true)
  store.restorePath('/other-session')
  reject(new Error('Late failure'))
  await pending
  assert.equal(useFileStore.getState().currentPath, '/other-session')
  assert.equal(useFileStore.getState().error, null)
  assert.equal(useFileStore.getState().isLoading, false)
})
