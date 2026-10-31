// Test-only entry served by Vite; not included in the application build.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { MessageList } from '../src/components/chat/MessageList'
import { MessageBubble } from '../src/components/chat/MessageBubble'
import { ResultsPanel } from '../src/components/viz/ResultsPanel'
import { FileBrowser } from '../src/components/files/FileBrowser'
import { WorkflowPanel } from '../src/components/workflows/WorkflowPanel'
import { VizPanel } from '../src/components/viz/VizPanel'
import { VizLauncher } from '../src/components/viz/VizLauncher'
import { ToolCallBox } from '../src/components/cards/ToolCallBox'
import { AppShell } from '../src/components/layout/AppShell'
import { useChatStore } from '../src/stores/chatStore'
import { useVizStore } from '../src/stores/vizStore'
import { useFileStore } from '../src/stores/fileStore'
import { BrowserSessionCache } from '../src/lib/browserSessionCache'
import '../src/index.css'

let root = createRoot(document.getElementById('root'))
window.apexaHarness = {
  useChatStore, useVizStore, useFileStore, BrowserSessionCache,
  render(count, unbounded = false) {
    flushSync(() => root.unmount())
    root = createRoot(document.getElementById('root'))
    const messages = Array.from({ length: count }, (_, i) => ({ id: `synthetic-${i}`, role: 'assistant', timestamp: i,
      content: `### Reconstruction ${i}\nInspected **synthetic detector data**. This is a UI fixture, not a scientific result.\n\n- Grain count: 125\n- Residual: 0.025\n- [Recorded report](/fixture/report.html)\n\n| metric | value |\n| --- | --- |\n| completeness | 0.98 |\n\nReview the evidence before continuing.`,
      toolResults: Array.from({ length: 2 }, (_, j) => ({ tool: `fixture_${j}`, status: 'completed', data: { value: i } })),
    }))
    useChatStore.setState({ messages, currentSessionId: 'benchmark', isLoading: false })
    const start = performance.now()
    flushSync(() => root.render(unbounded
      ? <div>{messages.map(message => <MessageBubble key={message.id} message={message} />)}</div>
      : <MessageList />))
    return { commitMs: performance.now() - start, renderedMessages: document.querySelectorAll('[data-message-id]').length,
      domNodes: document.querySelectorAll('*').length }
  },
  renderResults() { flushSync(() => root.render(<ResultsPanel />)) },
  renderFiles() { flushSync(() => root.render(<FileBrowser />)) },
  renderCanvas() { flushSync(() => root.render(<VizPanel />)) },
  renderLauncher() { flushSync(() => root.render(<VizLauncher />)) },
  renderShell() { flushSync(() => root.render(<AppShell />)) },
  renderToolCard() { flushSync(() => root.render(<ToolCallBox result={{ tool: 'fixture_summary', status: 'completed', data: { mean: 1 } }} />)) },
  renderWorkflows(sessionId = 'session-a') {
    useChatStore.setState({ currentSessionId: sessionId, messages: [], _pendingToolResults: [] })
    flushSync(() => root.render(<WorkflowPanel />))
  },
}
