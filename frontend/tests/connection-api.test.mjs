import { test } from 'node:test'
import assert from 'node:assert/strict'
import { getConnection, saveConnection } from '../src/api/connection.ts'

const config = { preset: 'argo', base_url: 'https://argo.example/v1', model: 'fixture-model', username: 'operator' }
const settings = { active: config, saved: null, environment_override: false, restart_required: false, error: null,
  presets: [{ id: 'argo', base_url: config.base_url, wire: 'openai', model_suggestions: ['fixture-model'], model_choices: [] }] }

test('a running server without the new settings route requires a server restart, not a browser refresh', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => new Response('{"detail":"Not Found"}', { status: 404 }))
  await assert.rejects(getConnection(), error => /HTTP 404/.test(error.message)
    && /restart APEXA/.test(error.message) && /Refreshing the browser alone/.test(error.message))
  assert.equal(fetch.mock.callCount(), 1) // No probing or automatic request replay.
  fetch.mock.mockImplementation(async () => new Response('', { status: 405 }))
  await assert.rejects(saveConnection(config, '', null), /HTTP 405/)
})

test('authorization and server failures preserve actionable status without displaying HTML', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => new Response('private proxy body', { status: 403 }))
  await assert.rejects(getConnection(), /Access.*denied.*HTTP 403/)
  fetch.mock.mockImplementation(async () => new Response('<html>private proxy body</html>', { status: 502 }))
  await assert.rejects(getConnection(), error => /HTTP 502/.test(error.message) && !/private proxy/.test(error.message))
  fetch.mock.mockImplementation(async () => new Response('{"detail":"Cannot read settings"}', { status: 503 }))
  await assert.rejects(getConnection(), /Cannot read settings.*HTTP 503/)
})

test('successful HTML and incomplete JSON are rejected before rendering a broken settings form', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => new Response('<html>Application shell</html>'))
  await assert.rejects(getConnection(), /unreadable.*HTTP 200/)
  for (const value of [null, {}, { ...settings, active: null }, { ...settings, presets: [] },
    { ...settings, presets: [null] }, { ...settings, saved: { revision: '1' } },
    { ...settings, presets: [{ ...settings.presets[0], model_choices: [{ id: 'fixture', label: null }] }] }]) {
    fetch.mock.mockImplementation(async () => Response.json(value))
    await assert.rejects(getConnection(), /incompatible connection settings/)
  }
  fetch.mock.mockImplementation(async () => Response.json(settings))
  assert.deepEqual(await getConnection(), settings)
})

test('network failures are explained and closing the dialog cancels its pending read', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => { throw new TypeError('offline') })
  await assert.rejects(getConnection(), /Could not reach the APEXA server/)
  const controller = new AbortController()
  let signal
  fetch.mock.mockImplementation(async (_url, options) => {
    signal = options.signal
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }))
  })
  const pending = getConnection(controller.signal)
  controller.abort()
  await assert.rejects(pending, error => error.name === 'AbortError')
  assert.equal(signal.aborted, true)
})
