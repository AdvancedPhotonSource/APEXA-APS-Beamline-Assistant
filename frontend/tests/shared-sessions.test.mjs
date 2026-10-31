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
const { hydrateSession, restoredWorkspace, durableWorkspace } = await import('../src/lib/sessionWorkspace.ts')
const { useChatStore } = await import('../src/stores/chatStore.ts')
const { useVizStore } = await import('../src/stores/vizStore.ts')
const { useImageStore } = await import('../src/stores/imageStore.ts')
const { useConnectionStore } = await import('../src/stores/connectionStore.ts')
const tick = () => new Promise(resolve => setTimeout(resolve, 10))
async function until(predicate) { for (let i = 0; i < 100; i++) { if (predicate()) return; await tick() } assert.fail('state did not settle') }
const artifact = { id: 'report', type: 'html-report', title: 'QC', data: { path: '/data/report.html' }, sourceMessageId: 'm' }
const message = { id: 'm', role: 'assistant', content: 'Ready', timestamp: 1, artifacts: [artifact] }
const workspace = { artifacts: [artifact], openIds: ['report'], activeId: 'report', pinned: ['report'], compareIds: [], draft: 'Review strain', attachments: [{ name: 'sample.tif', path: '/data/sample.tif', isImage: true }] }
function record(id, extra = {}) { return { id, title: id, messages: [message], origin: 'cli', createdAt: 1, updatedAt: 2, workspace: structuredClone(workspace), workspaceRevision: 0, ...extra } }
function setup(records, cache = []) {
  const disk = new Map(records.map(r => [r.id, structuredClone(r)]))
  const saved = new Map([['apexa.chat.sessions', JSON.stringify(cache)]])
  const calls = []
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) }
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  useVizStore.getState().clear()
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, ...options })
    const payload = options.body ? JSON.parse(options.body) : null
    let data
    if (url === '/api/sessions' && options.method === 'GET') data = { sessions: [...disk.values()].map(r => ({ ...r, messages: [] })), last_active: records[0]?.id }
    else if (url === '/api/sessions/import') { data = record(payload.id, payload); disk.set(data.id, data) }
    else if (url === '/api/sessions' && options.method === 'POST') { data = record(payload.id, { messages: [], workspace: {} }); disk.set(data.id, data) }
    else {
      const id = url.split('/')[3]
      data = disk.get(id)
      if (!data) return { ok: false, status: 404, json: async () => ({ detail: 'Missing saved session' }) }
      if (options.method === 'DELETE') { disk.delete(id); data = { archived: true } }
      else if (options.method === 'PUT') {
        if (payload.expected_revision !== data.workspaceRevision) return { ok: false, status: 409, json: async () => ({ detail: 'Workspace changed in another window' }) }
        data.workspace = payload.workspace; data.workspaceRevision += 1
      }
    }
    return { ok: true, json: async () => structuredClone(data) }
  }
  return { disk, calls, saved }
}

test('recorded plots keep IDs across reloads and changed data creates another view', () => {
  const source = record('stable', { messages: [{ ...message, artifacts: [], toolEvents: [{ tool: 'run_midas_viewer', result: JSON.stringify({ plotly: { data: [{ x: [1], y: [2] }] } }) }] }] })
  const first = hydrateSession(source).messages[0].artifacts[0]
  assert.equal(first.id, hydrateSession(source).messages[0].artifacts[0].id)
  const changed = structuredClone(source)
  changed.messages[0].toolEvents[0].result = JSON.stringify({ plotly: { data: [{ x: [1], y: [3] }] } })
  assert.notEqual(first.id, hydrateSession(changed).messages[0].artifacts[0].id)
})

test('closed and removed artifacts stay closed while new CLI results appear', () => {
  const newer = { ...artifact, id: 'new', data: { path: '/data/new.html' } }
  const restored = restoredWorkspace(record('removed', { messages: [{ ...message, artifacts: [artifact, newer] }], workspace: { ...workspace, artifacts: [], openIds: [], recordedArtifactIds: ['report'] } }))
  assert.deepEqual(restored.artifacts.map(a => a.id), ['new'])
  assert.deepEqual(restored.openIds, ['new'])
  const closed = restoredWorkspace(record('closed', { workspace: { ...workspace, openIds: [], activeId: null } }))
  assert.deepEqual(closed.openIds, [])
  assert.deepEqual(closed.pinned, ['report'])
})

test('image cache IDs are disposable while file paths and display settings persist', () => {
  const image = { ...artifact, type: 'diffraction', data: { fileId: 'expired', preview: 'large', path: '/data/a.tif', settings: { gamma: 2 } } }
  const result = durableWorkspace({ ...workspace, artifacts: [image] }).artifacts[0]
  assert.deepEqual(result.data, { path: '/data/a.tif', settings: { gamma: 2 } })
})

test('first launch opens the last CLI session with saved canvas draft attachments and model', async () => {
  const { disk, calls } = setup([record('cli-default', { model: 'saved-model' })])
  const dispose = useChatStore.getState().init()
  try {
    await until(() => useChatStore.getState().currentSessionId === 'cli-default' && useChatStore.getState().sessionSync === 'ready')
    const state = useChatStore.getState()
    assert.equal(state.messages[0].content, 'Ready')
    assert.equal(state.draft, 'Review strain')
    assert.equal(state.attachments[0].path, '/data/sample.tif')
    assert.equal(useConnectionStore.getState().selectedModel, 'saved-model')
    assert.equal(useVizStore.getState().activeId, 'report')
    assert.deepEqual(useVizStore.getState().pinned, ['report'])
    state.setDraft('Continue reconstruction')
    await state.saveWorkspace()
    assert.equal(disk.get('cli-default').workspace.draft, 'Continue reconstruction')
    assert.ok(calls.some(c => c.method === 'PUT'))
  } finally { dispose(); await tick() }
})

test('workspace conflicts retain the local draft until explicitly reloading saved state', async () => {
  const { disk } = setup([record('conflict')])
  const dispose = useChatStore.getState().init()
  try {
    await until(() => useChatStore.getState().currentSessionId === 'conflict' && useChatStore.getState().sessionSync === 'ready')
    const state = useChatStore.getState()
    state.setDraft('Local edits')
    disk.get('conflict').workspaceRevision = 3
    disk.get('conflict').workspace.draft = 'Other window'
    await state.saveWorkspace()
    assert.equal(useChatStore.getState().sessionSync, 'error')
    assert.equal(useChatStore.getState().draft, 'Local edits')
    await state.refreshSessions()
    assert.equal(useChatStore.getState().sessionSync, 'error')
    assert.equal(useChatStore.getState().draft, 'Local edits')
    await state.reloadSession('conflict', true, true)
    assert.equal(useChatStore.getState().draft, 'Other window')
    state.setDraft('Reconciled')
    await state.saveWorkspace()
    assert.equal(disk.get('conflict').workspace.draft, 'Reconciled')
  } finally { dispose(); await tick() }
})

test('slow session reads cannot replace the canvas after switching sessions', async () => {
  setup([record('slow'), record('fast', { workspace: { ...workspace, draft: 'Second session' } })])
  const dispose = useChatStore.getState().init()
  try {
    await until(() => useChatStore.getState().currentSessionId === 'slow' && useChatStore.getState().sessionSync === 'ready')
    const fetch = globalThis.fetch
    let resolve
    globalThis.fetch = async (url, options) => url === '/api/sessions/slow' ? new Promise(r => { resolve = r }) : fetch(url, options)
    const pending = useChatStore.getState().reloadSession('slow')
    useChatStore.getState().switchSession('fast')
    await until(() => useChatStore.getState().draft === 'Second session')
    resolve({ ok: true, json: async () => record('slow') })
    await pending
    assert.equal(useChatStore.getState().currentSessionId, 'fast')
    assert.equal(useChatStore.getState().draft, 'Second session')
    await useChatStore.getState().saveWorkspace()
  } finally { dispose(); await tick() }
})

test('archiving the active session selects the next session and restores its canvas', async () => {
  const { disk } = setup([record('archive-me'), record('keep-me', { workspace: { ...workspace, draft: 'Kept' } })])
  const dispose = useChatStore.getState().init()
  try {
    await until(() => useChatStore.getState().currentSessionId === 'archive-me' && useChatStore.getState().sessionSync === 'ready')
    useChatStore.getState().deleteSession('archive-me')
    await until(() => useChatStore.getState().currentSessionId === 'keep-me' && useChatStore.getState().draft === 'Kept')
    assert.equal(disk.has('archive-me'), false)
  } finally { dispose(); await tick() }
})

test('failed initial history read remains visibly failed', async () => {
  setup([record('broken')])
  const fetch = globalThis.fetch
  globalThis.fetch = async (url, options) => url === '/api/sessions/broken'
    ? { ok: false, status: 422, json: async () => ({ detail: 'Unreadable session file' }) } : fetch(url, options)
  const dispose = useChatStore.getState().init()
  try {
    await until(() => useChatStore.getState().sessionSync === 'error')
    assert.match(useChatStore.getState().sessionError, /Unreadable session/)
  } finally { dispose(); await tick() }
})

test('detector restoration keeps artifact identity and ignores results after a session switch', async () => {
  useImageStore.getState().resetSession()
  const image = { ...artifact, id: 'saved-image', type: 'diffraction', data: { path: '/data/a.tif' } }
  useVizStore.getState().clear()
  useVizStore.getState().addArtifact(image)
  let finish
  const response = { success: true, file_id: 'fresh-id', filename: 'a.tif', stats: { min: 0, max: 10, shape: [4, 4] }, preview: 'pixels' }
  globalThis.fetch = async () => new Promise(resolve => { finish = () => resolve({ ok: true, json: async () => response }) })
  const pending = useImageStore.getState().restoreImage(image, () => true)
  useImageStore.getState().resetSession()
  finish(); await pending
  assert.equal(useImageStore.getState().loadedImages.size, 0)
  globalThis.fetch = async () => ({ ok: true, json: async () => response })
  await useImageStore.getState().restoreImage(image, () => true)
  assert.equal(useVizStore.getState().artifacts.length, 1)
  assert.equal(useVizStore.getState().artifacts[0].id, 'saved-image')
  assert.equal(useVizStore.getState().artifacts[0].data.fileId, 'fresh-id')
})

test('cached unsaved drafts survive reload and save against their original revision', async () => {
  const server = record('cached-draft', { workspaceRevision: 2 })
  const cached = { ...structuredClone(server), serverBacked: true, workspaceDirty: true, workspace: { ...workspace, draft: 'Unsent work' } }
  const { disk } = setup([server], [cached])
  const dispose = useChatStore.getState().init()
  try {
    await until(() => disk.get('cached-draft').workspaceRevision === 3)
    assert.equal(useChatStore.getState().draft, 'Unsent work')
    assert.equal(disk.get('cached-draft').workspace.draft, 'Unsent work')
    assert.equal(useChatStore.getState().sessions[0].workspaceDirty, false)
  } finally { dispose(); await tick() }
})

test('edits made during a slow refresh survive the arriving server snapshot', async () => {
  setup([record('refresh-edit')])
  const dispose = useChatStore.getState().init()
  try {
    await until(() => useChatStore.getState().currentSessionId === 'refresh-edit' && useChatStore.getState().sessionSync === 'ready')
    const fetch = globalThis.fetch
    let finish
    globalThis.fetch = async (url, options) => url === '/api/sessions/refresh-edit' && options?.method === 'GET'
      ? new Promise(resolve => { finish = resolve }) : fetch(url, options)
    const reading = useChatStore.getState().reloadSession('refresh-edit')
    useChatStore.getState().setDraft('Edited during refresh')
    finish({ ok: true, json: async () => record('refresh-edit') })
    await reading
    assert.equal(useChatStore.getState().draft, 'Edited during refresh')
    assert.equal(useChatStore.getState().sessions[0].workspaceDirty, true)
    await useChatStore.getState().saveWorkspace()
  } finally { dispose(); await tick() }
})

test('a completed save cannot mark a newer unmaterialized edit as saved', async () => {
  const { disk, calls } = setup([record('inflight-edit')])
  const dispose = useChatStore.getState().init()
  try {
    await until(() => useChatStore.getState().currentSessionId === 'inflight-edit' && useChatStore.getState().sessionSync === 'ready')
    const fetch = globalThis.fetch
    let finish
    let delayFirstWrite = true
    globalThis.fetch = async (url, options) => {
      if (options?.method === 'PUT' && delayFirstWrite) {
        delayFirstWrite = false
        return new Promise(resolve => { finish = () => fetch(url, options).then(resolve) })
      }
      return fetch(url, options)
    }
    useChatStore.getState().setDraft('First snapshot')
    const writing = useChatStore.getState().saveWorkspace()
    await until(() => !!finish)
    useChatStore.getState().setDraft('Latest snapshot')
    await finish()
    await writing
    assert.equal(useChatStore.getState().sessions[0].workspaceDirty, true)
    await useChatStore.getState().saveWorkspace()
    assert.equal(disk.get('inflight-edit').workspace.draft, 'Latest snapshot')
    assert.equal(disk.get('inflight-edit').workspaceRevision, 2)
    assert.equal(calls.filter(call => call.method === 'PUT').length, 2)
    assert.equal(useChatStore.getState().sessions[0].workspaceDirty, false)
  } finally { dispose(); await tick() }
})
