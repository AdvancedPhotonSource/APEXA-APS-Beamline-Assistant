import { test } from 'node:test'
import assert from 'node:assert/strict'
import { workspaceLayout, parseWorkspaceView } from '../src/lib/workspaceLayout.ts'
import { WORKFLOW_OPTIONS, workflowPrompt, suggestedWorkflows } from '../src/lib/workflowCatalog.ts'

test('first launch exposes both chat and canvas on a laptop workspace', () => {
  assert.deepEqual(workspaceLayout(null, 1200), { canSplit: true, view: 'split' })
})

test('full-width choices are honored and narrow screens keep chat full height', () => {
  assert.equal(workspaceLayout('chat', 1200).view, 'chat')
  assert.equal(workspaceLayout('canvas', 1200).view, 'canvas')
  assert.equal(workspaceLayout(null, 500).view, 'chat')
  assert.equal(workspaceLayout('split', 500).view, 'chat')
  assert.equal(workspaceLayout('canvas', 500).view, 'canvas')
  assert.equal(parseWorkspaceView('invalid'), null)
  assert.equal(parseWorkspaceView('chat'), 'chat')
})

test('analysis setup does not inject a calibrant or beam energy into real data', () => {
  assert.equal(new Set(WORKFLOW_OPTIONS.map(o => o.id)).size, WORKFLOW_OPTIONS.length)
  for (const option of WORKFLOW_OPTIONS) {
    const prompt = workflowPrompt(option)
    assert.doesNotMatch(prompt, /61\.332|default parameters|auto-detect.*filename/i)
    assert.match(prompt, /filenames only as discovery hints/)
    if (!option.inspectOnly) assert.match(prompt, /Do not start processing/)
  }
  for (const id of ['ff', 'nf', 'pf', 'refine', 'pdf', 'stress', 'live', 'readiness']) {
    assert.ok(WORKFLOW_OPTIONS.some(o => o.id === id))
  }
})

test('suggestions follow the selected artifact type without claiming a technique inference', () => {
  const ids = kind => suggestedWorkflows(kind).map(o => o.id)
  assert.ok(ids('structure').includes('refine'))
  assert.ok(ids('sequence').includes('calibrate'))
  assert.ok(ids('plotly').includes('pdf'))
  assert.equal(ids(undefined)[0], 'inspect')
})
