import { useEffect, useRef } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { MessageBubble } from './MessageBubble'
import { TypingIndicator } from './TypingIndicator'
import { useVizStore } from '@/stores/vizStore'
import { suggestedWorkflows, workflowPrompt } from '@/lib/workflowCatalog'

export function MessageList() {
  const messages = useChatStore((s) => s.messages)
  const isLoading = useChatStore((s) => s.isLoading)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  if (messages.length === 0 && !isLoading) {
    return <WelcomeScreen />
  }

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
      <div className="w-full min-w-0">
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <TypingIndicator />
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
