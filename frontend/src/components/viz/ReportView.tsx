import { useState } from 'react'
import { useChatStore } from '@/stores/chatStore'

/**
 * A run report, rendered live in the Canvas.
 *
 * The same ReportSpec also renders to a standalone HTML file (apexa_report.py),
 * which is the artifact a scientist keeps and shares. The difference is here:
 * in a file that opens off disk, an action can only be copied to the clipboard,
 * because there is nothing to call back to. Inside APEXA there is, so each
 * action is a button that runs it.
 *
 * Styling uses var(--apexa-*) so it follows the light/midnight themes. The two
 * report accents (teal / copper) are defined locally because no semantic state
 * token exists yet, and they deliberately match the HTML renderer's palette
 * from Reconstruction_Reports.md §7 — the two surfaces should look like one
 * report, not two.
 */

const TEAL = '#0e7c86'
const TEAL_D = '#33aab9'
const COPPER = '#b06f33'
const COPPER_D = '#d69157'
const BAD = '#a8392b'

type Severity = 'solid' | 'warn' | 'roadmap'

interface Finding { title: string; body: string; severity: Severity; tag?: string }
interface Action { label: string; instruction: string; why?: string }
interface Tile { key: string; value: string; sub?: string }
interface Plate { key: string; title: string; subtitle?: string; png_b64: string }
interface LogbookEntry { text: string; timestamp?: string | null }
interface Logbook {
  entries?: LogbookEntry[]
  source?: string
  host?: string | null
  truncated?: boolean
  error?: string | null
  summary?: string
}
export interface ReportData {
  title?: string
  subtitle?: string
  eyebrow?: string
  header_pairs?: [string, string][]
  tiles?: Tile[]
  findings?: Finding[]
  actions?: Action[]
  plates?: Plate[]
  logbook?: Logbook
  provenance?: { name: string; status?: string; elapsed_s?: number | string }[]
  verdict?: 'pass' | 'look' | 'fail' | 'unknown'
  verdict_note?: string
  source_path?: string
  generated_at?: string
}

const VERDICT: Record<string, { label: string; color: string; sub: string }> = {
  pass: { label: 'PASS', color: TEAL, sub: 'accepted, and nothing flagged' },
  look: { label: 'LOOK', color: COPPER, sub: 'accepted — but read the flags before using this' },
  fail: { label: 'FAIL', color: BAD, sub: 'not usable as a result' },
  unknown: { label: '—', color: 'var(--apexa-text-muted)', sub: 'no status recorded' },
}

function useDark() {
  // Matches how the rest of the app resolves the three states: an explicit
  // stamp wins, otherwise the OS preference decides.
  if (typeof document === 'undefined') return false
  const stamped = document.documentElement.getAttribute('data-theme')
  if (stamped === 'dark') return true
  if (stamped === 'light') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

function Section({ title, kicker, children }: {
  title: string; kicker?: string; children: React.ReactNode
}) {
  return (
    <section style={{ marginTop: 34 }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap',
        marginBottom: 14, paddingBottom: 9,
        borderBottom: '1px solid var(--apexa-border)',
      }}>
        <h2 style={{ fontSize: 17, margin: 0, fontWeight: 700, color: 'var(--apexa-text)' }}>{title}</h2>
        {kicker && (
          <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 11.5, color: TEAL }}>
            {kicker}
          </span>
        )}
      </div>
      {children}
    </section>
  )
}

function FindingCard({ f, dark }: { f: Finding; dark: boolean }) {
  const warn = f.severity === 'warn'
  const accent = warn ? (dark ? COPPER_D : COPPER) : (dark ? TEAL_D : TEAL)
  return (
    <div style={{
      border: '1px solid var(--apexa-border)', borderLeft: `3px solid ${accent}`,
      borderRadius: '0 10px 10px 0', background: 'var(--apexa-surface)', padding: '13px 16px',
    }}>
      {f.tag && (
        <span style={{
          display: 'inline-block', fontFamily: 'ui-monospace, Menlo, monospace',
          fontSize: 10.5, padding: '2px 7px', borderRadius: 5, marginBottom: 7,
          background: `color-mix(in srgb, ${accent} 18%, transparent)`, color: accent,
        }}>{f.tag}</span>
      )}
      <h3 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 650, color: 'var(--apexa-text)' }}>
        {f.title}
      </h3>
      {f.body && (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--apexa-text-2, var(--apexa-text-muted))' }}>
          {f.body}
        </p>
      )}
    </div>
  )
}

function ActionCard({ a, dark }: { a: Action; dark: boolean }) {
  const send = useChatStore((s) => s.sendMessage)
  const [sent, setSent] = useState(false)
  const accent = dark ? COPPER_D : COPPER
  return (
    <div style={{
      border: '1px solid var(--apexa-border)', borderLeft: `3px solid ${accent}`,
      borderRadius: '0 10px 10px 0', background: 'var(--apexa-surface)', padding: '13px 16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 650, color: 'var(--apexa-text)' }}>{a.label}</h3>
        <button
          onClick={() => { send(a.instruction); setSent(true); setTimeout(() => setSent(false), 2200) }}
          style={{
            fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 11.5, padding: '5px 12px',
            borderRadius: 7, border: '1px solid var(--apexa-border)', background: 'transparent',
            color: accent, cursor: 'pointer', whiteSpace: 'nowrap',
          }}
        >{sent ? 'sent' : 'run this'}</button>
      </div>
      <pre style={{
        margin: '9px 0 0', padding: '10px 12px', background: 'var(--apexa-surface-2)',
        border: '1px solid var(--apexa-border)', borderRadius: 8,
        fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12,
        color: 'var(--apexa-text)', whiteSpace: 'pre-wrap', overflowX: 'auto',
      }}>{a.instruction}</pre>
      {a.why && (
        <p style={{ margin: '8px 0 0', fontSize: 12.5, color: 'var(--apexa-text-muted)' }}>{a.why}</p>
      )}
    </div>
  )
}

export function ReportView({ data }: { data: ReportData }) {
  const dark = useDark()
  const d = data || {}
  const v = VERDICT[d.verdict ?? 'unknown'] ?? VERDICT.unknown
  const findings = d.findings ?? []
  const solid = findings.filter((f) => f.severity === 'solid')
  const warn = findings.filter((f) => f.severity === 'warn')
  const road = findings.filter((f) => f.severity === 'roadmap')
  const grid: React.CSSProperties = {
    display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(270px, 1fr))', gap: 12,
  }

  return (
    <div style={{
      padding: 'clamp(16px, 3vw, 28px)', height: '100%', overflowY: 'auto',
      background: 'var(--apexa-panel-bg, var(--apexa-surface))', color: 'var(--apexa-text)',
    }}>
      <div style={{
        fontSize: 11.5, letterSpacing: '.15em', textTransform: 'uppercase',
        color: dark ? TEAL_D : TEAL, fontWeight: 650,
      }}>{d.eyebrow ?? 'APEXA · Run Report'}</div>
      <h1 style={{
        fontSize: 'clamp(22px, 3vw, 31px)', lineHeight: 1.1, margin: '.25em 0 .1em',
        letterSpacing: '-.02em', textWrap: 'balance', fontWeight: 750,
      }}>{d.title ?? 'Run report'}</h1>
      {d.subtitle && (
        <p style={{ margin: 0, fontSize: 14.5, color: 'var(--apexa-text-muted)', maxWidth: '72ch' }}>
          {d.subtitle}
        </p>
      )}

      <div style={{
        display: 'flex', alignItems: 'center', gap: 15, margin: '20px 0 0',
        padding: '14px 18px', border: '1px solid var(--apexa-border)',
        borderLeft: `5px solid ${v.color}`, borderRadius: 12, background: 'var(--apexa-surface)',
      }}>
        <span style={{
          fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 23, fontWeight: 750,
          letterSpacing: '.04em', color: v.color,
        }}>{v.label}</span>
        <span style={{ fontSize: 13.5, color: 'var(--apexa-text-2, var(--apexa-text-muted))' }}>
          {d.verdict_note || v.sub}
        </span>
      </div>

      {!!d.header_pairs?.length && (
        <dl style={{
          margin: '16px 0 0', display: 'grid', gap: 1, overflow: 'hidden',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          border: '1px solid var(--apexa-border)', borderRadius: 11,
          background: 'var(--apexa-border)',
        }}>
          {d.header_pairs.map(([k, val]) => (
            <div key={k} style={{ padding: '11px 15px', background: 'var(--apexa-surface)' }}>
              <dt style={{
                fontSize: 10.5, letterSpacing: '.08em', textTransform: 'uppercase',
                color: 'var(--apexa-text-muted)', margin: '0 0 3px',
              }}>{k}</dt>
              <dd style={{
                margin: 0, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12.5,
                wordBreak: 'break-word',
              }}>{val}</dd>
            </div>
          ))}
        </dl>
      )}

      {!!d.tiles?.length && (
        <Section title="Summary" kicker="from the run manifest">
          <div style={{ ...grid, gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
            {d.tiles.map((t) => (
              <div key={t.key} style={{
                border: '1px solid var(--apexa-border)', borderRadius: 11,
                background: 'var(--apexa-surface)', padding: 15,
                borderLeft: `3px solid ${dark ? TEAL_D : TEAL}`,
              }}>
                <div style={{
                  fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase',
                  color: 'var(--apexa-text-muted)',
                }}>{t.key}</div>
                <div style={{
                  fontSize: 22, fontWeight: 720, marginTop: 5, letterSpacing: '-.02em',
                  fontVariantNumeric: 'tabular-nums',
                }}>{t.value}</div>
                {t.sub && (
                  <div style={{
                    fontSize: 11.5, color: 'var(--apexa-text-muted)', marginTop: 3,
                    fontVariantNumeric: 'tabular-nums',
                  }}>{t.sub}</div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {!!(solid.length || warn.length) && (
        <Section title="What the data says" kicker="derived from this run">
          <div style={grid}>
            {[...solid, ...warn].map((f, i) => <FindingCard key={i} f={f} dark={dark} />)}
          </div>
        </Section>
      )}

      {!!d.actions?.length && (
        <Section title="What to do next" kicker="diagnosis → lever">
          <div style={{ display: 'grid', gap: 12 }}>
            {d.actions.map((a, i) => <ActionCard key={i} a={a} dark={dark} />)}
          </div>
        </Section>
      )}

      {!!d.plates?.length && (
        <Section title="Figures" kicker={`${d.plates.length} plates`}>
          {d.plates.map((p) => (
            <figure key={p.key} style={{
              margin: '0 0 22px', border: '1px solid var(--apexa-border)',
              borderRadius: 12, background: 'var(--apexa-surface)', overflow: 'hidden',
            }}>
              <figcaption style={{
                display: 'flex', alignItems: 'baseline', gap: 11, flexWrap: 'wrap',
                padding: '13px 17px 11px', borderBottom: '1px solid var(--apexa-border)',
              }}>
                <span style={{ fontSize: 15, fontWeight: 700 }}>{p.title}</span>
                {p.subtitle && (
                  <span style={{ fontSize: 12, color: 'var(--apexa-text-muted)' }}>{p.subtitle}</span>
                )}
              </figcaption>
              {/* Plates stay on a light ground in both themes — the figures are
                  drawn on paper, and inverting the card around them misreads. */}
              <div style={{ background: '#f7f6f3', padding: 13, overflowX: 'auto' }}>
                <img
                  src={`data:image/png;base64,${p.png_b64}`}
                  alt={p.title}
                  style={{ display: 'block', width: '100%', height: 'auto', margin: '0 auto' }}
                />
              </div>
            </figure>
          ))}
        </Section>
      )}

      {!!d.logbook?.entries?.length && (
        <Section title="Logbook" kicker={`${d.logbook.entries.length} most recent · verbatim`}>
          {d.logbook.summary && (
            <div style={{
              border: '1px dashed var(--apexa-border)', borderRadius: 10,
              padding: '11px 14px', marginBottom: 12, background: 'var(--apexa-surface)',
            }}>
              <span style={{
                display: 'inline-block', fontFamily: 'ui-monospace, Menlo, monospace',
                fontSize: 10.5, padding: '2px 7px', borderRadius: 5, marginBottom: 6,
                background: `color-mix(in srgb, ${dark ? TEAL_D : TEAL} 18%, transparent)`,
                color: dark ? TEAL_D : TEAL,
              }}>generated</span>
              <p style={{ margin: 0, fontSize: 13, color: 'var(--apexa-text-muted)' }}>
                {d.logbook.summary}
              </p>
            </div>
          )}
          <div style={{ display: 'grid', gap: 9 }}>
            {d.logbook.entries.map((e, i) => (
              <div key={i} style={{
                border: '1px solid var(--apexa-border)', borderRadius: 9,
                background: 'var(--apexa-surface)', padding: '10px 13px',
              }}>
                {e.timestamp && (
                  <div style={{
                    fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 11.5,
                    color: dark ? TEAL_D : TEAL, marginBottom: 3,
                  }}>{e.timestamp}</div>
                )}
                <pre style={{
                  margin: 0, fontSize: 12.5, whiteSpace: 'pre-wrap', fontFamily: 'inherit',
                  color: 'var(--apexa-text-2, var(--apexa-text-muted))',
                }}>{e.text}</pre>
              </div>
            ))}
          </div>
          <p style={{
            fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 11,
            color: 'var(--apexa-text-muted)', marginTop: 9, wordBreak: 'break-all',
          }}>
            {(d.logbook.host ? `${d.logbook.host}:` : '') + (d.logbook.source ?? '')}
            {d.logbook.truncated ? ' · earlier entries not shown (tailed)' : ''}
          </p>
        </Section>
      )}

      {!!d.logbook?.error && !d.logbook?.entries?.length && (
        <Section title="Logbook" kicker="unavailable">
          <FindingCard dark={dark} f={{ title: 'Could not read the logbook', body: d.logbook.error, severity: 'warn' }} />
        </Section>
      )}

      {!!road.length && (
        <Section title="What to watch for" kicker="handbook halts & traps">
          <div style={grid}>{road.map((f, i) => <FindingCard key={i} f={f} dark={dark} />)}</div>
        </Section>
      )}

      {!!d.provenance?.length && (
        <Section title="What produced this" kicker="executed tool calls">
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 5 }}>
            {d.provenance.map((p, i) => (
              <li key={i} style={{
                fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12.5,
                color: 'var(--apexa-text-muted)',
              }}>
                <code style={{ color: 'var(--apexa-text)' }}>{p.name}</code>{' '}
                <span style={{ color: dark ? TEAL_D : TEAL }}>{p.status}</span>
                {p.elapsed_s !== undefined && p.elapsed_s !== '' && <span> {p.elapsed_s}s</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <footer style={{
        marginTop: 34, paddingTop: 16, borderTop: '1px solid var(--apexa-border)',
        color: 'var(--apexa-text-muted)', fontSize: 11.5,
      }}>
        {d.source_path && (
          <div style={{ fontFamily: 'ui-monospace, Menlo, monospace', wordBreak: 'break-all' }}>
            {d.source_path}
          </div>
        )}
        {d.generated_at && <div style={{ marginTop: 5 }}>Generated by APEXA · {d.generated_at}</div>}
      </footer>
    </div>
  )
}
