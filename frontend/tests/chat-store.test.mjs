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
    assert.equal(useVizStore.getState().activeId, artifact.id)
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
