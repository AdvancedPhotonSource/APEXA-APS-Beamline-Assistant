import { useEffect, useState } from 'react'
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
 * Report an issue against ONE answer.
 *
 * The context panel is the point of this dialog. "It gave a wrong answer" is
 * unactionable a week later; the same words plus the model, the engine that ran,
 * and the tool calls in that turn is a bug report. So APEXA attaches that
 * automatically — and shows it, expanded on request, with a switch to drop it,
 * because a beamline path can name an unpublished experiment and the user is the
 * only one who knows whether that matters.
 */
export function FeedbackDialog({
  messageId, sessionId, model, initialKind = 'report', onClose,
}: {
  messageId: string
  sessionId?: string
  model?: string
  initialKind?: FeedbackKind
  onClose: () => void
}) {
  const [category, setCategory] = useState<FeedbackCategory>('wrong_result')
  const [comment, setComment] = useState('')
  const [ctx, setCtx] = useState<FeedbackContext | null>(null)
  const [attach, setAttach] = useState(true)
  const [showCtx, setShowCtx] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { fetchFeedbackContext().then(setCtx).catch(() => setCtx(null)) }, [])
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  async function submit() {
    if (!comment.trim()) { setErr('Please describe what went wrong.'); return }
    setBusy(true); setErr(null)
    const r = await sendFeedback({
      kind: initialKind === 'report' ? 'report' : initialKind,
      category, comment, session_id: sessionId, message_id: messageId, model,
      context: attach ? (ctx ?? {}) : {},
    })
    setBusy(false)
    if (r.ok) { setDone(r.id ?? ''); setTimeout(onClose, 1400) }
    else setErr(r.error ?? 'Could not save feedback.')
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
         onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl bg-[var(--apexa-surface-1)] shadow-2xl border border-[var(--apexa-border)]"
           onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-[var(--apexa-border)] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--apexa-text)]">Report an issue</h2>
          <button onClick={onClose} aria-label="Close"
                  className="text-[var(--apexa-text-muted)] hover:text-[var(--apexa-text)] bg-transparent border-none cursor-pointer text-lg leading-none">×</button>
        </div>

        {done !== null ? (
          <div className="px-5 py-8 text-center text-sm text-[var(--apexa-text)]">
            Thanks — saved locally{done ? <> as <code className="text-xs">{done}</code></> : null}.
            <div className="mt-1 text-xs text-[var(--apexa-text-muted)]">
              Nothing was sent off this machine.
            </div>
          </div>
        ) : (
          <div className="px-5 py-4 space-y-3">
            <div>
              <label className="block text-xs text-[var(--apexa-text-muted)] mb-1.5">What kind of problem?</label>
              <div className="grid grid-cols-2 gap-1.5">
                {CATEGORIES.map((c) => (
                  <button key={c.value} onClick={() => setCategory(c.value)} title={c.hint}
                    className={`text-left text-xs px-2.5 py-1.5 rounded-md border cursor-pointer transition-colors ${
                      category === c.value
                        ? 'border-blue-500 bg-blue-500/10 text-[var(--apexa-text)]'
                        : 'border-[var(--apexa-border)] bg-transparent text-[var(--apexa-text-muted)] hover:bg-[var(--apexa-surface-3)]'}`}>
                    {c.label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs text-[var(--apexa-text-muted)] mb-1.5">
                What happened, and what did you expect?
              </label>
              <textarea
                autoFocus value={comment} onChange={(e) => setComment(e.target.value)}
                rows={4} maxLength={8000}
                placeholder="e.g. calibration returned Lsd 595 mm; the setup is 900 mm"
                className="w-full text-xs rounded-md bg-[var(--apexa-surface-2)] border border-[var(--apexa-border)] p-2 text-[var(--apexa-text)] outline-none focus:border-blue-500 resize-y"
              />
            </div>

            <div className="rounded-md border border-[var(--apexa-border)] bg-[var(--apexa-surface-2)]">
              <div className="flex items-center justify-between px-2.5 py-1.5">
                <label className="flex items-center gap-2 text-xs text-[var(--apexa-text)] cursor-pointer">
                  <input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} />
                  Attach diagnostic context
                </label>
                <button onClick={() => setShowCtx((v) => !v)}
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
                Model, servers, package versions and the tool calls from this turn.
                Your home path and e-mail addresses are masked. Saved to
                <code className="mx-1">~/.apexa/feedback</code>on this machine only.
              </div>
            </div>

            {err && <div className="text-xs text-red-400">{err}</div>}

            <div className="flex justify-end gap-2 pt-1">
              <button onClick={onClose}
                className="text-xs px-3 py-1.5 rounded-md border border-[var(--apexa-border)] bg-transparent text-[var(--apexa-text-muted)] cursor-pointer hover:bg-[var(--apexa-surface-3)]">
                Cancel
              </button>
              <button onClick={submit} disabled={busy}
                className="text-xs px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-500 text-white border-none cursor-pointer disabled:opacity-50">
                {busy ? 'Saving…' : 'Send report'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
