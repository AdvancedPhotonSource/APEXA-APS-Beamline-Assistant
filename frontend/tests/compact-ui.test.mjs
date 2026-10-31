import { test } from 'node:test'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { workflowFixture } from './workflow-fixture.mjs'

// Component markup checks; these do not claim browser layout/interaction testing.
registerHooks({
  resolve(specifier, context, next) {
    const base = specifier.startsWith('@/') ? new URL(`../src/${specifier.slice(2)}`, import.meta.url)
      : specifier.startsWith('.') && !/\.[a-z]+$/.test(specifier) && context.parentURL ? new URL(specifier, context.parentURL) : null
    if (base) {
      for (const suffix of ['.ts', '.tsx']) {
        const candidate = new URL(`${base.href}${suffix}`)
        if (existsSync(fileURLToPath(candidate))) return next(candidate.href, context)
      }
    }
    return next(specifier, context)
  },
  load(url, context, next) {
    if (url.endsWith('.tsx')) return { format: 'module', shortCircuit: true,
      source: ts.transpileModule(readFileSync(new URL(url), 'utf8'), {
        compilerOptions: { jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext },
      }).outputText }
    return next(url, context)
  },
})
globalThis.localStorage = { getItem: () => null, setItem: () => {} }
const { Header } = await import('../src/components/layout/Header.tsx')
const { ChatInput } = await import('../src/components/chat/ChatInput.tsx')
const { IconRail } = await import('../src/components/layout/IconRail.tsx')
const { ResultsPanel } = await import('../src/components/viz/ResultsPanel.tsx')
const { HtmlReportView } = await import('../src/components/viz/HtmlReportView.tsx')
const { useVizStore } = await import('../src/stores/vizStore.ts')
const { useChatStore } = await import('../src/stores/chatStore.ts')
const { MessageList } = await import('../src/components/chat/MessageList.tsx')
const { MessageBubble } = await import('../src/components/chat/MessageBubble.tsx')
const { WorkflowRunCard } = await import('../src/components/workflows/WorkflowRunCard.tsx')

test('workflow card separates science from execution, respects allowed gates and links full evidence', () => {
  const run = workflowFixture({ orchestration_state: 'blocked', job_execution: 'completed', scientific_verdict: 'rejected',
    gate: { gate_id: 'reject-only', kind: 'result_review', step_id: 'calibrate', decisions: ['reject'], recovery: false, prompt: 'Review failed QC' },
    stages: [{ id: 'calibrate', tool: 'midas_auto_calibrate', state: 'completed', run_id: 'job/1',
      outcome: { execution: 'completed', scientific_verdict: 'rejected' }, cancellation_status: null }] })
  const markup = renderToStaticMarkup(createElement(WorkflowRunCard, { run, busy: false, uncertain: false,
    onDecide() {}, onCancel() {}, onRetry() {} }))
  assert.match(markup, /Workflow: blocked/)
  assert.match(markup, /Execution: completed/)
  assert.match(markup, /Science: rejected/)
  assert.match(markup, /Stop workflow/)
  assert.doesNotMatch(markup, /Approve &amp; continue/)
  assert.match(markup, /href="\/api\/runs\/job%2F1"/)
  assert.match(markup, /href="\/api\/workflows\/workflow-fixture"/)
  assert.match(markup, /midas_integrate_series/)
  assert.match(markup, /parameter_file uses calibrate/)
})

test('workflow cancellation pending is not presented as a confirmed external job stop', () => {
  const run = workflowFixture({ orchestration_state: 'cancel_requested', gate: null, job_execution: 'running' })
  const markup = renderToStaticMarkup(createElement(WorkflowRunCard, { run, busy: false, uncertain: false,
    onDecide() {}, onCancel() {}, onRetry() {} }))
  assert.match(markup, /Workflow: cancel requested/)
  assert.match(markup, /Execution: running/)
  assert.match(markup, /External jobs may still be active/)
  assert.doesNotMatch(markup, /Request cancellation/)
})

test('long conversation renders the newest bounded page and exposes navigation', () => {
  const initial = useChatStore.getInitialState()
  const previous = { ...initial }
  Object.assign(initial, { messages: Array.from({ length: 500 }, (_, i) => ({ id: `bounded-${i}`, role: 'user', content: `Message ${i}`, timestamp: i })), isLoading: false })
  try {
    const markup = renderToStaticMarkup(createElement(MessageList))
    assert.equal((markup.match(/data-message-id=/g) || []).length, 60)
    assert.match(markup, /data-message-id="bounded-499"/)
    assert.doesNotMatch(markup, /data-message-id="bounded-0"/)
    assert.match(markup, /aria-label="Conversation pages"/)
  } finally { Object.assign(initial, previous) }
})

test('one huge answer bounds initial text and tool rows while keeping access to the rest', () => {
  const message = { id: 'huge', role: 'assistant', content: 'x'.repeat(50000), timestamp: 1,
    toolResults: Array.from({ length: 1000 }, (_, i) => ({ tool: `fixture_${i}`, status: 'completed', data: {} })) }
  const markup = renderToStaticMarkup(createElement(MessageBubble, { message }))
  assert.match(markup, /Read full response/)
  assert.match(markup, /Next results/)
  assert.doesNotMatch(markup, /fixture_999/)
  assert.ok(markup.length < 60000)
})

test('header contains the layout control in one strip and one compact theme control', () => {
  const markup = renderToStaticMarkup(createElement(Header, null, createElement('div', { 'aria-label': 'Workspace layout' }, 'Chat Split Canvas')))
  assert.match(markup, /<header class="apexa-topbar">.*aria-label="Workspace layout"/)
  assert.equal((markup.match(/aria-label="Theme:/g) || []).length, 1)
  assert.doesNotMatch(markup, /apexa-header-tagline/)
})

test('compact prompt uses two rows and one border with controls in the upper strip', () => {
  const markup = renderToStaticMarkup(createElement(ChatInput, { expanded: false, onExpandedChange: () => {} }))
  const toolbar = markup.slice(markup.indexOf('apexa-prompt-toolbar'), markup.indexOf('<textarea'))
  for (const label of ['Attach files', 'Expand prompt', 'Send message']) assert.ok(toolbar.includes(`aria-label="${label}"`))
  assert.equal((markup.match(/aria-label="Send message"/g) || []).length, 1)
  assert.match(markup, /rows="2"/)
  assert.match(markup, /box-shadow:none/)
  assert.doesNotMatch(markup, /font-mono/)
})

test('left navigation orders data, analysis, results and chats with motors separate', () => {
  const markup = renderToStaticMarkup(createElement(IconRail, { activeView: 'viewers', onSelect: () => {} }))
  const labels = [...markup.matchAll(/<button[^>]+aria-label="([^"]+)"/g)].map(match => match[1])
  assert.deepEqual(labels, ['Data', 'Analyze', 'Results', 'Chats', 'Motors'])
  assert.match(markup, /aria-label="Results" aria-expanded="true"/)
})

test('Results exposes report opening and reopening of a closed session artifact', () => {
  const artifact = { id: 'report', type: 'html-report', title: 'Reconstruction QC', data: { path: '/data/report.html' }, sourceMessageId: '' }
  useVizStore.getState().clear()
  useVizStore.getState().addArtifact(artifact)
  useVizStore.getState().closeTab(artifact.id)
  const initial = useVizStore.getInitialState()
  const previous = { ...initial }
  Object.assign(initial, useVizStore.getState())
  try {
    const markup = renderToStaticMarkup(createElement(ResultsPanel))
    assert.match(markup, /Open an HTML report in canvas/)
    assert.match(markup, /Reports only/)
    assert.match(markup, /Reconstruction QC/)
    assert.match(markup, /Closed — click to reopen/)
    assert.match(markup, /Scan folder/)
  } finally { Object.assign(initial, previous); useVizStore.getState().clear() }
})

test('report view gives loading feedback and retains refresh and save controls', () => {
  const markup = renderToStaticMarkup(createElement(HtmlReportView, { path: '/data/report.html' }))
  assert.match(markup, /Loading report/)
  assert.match(markup, /Refresh/)
  assert.match(markup, /Save preview/)
})
