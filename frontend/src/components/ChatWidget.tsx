import React, { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import EvidenceTrail from './EvidenceTrail'
import type { TrailDecision, TrailStep } from './EvidenceTrail'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Evidence {
  id: string
  claim: string
  source: string
}

interface Hedge {
  instrument: string
  side: 'short' | 'reduce'
  notional: number
  expected_offset: number
  optional: boolean
  evidence: string
}

interface ForecastMove {
  mean: number
  min: number
  max: number
  n: number
}

// Average move over the sessions after comparable past events, with the range seen.
interface Forecast {
  horizon_sessions: number
  events: number
  portfolio: { mean: number; pnl: number; worst: number; best: number; evidence: string }
  holdings: (ForecastMove & { name: string; pnl: number })[]
  holdings_evidence: string | null
  cross_assets: (ForecastMove & { label: string })[]
  cross_assets_evidence: string | null
}

interface Answer {
  text: string
  evidence: Evidence[]
  hedges: Hedge[]
  action: 'hedge' | 'monitor' | 'no_hedge' | 'none'
  forecast?: Forecast | null
  decision?: TrailDecision | null
  trail?: TrailStep[]
  gaps: string[]
  writer: 'llm' | 'template'
}

interface Step {
  status: 'done' | 'degraded'
  summary: string
  ms: number
}

type StreamEvent =
  | { type: 'start'; agents: string[]; portfolio_source: 'user' | 'sample'; holdings: number }
  | ({ type: 'agent'; agent: string } & Step)
  | ({ type: 'answer' } & Answer)
  | { type: 'error'; message: string }

interface UserMessage {
  id: number
  role: 'user'
  text: string
}

interface AssistantMessage {
  id: number
  role: 'assistant'
  steps: Record<string, Step>
  answer?: Answer
  error?: string
}

type Message = UserMessage | AssistantMessage

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const BACKEND_URL: string =
  import.meta.env.VITE_BACKEND_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

// The pipeline, in the order it runs. Agents in the same row run in parallel.
const PIPELINE: { id: string; label: string }[][] = [
  [{ id: 'supervisor', label: 'Supervisor' }],
  [
    { id: 'sentiment', label: 'Sentiment' },
    { id: 'weather_macro', label: 'Weather / Macro' },
    { id: 'historical', label: 'Historical' },
  ],
  [{ id: 'risk', label: 'Risk' }],
  [{ id: 'hedging', label: 'Hedging' }],
  [{ id: 'synthesiser', label: 'Synthesiser' }],
]

const SUGGESTIONS = [
  'How would a severe cyclone on the Gujarat coast affect my holdings?',
  'What happens to my portfolio if the RBI hikes rates unexpectedly?',
  'What if crude oil prices spike?',
  'How risky is my portfolio right now?',
]

const ACTION_LABELS: Record<Answer['action'], { text: string; className: string }> = {
  hedge: { text: 'Hedge recommended', className: 'bg-red-50 text-red-600' },
  monitor: { text: 'Monitor', className: 'bg-amber-50 text-amber-700' },
  no_hedge: { text: 'No hedge needed', className: 'bg-groww-green-light text-groww-green' },
  none: { text: 'No hedge sized', className: 'bg-gray-100 text-groww-text-secondary' },
}

const formatInr = (value: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)

// ---------------------------------------------------------------------------
// Answer text: "**bold**", "## heading", "- bullet" and [E1, E2] citations
// ---------------------------------------------------------------------------
const INLINE = /(\*\*[^*]+\*\*|\[[A-Z]\d+(?:\s*,\s*[A-Z]\d+)*\])/

const Inline: React.FC<{ text: string; onCite: (id: string) => void; active: string | null }> = ({
  text,
  onCite,
  active,
}) => (
  <>
    {text.split(INLINE).map((part, i) => {
      if (part.startsWith('**')) {
        return <strong key={i}>{part.slice(2, -2)}</strong>
      }
      if (/^\[[A-Z]\d/.test(part)) {
        return part
          .slice(1, -1)
          .split(/\s*,\s*/)
          .map((id) => (
            <button
              key={`${i}-${id}`}
              onClick={() => onCite(id)}
              className={`mx-0.5 px-1.5 rounded text-[10px] font-semibold align-middle transition-colors duration-150 ${
                active === id
                  ? 'bg-groww-green text-white'
                  : 'bg-groww-green-light text-groww-green hover:bg-groww-green hover:text-white'
              }`}
              aria-label={`Show evidence ${id}`}
            >
              {id}
            </button>
          ))
      }
      return <React.Fragment key={i}>{part}</React.Fragment>
    })}
  </>
)

// ---------------------------------------------------------------------------
// Forecast: expected move per holding and per commodity, shown whatever the
// hedge decision. Bar = average across the past events, line = lowest to highest.
// ---------------------------------------------------------------------------
const FORECAST_ROWS = 6
const RANGE_ROOM = 2.5 // how far past the largest average the scale extends
const signedPct = (fraction: number) => {
  const value = (fraction * 100).toFixed(1)
  return Number(value) === 0 ? '0.0%' : `${fraction > 0 ? '+' : ''}${value}%`
}
const signedInr = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${formatInr(Math.abs(value))}`

const ForecastGroup: React.FC<{
  title: string
  evidenceId: string | null
  rows: (ForecastMove & { label: string; amount?: number })[]
  horizon: number
  onCite: (id: string) => void
  active: string | null
}> = ({ title, evidenceId, rows, horizon, onCite, active }) => {
  const [all, setAll] = useState(false)
  // One scale for the group so bars compare across rows. It is set by the averages;
  // a range that runs past it is cut at the edge and marked with an arrow.
  const widest = Math.max(...rows.flatMap((r) => [Math.abs(r.min), Math.abs(r.max)]), 0.001)
  const reach = Math.min(widest, Math.max(...rows.map((r) => Math.abs(r.mean)), 0.001) * RANGE_ROOM)
  const at = (fraction: number) => 50 + (Math.max(-reach, Math.min(reach, fraction)) / reach) * 50
  const shown = all ? rows : rows.slice(0, FORECAST_ROWS)
  return (
    <div>
      <p className="flex items-center gap-1 text-[11px] font-semibold text-groww-text-secondary">
        {title}
        {evidenceId && <Inline text={`[${evidenceId}]`} onCite={onCite} active={active} />}
      </p>
      <ul className="mt-1 flex flex-col gap-1">
        {shown.map((row) => {
          const down = row.mean < 0
          return (
            <li
              key={row.label}
              className="flex items-center gap-2 text-[11px] tabular-nums"
              title={`${row.label}: average ${signedPct(row.mean)} over ${horizon} sessions${
                row.amount === undefined ? '' : ` (${signedInr(row.amount)})`
              }; lowest ${signedPct(row.min)}, highest ${signedPct(row.max)} across ${row.n} past event${row.n === 1 ? '' : 's'}`}
            >
              <span className="w-[34%] truncate text-groww-text-primary">{row.label}</span>
              <span className="relative flex-1 h-3 mx-1.5" aria-hidden>
                <span className="absolute inset-y-0 left-1/2 w-px bg-[color:var(--viz-axis)]" />
                {row.min < -reach && (
                  <span className="absolute right-full top-1/2 -translate-y-1/2 leading-none text-[9px] text-[color:var(--viz-muted)]">◂</span>
                )}
                {row.max > reach && (
                  <span className="absolute left-full top-1/2 -translate-y-1/2 leading-none text-[9px] text-[color:var(--viz-muted)]">▸</span>
                )}
                <span
                  className="absolute top-1/2 h-px bg-[color:var(--viz-muted)]"
                  style={{ left: `${at(row.min)}%`, width: `${Math.max(at(row.max) - at(row.min), 0.5)}%` }}
                />
                <span
                  className={`absolute top-0.5 bottom-0.5 ${down ? 'rounded-l' : 'rounded-r'}`}
                  style={{
                    left: `${down ? at(row.mean) : 50}%`,
                    width: `${Math.max(Math.abs(at(row.mean) - 50), 0.8)}%`,
                    background: down ? 'var(--viz-critical)' : 'var(--viz-good)',
                  }}
                />
              </span>
              <span
                className="w-11 text-right font-semibold"
                style={{ color: down ? 'var(--viz-critical)' : row.mean > 0 ? 'var(--viz-good-text)' : undefined }}
              >
                {signedPct(row.mean)}
              </span>
              {row.amount !== undefined && (
                <span className="w-[62px] text-right text-groww-text-secondary">{signedInr(row.amount)}</span>
              )}
            </li>
          )
        })}
      </ul>
      {rows.length > FORECAST_ROWS && (
        <button onClick={() => setAll((v) => !v)} className="mt-1 text-[11px] text-groww-green hover:underline">
          {all ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  )
}

const ForecastCard: React.FC<{ forecast: Forecast; onCite: (id: string) => void; active: string | null }> = ({
  forecast,
  onCite,
  active,
}) => {
  // Largest rupee effect first, falls before rises.
  const holdings = [...forecast.holdings]
    .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
    .map((h) => ({ ...h, label: h.name, amount: h.pnl }))
  const plural = forecast.events === 1 ? '' : 's'
  return (
    <div id="chat-forecast" className="viz-root mt-3 rounded-xl border border-groww-border px-3 py-2.5 flex flex-col gap-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-xs font-bold text-groww-text-primary">Forecast · next {forecast.horizon_sessions} sessions</p>
        <p className="text-[11px] text-groww-text-muted">
          average of {forecast.events} past event{plural}
        </p>
      </div>
      <p className="text-[11px] text-groww-text-secondary -mt-1.5">
        Portfolio {signedPct(forecast.portfolio.mean)} ({signedInr(forecast.portfolio.pnl)}); past cases ranged from{' '}
        {signedPct(forecast.portfolio.worst)} to {signedPct(forecast.portfolio.best)}
        <Inline text={`[${forecast.portfolio.evidence}]`} onCite={onCite} active={active} />
      </p>
      {holdings.length > 0 && (
        <ForecastGroup
          title="Your holdings"
          evidenceId={forecast.holdings_evidence}
          rows={holdings}
          horizon={forecast.horizon_sessions}
          onCite={onCite}
          active={active}
        />
      )}
      {forecast.cross_assets.length > 0 ? (
        <ForecastGroup
          title="Commodities and the rupee"
          evidenceId={forecast.cross_assets_evidence}
          rows={forecast.cross_assets}
          horizon={forecast.horizon_sessions}
          onCite={onCite}
          active={active}
        />
      ) : (
        <p className="text-[11px] text-groww-text-muted">Commodity prices were unavailable, so there is no commodity forecast.</p>
      )}
      <p className="text-[10px] text-groww-text-muted leading-snug">
        Bar: average move after the past events. Line: lowest to highest, with an arrow where it runs past
        the scale. A small sample, so read it as a range, not a prediction. A rise in USD/INR is a weaker rupee.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pipeline progress
// ---------------------------------------------------------------------------
const PipelineView: React.FC<{ steps: Record<string, Step>; finished: boolean }> = ({ steps, finished }) => {
  const rowDone = (row: number) => PIPELINE[row].every((a) => steps[a.id])
  return (
    <div className="flex flex-col gap-1.5">
      {PIPELINE.map((row, rowIndex) => (
        <div key={rowIndex} className="flex gap-1.5">
          {row.map((agent) => {
            const step = steps[agent.id]
            const running = !step && !finished && (rowIndex === 0 || rowDone(rowIndex - 1))
            return (
              <div
                key={agent.id}
                className={`flex-1 min-w-0 rounded-lg border px-2 py-1.5 ${
                  step
                    ? step.status === 'degraded'
                      ? 'border-amber-200 bg-amber-50'
                      : 'border-groww-green/30 bg-groww-green-pale'
                    : running
                      ? 'border-groww-green bg-white'
                      : 'border-groww-border-light bg-white opacity-60'
                }`}
              >
                <div className="flex items-center gap-1.5">
                  {step ? (
                    <span className={step.status === 'degraded' ? 'text-amber-600' : 'text-groww-green'}>
                      {step.status === 'degraded' ? '!' : '✓'}
                    </span>
                  ) : running ? (
                    <span className="w-2.5 h-2.5 rounded-full border-2 border-groww-green border-t-transparent animate-spin" />
                  ) : (
                    <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
                  )}
                  <span className="text-[11px] font-semibold text-groww-text-primary truncate">{agent.label}</span>
                  {step && <span className="ml-auto text-[10px] text-groww-text-muted shrink-0">{step.ms} ms</span>}
                </div>
                {step && <p className="text-[11px] text-groww-text-secondary mt-0.5 leading-snug">{step.summary}</p>}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// One assistant reply
// ---------------------------------------------------------------------------
const AssistantReply: React.FC<{ message: AssistantMessage }> = ({ message }) => {
  const { answer, error, steps } = message
  const [showEvidence, setShowEvidence] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)

  const finished = Boolean(answer || error)
  const active = answer?.evidence.find((e) => e.id === activeId)
  const cite = (id: string) => setActiveId((current) => (current === id ? null : id))
  // Agents in the same row ran side by side, so a row takes as long as its slowest agent.
  const totalMs = PIPELINE.reduce((sum, row) => sum + Math.max(0, ...row.map((a) => steps[a.id]?.ms ?? 0)), 0)

  return (
    <div className="flex flex-col gap-2 text-sm text-groww-text-primary">
      {!finished && <PipelineView steps={steps} finished={false} />}

      {error && <p className="rounded-xl bg-red-50 text-red-600 px-3 py-2 text-xs">{error}</p>}

      {answer && (
        <div className="rounded-2xl rounded-tl-sm bg-white border border-groww-border-light px-3.5 py-3 shadow-card">
          <span
            className={`inline-block mb-2 px-2 py-0.5 rounded-full text-[11px] font-semibold ${ACTION_LABELS[answer.action].className}`}
          >
            {ACTION_LABELS[answer.action].text}
          </span>

          <div className="flex flex-col gap-1.5 leading-relaxed">
            {answer.text.split('\n').map((line, i) => {
              if (line.startsWith('## ')) {
                return (
                  <h4 key={i} className="mt-2 text-[11px] font-bold uppercase tracking-wide text-groww-text-muted">
                    {line.slice(3)}
                  </h4>
                )
              }
              if (line.startsWith('- ')) {
                return (
                  <div key={i} className="flex gap-2 text-[13px]">
                    <span className="mt-2 w-1 h-1 rounded-full bg-groww-green shrink-0" />
                    <p>
                      <Inline text={line.slice(2)} onCite={cite} active={activeId} />
                    </p>
                  </div>
                )
              }
              return (
                line.trim() && (
                  <p key={i}>
                    <Inline text={line} onCite={cite} active={activeId} />
                  </p>
                )
              )
            })}
          </div>

          {active && (
            <div className="mt-3 rounded-xl bg-groww-green-pale border border-groww-green/30 px-3 py-2 text-xs">
              <p className="font-semibold text-groww-green">Evidence {active.id}</p>
              <p className="mt-0.5">{active.claim}</p>
              <p className="mt-1 text-groww-text-muted">Source: {active.source}</p>
            </div>
          )}

          {/* Why this was recommended, step by step; open by default */}
          <div id="chat-trail" className="mt-3">
            <EvidenceTrail trail={answer.trail} decision={answer.decision} evidence={answer.evidence} steps={steps} />
          </div>

          {answer.forecast && <ForecastCard forecast={answer.forecast} onCite={cite} active={activeId} />}

          {answer.hedges.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5">
              {answer.hedges.map((hedge, i) => (
                <div key={i} className="rounded-xl border border-groww-border px-3 py-2 text-xs flex items-center gap-2">
                  <span
                    className={`px-1.5 py-0.5 rounded font-semibold uppercase text-[10px] ${
                      hedge.side === 'short' ? 'bg-red-50 text-red-600' : 'bg-amber-50 text-amber-700'
                    }`}
                  >
                    {hedge.side}
                  </span>
                  <span className="font-semibold truncate">{hedge.instrument}</span>
                  <span className="ml-auto tabular-nums shrink-0">{formatInr(hedge.notional)}</span>
                  {hedge.optional && <span className="text-groww-text-muted shrink-0">optional</span>}
                </div>
              ))}
            </div>
          )}

          {answer.gaps.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1 text-[11px] text-amber-700">
              {answer.gaps.map((gap, i) => (
                <li key={i} className="rounded-lg bg-amber-50 px-2.5 py-1.5">{gap}</li>
              ))}
            </ul>
          )}

          <div className="mt-3 pt-2 border-t border-groww-border-light flex items-center gap-3 text-[11px] text-groww-text-muted">
            <span>
              {Object.keys(steps).length} agents · {(totalMs / 1000).toFixed(1)}s
            </span>
            <button onClick={() => setShowEvidence((v) => !v)} aria-expanded={showEvidence} className="hover:text-groww-green">
              {showEvidence ? 'Hide' : 'All'} {answer.evidence.length} evidence rows
            </button>
            {answer.writer === 'template' && <span className="ml-auto">rule-based wording</span>}
          </div>

          {showEvidence && (
            <ul className="mt-2 flex flex-col gap-1.5 text-[11px]">
              {answer.evidence.map((item) => (
                <li key={item.id} className="rounded-lg bg-groww-bg-primary px-2.5 py-1.5">
                  <span className="font-semibold text-groww-green">{item.id}</span> {item.claim}
                  <span className="block text-groww-text-muted">Source: {item.source}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Widget
// ---------------------------------------------------------------------------
const ChatWidget: React.FC = () => {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const nextId = useRef(1)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, open])

  if (!user || pathname === '/login') return null

  const updateReply = (id: number, change: (message: AssistantMessage) => AssistantMessage) =>
    setMessages((all) => all.map((m) => (m.id === id && m.role === 'assistant' ? change(m) : m)))

  const loadHoldings = async () => {
    const { data, error } = await supabase
      .from('portfolio_holdings')
      .select('name, symbol, isin, units, buy_price, type')
      .eq('user_id', user.id)
    return error ? null : data
  }

  const send = async (text: string) => {
    const query = text.trim()
    if (!query || busy) return
    const replyId = nextId.current + 1
    setMessages((all) => [
      ...all,
      { id: nextId.current, role: 'user', text: query },
      { id: replyId, role: 'assistant', steps: {} },
    ])
    nextId.current += 2
    setInput('')
    setBusy(true)

    const handle = (event: StreamEvent) => {
      if (event.type === 'agent') {
        const { agent, status, summary, ms } = event
        updateReply(replyId, (m) => ({ ...m, steps: { ...m.steps, [agent]: { status, summary, ms } } }))
      } else if (event.type === 'answer') {
        updateReply(replyId, (m) => ({ ...m, answer: event }))
      } else if (event.type === 'error') {
        updateReply(replyId, (m) => ({ ...m, error: event.message }))
      }
    }

    try {
      const response = await fetch(`${BACKEND_URL}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, holdings: await loadHoldings() }),
      })
      if (!response.ok || !response.body) throw new Error(`Request failed (${response.status})`)

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let newline = buffer.indexOf('\n')
        while (newline >= 0) {
          const line = buffer.slice(0, newline).trim()
          buffer = buffer.slice(newline + 1)
          if (line) handle(JSON.parse(line) as StreamEvent)
          newline = buffer.indexOf('\n')
        }
      }
      updateReply(replyId, (m) =>
        m.answer || m.error ? m : { ...m, error: 'The analysis ended before an answer arrived.' },
      )
    } catch {
      updateReply(replyId, (m) => ({
        ...m,
        error: 'Could not reach the analysis service. Check that the backend is running.',
      }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {open && (
        <section
          id="chat-panel"
          aria-label="Portfolio assistant"
          className="fixed z-50 inset-0 sm:inset-auto sm:bottom-40 md:bottom-24 sm:right-6 sm:w-[440px] sm:h-[min(640px,calc(100dvh-12rem))] md:h-[min(640px,calc(100dvh-8rem))] flex flex-col sm:rounded-2xl bg-groww-bg-primary sm:border border-groww-border shadow-2xl overflow-hidden"
        >
          <header
            className="flex items-center justify-between px-4 py-3 text-white"
            style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)' }}
          >
            <div>
              <h2 className="text-sm font-bold">Portfolio Assistant</h2>
              <p className="text-[11px] opacity-90">Multi-agent risk analysis with evidence</p>
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close assistant"
              className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-white/20"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </header>

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-3">
            {messages.length === 0 && (
              <div className="flex flex-col gap-2">
                <p className="text-xs text-groww-text-secondary px-1">
                  Ask how an event could affect your holdings. Every figure in the answer links to its evidence.
                </p>
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => send(suggestion)}
                    className="text-left text-xs px-3 py-2 rounded-xl bg-white border border-groww-border-light text-groww-text-primary hover:border-groww-green hover:text-groww-green transition-colors duration-150"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}

            {messages.map((message) =>
              message.role === 'user' ? (
                <p
                  key={message.id}
                  className="self-end max-w-[85%] rounded-2xl rounded-tr-sm bg-groww-green text-white text-sm px-3.5 py-2"
                >
                  {message.text}
                </p>
              ) : (
                <AssistantReply key={message.id} message={message} />
              ),
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault()
              send(input)
            }}
            className="flex items-center gap-2 p-3 bg-white border-t border-groww-border-light"
            style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
          >
            <input
              id="chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about a risk to your portfolio…"
              maxLength={1000}
              className="flex-1 min-w-0 px-3 py-2 rounded-xl border border-groww-border text-base sm:text-sm outline-none focus:border-groww-green"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              aria-label="Send"
              className="w-9 h-9 shrink-0 rounded-xl bg-groww-green text-white flex items-center justify-center disabled:opacity-50 hover:bg-groww-green-dark transition-colors duration-150"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </form>
        </section>
      )}

      <button
        id="chat-toggle-btn"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? 'Close assistant' : 'Open assistant'}
        aria-expanded={open}
        className={`fixed z-50 bottom-20 md:bottom-6 right-4 sm:right-6 w-12 h-12 sm:w-14 sm:h-14 rounded-full text-white items-center justify-center shadow-lg transition-transform duration-200 hover:scale-105 ${open ? 'hidden sm:flex' : 'flex'}`}
        style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)' }}
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </button>
    </>
  )
}

export default ChatWidget
