import { useEffect, useRef } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { MessageBubble } from './MessageBubble'
import { TypingIndicator } from './TypingIndicator'

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
    <div style={{ flex: 1, overflowY: 'auto' }}>
      {/* Centered, max-width conversation column (ChatGPT/Claude layout) */}
      <div className="mx-auto w-full max-w-3xl">
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

  const examples = [
    { icon: '\uD83D\uDD2C', text: 'List files in the current directory', cat: 'Files' },
    { icon: '\uD83D\uDCD0', text: 'Calculate d-spacing for CeO2 at 61.332 keV', cat: 'Calculate' },
    { icon: '\uD83C\uDFAF', text: 'Calibrate detector using CeO2 standard', cat: 'Calibrate' },
    { icon: '\uD83D\uDCCA', text: 'Integrate the diffraction image to 1D', cat: 'Integrate' },
    { icon: '\uD83E\uDDE0', text: 'What is HEDM and how does it work?', cat: 'Learn' },
    { icon: '\u2699\uFE0F', text: 'Show motor positions', cat: 'Motors' },
  ]

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-8 py-12 animate-fade-in">
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
        Your AI assistant for synchrotron beamline experiments.
        Analyze diffraction data, calibrate detectors, run HEDM workflows, and control motors.
      </p>

      <div className="grid grid-cols-2 gap-2.5 max-w-lg w-full">
        {examples.map((ex, i) => (
          <button
            key={ex.text}
            onClick={() => sendMessage(ex.text)}
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
              <span className="text-base">{ex.icon}</span>
              <span className="text-[9px] font-semibold uppercase tracking-wider opacity-40">{ex.cat}</span>
            </div>
            <div className="text-[12px] leading-snug opacity-80 group-hover:opacity-100 transition-opacity">
              {ex.text}
            </div>
          </button>
        ))}
      </div>

      <div className="mt-8 flex items-center gap-4 text-[10px]" style={{ color: 'var(--apexa-text-muted)' }}>
        <span className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/60" />
          Powered by Argo AI Gateway
        </span>
        <span className="opacity-30">|</span>
        <span>Enter to send, Shift+Enter for new line</span>
      </div>
    </div>
  )
}
