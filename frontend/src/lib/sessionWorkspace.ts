import type { ChatMessage, ChatSession, SessionWorkspace, VizArtifact } from '../api/types'
import { extractArtifacts, parseDirectToolResult } from './parseToolResult.ts'

/** Restore recorded tool output without re-executing an analysis. */
export function hydrateSession(session: ChatSession): ChatSession {
  const messages = (session.messages ?? []).map(message => {
    const events = (message.toolEvents ?? []).flatMap(event => {
      const result = parseDirectToolResult(event.tool, event.result)
      if (!result) return []
      return [{ ...result, args: event.args }]
    })
    const toolResults = events.length ? events : message.toolResults ?? []
    return { ...message, toolResults, artifacts: message.artifacts?.length ? message.artifacts : extractArtifacts(toolResults, message.id) }
  })
  return { ...session, messages, serverBacked: true }
}

export function messageArtifacts(messages: ChatMessage[]): VizArtifact[] {
  return messages.flatMap(message => message.artifacts?.length ? message.artifacts : extractArtifacts(message.toolResults ?? [], message.id))
}

/** Strip disposable image/cache payloads, retaining reproducible source/settings. */
export function durableWorkspace(workspace: SessionWorkspace): SessionWorkspace {
  return { ...workspace, artifacts: workspace.artifacts.map(artifact => {
    if (!['sequence', 'diffraction'].includes(artifact.type) || !artifact.data || typeof artifact.data !== 'object') return artifact
    const data = { ...artifact.data as Record<string, unknown> }
    delete data.preview
    if (artifact.type === 'diffraction') delete data.fileId
    return { ...artifact, data }
  }) }
}

export function restoredWorkspace(session: ChatSession): SessionWorkspace {
  const recorded = messageArtifacts(session.messages)
  const saved = session.workspace
  const seen = new Set(saved?.recordedArtifactIds ?? saved?.artifacts?.map(a => a.id) ?? [])
  const newArtifacts = recorded.filter(a => !seen.has(a.id))
  const merged = new Map(newArtifacts.map(a => [a.id, a]))
  // Viewer state overrides the original tool request for the same artifact.
  saved?.artifacts?.forEach(artifact => merged.set(artifact.id, artifact))
  const artifacts = [...merged.values()]
  const valid = (ids: string[] = []) => [...new Set(ids)].filter(id => merged.has(id))
  const newIds = newArtifacts.map(a => a.id)
  const openIds = saved?.openIds ? valid([...saved.openIds, ...newIds]) : artifacts.map(a => a.id)
  return { artifacts, openIds, recordedArtifactIds: [...new Set([...seen, ...recorded.map(a => a.id)])],
    activeId: saved?.activeId && openIds.includes(saved.activeId) ? saved.activeId : openIds.at(-1) ?? null,
    pinned: valid(saved?.pinned), compareIds: valid(saved?.compareIds).slice(-2),
    draft: saved?.draft ?? '', attachments: saved?.attachments ?? [], browsePath: saved?.browsePath ?? '.',
  }
}
