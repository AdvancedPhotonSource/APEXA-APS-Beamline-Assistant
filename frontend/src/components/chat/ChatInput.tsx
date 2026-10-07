import { useState, useRef, type KeyboardEvent, type ClipboardEvent, type DragEvent } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { useImageStore } from '@/stores/imageStore'
import { useConnectionStore } from '@/stores/connectionStore'
import { uploadFile } from '@/api/endpoints'

interface Attachment {
  name: string
  path: string
  isImage: boolean
}

const IMAGE_EXTS = ['.tif', '.tiff', '.ge', '.ge1', '.ge2', '.ge3', '.ge4', '.ge5',
  '.h5', '.hdf5', '.hdf', '.nxs', '.zip', '.png', '.jpg', '.jpeg', '.cbf']

function isImageName(name: string): boolean {
  const lower = name.toLowerCase()
  return IMAGE_EXTS.some((e) => lower.endsWith(e))
}

export function ChatInput() {
  const [text, setText] = useState('')
  const [focused, setFocused] = useState(false)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { sendMessage, isLoading } = useChatStore()
  const loadImage = useImageStore((s) => s.loadImage)
  const selectedModel = useConnectionStore((s) => s.selectedModel)

  const handleFiles = async (files: FileList | File[]) => {
    const list = Array.from(files)
    if (list.length === 0) return
    setUploading(true)
    try {
      for (const f of list) {
        try {
          const res = await uploadFile(f)
          const att: Attachment = { name: res.filename, path: res.path, isImage: isImageName(res.filename) }
          setAttachments((a) => [...a, att])
          // Show images immediately in the viewer/canvas so the user sees what they attached.
          if (att.isImage) loadImage(res.path).catch(() => {})
        } catch (e) {
          console.error('upload failed', e)
        }
      }
    } finally {
      setUploading(false)
    }
  }

  const removeAttachment = (path: string) =>
    setAttachments((a) => a.filter((x) => x.path !== path))

  const handleSend = () => {
    const trimmed = text.trim()
    // Note: no isLoading guard — the backend /ws loop serializes turns, so a
    // message sent mid-turn is queued and runs after the current one finishes.
    if (!trimmed && attachments.length === 0) return
    // Compose the message with explicit attachment paths so the agent grounds on
    // the real files (and the anti-hallucination path-resolution works).
    let msg = trimmed
    if (attachments.length > 0) {
      const refs = attachments.map((a) => `- ${a.path}`).join('\n')
      const head = trimmed || 'Use the attached file(s):'
      msg = `${head}\n\nAttached file(s):\n${refs}`
    }
    sendMessage(msg)
    setText('')
    setAttachments([])
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = () => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
    }
  }

  const handlePaste = (e: ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files)
    if (files.length > 0) {
      e.preventDefault()
      handleFiles(files)
    }
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length > 0) handleFiles(e.dataTransfer.files)
  }

  return (
    <div className="px-5 pb-4 pt-3" style={{ background: 'transparent' }}>
      {/* Centered, max-width column so the input lines up with the messages */}
      <div className="mx-auto w-full max-w-3xl">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => { if (e.target.files) handleFiles(e.target.files); e.target.value = '' }}
      />

      <div
        className="relative rounded-2xl border transition-all duration-200"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        style={{
          borderColor: (dragOver || focused) ? 'var(--apexa-accent)' : 'var(--apexa-border)',
          background: dragOver ? 'var(--apexa-accent-soft)' : 'var(--apexa-surface-2)',
          boxShadow: focused
            ? '0 0 0 3px var(--apexa-accent-soft), var(--apexa-glow)'
            : 'var(--apexa-elev-1)',
        }}
      >
        {/* Attachment chips */}
        {(attachments.length > 0 || uploading) && (
          <div className="flex flex-wrap gap-1.5 px-4 pt-3">
            {attachments.map((a) => (
              <span
                key={a.path}
                className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px]"
                style={{ background: 'var(--apexa-surface-3)', color: 'var(--apexa-text)', border: '1px solid var(--apexa-border)' }}
                title={a.path}
              >
                <span>{a.isImage ? '🖼️' : '📄'}</span>
                <span style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                <button
                  onClick={() => removeAttachment(a.path)}
                  className="opacity-60 hover:opacity-100"
                  style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0 }}
                  title="Remove"
                >✕</button>
              </span>
            ))}
            {uploading && (
              <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px]"
                style={{ color: 'var(--apexa-text-muted)' }}>
                uploading…
              </span>
            )}
          </div>
        )}

        <div className="flex gap-2 items-end px-4 py-3">
          {/* Attach button */}
          <button
            onClick={() => fileInputRef.current?.click()}
            title="Attach image or data file"
            className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-150"
            style={{ background: 'var(--apexa-surface-3)', color: 'var(--apexa-text-muted)', cursor: 'pointer' }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>

          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => { setText(e.target.value); handleInput() }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={isLoading
              ? 'Working… type to queue the next command (Enter sends)'
              : 'Ask APEXA — or attach / drag / paste an image or data file…'}
            rows={1}
            className="flex-1 bg-transparent text-sm resize-none outline-none max-h-[200px] leading-relaxed"
            style={{ color: 'var(--apexa-text)' }}
          />
          <button
            onClick={handleSend}
            disabled={!text.trim() && attachments.length === 0}
            title={isLoading ? 'Queue this command (runs after the current turn)' : 'Send'}
            className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-150"
            style={{
              background: (text.trim() || attachments.length > 0) ? 'var(--apexa-accent-grad)' : 'var(--apexa-surface-3)',
              color: (text.trim() || attachments.length > 0) ? 'white' : 'var(--apexa-text-muted)',
              boxShadow: (text.trim() || attachments.length > 0) ? 'var(--apexa-glow)' : 'none',
              opacity: (!text.trim() && attachments.length === 0) ? 0.4 : 1,
              cursor: (!text.trim() && attachments.length === 0) ? 'default' : 'pointer',
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </div>
      </div>
      <div className="flex items-center justify-between mt-1.5 px-1">
        <div className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--apexa-text-muted)' }}>
          <span className="px-1.5 py-0.5 rounded bg-[var(--apexa-surface-3)] font-mono text-[9px]">{selectedModel || '…'}</span>
          <span className="opacity-60">via Argo</span>
        </div>
        <div className="text-[10px]" style={{ color: 'var(--apexa-text-muted)' }}>
          Enter to send · Shift+Enter newline · 📎 / drag / paste to attach
        </div>
      </div>
      </div>
    </div>
  )
}
