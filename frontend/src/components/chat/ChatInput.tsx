import { useState, useRef, useCallback, useEffect, useLayoutEffect, type KeyboardEvent, type ClipboardEvent, type DragEvent } from 'react'
import { useChatStore } from '@/stores/chatStore'
import { useImageStore } from '@/stores/imageStore'
import { useVizStore } from '@/stores/vizStore'
import { isHtmlReport, reportFileArtifact } from '@/lib/reportArtifacts'
import { uploadFile } from '@/api/endpoints'

import type { SessionAttachment } from '@/api/types'

const IMAGE_EXTS = ['.tif', '.tiff', '.ge', '.ge1', '.ge2', '.ge3', '.ge4', '.ge5',
  '.h5', '.hdf5', '.hdf', '.nxs', '.zip', '.png', '.jpg', '.jpeg', '.cbf']

function isImageName(name: string): boolean {
  const lower = name.toLowerCase()
  return IMAGE_EXTS.some((e) => lower.endsWith(e))
}

export function ChatInput({ expanded, onExpandedChange }: {
  expanded: boolean
  onExpandedChange: (expanded: boolean) => void
}) {
  const text = useChatStore(s => s.draft)
  const setText = useChatStore(s => s.setDraft)
  const attachments = useChatStore(s => s.attachments)
  const setAttachments = useChatStore(s => s.setAttachments)
  const sessionSync = useChatStore(s => s.sessionSync)
  const currentSessionId = useChatStore(s => s.currentSessionId)
  const [focused, setFocused] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const uploadCount = useRef(0)
  const [dragOver, setDragOver] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const isLoading = useChatStore((s) => s.isLoading)
  const loadImage = useImageStore((s) => s.loadImage)

  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    // CSS bounds the normal composer; the expanded editor fills its pane.
    if (!expanded) ta.style.height = `${ta.scrollHeight}px`
  }, [expanded])

  useLayoutEffect(resizeTextarea, [text, resizeTextarea])
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    let previousWidth = -1
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width === previousWidth) return
      previousWidth = entry.contentRect.width
      resizeTextarea()
    })
    observer.observe(ta)
    return () => observer.disconnect()
  }, [resizeTextarea])

  const toggleExpanded = () => {
    onExpandedChange(!expanded)
    textareaRef.current?.focus()
  }

  const handleFiles = async (files: FileList | File[]) => {
    const list = Array.from(files)
    if (list.length === 0) return
    uploadCount.current += 1
    setUploading(true)
    setUploadError('')
    try {
      for (const f of list) {
        try {
          const res = await uploadFile(f)
          const att: SessionAttachment = { name: res.filename, path: res.path, isImage: isImageName(res.filename) }
          // An upload belongs to the conversation that accepted it.
          if (useChatStore.getState().currentSessionId !== currentSessionId) {
            setUploadError(`Uploaded ${att.name} to ${att.path}; reopen the original session to attach it.`)
            continue
          }
          setAttachments([...useChatStore.getState().attachments, att])
          // Show images immediately in the viewer/canvas so the user sees what they attached.
          if (att.isImage) loadImage(res.path).catch(() => {})
          if (isHtmlReport(att.name)) {
            useVizStore.getState().addArtifact(reportFileArtifact(res.path, '', 'local', att.name))
            useVizStore.getState().showCanvas()
          }
        } catch (e) {
          setUploadError(`Could not attach ${f.name}: ${String(e)}`)
        }
      }
    } finally {
      uploadCount.current -= 1
      setUploading(uploadCount.current > 0)
    }
  }

  const removeAttachment = (path: string) =>
    setAttachments(attachments.filter(x => x.path !== path))

  const handleSend = () => {
    const trimmed = text.trim()
    // Note: no isLoading guard — the backend /ws loop serializes turns, so a
    // message sent mid-turn is queued and runs after the current one finishes.
    if (sessionSync === 'loading' || uploading || (!trimmed && attachments.length === 0)) return
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
    onExpandedChange(false)
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Escape' && expanded) {
      e.preventDefault()
      onExpandedChange(false)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
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
    <div className="apexa-composer" data-expanded={expanded}>
      <div className="apexa-prompt-toolbar">
        <label htmlFor="apexa-prompt" title="Enter to send · Shift+Enter for a new line">{expanded ? 'Write your prompt' : 'Prompt'}</label>
        <span className="apexa-prompt-hint">Enter ↵ · Shift+Enter for a new line</span>
        <span className="flex-1" />
        <button type="button" className="apexa-compact-button" onClick={() => fileInputRef.current?.click()}
          aria-label="Attach files" title="Attach files (or paste / drop them)" disabled={uploading}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
        </button>
        <button type="button" className="apexa-compact-button" onClick={toggleExpanded} aria-expanded={expanded} aria-controls="apexa-prompt"
          aria-label={expanded ? 'Back to conversation' : 'Expand prompt'} title={expanded ? 'Back to conversation (Esc)' : 'Expand prompt'}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d={expanded ? 'M3 9h6V3M15 3v6h6M21 15h-6v6M9 21v-6H3' : 'M9 3H3v6M15 3h6v6M21 15v6h-6M9 21H3v-6'} />
          </svg>
        </button>
        <button type="button" className="apexa-compact-button apexa-send-button" onClick={handleSend}
          disabled={sessionSync === 'loading' || uploading || (!text.trim() && attachments.length === 0)}
          aria-label={isLoading ? 'Queue message' : 'Send message'}
          title={uploading ? 'Wait for attachments to finish uploading' : isLoading ? 'Queue message (Enter)' : 'Send message (Enter)'}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 19V5m-6 6 6-6 6 6" />
          </svg>
        </button>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => { if (e.target.files) handleFiles(e.target.files); e.target.value = '' }}
      />

      <div
        className="apexa-composer-editor relative rounded-xl border transition-colors duration-150"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        style={{
          borderColor: (dragOver || focused) ? 'var(--apexa-accent)' : 'var(--apexa-border)',
          background: dragOver ? 'var(--apexa-accent-soft)' : 'var(--apexa-surface-2)',
          boxShadow: 'none',
        }}
      >
        {uploadError && <p role="alert" className="shrink-0 px-4 pt-2 text-xs" style={{ color: 'var(--apexa-text)' }}>{uploadError}</p>}
        {/* Attachment chips */}
        {(attachments.length > 0 || uploading) && (
          <div className="flex flex-wrap shrink-0 gap-1.5 px-4 pt-3 max-h-24 overflow-y-auto">
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

        <textarea
          id="apexa-prompt"
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={isLoading
            ? 'Working… write the next request here. Enter queues it.'
            : 'Ask APEXA about your data, describe an analysis, or attach a file…'}
          rows={2}
          className="apexa-prompt-textarea bg-transparent text-[14px] resize-none outline-none leading-relaxed"
          style={{ color: 'var(--apexa-text)' }}
        />
      </div>
    </div>
  )
}
