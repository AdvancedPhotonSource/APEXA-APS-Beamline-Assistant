import { create } from 'zustand'
import type { ChatMessage, ToolResult, VizArtifact, ChatSession } from '@/api/types'
import { wsManager } from '@/api/websocket'
import { sendChatHttp } from '@/api/endpoints'
import { parseToolResults, extractArtifacts, parseDirectToolResult } from '@/lib/parseToolResult'
import { useVizStore } from './vizStore'
import { useConfirmStore, nextConfirmId } from './confirmStore'

interface ChatState {
  messages: ChatMessage[]
  isLoading: boolean
  // Number of turns sent but not yet answered. The backend /ws loop serializes
  // turns (reads one message at a time), so a user can queue several while one
  // runs — isLoading stays true until the LAST queued turn is answered.
  pendingCount: number
  progress: { step: string; percent: number } | null
  // Live "what is it doing right now" line (e.g. the tool currently running), so a
  // long turn shows activity instead of a static "Thinking". Set on tool_start,
  // cleared when that tool returns or the turn ends.
  activity: string | null
  _pendingToolResults: ToolResult[]

  // Session history (ChatGPT/Claude-style), persisted to localStorage.
  sessions: ChatSession[]
  currentSessionId: string | null

  sendMessage: (content: string) => void
  addAssistantMessage: (content: string, toolResults: ToolResult[], artifacts: VizArtifact[]) => void
  updateProgress: (step: string, percent: number) => void
  clearHistory: () => void
  pushToPanel: (messageId: string) => void
  newChat: () => void
  switchSession: (id: string) => void
  deleteSession: (id: string) => void
  renameSession: (id: string, title: string) => void
  _persist: () => void
  init: () => void
}

let nextId = 1
function genId() {
  return `msg-${nextId++}-${Date.now()}`
}

// ── Session persistence (localStorage) ──────────────────────────────────────
const LS_SESSIONS = 'apexa.chat.sessions'
const LS_CURRENT = 'apexa.chat.current'

function genSid() {
  return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}
function loadSessions(): ChatSession[] {
  try { return JSON.parse(localStorage.getItem(LS_SESSIONS) || '[]') } catch { return [] }
}
function saveSessions(sessions: ChatSession[]) {
  try { localStorage.setItem(LS_SESSIONS, JSON.stringify(sessions)) } catch { /* quota */ }
}
function saveCurrentId(id: string | null) {
  try { id ? localStorage.setItem(LS_CURRENT, id) : localStorage.removeItem(LS_CURRENT) } catch { /* */ }
}
function titleFrom(messages: ChatMessage[]): string {
  const firstUser = messages.find((m) => m.role === 'user')
  if (!firstUser) return 'New chat'
  return firstUser.content.replace(/\s+/g, ' ').trim().slice(0, 48) || 'New chat'
}

function processResponse(text: string, msgId: string) {
  const toolResults = parseToolResults(text)
  const artifacts = extractArtifacts(toolResults, msgId)
  if (artifacts.length > 0) {
    useVizStore.getState().addArtifacts(artifacts)
  }
  // Auto-plot any integration results parsed from the response text (in case the
  // outputs arrived inline rather than as discrete tool_result events).
  for (const tr of toolResults) autoRenderIntegration(tr, msgId)
  return { toolResults, artifacts }
}

// ── Auto-render integration outputs in the side panel ───────────────────────
// Integration tools (midas_integrate_2d_to_1d / _series / _batch) return output
// file PATHS, not plot data — so they never carry a `plotly` field for
// extractArtifacts() to pick up. Without this bridge, "integrate and plot" leaves
// the side panel empty even though the .xy / .zarr.zip were written. Here we call
// the same /api/viz/* endpoints the VizLauncher uses and push the returned Plotly
// as artifacts, so the panel populates the moment integration finishes.
async function renderVizFile(
  endpoint: 'lineout' | 'caked',
  file: string,
  messageId: string,
  extras?: Record<string, string>,
) {
  try {
    const body = new FormData()
    body.append('file', file)
    if (extras) for (const [k, v] of Object.entries(extras)) body.append(k, v)
    const res = await fetch(`/api/viz/${endpoint}`, { method: 'POST', body })
    if (!res.ok) return
    const r = await res.json()
    if (r && r.plotly) {
      useVizStore.getState().addArtifact({
        id: `autoviz-${endpoint}-${genId()}`,
        type: 'plotly',
        title: (r.title as string) || (file.split('/').pop() ?? file),
        data: r.plotly,
        sourceMessageId: messageId,
      })
    }
  } catch {
    // best-effort: a failed auto-plot must never break the chat turn
  }
}

function autoRenderIntegration(result: ToolResult, messageId: string) {
  if (!result.tool || !result.tool.includes('integrate')) return
  if (result.status === 'error') return
  const d = result.data as Record<string, unknown>

  // Collect (lineout, caked) pairs from the payload: single-file tools expose
  // them at the top level; series/batch tools nest one per output.
  const pairs: Array<{ lineout?: unknown; caked?: unknown; peaks?: unknown }> = []
  pairs.push({ lineout: d.lineout_xy ?? d.lineout_file ?? d.lineout, caked: d.zarr_zip ?? d.caked_file ?? d.caked, peaks: d.peaks_csv })
  const nested = (d.outputs ?? d.results ?? d.series) as unknown
  if (Array.isArray(nested)) {
    for (const item of nested.slice(0, 8)) {   // cap: don't flood the panel
      if (item && typeof item === 'object') {
        const o = item as Record<string, unknown>
        pairs.push({ lineout: o.lineout_xy ?? o.lineout_file ?? o.lineout, caked: o.zarr_zip ?? o.caked_file ?? o.caked, peaks: o.peaks_csv })
      }
    }
  }

  for (const p of pairs) {
    if (typeof p.lineout === 'string' && p.lineout) {
      void renderVizFile('lineout', p.lineout, messageId, typeof p.peaks === 'string' ? { peaks_csv: p.peaks } : undefined)
    }
    if (typeof p.caked === 'string' && p.caked) {
      void renderVizFile('caked', p.caked, messageId)
    }
  }
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isLoading: false,
  pendingCount: 0,
  progress: null,
  activity: null,
  _pendingToolResults: [],
  sessions: [],
  currentSessionId: null,

  init: () => {
    // Restore session history (or create the first session).
    const loaded = loadSessions()
    if (loaded.length > 0) {
      let curId: string | null = null
      try { curId = localStorage.getItem(LS_CURRENT) } catch { curId = null }
      const cur = loaded.find((s) => s.id === curId) ?? loaded[0]
      set({ sessions: loaded, currentSessionId: cur.id, messages: cur.messages })
    } else {
      const id = genSid()
      const sess: ChatSession = { id, title: 'New chat', messages: [], createdAt: Date.now(), updatedAt: Date.now() }
      saveSessions([sess]); saveCurrentId(id)
      set({ sessions: [sess], currentSessionId: id, messages: [] })
    }

    wsManager.onMessage((data) => {
      switch (data.type) {
        case 'tool_start': {
          // A tool just began on the backend. Show it live so a long-running tool
          // (e.g. calibration) doesn't look like the app froze.
          const tool = data.tool ?? 'tool'
          set({ activity: `Running ${tool}…` })
          break
        }
        case 'tool_result': {
          // Tool finished — drop back to "Thinking" until the next tool starts or
          // the final answer arrives.
          set({ activity: null })
          const toolResult = parseDirectToolResult(
            data.tool ?? 'unknown',
            data.result ?? '{}'
          )
          if (toolResult) {
            if (data.args) toolResult.args = data.args   // for the "in:" view
            set((s) => ({
              _pendingToolResults: [...s._pendingToolResults, toolResult],
            }))
            const tempId = genId()
            const artifacts = extractArtifacts([toolResult], tempId)
            if (artifacts.length > 0) {
              useVizStore.getState().addArtifacts(artifacts)
            }
            // Integration tools carry only file paths — fetch + render them.
            autoRenderIntegration(toolResult, tempId)
          }
          break
        }
        case 'chat_response': {
          const msgId = genId()
          const text = data.message ?? ''
          const pending = get()._pendingToolResults
          const { toolResults: textResults, artifacts: textArtifacts } = processResponse(text, msgId)
          const allToolResults = [...pending, ...textResults]
          if (textArtifacts.length > 0) {
            useVizStore.getState().addArtifacts(textArtifacts)
          }
          get().addAssistantMessage(text, allToolResults, [])
          set({ _pendingToolResults: [] })
          break
        }
        case 'confirm_required': {
          // Backend (agent) asks a human to approve a consequential action.
          // Show the modal; relay the decision back over the same socket.
          const id = data.confirm_id ?? nextConfirmId()
          useConfirmStore.getState().request({
            id,
            title: data.action ? `Approve: ${data.action}?` : 'Approve action?',
            detail: data.detail,
            danger: data.danger ?? true,
            safety: data.safety,
            confirmLabel: 'Approve & run',
            onConfirm: () => wsManager.send({ type: 'confirm_response', confirm_id: id, approved: true }),
            onCancel: () => wsManager.send({ type: 'confirm_response', confirm_id: id, approved: false }),
          })
          break
        }

        case 'analysis_progress':
          get().updateProgress(data.step ?? '', data.progress ?? 0)
          break
        case 'error':
          get().addAssistantMessage(`Error: ${data.message ?? 'Unknown error'}`, [], [])
          set({ _pendingToolResults: [] })
          break
      }
    })
  },

  sendMessage: (content: string) => {
    const userMsg: ChatMessage = {
      id: genId(),
      role: 'user',
      content,
      timestamp: Date.now(),
    }
    set((s) => ({
      messages: [...s.messages, userMsg],
      isLoading: true,
      pendingCount: s.pendingCount + 1,
      progress: null,
      activity: null,
      _pendingToolResults: [],
    }))
    get()._persist()

    const threadId = get().currentSessionId ?? undefined
    if (wsManager.connected) {
      wsManager.send({ type: 'chat', message: content, thread_id: threadId })
    } else {
      sendChatHttp(content, threadId)
        .then(({ response }) => {
          const msgId = genId()
          const { toolResults, artifacts } = processResponse(response, msgId)
          get().addAssistantMessage(response, toolResults, artifacts)
        })
        .catch((err) => {
          get().addAssistantMessage(`Connection error: ${err.message}`, [], [])
        })
    }
  },

  addAssistantMessage: (content, toolResults, artifacts) => {
    const msg: ChatMessage = {
      id: genId(),
      role: 'assistant',
      content,
      timestamp: Date.now(),
      toolResults: toolResults.length > 0 ? toolResults : undefined,
      artifacts: artifacts.length > 0 ? artifacts : undefined,
    }
    set((s) => {
      const pendingCount = Math.max(0, s.pendingCount - 1)
      return { messages: [...s.messages, msg], pendingCount, isLoading: pendingCount > 0, progress: null, activity: null }
    })
    get()._persist()
  },

  updateProgress: (step, percent) => {
    set({ progress: { step, percent } })
  },

  clearHistory: () => {
    set({ messages: [], isLoading: false, pendingCount: 0, progress: null, _pendingToolResults: [] })
    get()._persist()
  },

  // Snapshot the live messages into the current session and persist to disk.
  _persist: () => {
    const { currentSessionId, messages, sessions } = get()
    if (!currentSessionId) return
    const updated = sessions.map((s) =>
      s.id === currentSessionId
        ? { ...s, messages, title: (!s.title || s.title === 'New chat') ? titleFrom(messages) : s.title, updatedAt: Date.now() }
        : s
    )
    saveSessions(updated)
    set({ sessions: updated })
  },

  newChat: () => {
    get()._persist()
    const id = genSid()
    const sess: ChatSession = { id, title: 'New chat', messages: [], createdAt: Date.now(), updatedAt: Date.now() }
    saveCurrentId(id)
    set((s) => ({ sessions: [sess, ...s.sessions], currentSessionId: id, messages: [], isLoading: false, progress: null, _pendingToolResults: [] }))
    saveSessions(get().sessions)
    useVizStore.getState().clear()
  },

  switchSession: (id) => {
    if (id === get().currentSessionId) return
    get()._persist()
    const sess = get().sessions.find((s) => s.id === id)
    if (!sess) return
    saveCurrentId(id)
    set({ currentSessionId: id, messages: sess.messages, isLoading: false, progress: null, _pendingToolResults: [] })
    useVizStore.getState().clear()
  },

  deleteSession: (id) => {
    // Drop the thread's server-side memory bucket too, so a deleted
    // conversation can't resurface or bleed into a later thread.
    if (wsManager.connected) wsManager.send({ type: 'delete_thread', thread_id: id })
    set((s) => {
      const sessions = s.sessions.filter((x) => x.id !== id)
      let currentSessionId = s.currentSessionId
      let messages = s.messages
      if (currentSessionId === id) {
        const next = sessions[0]
        currentSessionId = next?.id ?? null
        messages = next?.messages ?? []
        saveCurrentId(currentSessionId)
      }
      saveSessions(sessions)
      return { sessions, currentSessionId, messages }
    })
  },

  renameSession: (id, title) => {
    set((s) => {
      const sessions = s.sessions.map((x) => (x.id === id ? { ...x, title: title.trim() || x.title } : x))
      saveSessions(sessions)
      return { sessions }
    })
  },

  pushToPanel: (messageId: string) => {
    const msg = get().messages.find((m) => m.id === messageId)
    if (!msg) return
    useVizStore.getState().addArtifact({
      id: `pinned-${messageId}-${Date.now()}`,
      type: 'text',
      title: 'Pinned Response',
      data: msg.content,
      sourceMessageId: messageId,
    })
  },
}))
