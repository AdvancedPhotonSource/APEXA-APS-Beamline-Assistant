import { test } from 'node:test'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// Resolve the application's Vite aliases while retaining Node's type stripping.
registerHooks({ resolve(specifier, context, next) {
  const candidate = specifier.startsWith('@/') ? new URL(`../src/${specifier.slice(2)}.ts`, import.meta.url)
    : specifier.startsWith('.') && !/\.[a-z]+$/.test(specifier) && context.parentURL ? new URL(`${specifier}.ts`, context.parentURL) : null
  return next(candidate && existsSync(fileURLToPath(candidate)) ? candidate.href : specifier, context)
} })
const { useChatStore } = await import('../src/stores/chatStore.ts')
const { useVizStore } = await import('../src/stores/vizStore.ts')
const { wsManager } = await import('../src/api/websocket.ts')
const { useImageStore } = await import('../src/stores/imageStore.ts')

test('rapid viewer and draft edits defer payload serialization until a single flush', async () => {
  const saved = new Map()
  let cacheWrites = 0
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null,
    setItem: (k, v) => { saved.set(k, v); if (k === 'apexa.chat.sessions') cacheWrites += 1 },
    removeItem: k => saved.delete(k) }
  const dispose = useChatStore.getState().init({ sync: false })
  let serializations = 0
  try {
    cacheWrites = 0
    useVizStore.getState().addArtifact({ id: 'large-plot', type: 'plotly', title: 'Detector plot',
      data: { toJSON() { serializations += 1; return { data: [{ x: [1, 2], y: [3, 4] }] } } }, sourceMessageId: '' })
    for (let i = 0; i < 100; i++) {
      useChatStore.getState().setDraft(`Edit ${i}`)
      useImageStore.getState().updateSettings({ gamma: 1 + i / 100 })
    }
    assert.equal(serializations, 0, 'viewer interaction must not traverse/serialize plot payloads')
    assert.equal(cacheWrites, 0)
    await useChatStore.getState().saveWorkspace()
    assert.equal(serializations, 1)
    assert.equal(cacheWrites, 1)
    const current = JSON.parse(saved.get('apexa.chat.sessions'))[0]
    assert.equal(current.workspace.draft, 'Edit 99')
    assert.deepEqual(current.workspace.artifacts[0].data.data[0].y, [3, 4])
  } finally { dispose(); useVizStore.getState().clear() }
})

test('autosave materializes the latest workspace at the debounce boundary', context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const saved = new Map()
  let writes = 0
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null,
    setItem: (k, v) => { saved.set(k, v); if (k === 'apexa.chat.sessions') writes += 1 }, removeItem: k => saved.delete(k) }
  const dispose = useChatStore.getState().init({ sync: false })
  try {
    writes = 0
    useChatStore.getState().setDraft('First edit')
    context.mock.timers.tick(400)
    useChatStore.getState().setDraft('Latest edit')
    context.mock.timers.tick(599)
    assert.equal(writes, 0)
    context.mock.timers.tick(1)
    assert.equal(writes, 1)
    assert.equal(JSON.parse(saved.get('apexa.chat.sessions'))[0].workspace.draft, 'Latest edit')
  } finally { dispose(); context.mock.timers.reset() }
})

test('session switching before debounce retains each session draft and canvas', () => {
  const saved = new Map()
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) }
  const dispose = useChatStore.getState().init({ sync: false })
  try {
    const first = useChatStore.getState().currentSessionId
    useChatStore.getState().setDraft('First session draft')
    useVizStore.getState().addArtifact({ id: 'first-plot', type: 'plotly', title: 'First', data: { data: [] }, sourceMessageId: '' })
    useChatStore.getState().newChat()
    const second = useChatStore.getState().currentSessionId
    useChatStore.getState().setDraft('Second session draft')
    useVizStore.getState().addArtifact({ id: 'second-plot', type: 'plotly', title: 'Second', data: { data: [] }, sourceMessageId: '' })
    useChatStore.getState().switchSession(first)
    assert.equal(useChatStore.getState().draft, 'First session draft')
    assert.deepEqual(useVizStore.getState().artifacts.map(a => a.id), ['first-plot'])
    useChatStore.getState().switchSession(second)
    assert.equal(useChatStore.getState().draft, 'Second session draft')
    assert.deepEqual(useVizStore.getState().artifacts.map(a => a.id), ['second-plot'])
  } finally { dispose(); useVizStore.getState().clear() }
})

test('pagehide preserves edits even when the debounce has not fired', () => {
  const saved = new Map()
  const listeners = new Map()
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) }
  globalThis.window = { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  const dispose = useChatStore.getState().init({ sync: false })
  try {
    useChatStore.getState().setDraft('Recover me after navigation')
    listeners.get('pagehide')()
    assert.equal(JSON.parse(saved.get('apexa.chat.sessions'))[0].workspace.draft, 'Recover me after navigation')
  } finally { dispose() }
  assert.equal(listeners.has('pagehide'), false)
})

test('startup restores recorded canvas artifacts from the saved conversation', () => {
  const artifact = { id: 'saved-structure', type: 'structure', title: 'Ni', data: { path: '/fixture/Ni.cif' }, sourceMessageId: 'saved-message' }
  const message = { id: 'saved-message', role: 'assistant', content: 'Structure ready', timestamp: 1, artifacts: [artifact] }
  const session = { id: 'saved-session', title: 'Saved analysis', messages: [message], createdAt: 1, updatedAt: 1 }
  const saved = new Map([['apexa.chat.sessions', JSON.stringify([session])], ['apexa.chat.current', session.id]])
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) }
  useVizStore.getState().clear()
  const dispose = useChatStore.getState().init({ sync: false })
  try {
    assert.equal(useChatStore.getState().currentSessionId, session.id)
    // Legacy saved file IDs converge with Data/tool opens of the same resource.
    assert.equal(useVizStore.getState().activeId, 'canvas-structure-local-/fixture/Ni.cif')
    assert.equal(useVizStore.getState().artifacts[0].data.path, '/fixture/Ni.cif')
  } finally { dispose(); useVizStore.getState().clear() }
})

test('long-running tool results stay in the originating chat after switching', () => {
  const saved = new Map()
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) }
  globalThis.window = { location: { protocol: 'http:', host: 'fixture' } }
  let socket
  globalThis.WebSocket = class {
    static OPEN = 1; static CONNECTING = 0
    readyState = 1
    constructor() { socket = this }
    send() {}
    close() { this.readyState = 3 }
  }
  const store = useChatStore.getState()
  const dispose = store.init({ sync: false })
  wsManager.connect()
  try {
    const origin = useChatStore.getState().currentSessionId
    store.sendMessage('Show the structure')
    store.newChat()
    const event = data => socket.onmessage({ data: JSON.stringify({ thread_id: origin, ...data }) })
    event({ type: 'tool_result', tool: 'open_science_view', result: JSON.stringify({ status: 'success',
      canvas: { schema_version: 1, type: 'structure', title: 'Ni', data: { path: '/fixture/Ni.cif' } } }) })
    event({ type: 'chat_response', message: 'Structure ready' })
    assert.equal(useChatStore.getState().messages.length, 0)
    assert.equal(useVizStore.getState().artifacts.length, 0)
    assert.equal(useChatStore.getState().sessions.find(s => s.id === origin).messages.at(-1).content, 'Structure ready')
    store.switchSession(origin)
    assert.equal(useChatStore.getState().pendingCount, 0)
    assert.equal(useVizStore.getState().artifacts.at(-1).type, 'structure')
  } finally { dispose(); wsManager.disconnect() }
})

test('explicit report opening reveals the canvas and restores from recorded tool results', () => {
  const saved = new Map()
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) }
  globalThis.window = { location: { protocol: 'http:', host: 'fixture' } }
  let socket
  globalThis.WebSocket = class {
    static OPEN = 1; static CONNECTING = 0
    readyState = 1
    constructor() { socket = this }
    send() {}
    close() { this.readyState = 3 }
  }
  useVizStore.getState().clear()
  const dispose = useChatStore.getState().init({ sync: false })
  wsManager.connect()
  try {
    const thread_id = useChatStore.getState().currentSessionId
    const revision = useVizStore.getState().canvasRevision
    useChatStore.getState().sendMessage('Open the QC report')
    socket.onmessage({ data: JSON.stringify({ thread_id, type: 'tool_result', tool: 'open_science_view', result: JSON.stringify({
      status: 'success', canvas: { schema_version: 1, type: 'html-report', title: 'QC', data: { path: '/fixture/report.html', location: 'local' } },
    }) }) })
    assert.ok(useVizStore.getState().canvasRevision > revision)
    socket.onmessage({ data: JSON.stringify({ thread_id, type: 'chat_response', message: 'Report ready' }) })
    useChatStore.getState().newChat()
    useChatStore.getState().switchSession(thread_id)
    assert.equal(useVizStore.getState().artifacts.filter(a => a.type === 'html-report').length, 1)
    assert.equal(useVizStore.getState().artifacts[0].data.path, '/fixture/report.html')
  } finally { dispose(); wsManager.disconnect(); useVizStore.getState().clear() }
})
