import { test } from 'node:test'
import assert from 'node:assert/strict'
import { boundedPreview } from '../src/lib/boundedPreview.ts'

test('inspecting a million-element scientific array visits a bounded preview without altering values', () => {
  let reads = 0
  const data = new Array(1000000)
  for (let i = 0; i < 100; i++) Object.defineProperty(data, i, { get: () => { reads++; return i * 2 } })
  const preview = boundedPreview({ data })
  assert.ok(reads <= 30)
  assert.match(preview, /999970 more items/)
  assert.equal(data.length, 1000000)
  assert.ok(preview.length < 2000)
})

test('cyclic, deeply nested and long string results remain bounded and readable', () => {
  const data = { log: 'x'.repeat(1000000), value: 3 }
  data.self = data
  const result = boundedPreview(data)
  assert.match(result, /Repeated reference/)
  assert.match(result, /"value": 3/)
  assert.ok(result.length < 2000)
})
