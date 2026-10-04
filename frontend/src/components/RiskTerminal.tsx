import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { isLeftover } from '../lib/holdings'
import { openAlerts, useAlerts } from '../lib/alerts'
import type { Severity } from '../lib/alerts'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Overview {
  portfolio_source: 'user' | 'sample'
  price_source: 'live' | 'cache'
  as_of: string
  risk: {
    total: number
    holdings: number
    var_1d: number | null
    var_1d_pct: number | null
    cvar_1d: number | null
    var_horizon: number | null
    horizon_sessions: number
    beta: number | null
    volatility: number | null
    sessions: number
  }
  exposure: {
    largest: { name: string; weight: number; value: number }
    sectors: { sector: string; weight: number; value: number; holdings: string[] }[]
    effective_holdings: number
  }
  unpriced: string[]
}

type StreamStatus = 'live' | 'degraded' | 'down' | 'idle'

interface Stream {
  key: string
  group: 'feed' | 'model'
  label: string
  source: string
  status: StreamStatus
  detail: string
  checked_at: string | null
  latency_ms: number | null
}

interface Streams {
  streams: Stream[]
  counts: Record<StreamStatus, number>
  generated_at: string
}

type Loadable<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T }

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const OVERVIEW_REFRESH_MS = 180_000
const STREAMS_REFRESH_MS = 60_000
const MAX_ALERTS = 4

import { IconCritical, IconWarning, IconInfo, IconLive, IconDegraded, IconDown, IconIdle } from './Icons'

// Status is never shown by colour alone: each state has its own mark and word.
const STATUS: Record<StreamStatus, { Icon: React.FC<{ className?: string }>; label: string; className: string }> = {
  live: { Icon: IconLive, label: 'Live', className: 'text-groww-green' },
  degraded: { Icon: IconDegraded, label: 'Fallback', className: 'text-amber-600' },
  down: { Icon: IconDown, label: 'Down', className: 'text-red-600' },
  idle: { Icon: IconIdle, label: 'Idle', className: 'text-groww-text-muted' },
}

const SEVERITY: Record<Severity, { Icon: React.FC<{ className?: string }>; label: string; chip: string }> = {
  critical: { Icon: IconCritical, label: 'Critical', chip: 'bg-red-50 text-red-600 border border-red-200/50' },
  warning: { Icon: IconWarning, label: 'Warning', chip: 'bg-amber-50 text-amber-700 border border-amber-200/50' },
  info: { Icon: IconInfo, label: 'Info', chip: 'bg-gray-100 text-groww-text-secondary border border-gray-200/50' },
}

const inr = (value: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)
const pct = (fraction: number, digits = 1) => `${(fraction * 100).toFixed(digits)}%`
const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })

function ago(iso: string | null): string | null {
  if (!iso) return null
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / 86400)} d ago`
}

const Tile: React.FC<{ id: string; label: string; value: string; note?: string; children?: React.ReactNode }> = ({
  id,
  label,
  value,
  note,
  children,
}) => (
  <div id={id} className="min-w-0 rounded-2xl bg-white border border-groww-border-light shadow-card px-4 py-3.5">
    <p className="text-[11px] font-semibold uppercase tracking-wider text-groww-text-muted">{label}</p>
    <p className="mt-1 text-xl sm:text-2xl font-bold text-groww-text-primary tabular-nums truncate">{value}</p>
    {note && <p className="mt-0.5 text-xs text-groww-text-secondary line-clamp-2">{note}</p>}
    {children}
  </div>
)

// ---------------------------------------------------------------------------
// 1. Top Risk Summary Tiles
// ---------------------------------------------------------------------------
export const RiskOverviewTiles: React.FC<{ backendUrl: string; refreshKey?: number }> = ({ backendUrl, refreshKey = 0 }) => {
  const { user } = useAuth()
  const alerts = useAlerts()
  const [overview, setOverview] = useState<Loadable<Overview>>({ status: 'loading' })

  const loadOverview = useCallback(async () => {
    if (!user?.id) return
    try {
      const { data, error } = await supabase
        .from('portfolio_holdings')
        .select('name, symbol, isin, units, buy_price, type')
        .eq('user_id', user.id)
      const holdings = error ? null : (data ?? []).filter((h) => !isLeftover(h))
      const response = await fetch(`${backendUrl}/api/terminal/overview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holdings }),
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => null)
        throw new Error(detail?.detail ?? `Request failed (${response.status})`)
      }
      setOverview({ status: 'ready', data: await response.json() })
    } catch (e) {
      const message = e instanceof TypeError ? 'Could not reach the backend.' : (e as Error).message
      setOverview((previous) => (previous.status === 'ready' ? previous : { status: 'error', message }))
    }
  }, [user?.id, backendUrl])

  useEffect(() => {
    loadOverview()
    const timer = setInterval(loadOverview, OVERVIEW_REFRESH_MS)
    return () => clearInterval(timer)
  }, [loadOverview, refreshKey])

  const data = overview.status === 'ready' ? overview.data : null
  const pending = overview.status === 'loading' ? 'Loading…' : 'Unavailable'
  const topSector = data?.exposure.sectors[0]

  const live = (alerts.result?.alerts ?? []).filter((a) => !a.hedge?.drill)
  const count = (severity: Severity) => live.filter((a) => a.severity === severity).length
  const attention = count('critical') + count('warning')
  const alertsPending = alerts.loading ? 'Checking…' : 'Unavailable'

  return (
    <section id="risk-terminal" aria-label="Risk overview" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-base font-bold text-groww-text-primary">Risk overview</h2>
        <p className="text-xs text-groww-text-muted">
          {data
            ? `Closing prices of ${shortDate(data.as_of)}${data.price_source === 'cache' ? ' (saved copy, live feed unavailable)' : ''}${
                data.portfolio_source === 'sample' ? ' · sample portfolio' : ''
              }`
            : overview.status === 'error'
              ? overview.message
              : 'Loading risk figures…'}
        </p>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 sm:gap-4">
        <Tile
          id="risk-var"
          label="1-day 95% VaR"
          value={data ? (data.risk.var_1d === null ? '—' : inr(data.risk.var_1d)) : '—'}
          note={
            data
              ? data.risk.var_1d_pct === null
                ? 'Not enough price history'
                : `${pct(data.risk.var_1d_pct)} of ${inr(data.risk.total)}`
              : pending
          }
        >
          {data?.risk.var_horizon != null && data.risk.cvar_1d != null && (
            <p className="mt-2 pt-2 border-t border-groww-border-light text-[11px] text-groww-text-muted">
              {data.risk.horizon_sessions}-day {inr(data.risk.var_horizon)} · expected shortfall {inr(data.risk.cvar_1d)}
            </p>
          )}
        </Tile>

        <Tile
          id="risk-beta"
          label="Beta to Nifty 50"
          value={data ? (data.risk.beta === null ? '—' : data.risk.beta.toFixed(2)) : '—'}
          note={
            data
              ? data.risk.beta === null
                ? 'Not enough price history'
                : `A 1% index move is about ${data.risk.beta.toFixed(2)}% here`
              : pending
          }
        >
          {data?.risk.volatility != null && (
            <p className="mt-2 pt-2 border-t border-groww-border-light text-[11px] text-groww-text-muted">
              {pct(data.risk.volatility)} annualised volatility · last {data.risk.sessions} sessions
            </p>
          )}
        </Tile>

        <Tile
          id="risk-exposure"
          label="Top exposure"
          value={topSector ? pct(topSector.weight) : '—'}
          note={topSector ? `${topSector.sector}: ${topSector.holdings.join(', ')}` : pending}
        >
          {data && topSector && (
            <>
              <div className="mt-2 h-1.5 rounded-full bg-gray-100 overflow-hidden" aria-hidden>
                <div className="h-full rounded-full bg-groww-green" style={{ width: `${Math.max(2, topSector.weight * 100)}%` }} />
              </div>
              <p className="mt-2 text-[11px] text-groww-text-muted leading-snug">
                Largest holding {data.exposure.largest.name} {pct(data.exposure.largest.weight)} · spread like{' '}
                {data.exposure.effective_holdings.toFixed(1)} equal positions
              </p>
            </>
          )}
        </Tile>

        <Tile
          id="risk-alerts"
          label="Active alerts"
          value={alerts.result ? String(attention) : '—'}
          note={
            alerts.result
              ? attention === 0
                ? 'Nothing needs attention'
                : `need${attention === 1 ? 's' : ''} attention`
              : alertsPending
          }
        >
          {alerts.result && (
            <p className="mt-2 pt-2 border-t border-groww-border-light text-[11px] text-groww-text-muted">
              {count('critical')} critical · {count('warning')} warning · {count('info')} info
              {alerts.failed && ' · last refresh failed'}
            </p>
          )}
        </Tile>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// 2. What Needs Attention (Alerts List)
// ---------------------------------------------------------------------------
export const RiskAlertList: React.FC<{ className?: string }> = ({ className = '' }) => {
  const alerts = useAlerts()
  const live = (alerts.result?.alerts ?? []).filter((a) => !a.hedge?.drill)

  return (
    <div id="risk-alert-list" className={`rounded-2xl sm:rounded-3xl bg-white border border-groww-border-light shadow-card p-5 sm:p-6 transition-all duration-200 ${className}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-bold text-groww-text-primary">What needs attention</h3>
          {live.length > 0 && (
            <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200/60">
              {live.length} active
            </span>
          )}
        </div>
        <button
          onClick={openAlerts}
          className="text-xs font-semibold text-groww-green hover:text-groww-green-dark px-2.5 py-1 rounded-lg hover:bg-groww-green-light transition-colors"
        >
          {live.length > MAX_ALERTS ? `All ${live.length} alerts` : 'Open alerts'}
        </button>
      </div>
      {!alerts.result ? (
        <p className="mt-3 text-xs text-groww-text-secondary">
          {alerts.loading ? 'Checking prices, news, weather and the news scan…' : 'Alerts are unavailable right now.'}
        </p>
      ) : live.length === 0 ? (
        <p className="mt-3 text-xs text-groww-text-secondary">
          No alerts. Prices, news, weather and concentration were checked against your holdings.
        </p>
      ) : (
        <ul className="mt-3.5 flex flex-col divide-y divide-groww-border-light">
          {live.slice(0, MAX_ALERTS).map((alert) => {
            const SeverityIcon = SEVERITY[alert.severity].Icon
            return (
              <li key={alert.id} className="py-2.5 first:pt-0 last:pb-0 flex items-start gap-3">
                <span
                  className={`mt-0.5 shrink-0 inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold ${SEVERITY[alert.severity].chip}`}
                >
                  <SeverityIcon className="w-2.5 h-2.5 shrink-0" />
                  <span>{SEVERITY[alert.severity].label}</span>
                </span>
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold text-groww-text-primary leading-snug">{alert.title}</p>
                  <p className="mt-0.5 text-xs text-groww-text-secondary leading-snug line-clamp-2">
                    <span className="font-semibold text-groww-text-primary">Action: </span>
                    {alert.solution?.headline ?? alert.recommendation}
                  </p>
                </div>
              </li>
            )
          })}
        </ul>
      )}
      {alerts.result && alerts.result.unavailable.length > 0 && (
        <p className="mt-3 text-[11px] text-amber-700">
          Not checked this time: {alerts.result.unavailable.join(', ')}.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3. Data Streams Card
// ---------------------------------------------------------------------------
export const RiskStreamsCard: React.FC<{ backendUrl: string; className?: string }> = ({ backendUrl, className = '' }) => {
  const [streams, setStreams] = useState<Loadable<Streams>>({ status: 'loading' })

  const loadStreams = useCallback(async () => {
    try {
      const response = await fetch(`${backendUrl}/api/terminal/streams`)
      if (!response.ok) throw new Error(`Request failed (${response.status})`)
      setStreams({ status: 'ready', data: await response.json() })
    } catch {
      setStreams((previous) =>
        previous.status === 'ready' ? previous : { status: 'error', message: 'Feed status could not be read.' },
      )
    }
  }, [backendUrl])

  useEffect(() => {
    loadStreams()
    const timer = setInterval(loadStreams, STREAMS_REFRESH_MS)
    return () => clearInterval(timer)
  }, [loadStreams])

  const feeds = streams.status === 'ready' ? streams.data : null

  return (
    <div id="risk-streams" className={`rounded-2xl sm:rounded-3xl bg-white border border-groww-border-light shadow-card p-5 sm:p-6 transition-all duration-200 ${className}`}>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-base font-bold text-groww-text-primary">Data streams</h3>
        {feeds && (
          <p className="text-[11px] text-groww-text-muted">
            {(Object.keys(STATUS) as StreamStatus[])
              .filter((status) => feeds.counts[status] > 0)
              .map((status) => `${feeds.counts[status]} ${STATUS[status].label.toLowerCase()}`)
              .join(' · ')}
          </p>
        )}
      </div>
      {!feeds ? (
        <p className="mt-3 text-xs text-groww-text-secondary">
          {streams.status === 'error' ? streams.message : 'Checking each feed…'}
        </p>
      ) : (
        <ul className="mt-3.5 flex flex-col gap-2.5">
          {feeds.streams.map((stream) => {
            const status = STATUS[stream.status]
            const StatusIcon = status.Icon
            const when = ago(stream.checked_at)
            return (
              <li key={stream.key} className="flex items-start gap-2.5 text-xs">
                <span className={`w-[80px] shrink-0 whitespace-nowrap font-semibold inline-flex items-center gap-1.5 ${status.className}`}>
                  <StatusIcon className="w-2.5 h-2.5 shrink-0" />
                  <span>{status.label}</span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="font-semibold text-groww-text-primary">{stream.label}</span>{' '}
                  <span className="text-groww-text-muted">{stream.source}</span>
                  <span className="block text-groww-text-secondary leading-snug">{stream.detail}</span>
                </span>
                <span className="shrink-0 text-right text-[11px] text-groww-text-muted tabular-nums">
                  {when}
                  {stream.latency_ms !== null && <span className="block">{stream.latency_ms} ms</span>}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 4. Default composite component (kept for backward compatibility)
// ---------------------------------------------------------------------------
const RiskTerminal: React.FC<{ backendUrl: string; refreshKey?: number }> = ({ backendUrl, refreshKey = 0 }) => {
  return (
    <div className="flex flex-col gap-6">
      <RiskOverviewTiles backendUrl={backendUrl} refreshKey={refreshKey} />
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <div className="lg:col-span-7">
          <RiskAlertList />
        </div>
        <div className="lg:col-span-5">
          <RiskStreamsCard backendUrl={backendUrl} />
        </div>
      </div>
    </div>
  )
}

export default RiskTerminal
