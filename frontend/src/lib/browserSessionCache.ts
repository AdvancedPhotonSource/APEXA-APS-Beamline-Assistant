import type { ChatMessage, ChatSession, SessionWorkspace, VizArtifact } from '../api/types'

const DATABASE = 'apexa.browser-sessions.v1'
const JOURNAL = 'apexa.chat.recovery.v1'
const LEGACY = 'apexa.chat.sessions'
type StoredArtifact = Omit<VizArtifact, 'data'> & { data?: unknown; dataRef?: string }
type StoredSession = Omit<ChatSession, 'messages' | 'workspace'> & {
  messagesRef: string
  workspace?: Omit<SessionWorkspace, 'artifacts'> & { artifacts: StoredArtifact[] }
}
type Recovery = Omit<ChatSession, 'messages' | 'workspace'> & { workspace?: Omit<SessionWorkspace, 'artifacts'> }
function payloadId() {
  return typeof crypto.randomUUID === 'function' ? crypto.randomUUID()
    : [...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, '0')).join('')
}

function request<T>(value: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    value.onsuccess = () => resolve(value.result)
    value.onerror = () => reject(value.error ?? new Error('Browser cache read failed'))
  })
}
function complete(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve()
    tx.onabort = tx.onerror = () => reject(tx.error ?? new Error('Browser cache transaction failed'))
  })
}

/**
 * Async browser fallback, not the CLI/server source of truth. Immutable message
 * arrays and artifact data have separate payload records: changing a draft does
 * not JSON stringify or clone every plot. Snapshot + new payloads commit together.
 */
export class BrowserSessionCache {
  private database: Promise<IDBDatabase> | null = null
  private identities = new WeakMap<object, string>()
  private strings = new Map<string, string>()
  private stored = new Set<string>()
  private pending: ChatSession[] | null = null
  private drain: Promise<void> | null = null
  private closed = false
  private revision = 0
  private recovery: Recovery[]

  constructor() {
    try { this.recovery = JSON.parse(localStorage.getItem(JOURNAL) ?? '[]') } catch { this.recovery = [] }
    if (!Array.isArray(this.recovery)) this.recovery = []
  }

  private open(): Promise<IDBDatabase> {
    if (!this.database) this.database = new Promise((resolve, reject) => {
      const open = indexedDB.open(DATABASE, 1)
      open.onupgradeneeded = () => {
        open.result.createObjectStore('snapshot')
        open.result.createObjectStore('payloads')
      }
      open.onsuccess = () => {
        open.result.onversionchange = () => open.result.close()
        resolve(open.result)
      }
      open.onerror = () => reject(open.error ?? new Error('Browser cache unavailable'))
      open.onblocked = () => reject(new Error('Browser cache upgrade is blocked by another window'))
    })
    return this.database
  }

  async read(legacy: ChatSession[]): Promise<ChatSession[]> {
    const db = await this.open()
    const tx = db.transaction('snapshot', 'readonly')
    const [rows, revision] = await Promise.all([
      request<StoredSession[] | undefined>(tx.objectStore('snapshot').get('sessions')),
      request<number | undefined>(tx.objectStore('snapshot').get('revision')),
    ])
    this.revision = revision ?? 0
    let sessions = legacy
    if (rows) {
      const refs = new Set(rows.flatMap(row => [row.messagesRef, ...(row.workspace?.artifacts.flatMap(a => a.dataRef ? [a.dataRef] : []) ?? [])]))
      const payloadTx = db.transaction('payloads', 'readonly')
      const values = new Map(await Promise.all([...refs].map(async id => {
        const value = await request(payloadTx.objectStore('payloads').get(id))
        if (value === undefined) throw new Error(`Browser cache payload missing: ${id}. Reload the saved server session.`)
        if (value && typeof value === 'object') this.identities.set(value, id)
        else if (typeof value === 'string') this.strings.set(value, id)
        return [id, value] as const
      })))
      this.stored = refs
      sessions = rows.map(({ messagesRef, workspace, ...row }) => ({ ...row,
        messages: values.get(messagesRef) as ChatMessage[],
        workspace: workspace && { ...workspace, artifacts: workspace.artifacts.map(({ dataRef, ...a }) =>
          ({ ...a, data: dataRef ? values.get(dataRef) : a.data })) },
      }))
    }
    // The small journal can survive pagehide even if a new IDB transaction did
    // not finish. It contains drafts/settings only, never dangling payload refs.
    const recovered = new Map(sessions.map(session => [session.id, session]))
    for (const row of this.recovery) {
      if (!row || typeof row.id !== 'string') continue
      const previous = recovered.get(row.id)
      recovered.set(row.id, { ...previous, ...row, messages: previous?.messages ?? [],
        workspace: row.workspace ? { ...previous?.workspace, ...row.workspace, artifacts: previous?.workspace?.artifacts ?? [] } : previous?.workspace })
    }
    return [...recovered.values()]
  }

  save(sessions: ChatSession[]): Promise<void> {
    if (this.closed) return Promise.resolve()
    // Small synchronous recovery journal. Never stringify messages, results,
    // image previews or arbitrary artifact arrays in localStorage.
    const journal = sessions.map(({ messages: _messages, workspace, ...row }) => {
      void _messages
      const recovery: Recovery = row
      if (workspace) {
        const { artifacts: _artifacts, ...settings } = workspace
        void _artifacts
        recovery.workspace = settings
      }
      return recovery
    })
    try { localStorage.setItem(JOURNAL, JSON.stringify(journal)) } catch { /* IDB error is reported separately below. */ }
    this.pending = sessions
    if (!this.drain) this.drain = this.writePending().finally(() => { this.drain = null })
    return this.drain
  }

  private async writePending() {
    const db = await this.open()
    while (this.pending && !this.closed) {
      const sessions = this.pending
      this.pending = null
      const newPayloads = new Map<string, object | string>()
      const current = new Set<string>()
      const additions = new WeakMap<object, string>()
      const addedStrings = new Map<string, string>()
      const reference = (value: object | string) => {
        const existing = typeof value === 'string' ? this.strings.get(value) : this.identities.get(value)
        const added = typeof value === 'string' ? addedStrings.get(value) : additions.get(value)
        const id = existing && this.stored.has(existing) ? existing : added ?? payloadId()
        current.add(id)
        if (!this.stored.has(id)) {
          newPayloads.set(id, value)
          if (typeof value === 'string') addedStrings.set(value, id)
          else additions.set(value, id)
        }
        return id
      }
      const rows: StoredSession[] = sessions.map(({ messages, workspace, ...row }) => ({ ...row,
        messagesRef: reference(messages),
        workspace: workspace && { ...workspace, artifacts: workspace.artifacts.map(({ data, ...a }) =>
          data && (typeof data === 'object' || (typeof data === 'string' && data.length > 4096)) ? { ...a, dataRef: reference(data) } : { ...a, data }) },
      }))
      const tx = db.transaction(['snapshot', 'payloads'], 'readwrite')
      const done = complete(tx)
      try {
        const actual = await request<number | undefined>(tx.objectStore('snapshot').get('revision'))
        if ((actual ?? 0) !== this.revision) throw new Error('Browser backup changed in another tab. Reload saved sessions before retrying.')
        const payloads = tx.objectStore('payloads')
        for (const [id, value] of newPayloads) payloads.put(value, id)
        for (const id of this.stored) if (!current.has(id)) payloads.delete(id)
        tx.objectStore('snapshot').put(rows, 'sessions')
        tx.objectStore('snapshot').put(this.revision + 1, 'revision')
      } catch (error) { tx.abort(); await done.catch(() => {}); throw error }
      await done
      for (const [id, value] of newPayloads) {
        if (typeof value === 'string') this.strings.set(value, id)
        else this.identities.set(value, id)
      }
      for (const [value, id] of this.strings) if (!current.has(id)) this.strings.delete(value)
      this.stored = current
      this.revision += 1
      // Delete a legacy full-JSON cache only after its replacement is durable.
      try { localStorage.removeItem(LEGACY) } catch { /* Old cache is harmless. */ }
    }
  }

  async flush() { await this.drain }
  close() { this.closed = true; this.pending = null; void this.database?.then(db => db.close()).catch(() => {}) }
}
