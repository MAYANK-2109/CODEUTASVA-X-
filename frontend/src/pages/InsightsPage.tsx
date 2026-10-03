import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type Range = '1y' | '3y' | '5y' | 'max'

interface PriceSeries {
  ticker: string
  name: string
  values: (number | null)[]
}

interface MarketEvent {
  id: string
  date: string
  type: string
  type_label: string
  title: string
  description: string
  confound: string | null
  index: number
  nifty: number | null
  moves: Record<string, number | null>
}

interface PortfolioInsights {
  portfolio_source: 'user' | 'sample'
  price_source: 'live' | 'cache'
  as_of: string
  horizon_sessions: number
  stats: {
    total: number
    holdings: number
    var_1d: number | null
    var_1d_pct: number | null
    beta: number | null
    largest: { name: string; weight: number }
  }
  sectors: { sector: string; value: number; weight: number; holdings: string[] }[]
  positions: { ticker: string; name: string; sector: string; value: number; weight: number }[]
  prices: {
    dates: string[]
    series: PriceSeries[]
    benchmark: { name: string; values: (number | null)[] }
    truncated: boolean
  }
  events: MarketEvent[]
  weather: ChartWeather | null
}

type WeatherKind = 'rain' | 'wind' | 'heat'

interface WeatherMark {
  index: number
  site: number
  kind: WeatherKind
  value: number
  date: string
  days: number
}

interface ChartWeather {
  sites: { name: string; relevance: string; holdings: { name: string; operation: string }[] }[]
  marks: WeatherMark[]
  forecast: { dates: string[]; flags: { site: number; date: string; kind: WeatherKind; value: number }[] } | null
  thresholds: { rain_mm: number; gust_kmh: number; temp_c: number }
  through: string
  source: string
}

interface Lane {
  site: number
  name: string
  holdings: string[]
  emphasised: boolean
}

interface WeatherDay {
  date: string
  rain_mm: number | null
  gust_kmh: number | null
  temp_c: number | null
}

interface WeatherInsights {
  sites: {
    name: string
    relevance: string
    sectors: string[]
    companies: Record<string, string>
    flags: string[]
    days: WeatherDay[]
  }[]
  thresholds: { rain_mm: number; gust_kmh: number; temp_c: number }
  source: string
}

type Loadable<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T }

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const BACKEND_URL: string =
  import.meta.env.VITE_BACKEND_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const RANGES: { id: Range; label: string }[] = [
  { id: '1y', label: '1Y' },
  { id: '3y', label: '3Y' },
  { id: '5y', label: '5Y' },
  { id: 'max', label: 'Max' },
]

const SERIES_COLORS = Array.from({ length: 8 }, (_, i) => `var(--viz-series-${i + 1})`)
const MAX_SELECTED = 4
const DEFAULT_SELECTED = 3

const WEATHER_KINDS: Record<WeatherKind, { label: string; unit: string; color: string; row: number }> = {
  rain: { label: 'heavy rain', unit: 'mm', color: 'var(--viz-series-1)', row: 0 },
  wind: { label: 'gale gusts', unit: 'km/h', color: 'var(--viz-series-7)', row: 1 },
  heat: { label: 'extreme heat', unit: '°C', color: 'var(--viz-series-2)', row: 2 },
}
const KIND_ORDER: WeatherKind[] = ['rain', 'wind', 'heat']
const LANE_H = 14
const LANE_GAP = 3
const KIND_H = 4
const SHORT_SITE: Record<string, string> = { Visakhapatnam: 'Vizag' }
const shortSite = (name: string) => SHORT_SITE[name] ?? name
const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

const inr = (value: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)
const pct = (fraction: number, digits = 1) => `${(fraction * 100).toFixed(digits)}%`
const signedPct = (fraction: number) => `${fraction > 0 ? '+' : ''}${(fraction * 100).toFixed(1)}%`
const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })

function useElementWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

const Card: React.FC<{ id: string; title: string; subtitle?: string; children: React.ReactNode; className?: string }> = ({
  id, title, subtitle, children, className = '',
}) => (
  <section id={id} className={`bg-white rounded-2xl border border-groww-border-light shadow-card p-4 sm:p-5 ${className}`}>
    <h2 className="text-base font-bold text-groww-text-primary">{title}</h2>
    {subtitle && <p className="text-xs text-groww-text-secondary mt-0.5">{subtitle}</p>}
    <div className="mt-4">{children}</div>
  </section>
)

const Delta: React.FC<{ value: number | null | undefined }> = ({ value }) =>
  value === null || value === undefined ? (
    <span className="text-[color:var(--viz-muted)]">–</span>
  ) : (
    <span className={value < 0 ? 'text-[color:var(--viz-critical)]' : 'text-[color:var(--viz-good-text)]'}>
      {signedPct(value)}
    </span>
  )

// ---------------------------------------------------------------------------
// Weather site card — plain-language, no heat grid
// ---------------------------------------------------------------------------
const WEATHER_ICONS: Record<WeatherKind, React.ReactNode> = {
  rain: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 17.58A5 5 0 0 0 18 8h-1.26A8 8 0 1 0 4 16.25"/><line x1="8" y1="16" x2="8" y2="21"/><line x1="12" y1="16" x2="12" y2="21"/><line x1="16" y1="16" x2="16" y2="21"/>
    </svg>
  ),
  wind: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9.59 4.59A2 2 0 1 1 11 8H2m10.59 11.41A2 2 0 1 0 14 16H2m15.73-8.27A2.5 2.5 0 1 1 19.5 12H2"/>
    </svg>
  ),
  heat: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
    </svg>
  ),
}

const WeatherSiteCard: React.FC<{
  site: WeatherInsights['sites'][0]
  myHoldings: string[]
  sectorExposure: number
  exposedSectors: string[]
  thresholds: { rain_mm: number; gust_kmh: number; temp_c: number }
}> = ({ site, myHoldings, sectorExposure, exposedSectors, thresholds }) => {
  const isAlert = site.flags.length > 0
  const hasHoldings = myHoldings.length > 0

  // Find max values across the forecast days for each kind
  const summary = {
    rain: site.days.reduce((max, d) => Math.max(max, d.rain_mm ?? 0), 0),
    wind: site.days.reduce((max, d) => Math.max(max, d.gust_kmh ?? 0), 0),
    heat: site.days.reduce((max, d) => Math.max(max, d.temp_c ?? 0), 0),
  }

  // Count alert days
  const alertDays: Partial<Record<WeatherKind, number>> = {}
  site.days.forEach(d => {
    if ((d.rain_mm ?? 0) >= thresholds.rain_mm) alertDays.rain = (alertDays.rain ?? 0) + 1
    if ((d.gust_kmh ?? 0) >= thresholds.gust_kmh) alertDays.wind = (alertDays.wind ?? 0) + 1
    if ((d.temp_c ?? 0) >= thresholds.temp_c) alertDays.heat = (alertDays.heat ?? 0) + 1
  })
  const activeKinds = KIND_ORDER.filter(k => (alertDays[k] ?? 0) > 0)

  return (
    <div
      className={`rounded-xl border p-4 flex flex-col gap-3 transition-all ${
        isAlert
          ? 'border-red-200 bg-red-50'
          : 'border-groww-border-light bg-white'
      }`}
    >
      {/* Site header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className={`font-semibold text-sm leading-tight ${isAlert ? 'text-red-800' : 'text-groww-text-primary'}`}>
            {site.name}
          </p>
          <p className="text-[11px] text-groww-text-muted mt-0.5 line-clamp-1">{site.relevance}</p>
        </div>
        <span
          className={`shrink-0 flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full ${
            isAlert
              ? 'bg-red-100 text-red-700'
              : 'bg-green-100 text-green-700'
          }`}
        >
          {isAlert ? (
            <>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
              ALERT
            </>
          ) : (
            <>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
              CLEAR
            </>
          )}
        </span>
      </div>

      {/* Active alert kinds */}
      {activeKinds.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {activeKinds.map(kind => (
            <div key={kind} className="flex items-center gap-1.5 text-xs bg-white border border-red-200 rounded-lg px-2.5 py-1">
              <span className="text-red-600">{WEATHER_ICONS[kind]}</span>
              <span className="font-medium text-red-800">
                {alertDays[kind]}d {WEATHER_KINDS[kind].label}
              </span>
              <span className="text-red-600 text-[11px]">
                peak {Math.round(summary[kind])} {WEATHER_KINDS[kind].unit}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {KIND_ORDER.map(kind => (
            <div key={kind} className="flex items-center gap-1.5 text-[11px] text-groww-text-muted bg-gray-50 rounded-lg px-2 py-1">
              {WEATHER_ICONS[kind]}
              <span>{Math.round(summary[kind])} {WEATHER_KINDS[kind].unit}</span>
            </div>
          ))}
        </div>
      )}

      {/* Holdings exposure */}
      <div className="border-t border-groww-border-light pt-2 text-[11px]">
        {hasHoldings ? (
          <p className={`font-medium ${isAlert ? 'text-red-700' : 'text-groww-text-primary'}`}>
            Your holdings: {myHoldings.join(', ')}
          </p>
        ) : (
          <p className="text-groww-text-muted">No direct holdings here</p>
        )}
        {exposedSectors.length > 0 && (
          <p className="text-groww-text-muted mt-0.5">
            {pct(sectorExposure, 0)} in {exposedSectors.join(', ')}
          </p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Price chart: holdings and the Nifty indexed to 100, past events marked
// ---------------------------------------------------------------------------
interface Line {
  key: string
  name: string
  color: string
  values: (number | null)[]
}

const niceStep = (span: number, target: number) => {
  const raw = span / target
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const unit = raw / magnitude
  return (unit >= 5 ? 10 : unit >= 2 ? 5 : unit >= 1 ? 2 : 1) * magnitude
}

const PriceChart: React.FC<{
  dates: string[]
  lines: Line[]
  events: MarketEvent[]
  activeEventId: string | null
  weather: ChartWeather | null
  lanes: Lane[]
}> = ({ dates, lines, events, activeEventId, weather, lanes }) => {
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)

  const wide = width >= 520
  const margin = { top: 26, right: wide ? (lanes.length ? 128 : 118) : 14, bottom: 30, left: 42 }
  const plotH = 284
  const plotW = Math.max(0, width - margin.left - margin.right)
  const last = dates.length - 1
  const laneGap = wide ? LANE_GAP : 13
  const captionY = margin.top + plotH + margin.bottom + 9
  const lanesTop = captionY + (wide ? 5 : 17)
  const lanesH = lanes.length ? lanes.length * (LANE_H + laneGap) - laneGap : 0
  const height = lanes.length ? lanesTop + lanesH + 4 : margin.top + plotH + margin.bottom
  const laneY = (lane: number) => lanesTop + lane * (LANE_H + laneGap)
  const bottom = lanes.length ? lanesTop + lanesH : margin.top + plotH

  const all = lines.flatMap((l) => l.values).filter((v): v is number => v !== null)
  const lo = Math.min(...all, 100)
  const hi = Math.max(...all, 100)
  const step = niceStep(hi - lo || 1, 4)
  const yMin = Math.floor(lo / step) * step
  const yMax = Math.ceil(hi / step) * step
  const ticks: number[] = []
  for (let v = yMin; v <= yMax + step / 2; v += step) ticks.push(v)

  const x = (i: number) => margin.left + (last > 0 ? (i / last) * plotW : 0)
  const y = (v: number) => margin.top + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH

  const xTicks = useMemo(() => {
    const spanYears = dates.length ? (Date.parse(dates[last]) - Date.parse(dates[0])) / 31_557_600_000 : 0
    const byMonth = spanYears <= 1.5
    const found: { index: number; label: string }[] = []
    let previous = ''
    dates.forEach((iso, index) => {
      const bucket = byMonth ? iso.slice(0, 7) : iso.slice(0, 4)
      if (bucket !== previous && index > 0) {
        const d = new Date(iso)
        found.push({
          index,
          label: byMonth ? d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' }) : bucket,
        })
      }
      previous = bucket
    })
    const keepEvery = Math.ceil(found.length / Math.max(2, Math.floor(plotW / 70)))
    return found.filter((_, i) => i % keepEvery === 0)
  }, [dates, last, plotW])

  const pathFor = (values: (number | null)[]) => {
    let d = ''
    let pen = false
    values.forEach((v, i) => {
      if (v === null) { pen = false; return }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
      pen = true
    })
    return d
  }

  const ends = lines
    .map((l) => ({ ...l, end: l.values[last] }))
    .filter((l): l is Line & { end: number } => l.end !== null && l.end !== undefined)
    .sort((a, b) => y(a.end) - y(b.end))
  const labelsFit =
    margin.right > 60 && ends.every((l, i) => i === 0 || y(l.end) - y(ends[i - 1].end) >= 13)

  const eventsAt = (index: number) => events.filter((e) => e.index === index)

  const laneOf = useMemo(() => new Map(lanes.map((lane, i) => [lane.site, i])), [lanes])
  const marks = useMemo(
    () => (weather ? weather.marks.filter((m) => laneOf.has(m.site)) : []),
    [weather, laneOf],
  )
  const marksAt = (index: number) => marks.filter((m) => m.index === index)
  const markW = Math.max(2, last > 0 ? plotW / last : 2)
  const ahead = wide && weather?.forecast?.dates.length ? weather.forecast : null
  const aheadX = margin.left + plotW + 10
  const CELL = 6
  const move = (clientX: number, target: Element) => {
    const box = target.getBoundingClientRect()
    const ratio = (clientX - box.left) / box.width
    setHover(Math.max(0, Math.min(last, Math.round(ratio * last))))
  }

  const tooltipLeft = hover === null ? 0 : x(hover)
  const flip = tooltipLeft > width * 0.6

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label="Indexed price history of selected holdings with past events marked">
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={margin.left} x2={margin.left + plotW} y1={y(tick)} y2={y(tick)}
                stroke={tick === 100 ? 'var(--viz-axis)' : 'var(--viz-grid)'} strokeWidth={1} />
              <text x={margin.left - 8} y={y(tick) + 4} textAnchor="end" fontSize={11}
                fill="var(--viz-muted)" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {tick}
              </text>
            </g>
          ))}
          {xTicks.map((tick) => (
            <text key={tick.index} x={x(tick.index)} y={margin.top + plotH + 22}
              textAnchor="middle" fontSize={11} fill="var(--viz-muted)">
              {tick.label}
            </text>
          ))}

          {events.map((event) => {
            const active = event.id === activeEventId || (hover !== null && event.index === hover)
            return (
              <g key={event.id}>
                <line x1={x(event.index)} x2={x(event.index)} y1={margin.top - 6} y2={margin.top + plotH}
                  stroke={active ? 'var(--viz-ink)' : 'var(--viz-axis)'} strokeWidth={1} />
                <path d={`M${x(event.index) - 5},${margin.top - 16} h10 l-5,9 z`}
                  fill={active ? 'var(--viz-ink)' : 'var(--viz-muted)'} stroke="var(--viz-surface)" strokeWidth={1} />
              </g>
            )
          })}

          {lines.map((line) => (
            <path key={line.key} d={pathFor(line.values)} fill="none" stroke={line.color}
              strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {ends.map((line) => (
            <g key={line.key}>
              <circle cx={x(last)} cy={y(line.end)} r={4} fill={line.color} stroke="var(--viz-surface)" strokeWidth={2} />
              {labelsFit && (
                <text x={x(last) + 10} y={y(line.end) + 4} fontSize={11} fill="var(--viz-ink-2)">
                  {line.name.length > 16 ? `${line.name.slice(0, 15)}…` : line.name}
                </text>
              )}
            </g>
          ))}

          {/* Weather lanes */}
          {lanes.length > 0 && (
            <g>
              <text x={margin.left} y={captionY} fontSize={10} fill="var(--viz-muted)">
                Weather alert days by site
              </text>
              {ahead && (
                <text x={aheadX} y={captionY} fontSize={10} fill="var(--viz-muted)">
                  next {ahead.dates.length} days
                </text>
              )}
              {lanes.map((lane, i) => (
                <g key={lane.site}>
                  <rect x={margin.left} y={laneY(i)} width={plotW} height={LANE_H} rx={3} fill="var(--viz-grid)" opacity={0.45} />
                  {ahead?.dates.map((day, d) => {
                    const flags = ahead.flags.filter((f) => f.site === lane.site && f.date === day)
                    return (
                      <g key={day}>
                        <title>{`${lane.name}, ${shortDate(day)}: ${flags.length ? flags.map(f => `${WEATHER_KINDS[f.kind].label} ${f.value} ${WEATHER_KINDS[f.kind].unit}`).join(', ') : 'no alert'}`}</title>
                        <rect x={aheadX + d * CELL} y={laneY(i)} width={CELL - 1} height={LANE_H} rx={1} fill="var(--viz-grid)" opacity={0.45} />
                        {flags.map((f) => (
                          <rect key={f.kind} x={aheadX + d * CELL} y={laneY(i) + 1 + WEATHER_KINDS[f.kind].row * KIND_H}
                            width={CELL - 1} height={KIND_H} fill={WEATHER_KINDS[f.kind].color} />
                        ))}
                      </g>
                    )
                  })}
                  <text x={wide ? aheadX + (ahead ? ahead.dates.length * CELL + 7 : 0) : margin.left}
                    y={wide ? laneY(i) + 10.5 : laneY(i) - 3}
                    fontSize={wide ? 11 : 10}
                    fontWeight={lane.emphasised ? 600 : 400}
                    fill={lane.emphasised ? 'var(--viz-ink)' : 'var(--viz-ink-2)'}>
                    <title>{lane.holdings.length ? `${lane.name}: ${lane.holdings.join('; ')}` : `${lane.name}: none of your holdings operates here`}</title>
                    {shortSite(lane.name)}
                  </text>
                </g>
              ))}
              {marks.map((mark) => {
                const half = markW / 2
                const left = Math.max(margin.left, x(mark.index) - half)
                const right = Math.min(margin.left + plotW, x(mark.index) + half)
                return (
                  <rect key={`${mark.index}-${mark.site}-${mark.kind}`}
                    x={left} y={laneY(laneOf.get(mark.site) ?? 0) + 1 + WEATHER_KINDS[mark.kind].row * KIND_H}
                    width={Math.max(2, right - left)} height={KIND_H} fill={WEATHER_KINDS[mark.kind].color} />
                )
              })}
            </g>
          )}

          {hover !== null && (
            <g pointerEvents="none">
              <line x1={x(hover)} x2={x(hover)} y1={margin.top} y2={bottom} stroke="var(--viz-ink-2)" strokeWidth={1} />
              {lines.map((line) => {
                const value = line.values[hover]
                return value === null || value === undefined ? null : (
                  <circle key={line.key} cx={x(hover)} cy={y(value)} r={4} fill={line.color} stroke="var(--viz-surface)" strokeWidth={2} />
                )
              })}
            </g>
          )}

          <rect x={margin.left} y={margin.top - 18} width={plotW} height={bottom - margin.top + 18}
            fill="transparent" tabIndex={0}
            aria-label="Price chart. Use arrow keys to read values."
            style={{ outline: 'none', cursor: 'crosshair', touchAction: 'pan-y' }}
            onPointerMove={(e) => move(e.clientX, e.currentTarget)}
            onPointerDown={(e) => move(e.clientX, e.currentTarget)}
            onPointerLeave={() => setHover(null)}
            onFocus={() => setHover((h) => h ?? last)}
            onBlur={() => setHover(null)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowLeft') setHover((h) => Math.max(0, (h ?? last) - 1))
              if (e.key === 'ArrowRight') setHover((h) => Math.min(last, (h ?? last) + 1))
            }}
          />
        </svg>
      )}

      {hover !== null && (
        <div
          className="absolute z-10 pointer-events-none rounded-xl bg-white border border-groww-border shadow-lg px-3 py-2 text-xs w-56"
          style={{ top: margin.top, left: flip ? undefined : tooltipLeft + 12, right: flip ? width - tooltipLeft + 12 : undefined }}
        >
          <p className="font-semibold text-groww-text-primary">{shortDate(dates[hover])}</p>
          <ul className="mt-1.5 flex flex-col gap-1">
            {lines.map((line) => {
              const value = line.values[hover]
              return (
                <li key={line.key} className="flex items-center gap-2">
                  <span className="w-3 h-0.5 rounded shrink-0" style={{ background: line.color }} />
                  <span className="font-semibold text-groww-text-primary tabular-nums">
                    {value === null || value === undefined ? '–' : value.toFixed(1)}
                  </span>
                  <span className="text-groww-text-secondary truncate">{line.name}</span>
                </li>
              )
            })}
          </ul>
          {eventsAt(hover).map((event) => (
            <p key={event.id} className="mt-2 pt-2 border-t border-groww-border-light text-groww-text-primary">
              <span className="font-semibold">▼ {event.title}</span>
              <span className="block text-groww-text-secondary">{shortDate(event.date)}</span>
            </p>
          ))}
          {marksAt(hover).length > 0 && (
            <ul className="mt-2 pt-2 border-t border-groww-border-light flex flex-col gap-1">
              {marksAt(hover).map((mark) => (
                <li key={`${mark.site}-${mark.kind}`} className="flex items-start gap-2">
                  <span className="mt-1 w-3 h-1 rounded-sm shrink-0" style={{ background: WEATHER_KINDS[mark.kind].color }} />
                  <span className="text-groww-text-secondary">
                    <span className="font-semibold text-groww-text-primary">
                      {weather?.sites[mark.site].name}: {WEATHER_KINDS[mark.kind].label}
                    </span>{' '}
                    {mark.value} {WEATHER_KINDS[mark.kind].unit}
                    {mark.days > 1 ? `, worst of ${mark.days} days` : ` on ${shortDate(mark.date)}`}
                  </span>
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
// Page
// ---------------------------------------------------------------------------
const InsightsPage: React.FC = () => {
  const { user } = useAuth()
  const [range, setRange] = useState<Range>('3y')
  const [eventType, setEventType] = useState('all')
  const [portfolio, setPortfolio] = useState<Loadable<PortfolioInsights>>({ status: 'loading' })
  const [weather, setWeather] = useState<Loadable<WeatherInsights>>({ status: 'loading' })
  const [refreshing, setRefreshing] = useState(false)
  const [selected, setSelected] = useState<string[] | null>(null)
  const [activeEventId, setActiveEventId] = useState<string | null>(null)
  const [weatherView, setWeatherView] = useState<'mine' | 'all' | 'off'>('mine')

  const loadPortfolio = useCallback(async () => {
    if (!user?.id) return
    setRefreshing(true)
    try {
      const { data: rows, error } = await supabase
        .from('portfolio_holdings')
        .select('name, symbol, isin, units, buy_price, type')
        .eq('user_id', user.id)
      const response = await fetch(`${BACKEND_URL}/api/insights/portfolio`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holdings: error ? null : rows, range }),
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => null)
        throw new Error(detail?.detail ?? `Request failed (${response.status})`)
      }
      setPortfolio({ status: 'ready', data: await response.json() })
    } catch (e) {
      const message = e instanceof TypeError ? 'Could not reach the backend.' : (e as Error).message
      setPortfolio((previous) => (previous.status === 'ready' ? previous : { status: 'error', message }))
    } finally {
      setRefreshing(false)
    }
  }, [user?.id, range])

  useEffect(() => { loadPortfolio() }, [loadPortfolio])

  useEffect(() => {
    fetch(`${BACKEND_URL}/api/insights/weather`)
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? 'Weather unavailable')
        setWeather({ status: 'ready', data: await response.json() })
      })
      .catch((e: Error) =>
        setWeather({ status: 'error', message: e instanceof TypeError ? 'Could not reach the backend.' : e.message }),
      )
  }, [])

  const data = portfolio.status === 'ready' ? portfolio.data : null

  const eventTypes = useMemo(() => {
    const seen = new Map<string, string>()
    data?.events.forEach((e) => seen.set(e.type, e.type_label))
    return [...seen.entries()]
  }, [data])
  const visibleEvents = useMemo(
    () => (data ? data.events.filter((e) => eventType === 'all' || e.type === eventType) : []),
    [data, eventType],
  )

  const mostAffected = useMemo(() => {
    if (!data) return []
    const impact = (ticker: string) => {
      const moves = visibleEvents.map((e) => e.moves[ticker]).filter((m): m is number => m !== null && m !== undefined)
      return moves.length ? moves.reduce((sum, m) => sum + Math.abs(m), 0) / moves.length : 0
    }
    return [...data.prices.series]
      .sort((a, b) => impact(b.ticker) - impact(a.ticker))
      .slice(0, DEFAULT_SELECTED)
      .map((s) => s.ticker)
  }, [data, visibleEvents])
  const chosen = selected ?? mostAffected

  const colorOf = (ticker: string) =>
    SERIES_COLORS[data?.prices.series.findIndex((s) => s.ticker === ticker) ?? 0]
  const toggle = (ticker: string) =>
    setSelected(
      chosen.includes(ticker)
        ? chosen.filter((t) => t !== ticker)
        : [...chosen, ticker].slice(-MAX_SELECTED),
    )

  const lines: Line[] = data
    ? [
        { key: 'benchmark', name: data.prices.benchmark.name, color: 'var(--viz-context)', values: data.prices.benchmark.values },
        ...data.prices.series
          .filter((s) => chosen.includes(s.ticker))
          .map((s) => ({ key: s.ticker, name: s.name, color: colorOf(s.ticker), values: s.values })),
      ]
    : []
  const chosenSeries = data ? data.prices.series.filter((s) => chosen.includes(s.ticker)) : []
  const average = (pick: (e: MarketEvent) => number | null | undefined) => {
    const values = visibleEvents.map(pick).filter((v): v is number => v !== null && v !== undefined)
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
  }

  const chartWeather = data?.weather ?? null
  const chosenNames = new Set(chosenSeries.map((s) => s.name))
  const allLanes: Lane[] = (chartWeather?.sites ?? []).map((site, index) => ({
    site: index,
    name: site.name,
    holdings: site.holdings.map((h) => `${h.name} (${h.operation})`),
    emphasised: site.holdings.some((h) => chosenNames.has(h.name)),
  }))
  const myLanes = allLanes.filter((lane) => lane.holdings.length > 0)
  const lanes = weatherView === 'off' ? [] : weatherView === 'all' || myLanes.length === 0 ? allLanes : myLanes

  const sectorWeights = new Map(data?.sectors.map((s) => [s.sector, s]) ?? [])
  const maxSector = Math.max(...(data?.sectors.map((s) => s.weight) ?? [1]))

  return (
    <div id="insights-page" className="viz-root flex flex-col min-h-full">
      {/* ─── HEADER ─── */}
      <header
        className="sticky top-0 z-20 bg-white border-b border-groww-border-light px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between gap-4"
        style={{ minHeight: '60px' }}
      >
        <div>
          <h1 className="text-lg font-bold text-groww-text-primary">Insights</h1>
          <p className="text-xs text-groww-text-muted mt-0.5">
            {data
              ? `Prices to ${shortDate(data.as_of)}${data.price_source === 'cache' ? ' (saved copy)' : ''}`
              : 'Risk exposure, price history and weather outlook'}
          </p>
        </div>
        {/* Range selector stays in header for quick access */}
        <div className="inline-flex rounded-xl border border-groww-border bg-white p-0.5" role="group" aria-label="Date range">
          {RANGES.map((option) => (
            <button
              key={option.id}
              onClick={() => setRange(option.id)}
              aria-pressed={range === option.id}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors duration-150 ${
                range === option.id ? 'bg-groww-green text-white' : 'text-groww-text-secondary hover:text-groww-green'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      <div className="flex-1 p-4 sm:p-6 max-w-[1400px] w-full mx-auto flex flex-col gap-4 sm:gap-6">
        {portfolio.status === 'loading' && (
          <div className="flex flex-col items-center justify-center py-24 gap-4">
            <div className="w-12 h-12 border-4 border-groww-green/20 border-t-groww-green rounded-full animate-spin" />
            <p className="text-sm text-groww-text-secondary">Loading portfolio insights…</p>
          </div>
        )}
        {portfolio.status === 'error' && (
          <p className="rounded-xl bg-red-50 border border-red-100 text-red-600 text-sm px-4 py-3">{portfolio.message}</p>
        )}

        {data && (
          <>
            {data.portfolio_source === 'sample' && (
              <p className="rounded-xl bg-amber-50 border border-amber-100 text-amber-800 text-xs px-4 py-2.5">
                No holdings with a symbol and quantity were found — showing a sample portfolio. Add holdings on the Portfolio page to see your own data.
              </p>
            )}

            {/* ─── ROW 1: KPI STAT TILES ─── */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
              {[
                {
                  label: 'Portfolio Value',
                  value: inr(data.stats.total),
                  note: `${data.stats.holdings} holdings`,
                  icon: (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
                    </svg>
                  ),
                  color: '#6366F1',
                },
                {
                  label: '1-day 95% VaR',
                  value: data.stats.var_1d === null ? '–' : inr(data.stats.var_1d),
                  note: data.stats.var_1d_pct === null ? 'Not enough history' : `${pct(data.stats.var_1d_pct)} of value`,
                  icon: (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                    </svg>
                  ),
                  color: '#F59E0B',
                },
                {
                  label: 'Beta to Nifty 50',
                  value: data.stats.beta === null ? '–' : data.stats.beta.toFixed(2),
                  note: 'Last 250 sessions',
                  icon: (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                    </svg>
                  ),
                  color: '#00B386',
                },
                {
                  label: 'Largest Position',
                  value: pct(data.stats.largest.weight),
                  note: data.stats.largest.name,
                  icon: (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
                    </svg>
                  ),
                  color: '#8B5CF6',
                },
              ].map((tile) => (
                <div key={tile.label} className="bg-white rounded-2xl border border-groww-border-light shadow-card px-4 py-4 flex gap-3 items-start">
                  <span className="shrink-0 w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${tile.color}18`, color: tile.color }}>
                    {tile.icon}
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs text-groww-text-secondary">{tile.label}</p>
                    <p className="text-lg sm:text-xl font-bold text-groww-text-primary mt-0.5 leading-tight">{tile.value}</p>
                    <p className="text-[11px] text-groww-text-muted mt-0.5 truncate">{tile.note}</p>
                  </div>
                </div>
              ))}
            </div>

            {/* ─── ROW 2: PRICE CHART (full width) ─── */}
            <Card
              id="insights-prices"
              title="Price History & Market Events"
              subtitle={`Indexed to 100 at start of period. ${selected === null ? 'Showing holdings most affected by events.' : ''} Select up to ${MAX_SELECTED}.`}
            >
              {/* Event type & weather toggles inside card header area */}
              <div className="flex flex-wrap items-center gap-3 mb-4 -mt-2">
                <label className="flex items-center gap-2 text-xs text-groww-text-secondary">
                  Events
                  <select
                    value={eventType}
                    onChange={(e) => { setEventType(e.target.value); setSelected(null) }}
                    className="rounded-xl border border-groww-border bg-white px-2.5 py-1.5 text-xs text-groww-text-primary outline-none focus:border-groww-green capitalize"
                  >
                    <option value="all">All types</option>
                    {eventTypes.map(([id, label]) => (
                      <option key={id} value={id}>{label}</option>
                    ))}
                  </select>
                </label>
                {chartWeather && (
                  <label className="flex items-center gap-2 text-xs text-groww-text-secondary">
                    Weather lanes
                    <select
                      id="insights-weather-view"
                      value={weatherView}
                      onChange={(e) => setWeatherView(e.target.value as typeof weatherView)}
                      className="rounded-xl border border-groww-border bg-white px-2.5 py-1.5 text-xs text-groww-text-primary outline-none focus:border-groww-green"
                    >
                      <option value="mine">My holdings' sites</option>
                      <option value="all">All {chartWeather.sites.length} sites</option>
                      <option value="off">Hidden</option>
                    </select>
                  </label>
                )}
                {refreshing && (
                  <span className="text-xs text-groww-text-muted flex items-center gap-1.5">
                    <span className="w-3 h-3 border-2 border-groww-green/30 border-t-groww-green rounded-full animate-spin" />
                    Refreshing…
                  </span>
                )}
              </div>

              {/* Holding toggles */}
              <div className="flex flex-wrap gap-2 mb-4" role="group" aria-label="Holdings shown">
                <span className="inline-flex items-center gap-2 px-2.5 py-1 rounded-lg border border-transparent text-xs text-groww-text-secondary">
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
                      className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-lg border text-xs transition-colors duration-150 ${
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

              <div className={`transition-opacity duration-200 ${refreshing ? 'opacity-60' : ''}`}>
                <PriceChart
                  dates={data.prices.dates}
                  lines={lines}
                  events={visibleEvents}
                  activeEventId={activeEventId}
                  weather={chartWeather}
                  lanes={lanes}
                />
              </div>
              <p className="mt-2 text-[11px] text-groww-text-muted">
                ▼ marks a past event. Hover the chart to read indexed values.
                {data.prices.truncated && ' Only the eight largest holdings can be plotted.'}
              </p>
            </Card>

            {/* ─── ROW 3: SECTOR EXPOSURE + WEATHER OUTLOOK ─── */}
            <div className="grid grid-cols-1 xl:grid-cols-5 gap-4 sm:gap-6 items-start">
              {/* Sector exposure */}
              <div className="xl:col-span-2">
                <Card id="insights-exposure" title="Sector Exposure" subtitle="Share of portfolio value by sector">
                  <ul className="flex flex-col gap-3">
                    {data.sectors.map((sector) => (
                      <li
                        key={sector.sector}
                        className="group"
                        title={`${sector.sector}: ${pct(sector.weight)} (${inr(sector.value)}) in ${sector.holdings.join(', ')}`}
                      >
                        <div className="flex items-baseline justify-between gap-3 text-sm">
                          <span className="font-medium text-groww-text-primary">{sector.sector}</span>
                          <span className="tabular-nums text-groww-text-primary">
                            <span className="font-semibold">{pct(sector.weight)}</span>
                            <span className="text-groww-text-muted ml-2">{inr(sector.value)}</span>
                          </span>
                        </div>
                        <div className="mt-1 h-2.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className="h-2.5 rounded-full transition-all duration-500 group-hover:opacity-80"
                            style={{
                              width: `${Math.max(1.5, (sector.weight / maxSector) * 100)}%`,
                              background: 'linear-gradient(90deg, #00B386, #007A5A)',
                            }}
                          />
                        </div>
                        <p className="text-[11px] text-groww-text-muted mt-1 truncate">{sector.holdings.join(', ')}</p>
                      </li>
                    ))}
                  </ul>
                </Card>
              </div>

              {/* Weather outlook — simplified cards */}
              <div className="xl:col-span-3">
                <section id="insights-weather" className="bg-white rounded-2xl border border-groww-border-light shadow-card p-4 sm:p-5">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <h2 className="text-base font-bold text-groww-text-primary">Weather Outlook</h2>
                    <span className="text-[11px] text-groww-text-muted">7-day forecast</span>
                  </div>
                  <p className="text-xs text-groww-text-secondary mb-4">
                    Sites where listed companies operate, linked to your portfolio
                  </p>

                  {weather.status === 'loading' && (
                    <div className="flex items-center gap-2 text-sm text-groww-text-secondary py-4">
                      <span className="w-4 h-4 border-2 border-groww-green/30 border-t-groww-green rounded-full animate-spin" />
                      Loading forecast…
                    </div>
                  )}
                  {weather.status === 'error' && (
                    <p className="text-sm text-red-600 bg-red-50 rounded-xl px-3 py-2">{weather.message}</p>
                  )}

                  {weather.status === 'ready' && (
                    <>
                      {/* Alert banner if ANY site is flagged */}
                      {weather.data.sites.some(s => s.flags.length > 0) && (
                        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-xl px-3 py-2.5 mb-4 text-xs">
                          <svg className="shrink-0 mt-0.5 text-red-600" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                          </svg>
                          <p className="text-red-800 font-medium">
                            Weather alerts active at {weather.data.sites.filter(s => s.flags.length > 0).map(s => s.name).join(', ')}.
                            Review highlighted sites below for portfolio impact.
                          </p>
                        </div>
                      )}

                      {/* Threshold legend */}
                      <div className="flex flex-wrap gap-3 mb-4 text-[11px] text-groww-text-muted">
                        <span className="flex items-center gap-1.5">
                          <span className="text-blue-500">{WEATHER_ICONS.rain}</span>
                          Alert &gt; {weather.data.thresholds.rain_mm} mm/day
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="text-indigo-500">{WEATHER_ICONS.wind}</span>
                          Alert &gt; {weather.data.thresholds.gust_kmh} km/h gusts
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="text-orange-500">{WEATHER_ICONS.heat}</span>
                          Alert &gt; {weather.data.thresholds.temp_c}°C
                        </span>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {weather.data.sites.map((site) => {
                          const exposed = site.sectors.filter((s) => sectorWeights.has(s))
                          const share = exposed.reduce((sum, s) => sum + (sectorWeights.get(s)?.weight ?? 0), 0)
                          const myHoldings = data
                            ? data.positions
                                .filter((p) => site.companies[p.ticker.split('.')[0]])
                                .map((p) => `${p.name} (${site.companies[p.ticker.split('.')[0]]})`)
                            : []
                          return (
                            <WeatherSiteCard
                              key={site.name}
                              site={site}
                              myHoldings={myHoldings}
                              sectorExposure={share}
                              exposedSectors={exposed}
                              thresholds={weather.data.thresholds}
                            />
                          )
                        })}
                      </div>
                      <p className="mt-4 text-[11px] text-groww-text-muted">Source: {weather.data.source}</p>
                    </>
                  )}
                </section>
              </div>
            </div>

            {/* ─── ROW 4: EVENT TABLE ─── */}
            <Card
              id="insights-events"
              title="How Holdings Moved After Each Event"
              subtitle={`Change over the ${data.horizon_sessions} trading sessions from the close before the event`}
            >
              {visibleEvents.length === 0 ? (
                <p className="text-sm text-groww-text-secondary">No events of this type fall inside the selected period.</p>
              ) : (
                <div className="overflow-x-auto -mx-1">
                  <table className="w-full min-w-[560px] text-xs tabular-nums">
                    <thead>
                      <tr className="text-left text-groww-text-muted">
                        <th className="font-medium py-2 px-1">Date</th>
                        <th className="font-medium py-2 px-1">Event</th>
                        <th className="font-medium py-2 px-1 text-right">
                          <span className="inline-block w-3 h-0.5 rounded align-middle mr-1.5" style={{ background: 'var(--viz-context)' }} />
                          Nifty 50
                        </th>
                        {chosenSeries.map((series) => (
                          <th key={series.ticker} className="font-medium py-2 px-1 text-right">
                            <span className="inline-block w-3 h-0.5 rounded align-middle mr-1.5" style={{ background: colorOf(series.ticker) }} />
                            {series.name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {[...visibleEvents].reverse().map((event) => (
                        <tr
                          key={event.id}
                          onMouseEnter={() => setActiveEventId(event.id)}
                          onMouseLeave={() => setActiveEventId(null)}
                          className="border-t border-groww-border-light hover:bg-groww-bg-primary"
                        >
                          <td className="py-2 px-1 whitespace-nowrap text-groww-text-secondary">{shortDate(event.date)}</td>
                          <td className="py-2 px-1 text-groww-text-primary" title={event.description}>
                            {event.title}
                            <span className="block text-groww-text-muted capitalize">
                              {event.type_label}
                              {event.confound && ` · also: ${event.confound}`}
                            </span>
                          </td>
                          <td className="py-2 px-1 text-right"><Delta value={event.nifty} /></td>
                          {chosenSeries.map((series) => (
                            <td key={series.ticker} className="py-2 px-1 text-right">
                              <Delta value={event.moves[series.ticker]} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr className="border-t border-groww-border font-semibold">
                        <td className="py-2 px-1 text-groww-text-primary" colSpan={2}>
                          Average of {visibleEvents.length} event{visibleEvents.length === 1 ? '' : 's'}
                        </td>
                        <td className="py-2 px-1 text-right"><Delta value={average((e) => e.nifty)} /></td>
                        {chosenSeries.map((series) => (
                          <td key={series.ticker} className="py-2 px-1 text-right">
                            <Delta value={average((e) => e.moves[series.ticker])} />
                          </td>
                        ))}
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </Card>
          </>
        )}
      </div>
    </div>
  )
}

export default InsightsPage
