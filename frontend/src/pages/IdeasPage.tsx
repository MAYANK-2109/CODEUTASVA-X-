import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { isLeftover } from '../lib/holdings'

// ---------------------------------------------------------------------------
// Types: the payload of POST /api/advisor/suggestions. Every figure in it is
// computed on the server; this page only formats and lays it out.
// ---------------------------------------------------------------------------
type Maybe = number | null

interface Stats {
  n: number
  vol: Maybe
  sharpe: Maybe
  cvar: Maybe
  max_drawdown: Maybe
  sharpe_interval?: [number, number] | null
}

interface SectorRow {
  sector: string
  rank: number
  composite: number
  constituents: number
  scores: { fit: Maybe; evidence: Maybe; macro: Maybe; trend: Maybe }
  fit: { vol_change: number; cvar_change: number; sharpe_change: number; correlation: Maybe; n: number } | null
  trend: { relative_strength: Record<'3m' | '6m' | '12m', Maybe>; breadth: Maybe; extension: Maybe }
  evidence: {
    sentiment: Maybe
    headlines: number
    fall_chance: Maybe
    typical_fall_chance: Maybe
    analog: { mean: number; min: number; max: number; n: number; signals: string[] } | null
  }
  flags: string[]
}

interface Suggestion {
  sector: string
  sector_rank: number
  symbol: string
  stock_score: number
  thesis: string[]
  model_drivers: { note: string; top: [string, number][] }
  weight: Maybe
  tested_at: number
  sizing_note: string | null
  funding: { name: string; weight: number }[]
  metrics: {
    sessions: number
    basis: string
    vol: { before: number; after: number }
    cvar: { before: number; after: number }
    sharpe: { before: number; after: number; before_interval: [number, number] | null; after_interval: [number, number] | null }
    effective_n: { before: number; after: number }
    top_risk_share: { before: number; after: number }
    correlation: Maybe
  }
  fundamentals: { market_cap: Maybe; roe: Maybe; margin: Maybe; debt_to_equity: Maybe; pe: Maybe; pe_vs_sector: Maybe }
  momentum_6m: Maybe
  entry_risks: string[]
  bear_case: string
  triggers: string[]
}

interface Advice {
  status: 'ok' | 'insufficient data'
  reason?: string
  footer: string
  as_of: string
  portfolio_source: 'user' | 'sample'
  data_notes: string[]
  regime: string[]
  config: { sector_weights: Record<string, number>; risk_free_rate: number }
  diagnosis: {
    gaps: { title: string; detail: string; severity: number }[]
    sectors: { sector: string; weight: number; reference: number; risk_share: number }[]
    reference: string
    effective_n: number
    effective_n_risk: number
    holdings: number
    market_beta: Maybe
    macro_betas: { nifty: number; brent: number; usdinr: number; vix: number; weeks: number } | null
    sessions: number
  }
  sectors: SectorRow[]
  picks: { sector: string; considered: number; excluded: { symbol: string; reason: string }[] }[]
  sizing: {
    status: string
    reason?: string
    verdict?: string
    recommend_change?: boolean
    method?: string
    trade_cost?: number
    turnover_share?: number
    liquidity_unknown?: string[]
    weights?: { name: string; current: number; proposed: number; change: number; trade_value: number; new: boolean }[]
    walk_forward?: {
      rebalances: number
      hold_sessions: number
      lookback_sessions: number
      results: Record<'proposed' | 'current' | 'equal_weight', Stats>
      note: string
    }
  }
  suggestions: Suggestion[]
  narrative: string | null
  narrator: string
}

type Loadable = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: Advice }

const BACKEND_URL: string =
  import.meta.env.VITE_BACKEND_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const NONE = 'insufficient data'
const pct = (value: Maybe | undefined, digits = 1, signed = false) =>
  value === null || value === undefined
    ? NONE
    : `${signed && value > 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
const num = (value: Maybe | undefined, digits = 2, signed = false) =>
  value === null || value === undefined ? NONE : `${signed && value > 0 ? '+' : ''}${value.toFixed(digits)}`
const inr = (value: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)
const interval = (range: [number, number] | null | undefined) =>
  range ? `${range[0].toFixed(2)} to ${range[1].toFixed(2)}` : NONE
const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
const crore = (value: Maybe) => (value === null ? NONE : `₹${Math.round(value / 1e7).toLocaleString('en-IN')} cr`)

const SCORE_LABELS: Record<string, string> = { fit: 'Portfolio fit', evidence: 'Evidence', macro: 'Macro fit', trend: 'Trend' }
const WALK_LABELS = { proposed: 'Proposed', current: 'Current portfolio', equal_weight: 'Equal weight' } as const

const Card: React.FC<{ id: string; title: string; subtitle?: string; children: React.ReactNode }> = ({
  id,
  title,
  subtitle,
  children,
}) => (
  <section id={id} className="bg-white rounded-2xl border border-groww-border-light shadow-card p-4 sm:p-5">
    <h2 className="text-base font-bold text-groww-text-primary">{title}</h2>
    {subtitle && <p className="text-xs text-groww-text-secondary mt-0.5">{subtitle}</p>}
    <div className="mt-4">{children}</div>
  </section>
)

// A 0 to 100 sub-score as a number and a bar; a missing score says so.
const Score: React.FC<{ value: Maybe }> = ({ value }) =>
  value === null ? (
    <span className="text-[11px] text-groww-text-muted">neutral</span>
  ) : (
    <span className="flex items-center gap-1.5">
      <span className="w-7 text-right font-semibold text-groww-text-primary tabular-nums">{Math.round(value)}</span>
      <span className="relative w-12 h-1.5 rounded-full bg-gray-100" aria-hidden>
        <span className="absolute inset-y-0 left-0 rounded-full bg-groww-green" style={{ width: `${Math.max(3, value)}%` }} />
      </span>
    </span>
  )

const BeforeAfter: React.FC<{ label: string; before: string; after: string; note?: string }> = ({
  label,
  before,
  after,
  note,
}) => (
  <tr className="border-t border-groww-border-light">
    <td className="py-1.5 pr-2 text-groww-text-secondary">
      {label}
      {note && <span className="block text-[10px] text-groww-text-muted">{note}</span>}
    </td>
    <td className="py-1.5 px-2 text-right tabular-nums text-groww-text-secondary">{before}</td>
    <td className="py-1.5 pl-2 text-right tabular-nums font-semibold text-groww-text-primary">{after}</td>
  </tr>
)

// ---------------------------------------------------------------------------
// One suggestion: thesis and sizing up front, the working behind a toggle
// ---------------------------------------------------------------------------
const SuggestionCard: React.FC<{ suggestion: Suggestion; recommended: boolean }> = ({ suggestion, recommended }) => {
  const [open, setOpen] = useState(false)
  const { metrics, fundamentals } = suggestion
  return (
    <li className="idea-card rounded-2xl border border-groww-border-light p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-bold text-groww-text-primary">{suggestion.symbol}</h3>
        <span className="text-xs text-groww-text-secondary">
          {suggestion.sector} · stock score {suggestion.stock_score.toFixed(1)} of 100
        </span>
        <span
          className={`ml-auto px-2 py-0.5 rounded-full text-[11px] font-semibold ${
            suggestion.weight && recommended ? 'bg-groww-green-light text-groww-green' : 'bg-gray-100 text-groww-text-secondary'
          }`}
        >
          {suggestion.weight ? `Suggested weight ${pct(suggestion.weight)}` : 'No weight given'}
          {suggestion.weight && !recommended ? ' (for information)' : ''}
        </span>
      </div>

      <div className="mt-2 flex flex-col gap-1.5 text-xs text-groww-text-primary leading-relaxed">
        {suggestion.thesis.map((line, index) => (
          <p key={index}>{line}</p>
        ))}
      </div>

      <p className="mt-2 text-xs text-groww-text-secondary">
        {suggestion.weight ? (
          <>
            <span className="font-semibold text-groww-text-primary">Funded by trimming: </span>
            {suggestion.funding.slice(0, 4).map((f) => `${f.name} ${pct(f.weight, 2)}`).join(', ')}
            {suggestion.funding.length > 4 ? ` and ${suggestion.funding.length - 4} more` : ''}
          </>
        ) : (
          suggestion.sizing_note
        )}
      </p>

      <table className="mt-3 w-full text-xs">
        <thead>
          <tr className="text-[11px] text-groww-text-muted">
            <th className="text-left font-medium pb-1">
              Portfolio with {suggestion.symbol} at {pct(suggestion.tested_at)}
            </th>
            <th className="text-right font-medium pb-1 px-2">Before</th>
            <th className="text-right font-medium pb-1 pl-2">After</th>
          </tr>
        </thead>
        <tbody>
          <BeforeAfter label="Volatility, a year" before={pct(metrics.vol.before)} after={pct(metrics.vol.after)} />
          <BeforeAfter label="CVaR, 1 day at 95%" before={pct(metrics.cvar.before, 2)} after={pct(metrics.cvar.after, 2)} />
          <BeforeAfter
            label="Sharpe ratio"
            note={`90% interval after: ${interval(metrics.sharpe.after_interval)}`}
            before={num(metrics.sharpe.before)}
            after={num(metrics.sharpe.after)}
          />
          <BeforeAfter label="Effective number of positions" before={num(metrics.effective_n.before, 1)} after={num(metrics.effective_n.after, 1)} />
          <BeforeAfter label="Largest share of risk" before={pct(metrics.top_risk_share.before, 0)} after={pct(metrics.top_risk_share.after, 0)} />
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-groww-text-muted">
        {metrics.basis}, last {metrics.sessions} sessions. Correlation with the portfolio {num(metrics.correlation)}.
      </p>

      <button
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="mt-2 text-[11px] font-semibold text-groww-green hover:text-groww-green-dark"
      >
        {open ? 'Hide' : 'Show'} risks, bear case and what would change this view
      </button>
      {open && (
        <div className="mt-2 flex flex-col gap-2.5 text-xs leading-relaxed">
          <div>
            <p className="font-semibold text-groww-text-primary">Fundamentals</p>
            <p className="text-groww-text-secondary">
              Market value {crore(fundamentals.market_cap)} · return on equity {pct(fundamentals.roe)} · profit margin{' '}
              {pct(fundamentals.margin)} · debt to equity {num(fundamentals.debt_to_equity, 1)} · P/E {num(fundamentals.pe, 1)}
              {fundamentals.pe_vs_sector !== null && ` (${num(fundamentals.pe_vs_sector)} times the sector median)`} · 6-month
              price change {pct(suggestion.momentum_6m, 1, true)}
            </p>
          </div>
          <div>
            <p className="font-semibold text-groww-text-primary">Entry risks</p>
            <ul className="list-disc pl-4 text-groww-text-secondary">
              {suggestion.entry_risks.map((risk, index) => (
                <li key={index}>{risk}</li>
              ))}
            </ul>
          </div>
          <div>
            <p className="font-semibold text-groww-text-primary">Bear case</p>
            <p className="text-groww-text-secondary">{suggestion.bear_case}</p>
          </div>
          <div>
            <p className="font-semibold text-groww-text-primary">What would change this view</p>
            <ul className="list-disc pl-4 text-groww-text-secondary">
              {suggestion.triggers.map((trigger, index) => (
                <li key={index}>{trigger}</li>
              ))}
            </ul>
          </div>
          <p className="text-[11px] text-groww-text-muted">
            {suggestion.model_drivers.note}
            {suggestion.model_drivers.top.length > 0 &&
              ` Largest: ${suggestion.model_drivers.top.map(([name, share]) => `${name} ${pct(share, 0)}`).join(', ')}.`}
          </p>
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
const IdeasPage: React.FC = () => {
  const { user } = useAuth()
  const [state, setState] = useState<Loadable>({ status: 'loading' })
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(
    async (refresh = false) => {
      if (!user?.id) return
      setRefreshing(true)
      try {
        const { data: rows, error } = await supabase
          .from('portfolio_holdings')
          .select('name, symbol, isin, units, buy_price, type')
          .eq('user_id', user.id)
        const holdings = error ? null : (rows ?? []).filter((h) => !isLeftover(h))
        const response = await fetch(`${BACKEND_URL}/api/advisor/suggestions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ holdings, refresh }),
        })
        if (!response.ok) throw new Error(`Request failed (${response.status})`)
        setState({ status: 'ready', data: await response.json() })
      } catch (e) {
        const message = e instanceof TypeError ? 'Could not reach the backend.' : (e as Error).message
        setState((previous) => (previous.status === 'ready' ? previous : { status: 'error', message }))
      } finally {
        setRefreshing(false)
      }
    },
    [user?.id],
  )

  useEffect(() => {
    load()
  }, [load])

  const data = state.status === 'ready' ? state.data : null
  const ok = data?.status === 'ok'
  const sizing = data?.sizing
  const changes = (sizing?.weights ?? []).filter((row) => Math.abs(row.change) >= 0.0005)
  // A flag every sector carries is said once under the table, not eleven times.
  const sharedFlags = (data?.sectors[0]?.flags ?? []).filter((flag) => data?.sectors.every((row) => row.flags.includes(flag)))

  return (
    <div id="ideas-page" className="flex flex-col min-h-full">
      <header
        className="sticky top-0 z-20 bg-white border-b border-groww-border-light px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between gap-3"
        style={{ minHeight: '60px' }}
      >
        <div>
          <h1 className="text-lg font-bold text-groww-text-primary">Ideas</h1>
          <p className="text-xs text-groww-text-muted mt-0.5">
            {ok && data
              ? `Sector and stock suggestions for your portfolio · prices to ${shortDate(data.as_of)}`
              : 'Sector and stock suggestions for your portfolio'}
          </p>
        </div>
        <button
          id="ideas-refresh"
          onClick={() => load(true)}
          disabled={refreshing}
          className="shrink-0 px-3 py-1.5 rounded-xl border border-groww-border text-xs font-semibold text-groww-text-secondary hover:border-groww-green hover:text-groww-green disabled:opacity-60"
        >
          {refreshing ? 'Working…' : 'Recalculate'}
        </button>
      </header>

      <div className="flex-1 p-4 sm:p-6 max-w-[1200px] w-full mx-auto flex flex-col gap-4 sm:gap-6">
        {state.status === 'loading' && (
          <p className="py-24 text-center text-sm text-groww-text-secondary">
            Ranking sectors and testing additions against your portfolio. This takes about 15 seconds…
          </p>
        )}
        {state.status === 'error' && (
          <p className="rounded-xl bg-red-50 border border-red-100 text-red-600 text-sm px-4 py-3">{state.message}</p>
        )}
        {data && !ok && (
          <p id="ideas-insufficient" className="rounded-xl bg-amber-50 border border-amber-100 text-amber-800 text-sm px-4 py-3">
            Insufficient data: {data.reason}
          </p>
        )}

        {ok && data && sizing && (
          <>
            {data.portfolio_source === 'sample' && (
              <p className="rounded-xl bg-amber-50 border border-amber-100 text-amber-800 text-xs px-4 py-2.5">
                No holdings with a symbol and quantity were found, so this page analyses a sample portfolio.
              </p>
            )}

            {/* The verdict comes first: whether the walk-forward test supports changing anything */}
            <section
              id="ideas-verdict"
              className={`rounded-2xl border p-4 sm:p-5 ${
                sizing.recommend_change ? 'bg-groww-green-pale border-groww-green/30' : 'bg-amber-50 border-amber-200'
              }`}
            >
              <p className="text-[11px] font-bold uppercase tracking-wide text-groww-text-muted">
                {sizing.recommend_change ? 'Recommendation: make the additions' : 'Recommendation: no change'}
              </p>
              <p className="mt-1 text-sm font-semibold text-groww-text-primary">
                {sizing.verdict ?? `Sizing unavailable: ${sizing.reason}.`}
              </p>
              {data.narrative && <p className="mt-2 text-xs text-groww-text-primary leading-relaxed whitespace-pre-line">{data.narrative}</p>}
              <p className="mt-2 text-[11px] text-groww-text-muted">Summary: {data.narrator}.</p>
            </section>

            <Card
              id="ideas-gaps"
              title="Where the portfolio has gaps"
              subtitle={`${data.diagnosis.holdings} holdings, spread like ${data.diagnosis.effective_n.toFixed(1)} equal positions by value and ${data.diagnosis.effective_n_risk.toFixed(1)} by risk · beta to the Nifty ${num(data.diagnosis.market_beta)}`}
            >
              <ol className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {data.diagnosis.gaps.map((gap, index) => (
                  <li key={gap.title} className="rounded-xl bg-groww-bg-primary border border-groww-border-light px-3.5 py-3">
                    <p className="text-[11px] font-bold text-groww-text-muted">Gap {index + 1}</p>
                    <p className="mt-0.5 text-sm font-semibold text-groww-text-primary">{gap.title}</p>
                    <p className="mt-1 text-xs text-groww-text-secondary leading-relaxed">{gap.detail}</p>
                  </li>
                ))}
              </ol>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[420px] text-xs tabular-nums">
                  <thead>
                    <tr className="text-left text-[11px] text-groww-text-muted">
                      <th className="font-medium py-1.5">Sector held</th>
                      <th className="font-medium py-1.5 text-right">Share of value</th>
                      <th className="font-medium py-1.5 text-right">Reference</th>
                      <th className="font-medium py-1.5 text-right">Share of risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.diagnosis.sectors.map((row) => (
                      <tr key={row.sector} className="border-t border-groww-border-light">
                        <td className="py-1.5 text-groww-text-primary">{row.sector}</td>
                        <td className="py-1.5 text-right">{pct(row.weight, 0)}</td>
                        <td className="py-1.5 text-right text-groww-text-muted">{pct(row.reference, 0)}</td>
                        <td className="py-1.5 text-right font-semibold text-groww-text-primary">{pct(row.risk_share, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-groww-text-muted">
                Reference: {data.diagnosis.reference}.{' '}
                {data.diagnosis.macro_betas
                  ? `For a 1% weekly move, the portfolio moves ${num(data.diagnosis.macro_betas.brent, 2, true)}% with Brent, ${num(data.diagnosis.macro_betas.usdinr, 2, true)}% with USD/INR and ${num(data.diagnosis.macro_betas.vix, 2, true)}% with India VIX (${data.diagnosis.macro_betas.weeks} weeks).`
                  : `Macro sensitivities: ${NONE}.`}
              </p>
            </Card>

            <Card
              id="ideas-sectors"
              title="Sector ranking"
              subtitle={`Score out of 100 = ${Object.entries(data.config.sector_weights)
                .map(([name, weight]) => `${SCORE_LABELS[name].toLowerCase()} ${weight}`)
                .join(' + ')}. Each sub-score is the sector's percentile among the eleven.${
                data.regime.length ? ` Stress flags raised: ${data.regime.join(', ')}.` : ' No macro stress flag is raised.'
              }`}
            >
              <div className="overflow-x-auto -mx-1">
                <table className="w-full min-w-[820px] text-xs">
                  <thead>
                    <tr className="text-left text-[11px] text-groww-text-muted">
                      <th className="font-medium py-1.5 px-1">#</th>
                      <th className="font-medium py-1.5 px-1">Sector</th>
                      <th className="font-medium py-1.5 px-1 text-right">Score</th>
                      {Object.keys(SCORE_LABELS).map((key) => (
                        <th key={key} className="font-medium py-1.5 px-1">{SCORE_LABELS[key]}</th>
                      ))}
                      <th className="font-medium py-1.5 px-1 text-right">Volatility if added at 5%</th>
                      <th className="font-medium py-1.5 px-1 text-right">3-month vs Nifty</th>
                      <th className="font-medium py-1.5 px-1 text-right">News tone</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sectors.map((row) => (
                      <React.Fragment key={row.sector}>
                        <tr className={`border-t border-groww-border-light ${row.rank <= 3 ? 'bg-groww-green-pale/60' : ''}`}>
                          <td className="py-2 px-1 text-groww-text-muted tabular-nums">{row.rank}</td>
                          <td className="py-2 px-1 font-semibold text-groww-text-primary">{row.sector}</td>
                          <td className="py-2 px-1 text-right font-bold text-groww-text-primary tabular-nums">{row.composite.toFixed(1)}</td>
                          {(Object.keys(SCORE_LABELS) as (keyof SectorRow['scores'])[]).map((key) => (
                            <td key={key} className="py-2 px-1"><Score value={row.scores[key]} /></td>
                          ))}
                          <td className="py-2 px-1 text-right tabular-nums">{row.fit ? pct(row.fit.vol_change, 2, true) : NONE}</td>
                          <td className="py-2 px-1 text-right tabular-nums">{pct(row.trend.relative_strength['3m'], 1, true)}</td>
                          <td className="py-2 px-1 text-right tabular-nums">
                            {num(row.evidence.sentiment, 2, true)}
                            <span className="text-groww-text-muted"> ({row.evidence.headlines})</span>
                          </td>
                        </tr>
                        {row.flags.some((flag) => !sharedFlags.includes(flag)) && (
                          <tr>
                            <td />
                            <td colSpan={9} className="pb-2 px-1 text-[11px] text-amber-700">
                              {row.flags.filter((flag) => !sharedFlags.includes(flag)).join(' · ')}
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
              {sharedFlags.length > 0 && (
                <p id="ideas-shared-flags" className="mt-2 text-[11px] text-amber-700">
                  Applies to every sector: {sharedFlags.join(' · ')}.
                </p>
              )}
              <p className="mt-2 text-[11px] text-groww-text-muted">
                The top three are highlighted. News tone runs from -1 to +1, with the number of headlines in brackets.
              </p>
            </Card>

            <Card
              id="ideas-sizing"
              title="Sizing and the walk-forward check"
              subtitle={sizing.method}
            >
              {sizing.status !== 'ok' ? (
                <p className="text-sm text-groww-text-secondary">Insufficient data: {sizing.reason}.</p>
              ) : (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                  <div>
                    <table className="w-full text-xs tabular-nums">
                      <thead>
                        <tr className="text-left text-[11px] text-groww-text-muted">
                          <th className="font-medium py-1.5">Holding</th>
                          <th className="font-medium py-1.5 text-right">Now</th>
                          <th className="font-medium py-1.5 text-right">Proposed</th>
                          <th className="font-medium py-1.5 text-right">Trade</th>
                        </tr>
                      </thead>
                      <tbody>
                        {changes.map((row) => (
                          <tr key={row.name} className="border-t border-groww-border-light">
                            <td className="py-1.5 text-groww-text-primary">
                              {row.name}
                              {row.new && <span className="ml-1.5 px-1 rounded bg-groww-green-light text-groww-green text-[10px] font-semibold">new</span>}
                            </td>
                            <td className="py-1.5 text-right">{pct(row.current)}</td>
                            <td className="py-1.5 text-right font-semibold text-groww-text-primary">{pct(row.proposed)}</td>
                            <td className="py-1.5 text-right">{row.change > 0 ? 'Buy' : 'Sell'} {inr(row.trade_value)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="mt-2 text-[11px] text-groww-text-muted">
                      Estimated trade cost {inr(sizing.trade_cost ?? 0)} for moving {pct(sizing.turnover_share, 0)} of the portfolio
                      {sizing.liquidity_unknown?.length ? '; liquidity was unknown for some names, so a flat cost was used' : ''}.
                    </p>
                  </div>
                  {sizing.walk_forward && (
                    <div>
                      <table className="w-full text-xs tabular-nums">
                        <thead>
                          <tr className="text-left text-[11px] text-groww-text-muted">
                            <th className="font-medium py-1.5">Out of sample</th>
                            <th className="font-medium py-1.5 text-right">Sharpe (90% interval)</th>
                            <th className="font-medium py-1.5 text-right">Volatility</th>
                            <th className="font-medium py-1.5 text-right">Worst fall</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(Object.keys(WALK_LABELS) as (keyof typeof WALK_LABELS)[]).map((key) => {
                            const result = sizing.walk_forward!.results[key]
                            return (
                              <tr key={key} className="border-t border-groww-border-light">
                                <td className="py-1.5 font-semibold text-groww-text-primary">{WALK_LABELS[key]}</td>
                                <td className="py-1.5 text-right">
                                  {num(result.sharpe)}
                                  <span className="block text-[10px] text-groww-text-muted">{interval(result.sharpe_interval)}</span>
                                </td>
                                <td className="py-1.5 text-right">{pct(result.vol)}</td>
                                <td className="py-1.5 text-right">{pct(result.max_drawdown)}</td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                      <p className="mt-2 text-[11px] text-groww-text-muted">
                        {sizing.walk_forward.results.proposed.n} sessions out of sample: {sizing.walk_forward.rebalances} rebalances,
                        each held {sizing.walk_forward.hold_sessions} sessions and sized on the {sizing.walk_forward.lookback_sessions} before
                        it. {sizing.walk_forward.note} Sharpe ratios assume a risk-free rate of {pct(data.config.risk_free_rate)}.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </Card>

            <Card
              id="ideas-suggestions"
              title="Stock suggestions"
              subtitle={`Up to three stocks in each of the top three sectors: ${data.suggestions.length} in all`}
            >
              {data.suggestions.length === 0 ? (
                <p className="text-sm text-groww-text-secondary">No stock in the top sectors passed the filters.</p>
              ) : (
                <ul className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
                  {data.suggestions.map((suggestion) => (
                    <SuggestionCard key={suggestion.symbol} suggestion={suggestion} recommended={Boolean(sizing.recommend_change)} />
                  ))}
                </ul>
              )}
              <div className="mt-4 text-[11px] text-groww-text-muted leading-relaxed">
                <p className="font-semibold text-groww-text-secondary">Left out, and why</p>
                {data.picks.map((group) => (
                  <p key={group.sector}>
                    {group.sector} ({group.considered} considered):{' '}
                    {group.excluded.length ? group.excluded.map((e) => `${e.symbol}, ${e.reason}`).join('; ') : 'none excluded'}.
                  </p>
                ))}
              </div>
            </Card>

            <div id="ideas-notes" className="text-[11px] text-groww-text-muted leading-relaxed">
              {data.data_notes.map((note, index) => (
                <p key={index}>{note}</p>
              ))}
              <p className="mt-2 font-semibold text-groww-text-secondary">{data.footer}</p>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default IdeasPage
