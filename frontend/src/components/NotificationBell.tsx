import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { isLeftover } from '../lib/holdings'
import { onOpenAlerts, publishAlerts } from '../lib/alerts'
import type { AlertHedge as Hedge, AlertsResult, Severity } from '../lib/alerts'

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

const HEDGE_ACTION: Record<Hedge['action'], { label: string; chip: string }> = {
  hedge: { label: 'Hedge recommended', chip: 'bg-red-50 text-red-600' },
  monitor: { label: 'Monitor, no hedge', chip: 'bg-amber-50 text-amber-700' },
  none: { label: 'No hedge needed', chip: 'bg-groww-green-light text-groww-green' },
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

const NotificationBell: React.FC = () => {
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<AlertsResult | null>(null)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [seen, setSeen] = useState<string[]>([])
  const [drills, setDrills] = useState<{ id: string; title: string }[]>([])
  const [scenario, setScenario] = useState('')
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(
    typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,
  )
  const rootRef = useRef<HTMLDivElement>(null)

  const seenKey = `alerts_seen_${user?.id ?? 'anon'}`
  const notifiedKey = `alerts_notified_${user?.id ?? 'anon'}`

  useEffect(() => {
    setSeen(readIds(seenKey))
  }, [seenKey])

  const load = useCallback(async () => {
    if (!user?.id) return
    try {
      const { data, error } = await supabase
        .from('portfolio_holdings')
        .select('name, symbol, isin, units, buy_price, type')
        .eq('user_id', user.id)
      const holdings = error ? null : (data ?? []).filter((h) => !isLeftover(h))
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
  }, [user?.id, notifiedKey, scenario])

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

          <div className="flex-1 overflow-y-auto">
            {failed && !result && (
              <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">
                Alerts are unavailable right now. The backend could not be reached.
              </p>
            )}
            {loading && !result && !failed && (
              <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">Checking your holdings…</p>
            )}
            {result && alerts.length === 0 && (
              <p className="px-4 py-8 text-center text-xs text-groww-text-secondary">
                Nothing needs your attention. No sudden moves, losses beyond normal risk, or negative news were
                found.
              </p>
            )}
            <ul>
              {alerts.map((alert) => {
                const style = SEVERITY[alert.severity]
                return (
                  <li key={alert.id} className={`px-4 py-3 border-b border-groww-border-light border-l-4 ${style.border}`}>
                    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${style.chip}`}>
                      <span aria-hidden>{style.mark}</span>
                      {style.label}
                    </span>
                    <p className="mt-1.5 text-sm font-semibold text-groww-text-primary leading-snug">{alert.title}</p>
                    <p className="mt-1 text-xs text-groww-text-secondary leading-relaxed">{alert.detail}</p>
                    {alert.hedge && (
                      <div className="mt-2 rounded-xl bg-groww-bg-primary border border-groww-border-light px-3 py-2">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${HEDGE_ACTION[alert.hedge.action].chip}`}>
                          {HEDGE_ACTION[alert.hedge.action].label}
                        </span>
                        <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                          <div>
                            <dt className="text-groww-text-muted">Expected {alert.hedge.expected_loss >= 0 ? 'loss' : 'gain'}</dt>
                            <dd className="font-semibold text-groww-text-primary tabular-nums">{inr(Math.abs(alert.hedge.expected_loss))}</dd>
                          </div>
                          <div>
                            <dt className="text-groww-text-muted">Already priced in</dt>
                            <dd className="font-semibold text-groww-text-primary tabular-nums">
                              {alert.hedge.priced_in === null ? '—' : `${Math.round(alert.hedge.priced_in * 100)}%`}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-groww-text-muted">Cost of protection</dt>
                            <dd className="font-semibold text-groww-text-primary tabular-nums">
                              {alert.hedge.cost === null ? '—' : inr(alert.hedge.cost)}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-groww-text-muted">Signal confidence</dt>
                            <dd className="font-semibold text-groww-text-primary">{alert.hedge.confidence}</dd>
                          </div>
                          {alert.hedge.instrument && (
                            <div className="col-span-2">
                              <dt className="text-groww-text-muted">Hedge</dt>
                              <dd className="font-semibold text-groww-text-primary">
                                {alert.hedge.instrument}
                                {alert.hedge.notional !== null && `, ${inr(alert.hedge.notional)}`}
                              </dd>
                            </div>
                          )}
                        </dl>
                      </div>
                    )}
                    <p className="mt-2 text-xs text-groww-text-primary leading-relaxed">
                      <span className="font-semibold">What to do: </span>
                      {alert.recommendation}
                    </p>
                    <p className="mt-1.5 text-[11px] text-groww-text-muted leading-relaxed">
                      <span className="font-semibold">Based on: </span>
                      {alert.basis}
                    </p>
                  </li>
                )
              })}
            </ul>
            {result && result.unavailable.length > 0 && (
              <p className="px-4 py-2 text-[11px] text-amber-700 bg-amber-50">
                Some checks could not run: {result.unavailable.join(', ')}.
              </p>
            )}
          </div>

          <footer className="px-4 py-2.5 border-t border-groww-border-light bg-groww-bg-primary flex items-center justify-between gap-3 text-[11px] text-groww-text-muted">
            <span>
              {result?.risk_model
                ? `Risk model AUC ${result.risk_model.auc} on ${result.risk_model.tested_on.replace(' to ', ' – ')}`
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
