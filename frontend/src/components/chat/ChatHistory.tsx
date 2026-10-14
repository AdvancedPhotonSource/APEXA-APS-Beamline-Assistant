import { useState } from 'react'
import { useChatStore } from '@/stores/chatStore'

/** Shared session catalogue; CLI uses the same IDs and durable records. */
export function ChatHistory() {
  const { sessions, currentSessionId, newChat, switchSession, deleteSession, renameSession,
    refreshSessions, reloadSession, saveWorkspace, sessionSync, sessionError } = useChatStore()
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [search, setSearch] = useState('')
  const ordered = [...sessions].filter(s => `${s.title} ${s.id} ${s.origin ?? ''}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => b.updatedAt - a.updatedAt)
  const current = sessions.find(s => s.id === currentSessionId)
  const commitRename = (id: string) => { renameSession(id, draft); setEditing(null) }

  return <div className="flex h-full min-h-0 flex-col text-xs">
    <div className="shrink-0 space-y-2 border-b p-2" style={{ borderColor: 'var(--apexa-border)' }}>
      <div className="flex gap-2">
        <button className="apexa-gallery-button flex-1" onClick={newChat} disabled={sessionSync === 'loading'}>New session</button>
        <button className="apexa-gallery-button" onClick={() => void refreshSessions()} disabled={sessionSync === 'loading'}>Refresh</button>
      </div>
      <input type="search" value={search} onChange={e => setSearch(e.target.value)} aria-label="Search saved sessions" placeholder="Find a session…"
        className="w-full rounded border p-2" style={{ background: 'var(--apexa-surface-2)', borderColor: 'var(--apexa-border)' }} />
      <p style={{ color: 'var(--apexa-text-muted)' }}>CLI and WebUI share sessions saved on this APEXA host.</p>
      <p role="status" style={{ color: 'var(--apexa-text-muted)' }}>
        {sessionSync === 'loading' ? 'Loading saved session…' : sessionSync === 'saving' ? 'Saving workspace…' : sessionSync === 'error' ? 'Session needs attention' : 'Session history connected'}
      </p>
      {sessionError && <div role="alert" className="space-y-2 leading-relaxed">
        <p>{sessionError}</p>
        {currentSessionId && <button className="apexa-gallery-button" onClick={() => void reloadSession(currentSessionId, true, true)}
          title="Replace local canvas edits with the saved workspace">Reload saved workspace</button>}
      </div>}
      {current && <details className="leading-relaxed">
        <summary>Resume this session in CLI</summary>
        <code className="block break-all mt-1">session load {current.id}</code>
        <button className="apexa-gallery-button mt-2" onClick={() => void saveWorkspace()}>Save workspace now</button>
      </details>}
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto p-1">
      {!ordered.length && <p className="p-4" style={{ color: 'var(--apexa-text-muted)' }}>No matching sessions.</p>}
      {ordered.map(session => {
        const active = session.id === currentSessionId
        return <div key={session.id} className="rounded-lg border p-2 mb-1" style={{ background: active ? 'var(--apexa-rail-active)' : 'transparent', borderColor: active ? 'var(--apexa-border)' : 'transparent' }}>
          {editing === session.id ? <input autoFocus aria-label="Session title" value={draft} onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commitRename(session.id); if (e.key === 'Escape') setEditing(null) }}
            onBlur={() => commitRename(session.id)} className="w-full rounded border p-1" style={{ background: 'var(--apexa-surface)', borderColor: 'var(--apexa-border)' }} />
            : <div className="flex items-start gap-1">
              <button className="min-w-0 flex-1 text-left" onClick={() => active ? void reloadSession(session.id) : switchSession(session.id)}
                aria-current={active ? 'page' : undefined} title={`Resume ${session.title} (${session.id})`}>
                <span className="block truncate font-medium">{session.title || 'New session'}</span>
                <span className="block mt-1 text-[10px]" style={{ color: 'var(--apexa-text-muted)' }}>
                  {session.origin === 'cli' ? 'CLI' : 'WebUI'} · {session.messageCount ?? session.messages.length} messages · {session.status ?? 'local cache'}
                </span>
                {session.updatedAt > 0 && <span className="block text-[10px]" style={{ color: 'var(--apexa-text-muted)' }}>{new Date(session.updatedAt).toLocaleString()}</span>}
                {session.error && <span className="block mt-1">{session.error}</span>}
              </button>
              <button className="apexa-gallery-button" aria-label={`Rename ${session.title}`} title="Rename" onClick={() => { setEditing(session.id); setDraft(session.title) }}>✎</button>
              <button className="apexa-gallery-button" aria-label={`Archive ${session.title}`} title="Archive session (keeps saved files)" onClick={() => deleteSession(session.id)}>×</button>
            </div>}
        </div>
      })}
    </div>
  </div>
}
