import { create } from 'zustand'
import type { ChatMessage, ToolResult, VizArtifact, ChatSession, SessionWorkspace, SessionAttachment } from '@/api/types'
import { wsManager } from '@/api/websocket'
import { sendChatHttp } from '@/api/endpoints'
import { parseToolResults, extractArtifacts, parseDirectToolResult } from '@/lib/parseToolResult'
import { useVizStore } from './vizStore'
import { useConfirmStore, nextConfirmId } from './confirmStore'
import { canvasContext } from '@/lib/canvasContext'
import { listServerSessions, readServerSession, createServerSession, importBrowserSession, renameServerSession, archiveServerSession, writeServerWorkspace } from '@/api/sessions'
import { durableWorkspace, hydrateSession, restoredWorkspace, messageArtifacts } from '@/lib/sessionWorkspace'
import { useFileStore } from './fileStore'
import { useImageStore } from './imageStore'
import { useConnectionStore } from './connectionStore'


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

  // Disk-backed sessions shared with CLI; localStorage is only a migration/cache source.
  sessions: ChatSession[]
  currentSessionId: string | null
  sessionSync: 'loading' | 'ready' | 'saving' | 'error'
  sessionError: string | null
  draft: string
  attachments: SessionAttachment[]
  setDraft: (text: string) => void
  setAttachments: (attachments: SessionAttachment[]) => void
  refreshSessions: () => Promise<void>
  reloadSession: (id: string, restore?: boolean, discardLocal?: boolean) => Promise<void>
  saveWorkspace: () => Promise<void>

  sendMessage: (content: string) => void
  addAssistantMessage: (content: string, toolResults: ToolResult[], artifacts: VizArtifact[], threadId?: string) => void
  updateProgress: (step: string, percent: number) => void
  clearHistory: () => void
  pushToPanel: (messageId: string) => void
  newChat: () => void
  switchSession: (id: string) => void
  deleteSession: (id: string) => void
  renameSession: (id: string, title: string) => void
  _persist: () => void
  init: (options?: { sync?: boolean }) => () => void
}

let nextId = 1
const pendingByThread = new Map<string, number>()
const socketPendingByThread = new Map<string, number>()
const resultsByThread = new Map<string, ToolResult[]>()
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
  try { if (id) localStorage.setItem(LS_CURRENT, id); else localStorage.removeItem(LS_CURRENT) } catch { /* */ }
}
function titleFrom(messages: ChatMessage[]): string {
  const firstUser = messages.find((m) => m.role === 'user')
  if (!firstUser) return 'New chat'
  return firstUser.content.replace(/\s+/g, ' ').trim().slice(0, 48) || 'New chat'
}

let syncEnabled = false
let hydration = false
let lifecycle = 0
let selection = 0
let bootstrap: Promise<void> = Promise.resolve()
const workspaceVersions = new Map<string, number>()
const workspaceTimers = new Map<string, ReturnType<typeof setTimeout>>()
const workspacePending = new Map<string, SessionWorkspace>()
const workspaceQueues = new Map<string, Promise<void>>()
const workspaceSaved = new Map<string, string>()
const workspaceInflight = new Map<string, SessionWorkspace>()
const workspaceErrors = new Map<string, string>()

function captureWorkspace(): SessionWorkspace {
  const { artifacts, openIds, activeId, pinned, compareIds } = useVizStore.getState()
  const chat = useChatStore.getState()
  const images = useImageStore.getState()
  return durableWorkspace({
    artifacts: artifacts.map(a => {
      if (a.type !== 'diffraction') return a
      const data = a.data as { fileId?: string; path?: string; settings?: unknown }
      const image = images.loadedImages.get(data.fileId ?? '')
      return { ...a, data: { ...data, path: image?.path ?? data.path,
        settings: image?.fileId === images.activeImageId ? images.settings : image?.settings ?? data.settings } }
    }), openIds, activeId, pinned, compareIds,
    recordedArtifactIds: [...new Set([
      ...(chat.sessions.find(s => s.id === chat.currentSessionId)?.workspace?.recordedArtifactIds ?? []),
      ...messageArtifacts(chat.messages).map(a => a.id),
    ])],
    draft: chat.draft, attachments: chat.attachments, browsePath: useFileStore.getState().currentPath,
  })
}

function restoreSessionArtifacts(session: ChatSession) {
  const workspace = restoredWorkspace(session)
  hydration = true
  try {
    useImageStore.getState().resetSession()
    useVizStore.setState({ artifacts: workspace.artifacts, openIds: workspace.openIds, activeId: workspace.activeId,
      pinned: workspace.pinned, compareIds: workspace.compareIds, revealRevision: useVizStore.getState().revealRevision + 1 })
    useChatStore.setState({ draft: workspace.draft ?? '', attachments: workspace.attachments ?? [] })
    useFileStore.getState().restorePath(workspace.browsePath ?? '.')
    if (syncEnabled) void useFileStore.getState().browse(workspace.browsePath)
  } finally { hydration = false }
}

function syncError(error: unknown, id = useChatStore.getState().currentSessionId) {
  if (id) workspaceErrors.set(id, String(error))
  if (id === useChatStore.getState().currentSessionId) useChatStore.setState({ sessionSync: 'error', sessionError: String(error) })
}

function scheduleWorkspace() {
  if (hydration) return
  const state = useChatStore.getState()
  const id = state.currentSessionId
  if (!id) return
  const workspace = captureWorkspace()
  const changed = JSON.stringify(workspace) !== workspaceSaved.get(id) || workspacePending.has(id)
  useChatStore.setState(s => ({ sessions: s.sessions.map(session => session.id === id ? { ...session, workspace, workspaceDirty: syncEnabled && changed } : session) }))
  saveSessions(useChatStore.getState().sessions)
  if (!syncEnabled) return
  if (!changed) return
  workspacePending.set(id, workspace)
  clearTimeout(workspaceTimers.get(id))
  workspaceTimers.set(id, setTimeout(() => { void flushWorkspace(id).catch(error => syncError(error, id)) }, 600))
}

async function flushWorkspace(id: string): Promise<void> {
  clearTimeout(workspaceTimers.get(id))
  workspaceTimers.delete(id)
  const work = async () => {
    await bootstrap
    const epoch = lifecycle
    const workspace = workspacePending.get(id)
    if (!workspace || !syncEnabled) return
    workspacePending.delete(id)
    workspaceInflight.set(id, workspace)
    if (useChatStore.getState().currentSessionId === id) useChatStore.setState({ sessionSync: 'saving' })
    try {
      const result = await writeServerWorkspace(id, workspace, workspaceVersions.get(id) ?? 0)
      workspaceVersions.set(id, result.workspaceRevision)
      workspaceSaved.set(id, JSON.stringify(workspace))
      workspaceErrors.delete(id)
      if (epoch !== lifecycle) return
      useChatStore.setState(s => ({ ...(s.currentSessionId === id ? { sessionSync: 'ready' as const, sessionError: null } : {}),
        sessions: s.sessions.map(session => session.id === id ? { ...session, workspaceRevision: result.workspaceRevision, workspaceDirty: workspacePending.has(id) } : session) }))
      saveSessions(useChatStore.getState().sessions)
    } catch (error) {
      // Retain the local snapshot, but do not silently overwrite another window.
      if (!workspacePending.has(id)) workspacePending.set(id, workspace)
      workspaceErrors.set(id, String(error))
      throw error
    } finally { workspaceInflight.delete(id) }
  }
  const promise = (workspaceQueues.get(id) ?? Promise.resolve()).catch(() => {}).then(work)
  workspaceQueues.set(id, promise)
  await promise
}


function presentArtifacts(artifacts: VizArtifact[]) {
  if (!artifacts.length) return
  const canvas = useVizStore.getState()
  canvas.addArtifacts(artifacts)
  // An explicit agent "open" request should also reveal a hidden canvas.
  // Restoring history above retains the user's saved workspace preference.
  if (artifacts.some(a => a.provenance?.tool?.split(':').pop() === 'open_science_view')) canvas.showCanvas()
}

function processResponse(text: string, msgId: string) {
  const toolResults = parseToolResults(text)
  const artifacts = extractArtifacts(toolResults, msgId)
  if (artifacts.length > 0) {
    presentArtifacts(artifacts)
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
  const originThread = useChatStore.getState().currentSessionId
  try {
    const body = new FormData()
    body.append('file', file)
    if (extras) for (const [k, v] of Object.entries(extras)) body.append(k, v)
    const res = await fetch(`/api/viz/${endpoint}`, { method: 'POST', body })
    if (!res.ok) return
    const r = await res.json()
    if (useChatStore.getState().currentSessionId !== originThread) return
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
  sessionSync: 'ready', sessionError: null, draft: '', attachments: [],

  setDraft: draft => { set({ draft }); scheduleWorkspace() },
  setAttachments: attachments => { set({ attachments }); scheduleWorkspace() },

  saveWorkspace: async () => {
    scheduleWorkspace()
    const id = get().currentSessionId
    if (id) await flushWorkspace(id).catch(error => syncError(error, id))
  },

  reloadSession: async (id, restore = true, discardLocal = false) => {
    if (!syncEnabled) return
    const epoch = lifecycle
    const request = id === get().currentSessionId ? ++selection : selection
    if (restore && id === get().currentSessionId) set({ sessionSync: 'loading' })
    try {
      if (discardLocal) {
        await workspaceQueues.get(id)?.catch(() => {})
        workspacePending.delete(id)
        clearTimeout(workspaceTimers.get(id))
        workspaceErrors.delete(id)
      }
      const session = hydrateSession(await readServerSession(id))
      if (epoch !== lifecycle) return
      const previousWorkspace = get().sessions.find(s => s.id === id)?.workspace
      const pendingWorkspace = workspacePending.get(id) ?? workspaceInflight.get(id)
      if (!workspaceVersions.has(id) || (restore && !pendingWorkspace)) workspaceVersions.set(id, session.workspaceRevision ?? 0)
      if (restore && !pendingWorkspace) {
        workspaceSaved.set(id, JSON.stringify(session.workspace ?? {}))
      }
      if (pendingWorkspace) {
        session.workspace = pendingWorkspace
        session.workspaceDirty = true
        session.workspaceRevision = workspaceVersions.get(id)
      }
      else if (!restore && previousWorkspace) session.workspace = previousWorkspace
      set(s => ({ sessions: s.sessions.map(old => old.id === id ? session : old) }))
      if (request === selection && get().currentSessionId === id && !(pendingByThread.get(id) ?? 0)) {
        set({ messages: session.messages, isLoading: session.status === 'running', pendingCount: 0,
          activity: session.status === 'running' ? 'A request is running in another connection…' : null,
          sessionSync: workspaceErrors.has(id) ? 'error' : 'ready', sessionError: workspaceErrors.get(id) ?? session.resume_notice ?? null })
        if (restore) {
          restoreSessionArtifacts(session)
          if (session.model) useConnectionStore.setState({ selectedModel: session.model })
        } else {
          // Include new CLI results while preserving local viewer edits.
          const canvas = useVizStore.getState()
          const seen = new Set(previousWorkspace?.recordedArtifactIds ?? [])
          const newArtifacts = messageArtifacts(session.messages).filter(a => !seen.has(a.id) && !canvas.artifacts.some(old => old.id === a.id))
          if (newArtifacts.length) canvas.addArtifacts(newArtifacts)
        }
        if (!workspaceErrors.has(id)) bootstrap = Promise.resolve()
      }
      saveSessions(get().sessions)
    } catch (error) { if (epoch === lifecycle) syncError(error, id) }
  },

  refreshSessions: async () => {
    if (!syncEnabled) return
    const epoch = lifecycle
    try {
      const result = await listServerSessions()
      if (epoch !== lifecycle) return
      const cached = get().sessions
      const merged: ChatSession[] = result.sessions.map(row => ({ ...cached.find(s => s.id === row.id), ...row,
        messages: cached.find(s => s.id === row.id)?.messages ?? [], serverBacked: true }))
      // A just-created local session can be listed before its POST completes.
      for (const session of cached) if (!session.serverBacked && !merged.some(s => s.id === session.id)) merged.push(session)
      set({ sessions: merged })
      const current = merged.find(s => s.id === get().currentSessionId)
      const chosen = current ?? merged.find(s => s.id === result.last_active) ?? merged[0]
      if (chosen) {
        if (!current) { set({ currentSessionId: chosen.id }); saveCurrentId(chosen.id) }
        await get().reloadSession(chosen.id, !current)
        if (get().sessionSync !== 'error') bootstrap = Promise.resolve()
      }
    } catch (error) { if (epoch === lifecycle) syncError(error) }
  },

  init: (options) => {
    syncEnabled = options?.sync !== false
    const epoch = ++lifecycle
    let disposed = false

    // Restore session history (or create the first session).
    const loaded = loadSessions()
    for (const session of loaded) {
      if (session.workspaceDirty && session.workspace) {
        workspacePending.set(session.id, session.workspace)
        workspaceVersions.set(session.id, session.workspaceRevision ?? 0)
      }
    }
    if (loaded.length > 0) {
      let curId: string | null = null
      try { curId = localStorage.getItem(LS_CURRENT) } catch { curId = null }
      const cur = loaded.find((s) => s.id === curId) ?? loaded[0]
      set({ sessions: loaded, currentSessionId: cur.id, messages: cur.messages })
      restoreSessionArtifacts(cur)
    } else {
      const id = genSid()
      const sess: ChatSession = { id, title: 'New chat', messages: [], createdAt: Date.now(), updatedAt: Date.now() }
      saveSessions([sess]); saveCurrentId(id)
      set({ sessions: [sess], currentSessionId: id, messages: [] })
      restoreSessionArtifacts(sess)
    }

    // Migrate browser-only sessions before any new turn can use their IDs.
    bootstrap = (async () => {
      if (!syncEnabled) return
      set({ sessionSync: 'loading', sessionError: null })
      try {
        for (const session of loaded.filter(s => !s.serverBacked && s.messages.length)) {
          await importBrowserSession(session)
        }
        const result = await listServerSessions()
        if (disposed || epoch !== lifecycle) return
        const savedId = get().currentSessionId
        const existing = result.sessions.find(s => s.id === savedId)
        const chosen = existing ?? result.sessions.find(s => s.id === result.last_active) ?? result.sessions[0]
        if (chosen) {
          set({ sessions: result.sessions.map(s => ({ ...s, serverBacked: true })), currentSessionId: chosen.id })
          saveCurrentId(chosen.id)
          await get().reloadSession(chosen.id)
        } else if (savedId) {
          await createServerSession(savedId)
          await get().reloadSession(savedId)
        }
        if (!disposed && get().sessionSync === 'error') throw new Error(get().sessionError ?? 'Session could not be restored')
      } catch (error) { if (!disposed) syncError(error); throw error }
    })()
    void bootstrap.catch(() => {})
    void bootstrap.then(() => {
      if (disposed || epoch !== lifecycle) return
      for (const id of workspacePending.keys()) void flushWorkspace(id).catch(error => syncError(error, id))
    }).catch(() => {})
    const unsubCanvas = useVizStore.subscribe((state, previous) => {
      if (state.artifacts !== previous.artifacts || state.openIds !== previous.openIds || state.activeId !== previous.activeId
          || state.pinned !== previous.pinned || state.compareIds !== previous.compareIds) scheduleWorkspace()
    })
    const unsubFiles = useFileStore.subscribe((state, previous) => { if (state.currentPath !== previous.currentPath) scheduleWorkspace() })
    const unsubImages = useImageStore.subscribe((state, previous) => { if (state.settings !== previous.settings) scheduleWorkspace() })
    const onFocus = () => { if (!(get().pendingCount > 0)) void get().refreshSessions() }
    if (syncEnabled && typeof window !== 'undefined') window.addEventListener?.('focus', onFocus)
    const poll = syncEnabled ? setInterval(() => {
      const current = get().sessions.find(s => s.id === get().currentSessionId)
      if (current?.status === 'running' && !(pendingByThread.get(current.id) ?? 0)) void get().reloadSession(current.id, false)
    }, 10000) : null
    const unsubscribe = wsManager.onMessage((data) => {
      const thread = data.thread_id ?? get().currentSessionId ?? ''
      if (data.type === 'tool_result') {
        const parsed = parseDirectToolResult(data.tool ?? 'unknown', data.result ?? '{}')
        if (parsed) { parsed.args = data.args; resultsByThread.set(thread, [...(resultsByThread.get(thread) ?? []), parsed]) }
      }
      if (data.type === 'chat_response' || data.type === 'error') {
        pendingByThread.set(thread, Math.max(0, (pendingByThread.get(thread) ?? 0) - 1))
        socketPendingByThread.set(thread, Math.max(0, (socketPendingByThread.get(thread) ?? 0) - 1))
      }
      // A reconstruction may finish after the operator switches conversations.
      // Keep its results with the originating session, without stealing the canvas.
      if (data.thread_id && data.thread_id !== get().currentSessionId) {
        if (data.type === 'chat_response' || data.type === 'error') {
          const content = data.type === 'error' ? `Error: ${data.message}` : data.message ?? ''
          const toolResults = [...(resultsByThread.get(thread) ?? []), ...parseToolResults(content)]
          const id = genId()
          const message: ChatMessage = { id, role: 'assistant', content, timestamp: Date.now(), toolResults,
            artifacts: extractArtifacts(toolResults, id) }
          resultsByThread.delete(thread)
          set(s => ({ sessions: s.sessions.map(session => session.id === thread
            ? { ...session, messages: [...session.messages, message], updatedAt: Date.now() } : session) }))
          saveSessions(get().sessions)
          if (syncEnabled) void get().refreshSessions()
        }
        if (data.type !== 'confirm_required') return
      }
      switch (data.type) {
        case 'connection_status':
          if (!data.connected) {
            for (const [id, pending] of socketPendingByThread) {
              if (!pending) continue
              get().addAssistantMessage('Connection interrupted during this request. A tool may still be running on the server; check its state before retrying.', [], [], id)
              const remaining = Math.max(0, (pendingByThread.get(id) ?? 0) - pending)
              pendingByThread.set(id, remaining)
              resultsByThread.delete(id)
              if (id === get().currentSessionId) set({ pendingCount: remaining, isLoading: remaining > 0, activity: null, progress: null, _pendingToolResults: [] })
            }
            socketPendingByThread.clear()
          }
          break
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
              presentArtifacts(artifacts)
            }
            // Integration tools carry only file paths — fetch + render them.
            autoRenderIntegration(toolResult, tempId)
          }
          break
        }
        case 'chat_response': {
          const msgId = genId()
          const text = data.message ?? ''
          const pending = resultsByThread.get(thread) ?? get()._pendingToolResults
          const { toolResults: textResults } = processResponse(text, msgId)
          const allToolResults = [...pending, ...textResults]
          get().addAssistantMessage(text, allToolResults, [])
          resultsByThread.delete(thread)
          set({ _pendingToolResults: [] })
          if (syncEnabled) void get().reloadSession(thread, false)
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
          resultsByThread.delete(thread)
          set({ _pendingToolResults: [] })
          if (syncEnabled) void get().reloadSession(thread, false)
          break
      }
    })
    return () => {
      disposed = true
      lifecycle += 1
      unsubscribe(); unsubCanvas(); unsubFiles(); unsubImages()
      if (poll) clearInterval(poll)
      if (typeof window !== 'undefined') window.removeEventListener?.('focus', onFocus)
      for (const id of workspacePending.keys()) void flushWorkspace(id).catch(error => syncError(error, id))
    }
  },

  sendMessage: (content: string) => {
    if (syncEnabled && get().sessionSync === 'loading') return
    const thread = get().currentSessionId ?? ''
    pendingByThread.set(thread, (pendingByThread.get(thread) ?? 0) + 1)
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
      _pendingToolResults: s.pendingCount > 0 ? s._pendingToolResults : [],
    }))
    get()._persist()

    const threadId = get().currentSessionId ?? undefined
    const viz = useVizStore.getState()
    const viewer_context = canvasContext(viz.artifacts.find(a => a.id === viz.activeId))
    const send = () => {
    if (wsManager.connected) {
      socketPendingByThread.set(thread, (socketPendingByThread.get(thread) ?? 0) + 1)
      wsManager.send({ type: 'chat', message: content, thread_id: threadId, viewer_context, model: useConnectionStore.getState().selectedModel })
    } else {
      sendChatHttp(content, threadId, viewer_context, useConnectionStore.getState().selectedModel)
        .then(({ response, tool_results }) => {
          pendingByThread.set(thread, Math.max(0, (pendingByThread.get(thread) ?? 0) - 1))
          const msgId = genId()
          const active = threadId === get().currentSessionId
          const toolResults = parseToolResults(response)
          const directResults: ToolResult[] = []
          for (const item of tool_results ?? []) {
            const parsed = parseDirectToolResult(item.tool, item.result)
            if (parsed) {
              parsed.args = item.args
              directResults.push(parsed)
            }
          }
          const allResults = [...directResults, ...toolResults]
          const artifacts = extractArtifacts(allResults, msgId)
          if (active) {
            presentArtifacts(artifacts)
            allResults.forEach(result => autoRenderIntegration(result, msgId))
          }
          get().addAssistantMessage(response, allResults, artifacts, threadId)
          if (syncEnabled) void get().reloadSession(thread, false)
        })
        .catch((err) => {
          pendingByThread.set(thread, Math.max(0, (pendingByThread.get(thread) ?? 0) - 1))
          get().addAssistantMessage(`Connection error: ${err.message}`, [], [], threadId)
        })
    }
    }
    if (syncEnabled) void bootstrap.then(send).catch(error => {
      pendingByThread.set(thread, Math.max(0, (pendingByThread.get(thread) ?? 0) - 1))
      get().addAssistantMessage(`Session could not be loaded: ${String(error)}. Reload session history before sending.`, [], [], threadId)
    })
    else send()
  },

  addAssistantMessage: (content, toolResults, artifacts, threadId) => {
    const msg: ChatMessage = {
      id: genId(),
      role: 'assistant',
      content,
      timestamp: Date.now(),
      toolResults: toolResults.length > 0 ? toolResults : undefined,
      artifacts: artifacts.length > 0 ? artifacts : undefined,
    }
    if (threadId && threadId !== get().currentSessionId) {
      set(s => ({ sessions: s.sessions.map(session => session.id === threadId
        ? { ...session, messages: [...session.messages, msg], updatedAt: Date.now() } : session) }))
      saveSessions(get().sessions)
      return
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
    get().newChat()
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
    scheduleWorkspace()
  },

  newChat: () => {
    get()._persist()
    const id = genSid()
    const sess: ChatSession = { id, title: 'New chat', messages: [], createdAt: Date.now(), updatedAt: Date.now() }
    saveCurrentId(id)
    set((s) => ({ sessions: [sess, ...s.sessions], currentSessionId: id, messages: [], pendingCount: 0, isLoading: false, activity: null, progress: null, _pendingToolResults: [] }))
    saveSessions(get().sessions)
    restoreSessionArtifacts(sess)
    if (syncEnabled) {
      set({ sessionSync: 'loading' })
      void createServerSession(id).then(() => get().reloadSession(id)).catch(syncError)
    }
  },

  switchSession: (id) => {
    if (id === get().currentSessionId) return
    get()._persist()
    const sess = get().sessions.find((s) => s.id === id)
    if (!sess) return
    saveCurrentId(id)
    const pendingCount = pendingByThread.get(id) ?? 0
    set({ currentSessionId: id, messages: sess.messages, pendingCount, isLoading: pendingCount > 0, activity: null, progress: null, _pendingToolResults: resultsByThread.get(id) ?? [] })
    restoreSessionArtifacts(sess)
    if (syncEnabled) void get().reloadSession(id)
  },

  deleteSession: (id) => {
    const remove = () => {
      const sessions = get().sessions.filter(s => s.id !== id)
      const active = id === get().currentSessionId
      set({ sessions })
      workspacePending.delete(id)
      clearTimeout(workspaceTimers.get(id))
      if (active) {
        if (sessions[0]) get().switchSession(sessions[0].id)
        else get().newChat()
      }
      saveSessions(get().sessions)
    }
    if (syncEnabled) void archiveServerSession(id).then(remove).catch(syncError)
    else remove()
  },

  renameSession: (id, title) => {
    if (!title.trim()) return
    const renamed = () => {
      set(s => ({ sessions: s.sessions.map(session => session.id === id ? { ...session, title: title.trim() } : session) }))
      saveSessions(get().sessions)
    }
    if (syncEnabled) void renameServerSession(id, title).then(renamed).catch(syncError)
    else renamed()
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
    useVizStore.getState().showCanvas()
  },
}))
