import React, { useState, useRef, useMemo } from 'react'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import EvidenceTrail from './EvidenceTrail'
import type { TrailDecision, TrailStep } from './EvidenceTrail'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Evidence {
  id: string
  claim: string
  source: string
}

export interface Hedge {
  instrument: string
  side: 'short' | 'reduce'
  notional: number
  expected_offset: number
  optional: boolean
  evidence: string
}

export interface ForecastMove {
  mean: number
  min: number
  max: number
  n: number
}

export interface Forecast {
  horizon_sessions: number
  events: number
  portfolio: { mean: number; pnl: number; worst: number; best: number; evidence: string }
  holdings: (ForecastMove & { name: string; pnl: number })[]
  holdings_evidence: string | null
  cross_assets: (ForecastMove & { label: string })[]
  cross_assets_evidence: string | null
}

export interface Answer {
  text: string
  evidence: Evidence[]
  hedges: Hedge[]
  action: 'hedge' | 'monitor' | 'no_hedge' | 'none'
  forecast?: Forecast | null
  // Why the recommendation was made, and the steps of the analysis behind it.
  decision?: TrailDecision | null
  trail?: TrailStep[]
  gaps: string[]
  writer: 'llm' | 'template'
}

export interface Step {
  status: 'done' | 'degraded'
  summary: string
  ms: number
}

export type StreamEvent =
  | { type: 'start'; agents: string[]; portfolio_source: 'user' | 'sample'; holdings: number }
  | ({ type: 'agent'; agent: string } & Step)
  | ({ type: 'answer' } & Answer)
  | { type: 'error'; message: string }

export interface UserMessage {
  id: number
  role: 'user'
  text: string
  timestamp: string
}

export interface AssistantMessage {
  id: number
  role: 'assistant'
  steps: Record<string, Step>
  answer?: Answer
  error?: string
  timestamp: string
  simulatedHedgeApplied?: boolean
}

export type Message = UserMessage | AssistantMessage

// ── Constants & Helpers ───────────────────────────────────────────────────────

const BACKEND_URL: string =
  import.meta.env.VITE_BACKEND_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const PIPELINE_NODES = [
  { id: 'supervisor', label: 'Supervisor', icon: '🧠', role: 'Intent & Event Routing' },
  { id: 'sentiment', label: 'Sentiment', icon: '📰', role: 'News Polarity & Headlines' },
  { id: 'weather_macro', label: 'Weather / Macro', icon: '🌦️', role: 'Live Weather & Commodities' },
  { id: 'historical', label: 'Vector Retrieval', icon: '📚', role: 'Historical Crisis Parallels' },
  { id: 'risk', label: 'Risk Modeling', icon: '📊', role: 'VaR, CVaR & Exposure Sim' },
  { id: 'hedging', label: 'Hedging Strategy', icon: '🛡️', role: 'Derivatives & Sizing' },
  { id: 'synthesiser', label: 'Synthesizer', icon: '✍️', role: 'Multi-Modal Grounded Report' },
]

export const PRESET_SCENARIOS = [
  {
    label: '🌀 Hurricane in Gulf of Mexico',
    query: 'How will the forecasted Category 4 hurricane in the Gulf of Mexico affect our current energy holdings?',
    tag: 'Extreme Weather',
  },
  {
    label: '🛢️ Crude Oil Spikes to $95',
    query: 'What happens to our portfolio if crude oil prices spike sharply due to supply disruptions?',
    tag: 'Macro Shock',
  },
  {
    label: '🏛️ Surprise RBI Rate Hike',
    query: 'How will an unexpected 50 bps RBI repo rate hike affect our banking and interest-sensitive stocks?',
    tag: 'Monetary Policy',
  },
  {
    label: '⚠️ Geopolitical & Trade Shock',
    query: 'Analyze portfolio exposure if international trade tensions and shipping route disruptions escalate.',
    tag: 'Geopolitical',
  },
  {
    label: '📉 Rupee Depreciation',
    query: 'What is the impact on our holdings if the Rupee weakens sharply against the US Dollar?',
    tag: 'FX / Currency',
  },
  {
    label: '🛡️ Portfolio Health & Tail Risk',
    query: 'How risky is my portfolio right now and what hedging strategy is recommended to protect capital?',
    tag: 'Portfolio Risk',
  },
]

const ACTION_CONFIG: Record<
  Answer['action'],
  { label: string; badgeClass: string; borderClass: string; bgClass: string; icon: string }
> = {
  hedge: {
    label: 'Hedge Recommended',
    badgeClass: 'bg-red-50 text-red-600 border border-red-200',
    borderClass: 'border-red-200',
    bgClass: 'bg-red-50/40',
    icon: '🚨',
  },
  monitor: {
    label: 'Active Monitoring',
    badgeClass: 'bg-amber-50 text-amber-700 border border-amber-200',
    borderClass: 'border-amber-200',
    bgClass: 'bg-amber-50/40',
    icon: '👀',
  },
  no_hedge: {
    label: 'No Hedge Required',
    badgeClass: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
    borderClass: 'border-emerald-200',
    bgClass: 'bg-emerald-50/40',
    icon: '✅',
  },
  none: {
    label: 'Baseline Monitoring',
    badgeClass: 'bg-gray-100 text-gray-700 border border-gray-200',
    borderClass: 'border-gray-200',
    bgClass: 'bg-gray-50',
    icon: 'ℹ️',
  },
}

const formatInr = (value: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)

const signedPct = (fraction: number) => {
  const value = (fraction * 100).toFixed(1)
  return Number(value) === 0 ? '0.0%' : `${fraction > 0 ? '+' : ''}${value}%`
}

const signedInr = (value: number) =>
  `${value > 0 ? '+' : value < 0 ? '−' : ''}${formatInr(Math.abs(value))}`

// ── Inline Citation Formatter ─────────────────────────────────────────────────

const INLINE_REGEX = /(\*\*[^*]+\*\*|\[[A-Z]\d+(?:\s*,\s*[A-Z]\d+)*\])/

const InlineText: React.FC<{
  text: string
  onCite: (id: string) => void
  activeId: string | null
}> = ({ text, onCite, activeId }) => {
  return (
    <>
      {text.split(INLINE_REGEX).map((chunk, i) => {
        if (chunk.startsWith('**') && chunk.endsWith('**')) {
          return <strong key={i} className="font-semibold text-groww-text-primary">{chunk.slice(2, -2)}</strong>
        }
        if (/^\[[A-Z]\d/.test(chunk)) {
          const ids = chunk.slice(1, -1).split(/\s*,\s*/)
          return (
            <span key={i} className="inline-flex items-center gap-1 mx-0.5 align-baseline">
              {ids.map((id) => (
                <button
                  key={id}
                  onClick={() => onCite(id)}
                  className={`px-1.5 py-0.2 rounded text-[10px] font-mono font-bold transition-all shadow-xs ${
                    activeId === id
                      ? 'bg-emerald-600 text-white scale-105 ring-2 ring-emerald-400'
                      : 'bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-600 hover:text-white'
                  }`}
                  title={`View verified evidence citation ${id}`}
                >
                  {id}
                </button>
              ))}
            </span>
          )
        }
        return <React.Fragment key={i}>{chunk}</React.Fragment>
      })}
    </>
  )
}

// ── Multi-Agent Execution Graph Component ─────────────────────────────────────

const ExecutionGraph: React.FC<{
  steps: Record<string, Step>
  finished: boolean
}> = ({ steps, finished }) => {
  return (
    <div className="rounded-xl bg-slate-900 text-white p-4 border border-slate-800 shadow-xl overflow-hidden">
      <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            {!finished ? (
              <>
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
              </>
            ) : (
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-400"></span>
            )}
          </span>
          <span className="text-xs font-mono font-bold tracking-wider uppercase text-emerald-400">
            LangGraph Multi-Agent Orchestration
          </span>
        </div>
        <span className="text-[11px] font-mono text-slate-400">
          {finished ? 'Pipeline Complete' : 'Executing Subagents...'}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2.5">
        {PIPELINE_NODES.map((node) => {
          const step = steps[node.id]
          const isDone = Boolean(step)
          const isDegraded = step?.status === 'degraded'

          return (
            <div
              key={node.id}
              className={`rounded-lg p-2.5 border transition-all duration-300 ${
                isDone
                  ? isDegraded
                    ? 'bg-amber-950/40 border-amber-500/50 text-amber-200'
                    : 'bg-emerald-950/40 border-emerald-500/50 text-emerald-100 shadow-sm'
                  : 'bg-slate-800/40 border-slate-700/50 text-slate-400 opacity-60'
              }`}
            >
              <div className="flex items-center justify-between gap-1.5 mb-1">
                <div className="flex items-center gap-1.5 truncate">
                  <span className="text-sm">{node.icon}</span>
                  <span className="text-xs font-semibold text-white truncate">{node.label}</span>
                </div>
                {step ? (
                  <span
                    className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                      isDegraded ? 'bg-amber-500/20 text-amber-300' : 'bg-emerald-500/20 text-emerald-300'
                    }`}
                  >
                    {step.ms}ms
                  </span>
                ) : !finished ? (
                  <span className="w-2.5 h-2.5 rounded-full border-2 border-emerald-400 border-t-transparent animate-spin" />
                ) : (
                  <span className="text-[10px] text-slate-500">idle</span>
                )}
              </div>
              <p className="text-[10px] text-slate-400 line-clamp-1">{node.role}</p>
              {step && (
                <p className="text-[11px] mt-1 text-slate-300 border-t border-slate-800/80 pt-1 leading-snug line-clamp-2">
                  {step.summary}
                </p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Forecast Breakdown Card ───────────────────────────────────────────────────

const ForecastVisualizer: React.FC<{
  forecast: Forecast
  onCite: (id: string) => void
  activeId: string | null
}> = ({ forecast, onCite, activeId }) => {
  const [showAll, setShowAll] = useState(false)
  const holdings = useMemo(
    () =>
      [...forecast.holdings]
        .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
        .map((h) => ({ ...h, label: h.name, amount: h.pnl })),
    [forecast.holdings]
  )

  const displayedHoldings = showAll ? holdings : holdings.slice(0, 5)

  return (
    <div className="rounded-xl border border-groww-border-light bg-white p-4 shadow-sm space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-groww-border-light pb-3">
        <div>
          <h4 className="text-sm font-bold text-groww-text-primary flex items-center gap-2">
            <span>📈 Multi-Modal Impact Forecast</span>
            <span className="text-xs font-normal text-groww-text-muted">
              ({forecast.horizon_sessions}-Session Horizon)
            </span>
          </h4>
          <p className="text-xs text-groww-text-secondary mt-0.5">
            Synthesized from {forecast.events} analogous historical events retrieved from Vector DB
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs text-groww-text-muted">Portfolio Impact</p>
          <p
            className={`text-sm font-bold ${
              forecast.portfolio.pnl < 0 ? 'text-red-600' : 'text-emerald-600'
            }`}
          >
            {signedPct(forecast.portfolio.mean)} ({signedInr(forecast.portfolio.pnl)})
          </p>
        </div>
      </div>

      {/* Holdings Forecast Grid */}
      {holdings.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-groww-text-secondary">
            <span>Portfolio Holdings Impact</span>
            {forecast.holdings_evidence && (
              <InlineText text={`[${forecast.holdings_evidence}]`} onCite={onCite} activeId={activeId} />
            )}
          </div>
          <div className="space-y-1.5">
            {displayedHoldings.map((row) => {
              const isNegative = row.mean < 0
              return (
                <div
                  key={row.label}
                  className="flex items-center justify-between text-xs py-1.5 px-2.5 rounded-lg bg-groww-bg-primary hover:bg-slate-100 transition-colors"
                >
                  <span className="font-medium text-groww-text-primary truncate max-w-[180px] sm:max-w-xs">
                    {row.label}
                  </span>
                  <div className="flex items-center gap-3">
                    <span className="text-groww-text-muted text-[11px]">
                      Range: {signedPct(row.min)} to {signedPct(row.max)}
                    </span>
                    <span
                      className={`font-bold tabular-nums min-w-[60px] text-right ${
                        isNegative ? 'text-red-600' : 'text-emerald-600'
                      }`}
                    >
                      {signedPct(row.mean)}
                    </span>
                    <span className="text-groww-text-secondary tabular-nums min-w-[75px] text-right font-medium">
                      {signedInr(row.pnl)}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
          {holdings.length > 5 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              className="text-xs font-semibold text-emerald-600 hover:text-emerald-700 pt-1"
            >
              {showAll ? 'Show Fewer' : `View All ${holdings.length} Positions`}
            </button>
          )}
        </div>
      )}

      {/* Cross-Asset Commodities & Macro FX */}
      {forecast.cross_assets && forecast.cross_assets.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-groww-border-light">
          <div className="flex items-center justify-between text-xs font-semibold text-groww-text-secondary">
            <span>Cross-Asset Commodities & Macro Indicators</span>
            {forecast.cross_assets_evidence && (
              <InlineText text={`[${forecast.cross_assets_evidence}]`} onCite={onCite} activeId={activeId} />
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
            {forecast.cross_assets.map((item) => (
              <div
                key={item.label}
                className="p-2.5 rounded-lg border border-groww-border-light bg-slate-50/50 flex flex-col justify-between"
              >
                <span className="text-xs font-medium text-groww-text-secondary">{item.label}</span>
                <div className="flex items-baseline justify-between mt-1">
                  <span
                    className={`text-sm font-bold ${
                      item.mean > 0 ? 'text-emerald-600' : item.mean < 0 ? 'text-red-600' : 'text-slate-600'
                    }`}
                  >
                    {signedPct(item.mean)}
                  </span>
                  <span className="text-[10px] text-groww-text-muted">
                    ({signedPct(item.min)} to {signedPct(item.max)})
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Simulation Sandbox Card ───────────────────────────────────────────────────

const SimulationSandbox: React.FC<{
  answer: Answer
  simulated: boolean
  onToggleSimulate: () => void
}> = ({ answer, simulated, onToggleSimulate }) => {
  if (!answer.hedges || answer.hedges.length === 0) return null

  const totalHedgeNotional = answer.hedges.reduce((sum, h) => sum + h.notional, 0)
  const totalOffset = answer.hedges.reduce((sum, h) => sum + h.expected_offset, 0)
  const estimatedUnmitigatedLoss = answer.forecast?.portfolio?.pnl
    ? Math.abs(answer.forecast.portfolio.pnl)
    : totalOffset * 1.2
  const netResidualRisk = Math.max(0, estimatedUnmitigatedLoss - totalOffset)
  const riskReductionPct = ((totalOffset / Math.max(1, estimatedUnmitigatedLoss)) * 100).toFixed(0)

  return (
    <div className="rounded-xl border border-indigo-100 bg-linear-to-br from-indigo-50/50 via-white to-emerald-50/30 p-4 shadow-sm space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">🧪</span>
          <div>
            <h4 className="text-sm font-bold text-slate-800">Autonomous Hedging & Rebalance Sandbox</h4>
            <p className="text-xs text-slate-500">
              Total Sized Notional: {formatInr(totalHedgeNotional)} · Simulates execution before live deployment
            </p>
          </div>
        </div>
        <button
          onClick={onToggleSimulate}
          className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all shadow-sm flex items-center gap-1.5 ${
            simulated
              ? 'bg-emerald-600 text-white hover:bg-emerald-700 ring-2 ring-emerald-300'
              : 'bg-indigo-600 text-white hover:bg-indigo-700 hover:shadow-md'
          }`}
        >
          <span>{simulated ? '✓ Hedge Applied in Sandbox' : '⚡ Simulate Hedge Execution'}</span>
        </button>
      </div>

      {/* Sized Hedge Orders Table */}
      <div className="space-y-1.5">
        <p className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
          Agent-Recommended Derivatives & Trade Adjustments
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {answer.hedges.map((hedge, idx) => (
            <div
              key={idx}
              className="flex items-center justify-between p-2.5 rounded-lg bg-white border border-slate-200 shadow-xs"
            >
              <div className="flex items-center gap-2">
                <span
                  className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${
                    hedge.side === 'short'
                      ? 'bg-red-100 text-red-700'
                      : 'bg-amber-100 text-amber-700'
                  }`}
                >
                  {hedge.side}
                </span>
                <span className="text-xs font-semibold text-slate-800">{hedge.instrument}</span>
              </div>
              <div className="text-right">
                <p className="text-xs font-bold text-slate-900">{formatInr(hedge.notional)}</p>
                <p className="text-[10px] text-emerald-600 font-medium">
                  +{formatInr(hedge.expected_offset)} offset
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Simulated Before vs After Metrics */}
      {simulated && (
        <div className="pt-2 border-t border-indigo-100 grid grid-cols-3 gap-2 text-center animate-fadeIn">
          <div className="p-2 rounded-lg bg-red-50 border border-red-100">
            <p className="text-[10px] font-medium text-red-600 uppercase">Pre-Hedge Event Loss</p>
            <p className="text-sm font-bold text-red-700 mt-0.5">−{formatInr(estimatedUnmitigatedLoss)}</p>
          </div>
          <div className="p-2 rounded-lg bg-emerald-50 border border-emerald-100">
            <p className="text-[10px] font-medium text-emerald-600 uppercase">Hedge Protection</p>
            <p className="text-sm font-bold text-emerald-700 mt-0.5">+{formatInr(totalOffset)} ({riskReductionPct}%)</p>
          </div>
          <div className="p-2 rounded-lg bg-slate-50 border border-slate-200">
            <p className="text-[10px] font-medium text-slate-600 uppercase">Post-Hedge Net Risk</p>
            <p className="text-sm font-bold text-slate-800 mt-0.5">−{formatInr(netResidualRisk)}</p>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Evidence Drawer ───────────────────────────────────────────────────────────

const EvidenceDrawer: React.FC<{
  evidence: Evidence[]
  activeId: string | null
  onClose: () => void
}> = ({ evidence, activeId, onClose }) => {
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 space-y-3">
      <div className="flex items-center justify-between border-b border-emerald-200/60 pb-2">
        <h4 className="text-xs font-bold text-emerald-800 flex items-center gap-1.5 uppercase tracking-wide">
          <span>🛡️ Verified Grounding Evidence & Audit Trail</span>
          <span className="text-[11px] font-normal text-emerald-600">({evidence.length} facts verified)</span>
        </h4>
        <button onClick={onClose} className="text-xs text-emerald-700 hover:text-emerald-900 font-semibold">
          Dismiss
        </button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-60 overflow-y-auto pr-1">
        {evidence.map((item) => (
          <div
            key={item.id}
            className={`p-2.5 rounded-lg border text-xs transition-all ${
              activeId === item.id
                ? 'bg-emerald-100 border-emerald-400 text-emerald-950 font-medium ring-2 ring-emerald-300'
                : 'bg-white border-emerald-100 text-slate-700 hover:border-emerald-300'
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="font-mono font-bold text-emerald-700">{item.id}</span>
              <span className="text-[10px] text-slate-400 truncate max-w-[160px]">{item.source}</span>
            </div>
            <p className="leading-snug">{item.claim}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main AITerminal Component ─────────────────────────────────────────────────

export const AITerminal: React.FC<{
  title?: string
  subtitle?: string
  defaultOpen?: boolean
  className?: string
  compact?: boolean
}> = ({
  title = 'Autonomous Multi-Agent Intelligence Terminal',
  subtitle = 'Continuous multi-modal ingestion of real-time weather, global macro news, and historical market data',
  className = '',
  compact: _compact = false,
}) => {
  const { user } = useAuth()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [activeCitationId, setActiveCitationId] = useState<string | null>(null)
  const [showEvidenceDrawer, setShowEvidenceDrawer] = useState(false)
  const nextId = useRef(1)
  const scrollRef = useRef<HTMLDivElement>(null)

  const loadHoldings = async () => {
    if (!user) return null
    const { data, error } = await supabase
      .from('portfolio_holdings')
      .select('name, symbol, isin, units, buy_price, type')
      .eq('user_id', user.id)
    return error ? null : data
  }

  const updateReply = (id: number, change: (message: AssistantMessage) => AssistantMessage) => {
    setMessages((all) => all.map((m) => (m.id === id && m.role === 'assistant' ? change(m) : m)))
  }

  const toggleSimulate = (replyId: number) => {
    updateReply(replyId, (m) => ({
      ...m,
      simulatedHedgeApplied: !m.simulatedHedgeApplied,
    }))
  }

  const handleQuery = async (queryText: string) => {
    const query = queryText.trim()
    if (!query || busy) return

    const replyId = nextId.current + 1
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

    setMessages((all) => [
      ...all,
      { id: nextId.current, role: 'user', text: query, timestamp: now },
      { id: replyId, role: 'assistant', steps: {}, timestamp: now, simulatedHedgeApplied: false },
    ])
    nextId.current += 2
    setInput('')
    setBusy(true)

    const handle = (event: StreamEvent) => {
      if (event.type === 'agent') {
        const { agent, status, summary, ms } = event
        updateReply(replyId, (m) => ({
          ...m,
          steps: { ...m.steps, [agent]: { status, summary, ms } },
        }))
      } else if (event.type === 'answer') {
        updateReply(replyId, (m) => ({ ...m, answer: event }))
      } else if (event.type === 'error') {
        updateReply(replyId, (m) => ({ ...m, error: event.message }))
      }
    }

    try {
      const holdings = await loadHoldings()
      const response = await fetch(`${BACKEND_URL}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, holdings }),
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
        m.answer || m.error ? m : { ...m, error: 'Analysis completed before full synthesis arrived.' }
      )
    } catch {
      updateReply(replyId, (m) => ({
        ...m,
        error: 'Could not connect to the Multi-Agent Engine. Ensure the backend server is running.',
      }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      id="ai-terminal-root"
      className={`rounded-2xl bg-white border border-groww-border-light shadow-card overflow-hidden flex flex-col ${className}`}
    >
      {/* Terminal Header */}
      <div className="bg-linear-to-r from-slate-900 via-slate-800 to-emerald-950 p-4 text-white flex flex-wrap items-center justify-between gap-3 border-b border-slate-700">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-lg">
            ⚡
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold tracking-tight text-white">{title}</h3>
              <span className="text-[10px] font-mono uppercase bg-emerald-500/20 text-emerald-300 border border-emerald-400/30 px-2 py-0.5 rounded-full">
                PS5 Multi-Agent Engine
              </span>
            </div>
            <p className="text-xs text-slate-300 mt-0.5">{subtitle}</p>
          </div>
        </div>
      </div>

      {/* Preset Scenario Selector Pills */}
      <div className="px-4 py-3 bg-slate-50 border-b border-groww-border-light flex items-center gap-2 overflow-x-auto scrollbar-none">
        <span className="text-xs font-bold text-slate-500 uppercase tracking-wider shrink-0 flex items-center gap-1">
          <span>🎯 Scenarios:</span>
        </span>
        {PRESET_SCENARIOS.map((preset, i) => (
          <button
            key={i}
            onClick={() => handleQuery(preset.query)}
            disabled={busy}
            className="shrink-0 px-3 py-1.5 rounded-full text-xs font-medium bg-white border border-slate-200 text-slate-700 hover:border-emerald-500 hover:text-emerald-700 hover:bg-emerald-50/50 transition-all shadow-2xs disabled:opacity-50"
          >
            {preset.label}
          </button>
        ))}
      </div>

      {/* Main Conversation Stream */}
      <div
        ref={scrollRef}
        className="flex-1 p-4 md:p-6 overflow-y-auto space-y-6 max-h-[700px] min-h-[300px] bg-slate-50/30"
      >
        {messages.length === 0 && (
          <div className="text-center py-10 space-y-4 max-w-lg mx-auto">
            <div className="w-14 h-14 rounded-2xl bg-emerald-100 text-emerald-700 mx-auto flex items-center justify-center text-2xl shadow-sm">
              🤖
            </div>
            <div>
              <h4 className="text-base font-bold text-slate-800">Ask the Multi-Agent Financial Intelligence Engine</h4>
              <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                Query extreme weather impacts, global crude oil spikes, unexpected central bank rate decisions, or request an automated portfolio hedging breakdown.
              </p>
            </div>
          </div>
        )}

        {messages.map((msg) => {
          if (msg.role === 'user') {
            return (
              <div key={msg.id} className="flex justify-end">
                <div className="max-w-2xl bg-emerald-700 text-white rounded-2xl rounded-tr-xs px-4 py-3 shadow-md space-y-1">
                  <p className="text-xs font-medium">{msg.text}</p>
                  <span className="text-[10px] text-emerald-200 block text-right">{msg.timestamp}</span>
                </div>
              </div>
            )
          }

          const { answer, error, steps, simulatedHedgeApplied } = msg
          const finished = Boolean(answer || error)
          const config = answer ? ACTION_CONFIG[answer.action] : ACTION_CONFIG.none

          return (
            <div key={msg.id} className="space-y-4 max-w-4xl">
              {/* Multi-Agent Orchestration Pipeline Visualization */}
              <ExecutionGraph steps={steps} finished={finished} />

              {/* Error Box */}
              {error && (
                <div className="rounded-xl bg-red-50 border border-red-200 p-4 text-xs text-red-700">
                  <p className="font-bold">⚠️ Multi-Agent Pipeline Encountered an Issue</p>
                  <p className="mt-1">{error}</p>
                </div>
              )}

              {/* Synthesized Answer Card */}
              {answer && (
                <div className={`rounded-2xl border ${config.borderClass} bg-white p-5 shadow-sm space-y-4`}>
                  {/* Status Banner */}
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-groww-border-light pb-3">
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{config.icon}</span>
                      <span className={`px-3 py-1 rounded-full text-xs font-bold ${config.badgeClass}`}>
                        {config.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setShowEvidenceDrawer((v) => !v)}
                        className="text-xs font-semibold text-emerald-600 hover:text-emerald-700 underline"
                      >
                        {answer.evidence.length} Verified Evidence Items
                      </button>
                    </div>
                  </div>

                  {/* Formatted Report Text with Grounded Citations */}
                  <div className="text-sm text-groww-text-primary space-y-2 leading-relaxed">
                    {answer.text.split('\n').map((line, idx) => {
                      if (line.startsWith('## ')) {
                        return (
                          <h4
                            key={idx}
                            className="text-xs font-bold uppercase tracking-wider text-slate-500 pt-2 pb-1 border-b border-slate-100"
                          >
                            {line.slice(3)}
                          </h4>
                        )
                      }
                      if (line.startsWith('- ')) {
                        return (
                          <div key={idx} className="flex items-start gap-2 text-xs">
                            <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
                            <p className="flex-1">
                              <InlineText
                                text={line.slice(2)}
                                onCite={(id) => {
                                  setActiveCitationId(id)
                                  setShowEvidenceDrawer(true)
                                }}
                                activeId={activeCitationId}
                              />
                            </p>
                          </div>
                        )
                      }
                      return (
                        line.trim() && (
                          <p key={idx} className="text-xs">
                            <InlineText
                              text={line}
                              onCite={(id) => {
                                setActiveCitationId(id)
                                setShowEvidenceDrawer(true)
                              }}
                              activeId={activeCitationId}
                            />
                          </p>
                        )
                      )
                    })}
                  </div>

                  {/* Evidence trail: why this was recommended, step by step; open by default */}
                  <div className="ai-terminal-trail">
                    <EvidenceTrail
                      trail={answer.trail}
                      decision={answer.decision}
                      evidence={answer.evidence}
                      steps={steps}
                    />
                  </div>

                  {/* Evidence Drawer */}
                  {showEvidenceDrawer && (
                    <EvidenceDrawer
                      evidence={answer.evidence}
                      activeId={activeCitationId}
                      onClose={() => setShowEvidenceDrawer(false)}
                    />
                  )}

                  {/* Forecast Breakdown */}
                  {answer.forecast && (
                    <ForecastVisualizer
                      forecast={answer.forecast}
                      onCite={(id) => {
                        setActiveCitationId(id)
                        setShowEvidenceDrawer(true)
                      }}
                      activeId={activeCitationId}
                    />
                  )}

                  {/* Simulation Sandbox with One-Click Execution */}
                  <SimulationSandbox
                    answer={answer}
                    simulated={Boolean(simulatedHedgeApplied)}
                    onToggleSimulate={() => toggleSimulate(msg.id)}
                  />

                  {/* Known Coverage Gaps */}
                  {answer.gaps && answer.gaps.length > 0 && (
                    <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-xs text-amber-800 space-y-1">
                      <p className="font-semibold">Coverage Notes:</p>
                      {answer.gaps.map((g, i) => (
                        <p key={i}>• {g}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Query Input Bar */}
      <div className="p-4 bg-white border-t border-groww-border-light">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleQuery(input)
          }}
          className="flex items-center gap-2"
        >
          <div className="relative flex-1">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 text-sm">🔍</span>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask natural language scenario (e.g. 'How will Hurricane Ida impact our energy holdings?')..."
              disabled={busy}
              className="w-full pl-9 pr-4 py-2.5 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 text-xs text-slate-800 shadow-2xs"
            />
          </div>
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed shrink-0 flex items-center gap-1.5"
          >
            {busy ? (
              <>
                <span className="w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent animate-spin" />
                <span>Analyzing...</span>
              </>
            ) : (
              <>
                <span>Run Agent Graph</span>
                <span>➔</span>
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  )
}

export default AITerminal
