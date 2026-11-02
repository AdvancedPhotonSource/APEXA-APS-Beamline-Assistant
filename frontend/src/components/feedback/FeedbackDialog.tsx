import { useEffect, useId, useRef, useState } from 'react'
import {
  fetchFeedbackContext, sendFeedback,
  type FeedbackCategory, type FeedbackContext, type FeedbackKind,
} from '../../api/endpoints'

const CATEGORIES: { value: FeedbackCategory; label: string; hint: string }[] = [
  { value: 'wrong_result',    label: 'Wrong result',    hint: 'it ran, and the answer is wrong' },
  { value: 'failed_to_run',   label: 'Failed to run',   hint: 'refused or errored when it should not have' },
  { value: 'misleading',      label: 'Misleading',      hint: 'the wording led me somewhere wrong' },
  { value: 'slow',            label: 'Too slow',        hint: '' },
  { value: 'feature_request', label: 'Feature request', hint: '' },
  { value: 'other',           label: 'Other',           hint: '' },
]

/**
 * Capture an answer report or a standalone feature request.
 *
 * The context panel is the point of this dialog. "It gave a wrong answer" is
 * unactionable a week later; the same words plus the model, the engine that ran,
 * and the tool calls in that turn is a bug report. So APEXA attaches that
 * automatically — and shows it, expanded on request, with a switch to drop it,
 * because a beamline path can name an unpublished experiment and the user is the
 * only one who knows whether that matters.
 */
export function FeedbackDialog({
  messageId = '', sessionId, model, initialKind = 'report', mode = 'issue', onClose,
}: {
  messageId?: string
  sessionId?: string
  model?: string
  initialKind?: FeedbackKind
  mode?: 'issue' | 'feature'
  onClose: () => void
}) {
  const [category, setCategory] = useState<FeedbackCategory>(mode === 'feature' ? 'feature_request' : 'wrong_result')
  const isFeature = category === 'feature_request'
  const [comment, setComment] = useState('')
  const [ctx, setCtx] = useState<FeedbackContext | null>(null)
  const [attach, setAttach] = useState(true)
  const [showCtx, setShowCtx] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const commentRef = useRef<HTMLTextAreaElement>(null)
  const submitting = useRef(false)
  const titleId = useId()
  const commentId = useId()

  useEffect(() => {
    if (mode === 'feature') return
    let active = true
    fetchFeedbackContext().then(value => { if (active) setCtx(value) }).catch(() => {})
    return () => { active = false }
  }, [mode])
  useEffect(() => {
    const dialog = dialogRef.current!
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialog.showModal()
    commentRef.current?.focus()
    return () => {
      dialog.close()
      if (opener?.isConnected) opener.focus()
    }
  }, [])

  async function submit() {
    if (submitting.current || done !== null) return
    if (!comment.trim()) { setErr(isFeature ? 'Please describe the feature you would like.' : 'Please describe what went wrong.'); return }
    submitting.current = true
    setBusy(true); setErr(null)
    const r = await sendFeedback({
      kind: initialKind === 'report' ? 'report' : initialKind,
      category, comment: comment.trim(), session_id: sessionId, message_id: messageId, model,
      context: mode !== 'feature' && attach ? (ctx ?? {}) : {},
    })
    submitting.current = false
    setBusy(false)
    if (r.ok) setDone(r.id ?? '')
    else setErr(r.error ?? 'Could not save feedback.')
  }

  return (
    <dialog ref={dialogRef} aria-labelledby={titleId}
      onCancel={e => { e.preventDefault(); if (!busy) onClose() }}
      onClick={e => { if (e.target === e.currentTarget && !busy) onClose() }}
      className="m-auto w-[calc(100%_-_2rem)] max-w-lg max-h-[calc(100dvh_-_2rem)] overflow-y-auto rounded-xl bg-[var(--apexa-surface)] p-0 shadow-2xl border border-[var(--apexa-border)] backdrop:bg-black/50">
        <div className="px-5 py-3 border-b border-[var(--apexa-border)] flex items-center justify-between">
          <h2 id={titleId} className="text-sm font-semibold text-[var(--apexa-text)]">{isFeature ? 'Request a feature' : 'Report an issue'}</h2>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Close"
                  className="text-[var(--apexa-text-muted)] hover:text-[var(--apexa-text)] bg-transparent border-none cursor-pointer text-lg leading-none">×</button>
        </div>

        {done !== null ? (
          <div className="px-5 py-8 text-center text-sm text-[var(--apexa-text)]" role="status">
            {isFeature ? 'Feature request saved' : 'Report saved'}{done ? <> as <code className="text-xs">{done}</code></> : null}.
            <div className="mt-1 text-xs text-[var(--apexa-text-muted)]">
              Stored on the APEXA server for maintainers to review.
            </div>
            <button type="button" onClick={onClose}
              className="mt-4 px-3 py-1.5 rounded-md bg-[var(--apexa-accent)] text-white border-none cursor-pointer">Done</button>
          </div>
        ) : (
          <form className="px-5 py-4 space-y-3" onSubmit={e => { e.preventDefault(); void submit() }}>
            {mode !== 'feature' && <div>
              <label className="block text-xs text-[var(--apexa-text-muted)] mb-1.5">What kind of problem?</label>
              <div className="grid grid-cols-2 gap-1.5">
                {CATEGORIES.map((c) => (
                  <button type="button" key={c.value} onClick={() => setCategory(c.value)} disabled={busy} title={c.hint}
                    className={`text-left text-xs px-2.5 py-1.5 rounded-md border cursor-pointer transition-colors ${
                      category === c.value
                        ? 'border-blue-500 bg-blue-500/10 text-[var(--apexa-text)]'
                        : 'border-[var(--apexa-border)] bg-transparent text-[var(--apexa-text-muted)] hover:bg-[var(--apexa-surface-3)]'}`}>
                    {c.label}
                  </button>
                ))}
              </div>
            </div>}

            <div>
              <label htmlFor={commentId} className="block text-xs text-[var(--apexa-text-muted)] mb-1.5">
                {isFeature ? 'What would you like APEXA to do?' : 'What happened, and what did you expect?'}
              </label>
              <textarea
                ref={commentRef} id={commentId} value={comment} onChange={(e) => setComment(e.target.value)} disabled={busy}
                rows={4} maxLength={8000}
                placeholder={isFeature ? 'Describe the feature and how it would help your beamline work…' : 'e.g. calibration returned Lsd 595 mm; the setup is 900 mm'}
                className="w-full text-xs rounded-md bg-[var(--apexa-surface-2)] border border-[var(--apexa-border)] p-2 text-[var(--apexa-text)] outline-none focus:border-blue-500 resize-y"
              />
            </div>

            {mode !== 'feature' && <div className="rounded-md border border-[var(--apexa-border)] bg-[var(--apexa-surface-2)]">
              <div className="flex items-center justify-between px-2.5 py-1.5">
                <label className="flex items-center gap-2 text-xs text-[var(--apexa-text)] cursor-pointer">
                  <input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} disabled={busy} />
                  Attach diagnostic context
                </label>
                <button type="button" onClick={() => setShowCtx((v) => !v)}
                  className="text-[11px] text-blue-400 bg-transparent border-none cursor-pointer">
                  {showCtx ? 'hide' : 'show what is attached'}
                </button>
              </div>
              {showCtx && (
                <pre className="px-2.5 pb-2 text-[10px] leading-relaxed text-[var(--apexa-text-muted)] max-h-40 overflow-auto whitespace-pre-wrap">
{JSON.stringify(ctx ?? {}, null, 1)}
                </pre>
              )}
              <div className="px-2.5 pb-2 text-[10px] text-[var(--apexa-text-muted)]">
                Model, servers, package versions and recent tool-call metadata.
                Home paths and e-mail addresses are masked.
              </div>
            </div>}
            <p className="text-xs text-[var(--apexa-text-muted)]">
              Saved on the APEXA server for maintainers to review.
            </p>

            {err && <div role="alert" className="text-xs text-red-500">{err}</div>}

            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={onClose} disabled={busy}
                className="text-xs px-3 py-1.5 rounded-md border border-[var(--apexa-border)] bg-transparent text-[var(--apexa-text-muted)] cursor-pointer hover:bg-[var(--apexa-surface-3)]">
                Cancel
              </button>
              <button type="submit" disabled={busy}
                className="text-xs px-3 py-1.5 rounded-md bg-[var(--apexa-accent)] text-white border-none cursor-pointer disabled:opacity-50">
                {busy ? 'Saving…' : isFeature ? 'Save request' : 'Send report'}
              </button>
            </div>
          </form>
        )}
    </dialog>
  )
}
