import { test } from 'node:test'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

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
