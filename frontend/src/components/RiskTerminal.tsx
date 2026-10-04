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

const TILE_ICONS: Record<string, React.ReactNode> = {
  loss: <path d="M3 7l6 6 4-4 8 8M21 11v6h-6" />,
  market: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  exposure: <><circle cx="12" cy="12" r="9" /><path d="M12 3v9l7 5" /></>,
  alerts: <><path d="M6 9a6 6 0 1112 0c0 6 2.5 7 2.5 7h-17S6 15 6 9z" /><path d="M10 20a2 2 0 004 0" /></>,
}

const Tile: React.FC<{
  id: string
  icon: keyof typeof TILE_ICONS
  tone: string
  label: string
  value: string
  note?: string
  hint?: string
  onClick?: () => void
  children?: React.ReactNode
}> = ({ id, icon, tone, label, value, note, hint, onClick, children }) => {
  const body = (
    <>
      <span className={`inline-flex w-10 h-10 rounded-xl items-center justify-center ${tone}`} aria-hidden>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          {TILE_ICONS[icon]}
        </svg>
      </span>
      <p className="mt-4 text-sm font-medium text-groww-text-secondary">{label}</p>
      <p className="mt-1 text-3xl font-bold text-groww-text-primary tabular-nums tracking-tight truncate">{value}</p>
      {note && <p className="mt-1.5 text-sm text-groww-text-secondary line-clamp-2">{note}</p>}
      {children}
    </>
  )
  const className = 'min-w-0 rounded-2xl sm:rounded-3xl bg-white border border-groww-border-light shadow-card p-5 sm:p-6 text-left'
  return onClick ? (
    <button id={id} onClick={onClick} title={hint} className={`${className} hover:border-groww-green/50 transition-colors`}>
      {body}
    </button>
  ) : (
    <div id={id} title={hint} className={className}>
      {body}
    </div>
  )
}

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
        <h2 className="text-lg font-bold text-groww-text-primary">Your risk at a glance</h2>
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

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 sm:gap-5">
        <Tile
          id="risk-var"
          icon="loss"
          tone="bg-red-50 text-red-500"
          label="Could lose in a day"
          value={data ? (data.risk.var_1d === null ? '—' : inr(data.risk.var_1d)) : '—'}
          note={
            data
              ? data.risk.var_1d_pct === null
                ? 'Not enough price history'
                : `${pct(data.risk.var_1d_pct)} of your ${inr(data.risk.total)}`
              : pending
          }
          hint={
            data?.risk.var_horizon != null && data.risk.cvar_1d != null
              ? `1-day 95% value at risk. ${data.risk.horizon_sessions}-day: ${inr(data.risk.var_horizon)}. Expected shortfall: ${inr(data.risk.cvar_1d)}.`
              : '1-day 95% value at risk'
          }
        />

        <Tile
          id="risk-beta"
          icon="market"
          tone="bg-blue-50 text-blue-600"
          label="Moves with the market"
          value={data ? (data.risk.beta === null ? '—' : `${data.risk.beta.toFixed(2)}×`) : '—'}
          note={
            data
              ? data.risk.beta === null
                ? 'Not enough price history'
                : `${data.risk.beta.toFixed(2)}% for every 1% in the Nifty`
              : pending
          }
          hint={
            data?.risk.volatility != null
              ? `Beta to the Nifty 50. Annualised volatility ${pct(data.risk.volatility)} over the last ${data.risk.sessions} sessions.`
              : 'Beta to the Nifty 50'
          }
        />

        <Tile
          id="risk-exposure"
          icon="exposure"
          tone="bg-amber-50 text-amber-600"
          label="Biggest exposure"
          value={topSector ? pct(topSector.weight) : '—'}
          note={topSector ? `${topSector.sector} · ${topSector.holdings.join(', ')}` : pending}
          hint={
            data && topSector
              ? `Largest holding ${data.exposure.largest.name} ${pct(data.exposure.largest.weight)}. The portfolio is spread like ${data.exposure.effective_holdings.toFixed(1)} equal positions.`
              : undefined
          }
        />

        <Tile
          id="risk-alerts"
          icon="alerts"
          tone={attention > 0 ? 'bg-amber-50 text-amber-600' : 'bg-groww-green-light text-groww-green'}
          label="Needs attention"
          value={alerts.result ? String(attention) : '—'}
          note={
            alerts.result
              ? attention === 0
                ? 'All clear'
                : `${attention === 1 ? 'alert' : 'alerts'} · tap to review`
              : alertsPending
          }
          hint={
            alerts.result
              ? `${count('critical')} critical, ${count('warning')} warning, ${count('info')} info${alerts.failed ? '; last refresh failed' : ''}`
              : undefined
          }
          onClick={openAlerts}
        />
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
        <ul className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-1">
          {live.slice(0, MAX_ALERTS).map((alert) => {
            const SeverityIcon = SEVERITY[alert.severity].Icon
            return (
              <li key={alert.id} className="py-1">
                <button onClick={openAlerts} className="w-full flex items-start gap-3 text-left rounded-xl hover:bg-groww-bg-primary transition-colors -mx-1.5 px-1.5 py-1">
                  <span
                    className={`mt-0.5 shrink-0 w-7 h-7 rounded-lg flex items-center justify-center ${SEVERITY[alert.severity].chip}`}
                    title={SEVERITY[alert.severity].label}
                  >
                    <SeverityIcon className="w-3 h-3 shrink-0" />
                    <span className="sr-only">{SEVERITY[alert.severity].label}</span>
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[13px] font-semibold text-groww-text-primary leading-snug">{alert.title}</span>
                    <span className="mt-0.5 flex gap-1.5 text-xs text-groww-text-secondary leading-snug">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-[3px] text-groww-green" aria-hidden>
                        <path d="M5 12h14M13 6l6 6-6 6" />
                      </svg>
                      <span className="line-clamp-1">{alert.solution?.headline ?? alert.recommendation}</span>
                    </span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {alerts.result && alerts.result.unavailable.length > 0 && (
        <p className="mt-3 text-xs text-amber-700">
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
  // One line per feed; its detail opens on click.
  const [openStream, setOpenStream] = useState<string | null>(null)

  return (
    <div id="risk-streams" className={`rounded-2xl sm:rounded-3xl bg-white border border-groww-border-light shadow-card p-5 sm:p-6 transition-all duration-200 ${className}`}>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-base font-bold text-groww-text-primary">Data streams</h3>
        {feeds && (
          <p className="text-xs text-groww-text-muted">
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
        <ul className="mt-3 flex flex-col divide-y divide-groww-border-light">
          {feeds.streams.map((stream) => {
            const status = STATUS[stream.status]
            const StatusIcon = status.Icon
            const open = openStream === stream.key
            return (
              <li key={stream.key} className="stream-row">
                <button
                  onClick={() => setOpenStream(open ? null : stream.key)}
                  aria-expanded={open}
                  className="w-full flex items-center gap-2.5 py-2.5 text-left text-[13px] hover:bg-groww-bg-primary rounded-lg px-1.5 -mx-1.5 transition-colors"
                >
                  <span className={`shrink-0 inline-flex items-center ${status.className}`} title={status.label}>
                    <StatusIcon className="w-3 h-3" />
                    <span className="sr-only">{status.label}</span>
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-semibold text-groww-text-primary">{stream.label}</span>{' '}
                    <span className="text-xs text-groww-text-muted">{stream.source}</span>
                  </span>
                  <span className={`shrink-0 text-xs font-semibold tabular-nums ${stream.status === 'live' ? 'text-groww-text-secondary' : status.className}`}>
                    {stream.status !== 'live' ? status.label : stream.latency_ms !== null ? `${stream.latency_ms} ms` : 'Live'}
                  </span>
                </button>
                {open && (
                  <p className="stream-detail pb-2.5 pl-6 pr-1 text-xs text-groww-text-secondary leading-relaxed">
                    {stream.detail}
                    {ago(stream.checked_at) && <span className="text-groww-text-muted"> · checked {ago(stream.checked_at)}</span>}
                  </p>
                )}
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
