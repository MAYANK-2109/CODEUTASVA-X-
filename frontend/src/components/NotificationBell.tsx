import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { isLeftover } from '../lib/holdings'
import { onOpenAlerts, publishAlerts } from '../lib/alerts'
import type {
  Alert, AlertsResult, HoldingRisk, PaperAccount, PaperExecution, PaperOrder, PaperPolicy, RiskModel, Severity,
  SolutionAction,
} from '../lib/alerts'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const BACKEND_URL: string =
  import.meta.env.VITE_BACKEND_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const REFRESH_MS = 180_000

// Status is never shown by colour alone: each level has its own mark and label.
const SEVERITY: Record<Severity, { mark: string; label: string; chip: string; border: string }> = {
  critical: { mark: '▲', label: 'Critical', chip: 'bg-red-50 text-red-600', border: 'border-l-red-500' },
  warning: { mark: '●', label: 'Warning', chip: 'bg-amber-50 text-amber-700', border: 'border-l-amber-500' },
  info: { mark: 'i', label: 'Info', chip: 'bg-gray-100 text-groww-text-secondary', border: 'border-l-gray-300' },
}
const NEEDS_ATTENTION: Severity[] = ['critical', 'warning']

// The action a solution asks for. Each has its own word, so colour is never the only cue.
const ACTION: Record<SolutionAction, { label: string; chip: string }> = {
  hedge: { label: 'Hedge', chip: 'bg-red-50 text-red-600' },
  trim: { label: 'Trim', chip: 'bg-amber-50 text-amber-700' },
  rebalance: { label: 'Rebalance', chip: 'bg-amber-50 text-amber-700' },
  review: { label: 'Review', chip: 'bg-amber-50 text-amber-700' },
  watch: { label: 'Watch', chip: 'bg-gray-100 text-groww-text-secondary' },
  hold: { label: 'Hold', chip: 'bg-groww-green-light text-groww-green' },
}

const inr = (value: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)

const readIds = (key: string): string[] => {
  try {
    return JSON.parse(localStorage.getItem(key) ?? '[]')
  } catch {
    return []
  }
}
const writeIds = (key: string, ids: string[]) => {
  try {
    localStorage.setItem(key, JSON.stringify(ids.slice(-200)))
  } catch {
    // Storage can be unavailable; the badge then simply resets on reload.
  }
}

const percent = (fraction: number, digits = 0) => `${(Math.abs(fraction) * 100).toFixed(digits)}%`

const orderText = (order: PaperOrder) =>
  order.type === 'sell'
    ? `Sell ${order.quantity?.toLocaleString('en-IN')} ${order.name} @ ${inr(order.price ?? 0)}`
    : `Buy put on ${inr(order.notional ?? 0)} of ${order.name}, about ${inr(order.premium ?? 0)}`

const time = (iso: string) =>
  new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

// What the paper account did with one alert: the fills, the slippage and the VaR check.
const ExecutionResult: React.FC<{ execution: PaperExecution; showStates?: boolean }> = ({ execution, showStates }) => {
  const filled = execution.state === 'FILLED'
  const check = execution.verification
  return (
    <div
      className={`paper-execution mt-2 rounded-xl border px-3 py-2 text-[11px] leading-relaxed ${
        filled ? 'bg-groww-green-pale border-groww-green/30' : 'bg-red-50 border-red-100'
      }`}
    >
      <p className={`font-bold ${filled ? 'text-groww-green-dark' : 'text-red-600'}`}>
        {filled ? '✓ Filled in the paper account' : '✕ Rejected'} · {execution.mode === 'auto' ? 'by your policy' : 'one click'} ·{' '}
        {time(execution.created_at)}
      </p>
      {execution.fills.map((fill, index) => (
        <p key={index} className="text-groww-text-primary tabular-nums">
          {fill.type === 'sell'
            ? `Sold ${fill.quantity?.toLocaleString('en-IN')} ${fill.name} @ ₹${fill.fill_price.toFixed(2)} (reference ₹${fill.price?.toFixed(2)})`
            : `Bought put on ${inr(fill.notional ?? 0)} of ${fill.name} for ${inr(fill.fill_price)}`}
          <span className="text-groww-text-muted">
            {' '}
            · slippage {inr(fill.slippage_amount)} ({(fill.slippage_bps / 100).toFixed(2)}%
            {fill.liquidity_known ? '' : ', liquidity unknown'})
          </span>
        </p>
      ))}
      {!filled && <p className="text-groww-text-primary">{execution.events[execution.events.length - 1]?.detail}</p>}
      {check && (
        <p className="mt-1 font-semibold text-groww-text-primary tabular-nums">
          {check.horizon_sessions}-session 95% VaR: {inr(check.var_before)} → {inr(check.var_after)}
          <span className="font-normal text-groww-text-muted">
            {' '}
            ({check.reduction >= 0 ? 'down' : 'up'} {inr(Math.abs(check.reduction))})
          </span>
        </p>
      )}
      {showStates && (
        <ol className="mt-1.5 flex flex-col gap-0.5 text-groww-text-secondary">
          {execution.events.map((event, index) => (
            <li key={index}>
              <span className="font-semibold text-groww-text-primary">{event.state}</span> {event.detail}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Paper account: the auto-execution policy and the ledger of what was traded
// ---------------------------------------------------------------------------
const PaperLedger: React.FC<{
  account: PaperAccount | null
  onPolicy: (policy: PaperPolicy) => void
}> = ({ account, onPolicy }) => {
  if (!account) {
    return <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">The paper account could not be loaded.</p>
  }
  const { policy, summary } = account
  const percentInput = (value: number, change: (fraction: number) => void, id: string) => (
    <input
      id={id}
      type="number"
      min={0.1}
      max={50}
      step={0.5}
      value={Number((value * 100).toFixed(1))}
      onChange={(e) => change(Math.max(0.001, Number(e.target.value) / 100))}
      className="w-14 rounded-lg border border-groww-border bg-white px-1.5 py-0.5 text-[11px] text-right tabular-nums outline-none focus:border-groww-green"
    />
  )
  return (
    <div id="alerts-ledger" className="px-4 py-3 flex flex-col gap-3">
      <p className="text-[11px] text-groww-text-secondary">
        {account.broker}. Trades here are simulated and never reach a real broker.
      </p>

      <div className="rounded-xl border border-groww-border-light px-3 py-2.5">
        <label className="flex items-center gap-2 text-xs font-semibold text-groww-text-primary">
          <input
            id="paper-policy-enabled"
            type="checkbox"
            checked={policy.enabled}
            onChange={(e) => onPolicy({ ...policy, enabled: e.target.checked })}
            className="accent-groww-green"
          />
          Autonomous mode: execute hedge and trim alerts by itself
        </label>
        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-groww-text-secondary">
          Only when the downside is at least
          {percentInput(policy.min_downside, (min_downside) => onPolicy({ ...policy, min_downside }), 'paper-policy-downside')}
          % of the position, and a put costs no more than
          {percentInput(policy.max_put_cost, (max_put_cost) => onPolicy({ ...policy, max_put_cost }), 'paper-policy-cost')}
          % of it.
        </p>
        <p className="mt-1 text-[11px] text-groww-text-muted">
          Weather and news signals also need high confidence. Each alert is executed at most once a day.
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
        {[
          ['Orders filled', `${summary.filled}${summary.rejected ? ` (${summary.rejected} rejected)` : ''}`],
          ['Slippage paid', inr(summary.slippage_cost)],
          ['Cash from sales', inr(summary.cash_from_sales)],
          ['Put premium paid', inr(summary.premium_paid)],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-groww-text-muted">{label}</dt>
            <dd className="font-semibold text-groww-text-primary tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {account.executions.length === 0 ? (
        <p className="py-4 text-center text-xs text-groww-text-secondary">
          Nothing traded yet. Hedge, trim and rebalance alerts have an Execute button.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {account.executions.map((execution) => (
            <li key={execution.id}>
              <p className="text-xs font-semibold text-groww-text-primary">{execution.title}</p>
              <p className="text-[11px] text-groww-text-secondary">{execution.detail}</p>
              <ExecutionResult execution={execution} showStates />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// One alert: what happened, then the recommended action with its steps
// ---------------------------------------------------------------------------
const AlertItem: React.FC<{
  alert: Alert
  execution: PaperExecution | undefined
  onExecute: (alert: Alert) => Promise<string | null>
}> = ({ alert, execution, onExecute }) => {
  const style = SEVERITY[alert.severity]
  const { solution } = alert
  // Alerts that need attention open with their steps; the rest show the action and expand on request.
  const [open, setOpen] = useState(NEEDS_ATTENTION.includes(alert.severity) || Boolean(alert.hedge?.drill))
  const [showTrail, setShowTrail] = useState(true)
  const [executing, setExecuting] = useState(false)
  const [executeError, setExecuteError] = useState<string | null>(null)
  const trail = alert.trail ?? []
  const orders = alert.hedge?.drill ? [] : (solution.orders ?? [])
  const run = async () => {
    setExecuting(true)
    setExecuteError(await onExecute(alert))
    setExecuting(false)
  }
  return (
    <li className={`px-4 py-3 border-b border-groww-border-light border-l-4 ${style.border}`}>
      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${style.chip}`}>
        <span aria-hidden>{style.mark}</span>
        {style.label}
      </span>
      <p className="mt-1.5 text-sm font-semibold text-groww-text-primary leading-snug">{alert.title}</p>
      <p className="mt-1 text-xs text-groww-text-secondary leading-relaxed">{alert.detail}</p>

      <div className="alert-solution mt-2 rounded-xl bg-groww-bg-primary border border-groww-border-light px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-bold uppercase tracking-wide text-groww-text-muted">Recommended action</span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${ACTION[solution.action].chip}`}>
            {ACTION[solution.action].label}
          </span>
        </div>
        <p className="mt-1 text-[13px] font-semibold text-groww-text-primary leading-snug">{solution.headline}</p>

        {open && (
          <>
            {solution.figures.length > 0 && (
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                {solution.figures.map((figure) => (
                  <div key={figure.label}>
                    <dt className="text-groww-text-muted">{figure.label}</dt>
                    <dd className="font-semibold text-groww-text-primary tabular-nums">{figure.value}</dd>
                  </div>
                ))}
              </dl>
            )}
            <ol className="mt-2 flex flex-col gap-1.5 text-xs text-groww-text-primary leading-relaxed">
              {solution.steps.map((step, index) => (
                <li key={index} className="flex gap-2">
                  <span className="shrink-0 w-4 h-4 mt-0.5 rounded-full bg-white border border-groww-border text-[10px] font-bold text-groww-text-secondary flex items-center justify-center">
                    {index + 1}
                  </span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
            {solution.alternative && (
              <p className="mt-2 pt-2 border-t border-groww-border-light text-[11px] text-groww-text-secondary leading-relaxed">
                <span className="font-semibold text-groww-text-primary">Alternative: </span>
                {solution.alternative}
              </p>
            )}
          </>
        )}
        <button
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="mt-2 text-[11px] font-semibold text-groww-green hover:text-groww-green-dark"
        >
          {open ? 'Hide steps' : `Show ${solution.steps.length} step${solution.steps.length === 1 ? '' : 's'} and figures`}
        </button>

        {/* One-click execution in the paper account */}
        {orders.length > 0 && !execution && (
          <div className="mt-2 pt-2 border-t border-groww-border-light">
            <button
              onClick={run}
              disabled={executing}
              className="paper-execute w-full rounded-lg bg-groww-green text-white text-xs font-bold px-3 py-2 hover:bg-groww-green-dark disabled:opacity-60 transition-colors"
            >
              {executing ? 'Executing…' : `Execute in paper account: ${orderText(orders[0])}`}
              {!executing && orders.length > 1 ? ` and ${orders.length - 1} more` : ''}
            </button>
            <p className="mt-1 text-[10px] text-groww-text-muted">
              Simulated fill with slippage; no real order is placed. The portfolio VaR is rechecked after the fill.
            </p>
            {executeError && <p className="mt-1 text-[11px] text-red-600">{executeError}</p>}
          </div>
        )}
        {execution && <ExecutionResult execution={execution} />}
      </div>

      {/* Evidence trail: from the signal to the decision, one step at a time */}
      {trail.length > 0 && (
        <div className="alert-trail mt-2">
          <button
            onClick={() => setShowTrail((value) => !value)}
            aria-expanded={showTrail}
            className="inline-flex items-center gap-1.5 text-[11px] font-bold text-groww-green hover:text-groww-green-dark"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M9 6h11M9 12h11M9 18h11" />
              <circle cx="4" cy="6" r="1.2" />
              <circle cx="4" cy="12" r="1.2" />
              <circle cx="4" cy="18" r="1.2" />
            </svg>
            {showTrail ? `Evidence trail: how this was worked out (${trail.length} steps) · hide` : `Show evidence trail (${trail.length} steps)`}
          </button>
          {showTrail && (
            <ol className="mt-2 flex flex-col">
              {trail.map((step, index) => (
                <li key={index} className="relative flex gap-2.5 pb-3 last:pb-0">
                  {/* The line that joins one step to the next */}
                  {index < trail.length - 1 && (
                    <span className="absolute left-[9px] top-5 bottom-0 w-px bg-groww-border" aria-hidden />
                  )}
                  <span className="relative shrink-0 w-[19px] h-[19px] rounded-full bg-groww-green text-white text-[10px] font-bold flex items-center justify-center">
                    {index + 1}
                  </span>
                  <div className="min-w-0 text-xs leading-relaxed">
                    <p className="font-semibold text-groww-text-primary">{step.title}</p>
                    <p className="text-groww-text-secondary">{step.finding}</p>
                    {step.method && (
                      <p className="mt-0.5 text-[11px] text-groww-text-muted">
                        <span className="font-semibold">How: </span>
                        {step.method}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Risk forecast: every holding as the impact model scores it for the week ahead
// ---------------------------------------------------------------------------
const RiskForecast: React.FC<{ ranking: HoldingRisk[]; model: RiskModel | null }> = ({ ranking, model }) => {
  if (ranking.length === 0) {
    return (
      <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">
        The impact model could not score your holdings this time, because its macro data was unavailable.
      </p>
    )
  }
  const highest = Math.max(...ranking.map((row) => row.fall))
  const fallSize = percent(model?.fall_size ?? 0.05)
  const sessions = model?.horizon_sessions ?? 5
  return (
    <div id="alerts-forecast" className="px-4 py-3">
      <table className="w-full text-xs tabular-nums">
        <caption className="text-left text-[11px] text-groww-text-secondary pb-2">
          The next {sessions} sessions for each holding, largest rupee downside first.
        </caption>
        <thead>
          <tr className="text-left text-[11px] text-groww-text-muted">
            <th className="font-medium pb-1.5">Holding</th>
            <th className="font-medium pb-1.5">Chance of a {fallSize}+ fall</th>
            <th className="font-medium pb-1.5 text-right">Downside, 1 in 20</th>
          </tr>
        </thead>
        <tbody>
          {ranking.map((row) => (
            <tr key={row.name} className="border-t border-groww-border-light align-top">
              <td className="py-2 pr-2">
                <span className="font-semibold text-groww-text-primary">{row.name}</span>
                <span className="block text-[11px] text-groww-text-muted">{percent(row.weight)} of portfolio</span>
              </td>
              <td className="py-2 pr-2 w-[104px]">
                <span className="flex items-center gap-2">
                  <span className="relative flex-1 h-1.5 rounded-full bg-gray-100" aria-hidden>
                    <span
                      className={`absolute inset-y-0 left-0 rounded-full ${row.elevated ? 'bg-red-500' : 'bg-gray-400'}`}
                      style={{ width: `${Math.max(4, (row.fall / highest) * 100)}%` }}
                    />
                  </span>
                  <span className="w-8 text-right font-semibold text-groww-text-primary">{percent(row.fall)}</span>
                </span>
                {row.elevated && <span className="block text-[11px] font-semibold text-red-600">▲ Elevated</span>}
              </td>
              <td className="py-2 text-right whitespace-nowrap">
                <span className="font-semibold text-groww-text-primary">{inr(row.downside_amount)}</span>
                <span className="block text-[11px] text-groww-text-muted">
                  −{percent(row.downside, 1)} · usual −{percent(row.usual_downside, 1)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {model?.rate_when_elevated !== undefined && model.rate_otherwise !== undefined && (
        <p className="mt-3 text-[11px] text-groww-text-muted leading-relaxed">
          {model.name}, tested on {model.tested_on.replace(' to ', ' – ')}: on days it flagged, a fall of {fallSize} or
          more followed {percent(model.rate_when_elevated)} of the time, against {percent(model.rate_otherwise)} otherwise.
          AUC {model.auc} using price, macro, sector and weather data; {model.auc_price_only} from price history alone.
          Downside is the loss that only one week in twenty should exceed.
        </p>
      )}
    </div>
  )
}

const NotificationBell: React.FC = () => {
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<AlertsResult | null>(null)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [seen, setSeen] = useState<string[]>([])
  const [drills, setDrills] = useState<{ id: string; title: string }[]>([])
  const [scenario, setScenario] = useState('')
  const [tab, setTab] = useState<'alerts' | 'forecast' | 'ledger'>('alerts')
  const [account, setAccount] = useState<PaperAccount | null>(null)
  const holdingsRef = useRef<unknown[] | null>(null)
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(
    typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,
  )
  const rootRef = useRef<HTMLDivElement>(null)

  const seenKey = `alerts_seen_${user?.id ?? 'anon'}`
  const notifiedKey = `alerts_notified_${user?.id ?? 'anon'}`

  useEffect(() => {
    setSeen(readIds(seenKey))
  }, [seenKey])

  const loadAccount = useCallback(async () => {
    if (!user?.id) return
    try {
      const response = await fetch(`${BACKEND_URL}/api/broker/account?user_id=${encodeURIComponent(user.id)}`)
      if (response.ok) setAccount(await response.json())
    } catch {
      // The ledger tab says it could not be loaded.
    }
  }, [user?.id])

  // Returns an error message, or null when the order went through to the ledger.
  const execute = async (alert: Alert): Promise<string | null> => {
    if (!user?.id) return 'Sign in to use the paper account.'
    try {
      const response = await fetch(`${BACKEND_URL}/api/broker/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: user.id, alert_id: alert.id, holdings: holdingsRef.current, scenario: scenario || null,
        }),
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => null)
        return detail?.detail ?? `The order could not be placed (${response.status}).`
      }
      await loadAccount()
      return null
    } catch {
      return 'The paper account could not be reached.'
    }
  }

  const setPolicy = async (policy: PaperPolicy) => {
    if (!user?.id) return
    setAccount((current) => (current ? { ...current, policy } : current))
    await fetch(`${BACKEND_URL}/api/broker/policy`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: user.id, ...policy }),
    }).catch(() => null)
  }

  const load = useCallback(async () => {
    if (!user?.id) return
    try {
      const { data, error } = await supabase
        .from('portfolio_holdings')
        .select('name, symbol, isin, units, buy_price, type')
        .eq('user_id', user.id)
      const holdings = error ? null : (data ?? []).filter((h) => !isLeftover(h))
      holdingsRef.current = holdings
      const response = await fetch(`${BACKEND_URL}/api/alerts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holdings, scenario: scenario || null }),
      })
      if (!response.ok) throw new Error(`Request failed (${response.status})`)
      const fresh: AlertsResult = await response.json()
      setResult(fresh)
      setFailed(false)
      publishAlerts({ result: fresh, failed: false, loading: false })

      // Autonomous mode: the backend executes what the user's policy approves; it does nothing when the policy is off.
      if (!scenario) {
        await fetch(`${BACKEND_URL}/api/broker/auto`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: user.id, holdings }),
        }).catch(() => null)
      }
      loadAccount()

      // Tell the browser about alerts it has not announced yet, if the user allowed it.
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        const notified = readIds(notifiedKey)
        const unannounced = fresh.alerts.filter(
          (a) => NEEDS_ATTENTION.includes(a.severity) && !a.hedge?.drill && !notified.includes(a.id),
        )
        unannounced.forEach((a) => new Notification(a.title, { body: a.recommendation, tag: a.id }))
        if (unannounced.length) writeIds(notifiedKey, [...notified, ...unannounced.map((a) => a.id)])
      }
    } catch {
      // Keep showing the last alerts; only flag that the refresh failed.
      setFailed(true)
      publishAlerts({ failed: true, loading: false })
    } finally {
      setLoading(false)
    }
  }, [user?.id, notifiedKey, scenario, loadAccount])

  useEffect(() => {
    fetch(`${BACKEND_URL}/api/alerts/drills`)
      .then((response) => (response.ok ? response.json() : { drills: [] }))
      .then((data) => setDrills(data.drills ?? []))
      .catch(() => setDrills([]))
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const alerts = result?.alerts ?? []
  // Today's execution per alert, so an alert that was traded shows its fill instead of the button.
  const today = new Date().toDateString()
  const todays = new Map(
    (account?.executions ?? [])
      .filter((execution) => new Date(execution.created_at).toDateString() === today)
      .map((execution) => [execution.alert_id, execution]),
  )
  const unseen = alerts.filter(
    (a) => NEEDS_ATTENTION.includes(a.severity) && !a.hedge?.drill && !seen.includes(a.id),
  ).length

  const show = (opening: boolean) => {
    setOpen(opening)
    if (opening && alerts.length) {
      const all = [...new Set([...seen, ...alerts.map((a) => a.id)])]
      setSeen(all)
      writeIds(seenKey, all)
    }
  }
  const toggle = () => show(!open)
  // The dashboard's risk overview can ask for the panel.
  const showRef = useRef(show)
  useEffect(() => {
    showRef.current = show
  })
  useEffect(() => onOpenAlerts(() => showRef.current(true)), [])

  const enableBrowserNotifications = async () => {
    if (typeof Notification === 'undefined') return
    const answer = await Notification.requestPermission()
    setPermission(answer)
    if (answer === 'granted') load()
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        id="dashboard-notifications-btn"
        onClick={toggle}
        aria-label={unseen ? `Alerts, ${unseen} new` : 'Alerts'}
        aria-expanded={open}
        className="relative w-9 h-9 rounded-xl border border-groww-border-light flex items-center justify-center text-groww-text-secondary hover:text-groww-green hover:bg-groww-green-light/40 transition-all duration-200"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unseen > 0 && (
          <span
            id="alerts-badge"
            className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center"
          >
            {unseen}
          </span>
        )}
      </button>

      {open && (
        <section
          id="alerts-panel"
          aria-label="Alerts"
          className="fixed sm:absolute z-40 inset-x-3 sm:inset-x-auto sm:right-0 top-16 sm:top-11 sm:w-[420px] max-h-[75vh] flex flex-col rounded-2xl bg-white border border-groww-border shadow-2xl overflow-hidden"
        >
          <header className="px-4 py-3 border-b border-groww-border-light">
            <h2 className="text-sm font-bold text-groww-text-primary">Alerts</h2>
            <p className="text-[11px] text-groww-text-muted mt-0.5">
              {result?.prices_as_of
                ? `From prices of ${new Date(result.prices_as_of).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}, checked ${new Date(result.generated_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}`
                : loading
                  ? 'Checking your holdings…'
                  : 'Not checked yet'}
              {failed && result && ' · last refresh failed'}
            </p>
            {result?.portfolio_source === 'sample' && (
              <p className="text-[11px] text-amber-700 mt-1">
                No holdings saved yet, so these alerts are for the sample portfolio.
              </p>
            )}
            {drills.length > 0 && (
              <label className="mt-2 flex items-center gap-2 text-[11px] text-groww-text-secondary">
                Rehearse a response
                <select
                  id="alerts-drill"
                  value={scenario}
                  onChange={(e) => setScenario(e.target.value)}
                  className="flex-1 min-w-0 rounded-lg border border-groww-border bg-white px-2 py-1 text-[11px] text-groww-text-primary outline-none focus:border-groww-green"
                >
                  <option value="">No drill (live signals only)</option>
                  {drills.map((drill) => (
                    <option key={drill.id} value={drill.id}>
                      {drill.title}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </header>

          <div className="flex border-b border-groww-border-light text-xs font-semibold" role="tablist">
            {(
              [
                ['alerts', `Alerts${result ? ` (${alerts.length})` : ''}`],
                ['forecast', 'Risk forecast'],
                ['ledger', `Paper ledger${account?.executions.length ? ` (${account.executions.length})` : ''}`],
              ] as const
            ).map(
              ([id, label]) => (
                <button
                  key={id}
                  id={`alerts-tab-${id}`}
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                  className={`flex-1 py-2 border-b-2 transition-colors ${
                    tab === id
                      ? 'border-groww-green text-groww-green'
                      : 'border-transparent text-groww-text-secondary hover:text-groww-text-primary'
                  }`}
                >
                  {label}
                </button>
              ),
            )}
          </div>

          <div className="flex-1 overflow-y-auto">
            {failed && !result && (
              <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">
                Alerts are unavailable right now. The backend could not be reached.
              </p>
            )}
            {loading && !result && !failed && (
              <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">Checking your holdings…</p>
            )}
            {result && tab === 'forecast' && <RiskForecast ranking={result.risk_ranking ?? []} model={result.risk_model} />}
            {tab === 'ledger' && <PaperLedger account={account} onPolicy={setPolicy} />}
            {result && tab === 'alerts' && alerts.length === 0 && (
              <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">
                Nothing needs your attention. No sudden moves, losses beyond normal risk, or negative news were
                found.
              </p>
            )}
            {tab === 'alerts' && (
              <ul>
                {alerts.map((alert) => (
                  <AlertItem
                    key={alert.id}
                    alert={alert}
                    execution={todays.get(alert.id)}
                    onExecute={execute}
                  />
                ))}
              </ul>
            )}
            {result && result.unavailable.length > 0 && (
              <p className="px-4 py-2 text-[11px] text-amber-700 bg-amber-50">
                Some checks could not run: {result.unavailable.join(', ')}.
              </p>
            )}
          </div>

          <footer className="px-4 py-2.5 border-t border-groww-border-light bg-groww-bg-primary flex items-center justify-between gap-3 text-[11px] text-groww-text-muted">
            <span>
              {result?.risk_model
                ? `${result.risk_model.name}, AUC ${result.risk_model.auc} on ${result.risk_model.tested_on.replace(' to ', ' – ')}`
                : 'Rechecked every 3 minutes'}
            </span>
            {permission === 'default' && (
              <button
                onClick={enableBrowserNotifications}
                className="shrink-0 font-semibold text-groww-green hover:text-groww-green-dark"
              >
                Notify me on this device
              </button>
            )}
            {permission === 'granted' && <span className="shrink-0">Device notifications on</span>}
            {permission === 'denied' && <span className="shrink-0">Device notifications blocked</span>}
          </footer>
        </section>
      )}
    </div>
  )
}

export default NotificationBell
