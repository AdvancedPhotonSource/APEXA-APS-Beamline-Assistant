import { useState } from 'react'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { useChatStore } from '@/stores/chatStore'

export function ChatPanel() {
  const [promptExpanded, setPromptExpanded] = useState(false)
  const notice = useChatStore(s => s.sessionError)
  return (
    <div className="flex flex-col h-full min-h-0 min-w-0" style={{ background: 'var(--apexa-panel-bg)' }}>
      <div style={{ display: promptExpanded ? 'none' : 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        <MessageList />
      </div>
      {notice && <p role="status" className="shrink-0 px-3 py-1 text-xs" style={{ color: 'var(--apexa-text-muted)' }}>
        {notice} <span>Open Chats for session recovery.</span>
      </p>}
      <ChatInput expanded={promptExpanded} onExpandedChange={setPromptExpanded} />
    </div>
  )
}
