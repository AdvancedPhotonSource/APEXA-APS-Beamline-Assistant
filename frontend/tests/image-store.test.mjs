import { test, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

registerHooks({ resolve(specifier, context, next) {
  const candidate = specifier.startsWith('@/') ? new URL(`../src/${specifier.slice(2)}.ts`, import.meta.url)
    : specifier.startsWith('.') && !/\.[a-z]+$/.test(specifier) && context.parentURL ? new URL(`${specifier}.ts`, context.parentURL) : null
  return next(candidate && existsSync(fileURLToPath(candidate)) ? candidate.href : specifier, context)
} })
const { useImageStore } = await import('../src/stores/imageStore.ts')
const { useVizStore } = await import('../src/stores/vizStore.ts')
const originalFetch = globalThis.fetch
const response = data => ({ ok: true, json: async () => data })
const image = id => ({ success: true, file_id: id, filename: `${id}.tif`, preview: `preview-${id}`,
  stats: { min: 0, max: 100, mean: 50, std: 10, shape: [20, 30] } })
let requests

beforeEach(() => {
  useImageStore.getState().resetSession()
  useVizStore.getState().clear()
  requests = []
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }))
})
afterEach(() => { globalThis.fetch = originalFetch })

test('an old session image response cannot open a tab or clear a newer load', async () => {
  const first = useImageStore.getState().loadImage('/session-a/a.tif')
  useImageStore.getState().resetSession()
  const second = useImageStore.getState().loadImage('/session-b/b.tif')
  requests[0].resolve(response(image('a')))
  await first
  assert.equal(useImageStore.getState().isLoading, true)
  assert.equal(useImageStore.getState().loadedImages.size, 0)
  assert.equal(useVizStore.getState().artifacts.length, 0)
  requests[1].resolve(response(image('b')))
  await second
  assert.equal(useImageStore.getState().activeImageId, 'b')
  assert.equal(useImageStore.getState().isLoading, false)
  assert.deepEqual(useVizStore.getState().artifacts.map(item => item.data.path), ['/session-b/b.tif'])
})

test('a late image failure is ignored after a session switch', async () => {
  const pending = useImageStore.getState().loadImage('/session-a/a.tif')
  useImageStore.getState().resetSession()
  requests[0].reject(new Error('Old file is no longer available'))
  await assert.doesNotReject(pending)
  assert.equal(useImageStore.getState().isLoading, false)
  assert.equal(useVizStore.getState().artifacts.length, 0)
})

test('radial profile completion belongs to the session that requested it', async () => {
  useImageStore.setState({ activeImageId: 'a', loadedImages: new Map([['a', {
    fileId: 'a', filename: 'a.tif', path: '/session-a/a.tif', stats: image('a').stats, preview: '',
  }]]) })
  const stale = useImageStore.getState().computeRadialProfile()
  useImageStore.getState().resetSession()
  requests[0].resolve(response({ success: true, radii: [0, 1], intensity: [2, 3] }))
  await stale
  assert.equal(useVizStore.getState().artifacts.length, 0)

  useImageStore.setState({ activeImageId: 'b', loadedImages: new Map([['b', {
    fileId: 'b', filename: 'b.tif', path: '/session-b/b.tif', stats: image('b').stats, preview: '',
  }]]) })
  const current = useImageStore.getState().computeRadialProfile()
  requests[1].resolve(response({ success: true, radii: [0, 1], intensity: [5, 8] }))
  await current
  assert.equal(useVizStore.getState().artifacts.length, 1)
  assert.equal(useVizStore.getState().artifacts[0].title, 'Radial Profile - b.tif')
  assert.deepEqual(useVizStore.getState().artifacts[0].data.data[0].y, [5, 8])
})

test('restoring display settings ignores a late failure after session reset', async () => {
  const artifact = { id: 'saved-image', type: 'diffraction', title: 'Saved image', sourceMessageId: '',
    data: { path: '/session-a/a.tif', settings: { vmin: 1, vmax: 90, gamma: 1, colormap: 'viridis' } } }
  const restored = useImageStore.getState().restoreImage(artifact, () => true)
  requests[0].resolve(response(image('a')))
  for (let i = 0; requests.length < 2 && i < 20; i++) await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(requests[1].url, '/api/viewer/adjust')
  useImageStore.getState().resetSession()
  requests[1].reject(new Error('Old adjustment failed'))
  await assert.doesNotReject(restored)
  assert.equal(useImageStore.getState().loadedImages.size, 0)
  assert.equal(useVizStore.getState().artifacts.length, 0)
})
