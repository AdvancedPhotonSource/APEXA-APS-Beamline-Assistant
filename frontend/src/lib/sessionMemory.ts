import type { ChatSession } from '../api/types'

/** Retain active/unsaved work, unload older clean server-backed sessions. */
export function boundSessionMemory(sessions: ChatSession[], activeId: string | null, protectedIds: Set<string>, limit = 3): ChatSession[] {
  const candidates = sessions.filter(s => s.id !== activeId && s.serverBacked && !s.workspaceDirty
    && s.status !== 'running' && !protectedIds.has(s.id)).sort((a, b) => b.updatedAt - a.updatedAt)
  const retained = new Set(candidates.slice(0, Math.max(0, limit - 1)).map(s => s.id))
  return sessions.map(s => candidates.includes(s) && !retained.has(s.id)
    ? { ...s, messageCount: s.messageCount ?? s.messages.length, messages: [], workspace: undefined }
    : s)
}
