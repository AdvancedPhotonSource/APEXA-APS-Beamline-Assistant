import { test } from 'node:test'
import assert from 'node:assert/strict'
import { boundSessionMemory } from '../src/lib/sessionMemory.ts'

test('old clean sessions unload while active, local, unsaved and in-flight data remain intact', () => {
  const sessions = Array.from({ length: 10 }, (_, i) => ({ id: String(i), updatedAt: i, createdAt: i,
    title: `Session ${i}`, serverBacked: true, messages: [{ content: 'retained server transcript' }],
    workspace: { artifacts: [{ data: new Array(1000).fill(i) }] } }))
  sessions[0].serverBacked = false
  sessions[1].workspaceDirty = true
  sessions[2].status = 'running'
  const bounded = boundSessionMemory(sessions, '3', new Set(['4']))
  for (const i of [0, 1, 2, 3, 4, 8, 9]) assert.equal(bounded[i], sessions[i])
  for (const i of [5, 6, 7]) {
    assert.deepEqual(bounded[i].messages, [])
    assert.equal(bounded[i].workspace, undefined)
    assert.equal(bounded[i].messageCount, 1)
    assert.equal(bounded[i].title, sessions[i].title)
  }
  assert.equal(sessions[5].workspace.artifacts[0].data.length, 1000)
})
