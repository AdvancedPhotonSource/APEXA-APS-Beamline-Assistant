import { useEffect, useRef, useState } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { MessageBubble } from './MessageBubble'
import { TypingIndicator } from './TypingIndicator'
import { useVizStore } from '@/stores/vizStore'
import { suggestedWorkflows, workflowPrompt } from '@/lib/workflowCatalog'

export function MessageList() {
  const sessionId = useChatStore(s => s.currentSessionId)
  return <SessionMessages key={sessionId} />
}

export const CHAT_PAGE_SIZE = 60

function SessionMessages() {
  const messages = useChatStore((s) => s.messages)
  const isLoading = useChatStore((s) => s.isLoading)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const followBottom = useRef(true)
  // null follows the newest page; an explicit index stays anchored while a new
  // answer arrives. Paging bounds Markdown/card DOM without variable-height guesses.
  const [anchor, setAnchor] = useState<number | null>(null)
  const latestStart = Math.max(0, messages.length - CHAT_PAGE_SIZE)
  const start = Math.min(anchor ?? latestStart, latestStart)
  const end = Math.min(messages.length, start + CHAT_PAGE_SIZE)
  const latest = anchor == null
  const move = (next: number | null) => {
    setAnchor(next)
    followBottom.current = next == null
    scrollRef.current?.scrollTo({ top: 0 })
  }

  useEffect(() => {
    if (latest && followBottom.current) bottomRef.current?.scrollIntoView({ behavior: 'instant' })
  }, [messages.length, isLoading, latest])

  if (messages.length === 0 && !isLoading) {
    return <WelcomeScreen />
  }

  return (
    <div ref={scrollRef} data-testid="chat-scroll" onScroll={e => {
      const el = e.currentTarget
      followBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100
    }} style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
      {messages.length > CHAT_PAGE_SIZE && <nav aria-label="Conversation pages" className="sticky top-0 z-10 flex items-center gap-2 p-2" style={{ background: 'var(--apexa-bg)' }}>
        <button className="apexa-gallery-button" disabled={start === 0} onClick={() => move(Math.max(0, start - CHAT_PAGE_SIZE))}>Older</button>
        <span className="flex-1 text-center text-xs">Messages {start + 1}–{end} of {messages.length}</span>
        <button className="apexa-gallery-button" disabled={end >= messages.length} onClick={() => move(start + CHAT_PAGE_SIZE >= latestStart ? null : start + CHAT_PAGE_SIZE)}>Newer</button>
        <button className="apexa-gallery-button" disabled={latest} onClick={() => move(null)}>Latest</button>
      </nav>}
      <div className="w-full min-w-0">
        {messages.slice(start, end).map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        {latest && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

function WelcomeScreen() {
  const sendMessage = useChatStore((s) => s.sendMessage)
  const selectedType = useVizStore(s => s.artifacts.find(a => a.id === s.activeId)?.type)
  const examples = suggestedWorkflows(selectedType)

  return (
    <div className="apexa-chat-welcome flex-1 min-h-0 overflow-y-auto px-5 py-6 animate-fade-in">
      <div className="flex min-h-full flex-col items-center justify-center">
      <div className="relative mb-6">
        <div className="text-[56px] font-extrabold tracking-tight"
          style={{
            background: 'linear-gradient(135deg, #3b82f6 0%, #8b5cf6 40%, #06b6d4 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}>
          APEXA
        </div>
        <div className="absolute -inset-4 rounded-full opacity-20 blur-2xl"
          style={{ background: 'linear-gradient(135deg, #3b82f6, #8b5cf6, #06b6d4)' }} />
      </div>

      <p className="text-sm text-center max-w-md mb-8 leading-relaxed" style={{ color: 'var(--apexa-text-muted)' }}>
        Open your data, inspect its metadata, then choose an analysis.
        Images, structures and results appear in the Canvas beside this conversation.
      </p>

      <div className="apexa-chat-examples grid gap-2.5 max-w-4xl w-full">
        {examples.map((ex, i) => (
          <button
            key={ex.id}
            onClick={() => sendMessage(workflowPrompt(ex))}
            className="group text-left p-3.5 rounded-xl border transition-all duration-200 animate-fade-in"
            style={{
              animationDelay: `${i * 60}ms`,
              animationFillMode: 'backwards',
              borderColor: 'var(--apexa-border)',
              background: 'var(--apexa-surface)',
              color: 'var(--apexa-text-2)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = '#3b82f6'
              e.currentTarget.style.boxShadow = '0 0 20px rgba(59,130,246,0.12)'
              e.currentTarget.style.transform = 'translateY(-2px)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--apexa-border)'
              e.currentTarget.style.boxShadow = 'none'
              e.currentTarget.style.transform = 'none'
            }}
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider opacity-70">{ex.inspectOnly ? 'Inspect / review' : 'Prepare a plan'}</span>
            </div>
            <div className="text-[12px] leading-snug opacity-80 group-hover:opacity-100 transition-opacity">
              <span className="block font-semibold mb-1">{ex.name}</span>
              {ex.description}
            </div>
          </button>
        ))}
      </div>

      <div className="mt-6 text-center text-[11px]" style={{ color: 'var(--apexa-text-muted)' }}>
        Open Data to browse files, Analyze for more techniques, or attach a file below.
      </div>
      </div>
    </div>
  )
}
