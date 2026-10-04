import React, { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { isLeftover } from '../lib/holdings'
import EvidenceTrail from './EvidenceTrail'
import Notes from './Notes'
import type { TrailDecision, TrailEvidence, TrailStep } from './EvidenceTrail'
import { KIND_ORDER, PriceChart, SERIES_COLORS, WEATHER_KINDS } from '../pages/InsightsPage'
import type { Lane, Line, PortfolioInsights } from '../pages/InsightsPage'

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------
type Loadable<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T }

const inr = (value: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)
const pct = (fraction: number, digits = 0) => `${(fraction * 100).toFixed(digits)}%`

async function loadHoldings(userId: string) {
  const { data, error } = await supabase
    .from('portfolio_holdings')
    .select('name, symbol, isin, units, buy_price, type')
    .eq('user_id', userId)
  return error ? null : (data ?? []).filter((h) => !isLeftover(h))
}

const cardClass = 'bg-white rounded-2xl sm:rounded-3xl border border-groww-border-light shadow-card p-5 sm:p-6'

// ===========================================================================
// 1. Live multi-agent execution graph, with the agents' trade and hedge recommendation
// ===========================================================================
interface AgentStep {
  status: 'done' | 'degraded'
  summary: string
  ms: number
}

interface Hedge {
  instrument: string
  side: 'short' | 'reduce'
  notional: number
  expected_offset: number
  optional: boolean
  evidence: string
}

interface AgentAnswer {
  text: string
  evidence: TrailEvidence[]
  hedges: Hedge[]
  action: 'hedge' | 'monitor' | 'no_hedge' | 'none'
  decision?: TrailDecision | null
  trail?: TrailStep[]
  gaps: string[]
  writer: 'llm' | 'template'
}

type AgentEvent =
  | { type: 'start' }
  | ({ type: 'agent'; agent: string } & AgentStep)
  | ({ type: 'answer' } & AgentAnswer)
  | { type: 'error'; message: string }

// The graph, stage by stage. Agents in one stage run at the same time.
const STAGES: { id: string; label: string; role: string }[][] = [
  [{ id: 'supervisor', label: 'Supervisor', role: 'Reads the question, picks the scenario' }],
  [
    { id: 'sentiment', label: 'Sentiment', role: 'Scores the news' },
    { id: 'weather_macro', label: 'Weather / Macro', role: 'Forecasts and market stress' },
    { id: 'historical', label: 'Historical', role: 'Finds similar past events' },
  ],
  [{ id: 'risk', label: 'Risk', role: 'VaR, beta, event scenario' }],
  [{ id: 'hedging', label: 'Hedging', role: 'Decides and sizes the hedge' }],
  [{ id: 'synthesiser', label: 'Synthesiser', role: 'Writes the checked answer' }],
]

const PRESETS = [
  'How would a severe cyclone on the Gujarat coast affect my holdings?',
  'What if crude oil prices spike?',
  'What happens to my portfolio if the RBI hikes rates unexpectedly?',
]

const ACTION: Record<AgentAnswer['action'], { text: string; className: string }> = {
  hedge: { text: 'Hedge recommended', className: 'bg-red-50 text-red-600' },
  monitor: { text: 'Monitor, no hedge', className: 'bg-amber-50 text-amber-700' },
  no_hedge: { text: 'No hedge needed', className: 'bg-groww-green-light text-groww-green' },
  none: { text: 'No hedge sized', className: 'bg-gray-100 text-groww-text-secondary' },
}

const duration = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)

// "**bold**" and [R3] citations inside one line of the answer.
const AnswerLine: React.FC<{ text: string }> = ({ text }) => (
  <>
    {text.split(/(\*\*[^*]+\*\*|\[[A-Z]\d+(?:\s*,\s*[A-Z]\d+)*\])/).map((part, i) => {
      if (part.startsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>
      if (/^\[[A-Z]\d/.test(part)) {
        return (
          <span key={i} className="mx-0.5 px-1 rounded bg-groww-green-light text-groww-green text-[10px] font-semibold align-middle">
            {part.slice(1, -1)}
          </span>
        )
      }
      return <React.Fragment key={i}>{part}</React.Fragment>
    })}
  </>
)

export const AgentGraphCard: React.FC<{ backendUrl: string }> = ({ backendUrl }) => {
  const { user } = useAuth()
  const [query, setQuery] = useState('')
  const [asked, setAsked] = useState<string | null>(null)
  const [steps, setSteps] = useState<Record<string, AgentStep>>({})
  const [answer, setAnswer] = useState<AgentAnswer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (text: string) => {
    const question = text.trim()
    if (!question || busy || !user?.id) return
    setAsked(question)
    setSteps({})
    setAnswer(null)
    setError(null)
    setBusy(true)
    let finished = false
    const handle = (event: AgentEvent) => {
      if (event.type === 'agent') {
        const { agent, status, summary, ms } = event
        setSteps((current) => ({ ...current, [agent]: { status, summary, ms } }))
      } else if (event.type === 'answer') {
        finished = true
        setAnswer(event)
      } else if (event.type === 'error') {
        finished = true
        setError(event.message)
      }
    }
    try {
      const response = await fetch(`${backendUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: question, holdings: await loadHoldings(user.id) }),
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
          if (line) handle(JSON.parse(line) as AgentEvent)
          newline = buffer.indexOf('\n')
        }
      }
      if (!finished) setError('The analysis ended before an answer arrived.')
    } catch {
      setError('Could not reach the analysis service.')
    } finally {
      setBusy(false)
    }
  }

  const stageDone = (index: number) => STAGES[index].every((agent) => steps[agent.id])
  const totalMs = STAGES.reduce((sum, stage) => sum + Math.max(0, ...stage.map((a) => steps[a.id]?.ms ?? 0)), 0)
  const done = Object.keys(steps).length

  return (
    <section id="dashboard-agent-graph" className={cardClass}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-lg font-bold text-groww-text-primary tracking-tight">Agent pipeline</h3>
        <p className="text-xs text-groww-text-muted">
          {busy
            ? `Running: ${done} of 7 agents finished`
            : answer
              ? `7 agents · ${(totalMs / 1000).toFixed(1)} s · ${answer.evidence.length} evidence rows`
              : 'Ask a question and watch each agent work'}
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          run(query)
        }}
        className="mt-3 flex gap-2"
      >
        <input
          id="agent-graph-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={1000}
          placeholder="For example: how would a cyclone on the Gujarat coast affect my holdings?"
          className="flex-1 min-w-0 px-3 py-2 rounded-xl border border-groww-border text-sm outline-none focus:border-groww-green"
        />
        <button
          type="submit"
          disabled={busy || !query.trim()}
          className="shrink-0 px-4 py-2 rounded-xl bg-groww-green text-white text-sm font-semibold hover:bg-groww-green-dark disabled:opacity-50 transition-colors"
        >
          {busy ? 'Running…' : 'Run'}
        </button>
      </form>
      <div className="mt-2 flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <button
            key={preset}
            onClick={() => {
              setQuery(preset)
              run(preset)
            }}
            disabled={busy}
            className="agent-preset px-2.5 py-1 rounded-lg bg-groww-bg-primary border border-groww-border-light text-[11px] text-groww-text-secondary hover:border-groww-green hover:text-groww-green disabled:opacity-50 transition-colors"
          >
            {preset}
          </button>
        ))}
      </div>

      {/* The graph: five stages left to right (top to bottom on a phone); the three in stage two run in parallel */}
      <ol className="mt-4 flex flex-col lg:flex-row lg:items-stretch gap-1.5" aria-label="Agent execution graph">
        {STAGES.map((stage, index) => (
          <React.Fragment key={index}>
            {index > 0 && (
              <li aria-hidden className="flex items-center justify-center text-groww-text-muted text-sm lg:px-0.5">
                <span className="lg:hidden">↓</span>
                <span className="hidden lg:inline">→</span>
              </li>
            )}
            <li className={`flex flex-col gap-1.5 justify-center ${stage.length > 1 ? 'lg:flex-[1.25]' : 'lg:flex-1'} min-w-0`}>
              {stage.map((agent) => {
                const step = steps[agent.id]
                const running = busy && !step && (index === 0 || stageDone(index - 1))
                return (
                  <div
                    key={agent.id}
                    data-agent={agent.id}
                    data-state={step ? step.status : running ? 'running' : 'idle'}
                    className={`rounded-xl border px-3 py-2 transition-colors duration-200 ${
                      step
                        ? step.status === 'degraded'
                          ? 'border-amber-300 bg-amber-50'
                          : 'border-groww-green/40 bg-groww-green-pale'
                        : running
                          ? 'border-groww-green bg-white shadow-sm'
                          : 'border-groww-border-light bg-groww-bg-primary'
                    }`}
                  >
                    <div className="flex items-center gap-1.5">
                      {step ? (
                        <span className={`text-xs font-bold ${step.status === 'degraded' ? 'text-amber-600' : 'text-groww-green'}`}>
                          {step.status === 'degraded' ? '!' : '✓'}
                        </span>
                      ) : running ? (
                        <span className="w-3 h-3 rounded-full border-2 border-groww-green border-t-transparent animate-spin" />
                      ) : (
                        <span className="w-2 h-2 rounded-full bg-gray-300" />
                      )}
                      <span className="text-xs font-bold text-groww-text-primary truncate">{agent.label}</span>
                      {step && <span className="ml-auto shrink-0 text-[10px] text-groww-text-muted tabular-nums">{duration(step.ms)}</span>}
                    </div>
                    <p className="mt-0.5 text-[11px] leading-snug text-groww-text-secondary">
                      {step ? step.summary : running ? 'Working…' : agent.role}
                    </p>
                  </div>
                )
              })}
            </li>
          </React.Fragment>
        ))}
      </ol>
      <p className="mt-2 text-[11px] text-groww-text-muted">Green: finished · amber "!": ran with missing data.</p>

      {error && <p className="mt-3 rounded-xl bg-red-50 text-red-600 px-3 py-2 text-xs">{error}</p>}

      {answer && (
        <div id="agent-graph-answer" className="mt-4 pt-4 border-t border-groww-border-light grid grid-cols-1 lg:grid-cols-2 gap-5 items-start">
          <div className="min-w-0">
            <p className="text-[11px] text-groww-text-muted">You asked: {asked}</p>
            <span className={`inline-block mt-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold ${ACTION[answer.action].className}`}>
              {ACTION[answer.action].text}
            </span>
            <div className="mt-2 flex flex-col gap-1.5 text-[13px] leading-relaxed text-groww-text-primary">
              {answer.text.split('\n').map((line, i) => {
                if (line.startsWith('## ')) {
                  return (
                    <h4 key={i} className="mt-1.5 text-[11px] font-bold uppercase tracking-wide text-groww-text-muted">
                      {line.slice(3)}
                    </h4>
                  )
                }
                if (line.startsWith('- ')) {
                  return (
                    <div key={i} className="flex gap-2">
                      <span className="mt-2 w-1 h-1 rounded-full bg-groww-green shrink-0" />
                      <p><AnswerLine text={line.slice(2)} /></p>
                    </div>
                  )
                }
                return line.trim() ? <p key={i}><AnswerLine text={line} /></p> : null
              })}
            </div>
            {answer.writer === 'template' && <p className="mt-2 text-[11px] text-groww-text-muted">Rule-based wording.</p>}
          </div>

          <div className="min-w-0 flex flex-col gap-3">
            {/* The agents' explicit trade and hedge recommendation */}
            <div id="agent-graph-trades" className="rounded-xl border border-groww-border px-3.5 py-3">
              <p className="text-[10px] font-bold uppercase tracking-wide text-groww-text-muted">Trade and hedge recommendation</p>
              {answer.hedges.length > 0 ? (
                <ul className="mt-2 flex flex-col gap-1.5">
                  {answer.hedges.map((hedge, i) => (
                    <li key={i} className="rounded-lg bg-groww-bg-primary px-3 py-2 text-xs">
                      <div className="flex items-center gap-2">
                        <span
                          className={`px-1.5 py-0.5 rounded font-bold uppercase text-[10px] ${
                            hedge.side === 'short' ? 'bg-red-50 text-red-600' : 'bg-amber-50 text-amber-700'
                          }`}
                        >
                          {hedge.side === 'short' ? 'Short' : 'Reduce'}
                        </span>
                        <span className="font-semibold text-groww-text-primary truncate">{hedge.instrument}</span>
                        <span className="ml-auto shrink-0 font-semibold tabular-nums">{inr(hedge.notional)}</span>
                      </div>
                      <p className="mt-1 text-[11px] text-groww-text-secondary">
                        Expected to offset about {inr(hedge.expected_offset)} of the loss
                        {hedge.optional ? '; optional, because no hedge is needed on the numbers' : ''}. Evidence {hedge.evidence}.
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1.5 text-xs text-groww-text-primary">No trade is recommended.</p>
              )}
              {answer.decision && (
                <p className="mt-2 text-[11px] text-groww-text-secondary leading-relaxed">
                  <span className="font-semibold text-groww-text-primary">Why: </span>
                  {answer.decision.comparison}
                </p>
              )}
            </div>

            <EvidenceTrail
              trail={answer.trail}
              decision={answer.decision}
              evidence={answer.evidence}
              steps={steps}
              defaultOpen={false}
            />

            {answer.gaps.length > 0 && (
              <ul className="flex flex-col gap-1 text-[11px] text-amber-700">
                {answer.gaps.map((gap, i) => (
                  <li key={i} className="rounded-lg bg-amber-50 px-2.5 py-1.5">{gap}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

// ===========================================================================
// 2 and 3. Interactive price and weather chart, and the risk exposure breakdown.
// Both read the same insights data, fetched once.
// ===========================================================================
type ChartRange = '1y' | '3y'
const MAX_LINES = 4

const PriceWeatherCard: React.FC<{
  state: Loadable<PortfolioInsights>
  range: ChartRange
  onRange: (range: ChartRange) => void
  refreshing: boolean
}> = ({ state, range, onRange, refreshing }) => {
  const navigate = useNavigate()
  const [picked, setPicked] = useState<string[] | null>(null)
  const data = state.status === 'ready' ? state.data : null

  // The three largest holdings to begin with; the user can change them.
  const chosen = picked ?? data?.prices.series.slice(0, 3).map((s) => s.ticker) ?? []
  const colorOf = (ticker: string) => SERIES_COLORS[data?.prices.series.findIndex((s) => s.ticker === ticker) ?? 0]
  const toggle = (ticker: string) =>
    setPicked(chosen.includes(ticker) ? chosen.filter((t) => t !== ticker) : [...chosen, ticker].slice(-MAX_LINES))

  const lines: Line[] = data
    ? [
        { key: 'benchmark', name: data.prices.benchmark.name, color: 'var(--viz-context)', values: data.prices.benchmark.values },
        ...data.prices.series
          .filter((s) => chosen.includes(s.ticker))
          .map((s) => ({ key: s.ticker, name: s.name, color: colorOf(s.ticker), values: s.values })),
      ]
    : []
  const chosenNames = new Set(data?.prices.series.filter((s) => chosen.includes(s.ticker)).map((s) => s.name))
  const lanes: Lane[] = (data?.weather?.sites ?? [])
    .map((site, index) => ({
      site: index,
      name: site.name,
      holdings: site.holdings.map((h) => `${h.name} (${h.operation})`),
      emphasised: site.holdings.some((h) => chosenNames.has(h.name)),
    }))
    .filter((lane) => lane.holdings.length > 0)

  return (
    <section id="dashboard-price-weather" className={`viz-root ${cardClass} min-w-0`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold text-groww-text-primary tracking-tight">Prices, events and weather</h3>
          <p className="text-xs text-groww-text-muted mt-0.5">
            Indexed to 100. ▼ past event · lanes show weather alert days.
          </p>
        </div>
        <div className="inline-flex rounded-xl border border-groww-border bg-white p-0.5" role="group" aria-label="Date range">
          {(['1y', '3y'] as ChartRange[]).map((option) => (
            <button
              key={option}
              onClick={() => onRange(option)}
              aria-pressed={range === option}
              className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${
                range === option ? 'bg-groww-green text-white' : 'text-groww-text-secondary hover:text-groww-green'
              }`}
            >
              {option.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {state.status === 'loading' && <p className="py-16 text-center text-sm text-groww-text-secondary">Loading price history…</p>}
      {state.status === 'error' && <p className="mt-4 text-sm text-red-600">{state.message}</p>}
      {data && (
        <>
          <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Holdings shown">
            <span className="inline-flex items-center gap-2 px-2.5 py-1 text-xs text-groww-text-secondary">
              <span className="w-4 h-0.5 rounded" style={{ background: 'var(--viz-context)' }} />
              {data.prices.benchmark.name}
            </span>
            {data.prices.series.map((series) => {
              const on = chosen.includes(series.ticker)
              return (
                <button
                  key={series.ticker}
                  onClick={() => toggle(series.ticker)}
                  aria-pressed={on}
                  className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-lg border text-xs transition-colors ${
                    on
                      ? 'border-groww-border bg-white text-groww-text-primary font-medium'
                      : 'border-groww-border-light bg-groww-bg-primary text-groww-text-muted hover:text-groww-text-primary'
                  }`}
                >
                  <span className="w-4 h-0.5 rounded" style={{ background: on ? colorOf(series.ticker) : 'var(--viz-axis)' }} />
                  {series.name}
                </button>
              )
            })}
          </div>
          <div className={`mt-3 transition-opacity duration-200 ${refreshing ? 'opacity-60' : ''}`}>
            <PriceChart
              dates={data.prices.dates}
              lines={lines}
              events={data.events}
              activeEventId={null}
              weather={data.weather}
              lanes={lanes}
            />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-groww-text-secondary">
            {data.weather && lanes.length > 0 ? (
              KIND_ORDER.map((kind) => (
                <span key={kind} className="inline-flex items-center gap-1.5">
                  <span className="w-3 h-1 rounded-sm" style={{ background: WEATHER_KINDS[kind].color }} />
                  {WEATHER_KINDS[kind].label}
                </span>
              ))
            ) : (
              <span>
                {data.weather ? 'None of your holdings has a mapped site, so no weather lanes are shown.' : 'Weather history is unavailable.'}
              </span>
            )}
            <button onClick={() => navigate('/insights')} className="ml-auto font-semibold text-groww-green hover:text-groww-green-dark">
              Event table and more ranges in Insights →
            </button>
          </div>
        </>
      )}
    </section>
  )
}

const RiskBreakdownCard: React.FC<{ state: Loadable<PortfolioInsights> }> = ({ state }) => {
  const data = state.status === 'ready' ? state.data : null
  const hasRisk = data?.sectors.every((s) => s.risk_share !== null && s.risk_share !== undefined) ?? false
  const scale = data ? Math.max(...data.sectors.flatMap((s) => [s.weight, s.risk_share ?? 0]), 0.01) : 1
  const riskiest = data
    ? [...data.positions].filter((p) => p.risk_share != null).sort((a, b) => (b.risk_share ?? 0) - (a.risk_share ?? 0)).slice(0, 5)
    : []
  return (
    <section id="dashboard-risk-breakdown" className={`viz-root ${cardClass} min-w-0`}>
      <h3 className="text-lg font-bold text-groww-text-primary tracking-tight">Risk exposure breakdown</h3>
      <p className="text-xs text-groww-text-muted mt-0.5">
        Where the money is against where the risk is
      </p>
      {state.status === 'loading' && <p className="py-16 text-center text-sm text-groww-text-secondary">Measuring exposure…</p>}
      {state.status === 'error' && <p className="mt-4 text-sm text-red-600">{state.message}</p>}
      {data && (
        <>
          <p className="mt-3 flex items-center gap-4 text-[11px] text-groww-text-secondary">
            <span className="inline-flex items-center gap-1.5">
              <span className="w-3 h-2 rounded-sm" style={{ background: 'var(--viz-series-1)' }} /> Share of value
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="w-3 h-2 rounded-sm" style={{ background: 'var(--viz-series-2)' }} /> Share of risk
            </span>
          </p>
          <ul className="mt-2 flex flex-col gap-2.5">
            {data.sectors.map((sector) => (
              <li
                key={sector.sector}
                title={`${sector.sector}: ${pct(sector.weight, 1)} of value (${inr(sector.value)})${
                  sector.risk_share != null ? `, ${pct(sector.risk_share, 1)} of risk` : ''
                }. ${sector.holdings.join(', ')}`}
              >
                <div className="flex items-baseline justify-between gap-3 text-xs">
                  <span className="font-semibold text-groww-text-primary">{sector.sector}</span>
                  <span className="tabular-nums text-groww-text-secondary">
                    {pct(sector.weight)} of value
                    {sector.risk_share != null && (
                      <>
                        {' · '}
                        <span className="font-semibold text-groww-text-primary">{pct(sector.risk_share)} of risk</span>
                      </>
                    )}
                  </span>
                </div>
                <div className="mt-1 flex flex-col gap-0.5">
                  <div className="h-2 rounded-r" style={{ width: `${Math.max(1.5, (sector.weight / scale) * 100)}%`, background: 'var(--viz-series-1)' }} />
                  {sector.risk_share != null && (
                    <div className="h-2 rounded-r" style={{ width: `${Math.max(1.5, (sector.risk_share / scale) * 100)}%`, background: 'var(--viz-series-2)' }} />
                  )}
                </div>
                <p className="mt-0.5 text-[11px] text-groww-text-muted truncate">{sector.holdings.join(', ')}</p>
              </li>
            ))}
          </ul>

          {riskiest.length > 0 && (
            <table className="mt-4 w-full text-xs tabular-nums">
              <thead>
                <tr className="text-left text-[11px] text-groww-text-muted">
                  <th className="font-medium py-1.5">Holdings carrying the most risk</th>
                  <th className="font-medium py-1.5 text-right">Value</th>
                  <th className="font-medium py-1.5 text-right">Risk</th>
                  <th className="font-medium py-1.5 text-right">Beta</th>
                </tr>
              </thead>
              <tbody>
                {riskiest.map((position) => (
                  <tr key={position.ticker} className="border-t border-groww-border-light">
                    <td className="py-1.5 text-groww-text-primary truncate max-w-[160px]">{position.name}</td>
                    <td className="py-1.5 text-right">{pct(position.weight)}</td>
                    <td className="py-1.5 text-right font-semibold text-groww-text-primary">{pct(position.risk_share ?? 0)}</td>
                    <td className="py-1.5 text-right">{position.beta != null ? position.beta.toFixed(2) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {hasRisk ? (
            <Notes className="mt-3">
              <p>
                Share of risk is each holding's contribution to portfolio variance over the last 250 sessions. A sector
                whose risk bar is longer than its value bar adds more risk than its size suggests.
              </p>
            </Notes>
          ) : (
            <p className="mt-3 text-xs text-groww-text-muted">
              Share of risk is unavailable: the holdings share too little price history.
            </p>
          )}
        </>
      )}
    </section>
  )
}

// The chart and the breakdown side by side, from one request.
export const MarketRiskRow: React.FC<{ backendUrl: string; refreshKey?: number }> = ({ backendUrl, refreshKey = 0 }) => {
  const { user } = useAuth()
  const [range, setRange] = useState<ChartRange>('1y')
  const [state, setState] = useState<Loadable<PortfolioInsights>>({ status: 'loading' })
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    if (!user?.id) return
    setRefreshing(true)
    try {
      const response = await fetch(`${backendUrl}/api/insights/portfolio`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holdings: await loadHoldings(user.id), range }),
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => null)
        throw new Error(detail?.detail ?? `Request failed (${response.status})`)
      }
      setState({ status: 'ready', data: await response.json() })
    } catch (e) {
      const message = e instanceof TypeError ? 'Could not reach the backend.' : (e as Error).message
      // Keep the last good chart if a refresh fails.
      setState((previous) => (previous.status === 'ready' ? previous : { status: 'error', message }))
    } finally {
      setRefreshing(false)
    }
  }, [user?.id, backendUrl, range])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      <div className="lg:col-span-7 min-w-0">
        <PriceWeatherCard state={state} range={range} onRange={setRange} refreshing={refreshing} />
      </div>
      <div className="lg:col-span-5 min-w-0">
        <RiskBreakdownCard state={state} />
      </div>
    </div>
  )
}
