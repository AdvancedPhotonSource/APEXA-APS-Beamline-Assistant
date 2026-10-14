import { test, beforeEach, afterEach, mock } from 'node:test'
import assert from 'node:assert/strict'
import { wsManager } from '../src/api/websocket.ts'

class Socket {
  static OPEN = 1; static CONNECTING = 0
  static instances = []
  readyState = 0
  constructor(url) { this.url = url; Socket.instances.push(this) }
  open() { this.readyState = 1; this.onopen?.() }
  close(code = 1006, reason = '') { this.readyState = 3; this.onclose?.({ code, reason, wasClean: code === 1000 }) }
  send() {}
}
beforeEach(() => {
  globalThis.WebSocket = Socket
  globalThis.window = { location: { protocol: 'https:', host: 'beamline.example' } }
  Socket.instances = []
  mock.timers.enable({ apis: ['setTimeout'] })
})
afterEach(() => { wsManager.disconnect(); mock.timers.reset() })

test('connect is idempotent while connecting and uses wss for HTTPS', () => {
  wsManager.connect(); wsManager.connect()
  assert.equal(Socket.instances.length, 1)
  assert.equal(Socket.instances[0].url, 'wss://beamline.example/ws')
})

test('disposing cancels queued reconnects', () => {
  wsManager.connect(); Socket.instances[0].close()
  wsManager.disconnect(); mock.timers.tick(60000)
  assert.equal(Socket.instances.length, 1)
})

test('stale socket callbacks cannot close or reconnect the new socket', () => {
  wsManager.connect(); const old = Socket.instances[0]
  wsManager.disconnect(); wsManager.connect(); const current = Socket.instances[1]
  current.open(); old.onerror?.(); old.onclose?.({ code: 1006 })
  mock.timers.tick(60000)
  assert.equal(current.readyState, 1)
  assert.equal(Socket.instances.length, 2)
})

test('brief connections retain backoff instead of retrying every two seconds', () => {
  wsManager.connect(); Socket.instances[0].open(); Socket.instances[0].close()
  mock.timers.tick(2000); Socket.instances[1].open(); Socket.instances[1].close()
  mock.timers.tick(2000); assert.equal(Socket.instances.length, 2)
  mock.timers.tick(2000); assert.equal(Socket.instances.length, 3)
})

test('connection loss emits diagnostics without a fabricated chat error', () => {
  const messages = []; const dispose = wsManager.onMessage(x => messages.push(x))
  wsManager.connect(); Socket.instances[0].open(); Socket.instances[0].close(1011, 'ping timeout')
  dispose()
  assert.equal(messages.at(-1).type, 'connection_status')
  assert.equal(messages.at(-1).close_code, 1011)
  assert.equal(messages.at(-1).close_reason, 'ping timeout')
})
