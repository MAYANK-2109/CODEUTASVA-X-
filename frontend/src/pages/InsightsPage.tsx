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

// Days that crossed an alert threshold at each site, placed on the chart's dates.
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

// One fixed colour per holding, in portfolio order, so a holding keeps its
// colour whatever else is selected. Declared as --viz-* in index.css.
const SERIES_COLORS = Array.from({ length: 8 }, (_, i) => `var(--viz-series-${i + 1})`)
const MAX_SELECTED = 4
const DEFAULT_SELECTED = 3

// Each kind of alert day has its own row inside a site's lane as well as its own
// colour, so the kinds can be told apart without colour. Checked with the palette validator.
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

const Delta: React.FC<{ value: number | null | undefined }> = ({ value }) =>
  value === null || value === undefined ? (
    <span className="text-[color:var(--viz-muted)]">–</span>
  ) : (
    <span className={value < 0 ? 'text-[color:var(--viz-critical)]' : 'text-[color:var(--viz-good-text)]'}>
      {signedPct(value)}
    </span>
  )

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

  // The price plot, its date axis, then one lane per weather site on the same dates.
  const wide = width >= 520
  const margin = { top: 26, right: wide ? (lanes.length ? 128 : 118) : 14, bottom: 30, left: 42 }
  const plotH = 284
  const plotW = Math.max(0, width - margin.left - margin.right)
  const last = dates.length - 1
  // On a narrow screen there is no room beside a lane, so its name goes above it.
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

  // Label the first plotted point of each year (each month on the 1Y view).
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
      if (v === null) {
        pen = false
        return
      }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
      pen = true
    })
    return d
  }

  // End labels only when they do not collide; the legend always carries identity.
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
  // The forecast days still ahead sit to the right of the last price, when there is room.
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
        <svg width={width} height={height} role="img" aria-label="Indexed price history of selected holdings with past events marked, and weather alert days by site below">
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={margin.left}
                x2={margin.left + plotW}
                y1={y(tick)}
                y2={y(tick)}
                stroke={tick === 100 ? 'var(--viz-axis)' : 'var(--viz-grid)'}
                strokeWidth={1}
              />
              <text x={margin.left - 8} y={y(tick) + 4} textAnchor="end" fontSize={11} fill="var(--viz-muted)" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {tick}
              </text>
            </g>
          ))}
          {xTicks.map((tick) => (
            <text key={tick.index} x={x(tick.index)} y={margin.top + plotH + 22} textAnchor="middle" fontSize={11} fill="var(--viz-muted)">
              {tick.label}
            </text>
          ))}

          {/* Event markers: a hairline and a pin above the plot */}
          {events.map((event) => {
            const active = event.id === activeEventId || (hover !== null && event.index === hover)
            return (
              <g key={event.id}>
                <line
                  x1={x(event.index)}
                  x2={x(event.index)}
                  y1={margin.top - 6}
                  y2={margin.top + plotH}
                  stroke={active ? 'var(--viz-ink)' : 'var(--viz-axis)'}
                  strokeWidth={1}
                />
                <path
                  d={`M${x(event.index) - 5},${margin.top - 16} h10 l-5,9 z`}
                  fill={active ? 'var(--viz-ink)' : 'var(--viz-muted)'}
                  stroke="var(--viz-surface)"
                  strokeWidth={1}
                />
              </g>
            )
          })}

          {lines.map((line) => (
            <path key={line.key} d={pathFor(line.values)} fill="none" stroke={line.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
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

          {/* Weather lanes: alert days at each site, on the same dates as the prices */}
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
                        <title>
                          {`${lane.name}, ${shortDate(day)} forecast: ${
                            flags.length
                              ? flags.map((f) => `${WEATHER_KINDS[f.kind].label} ${f.value} ${WEATHER_KINDS[f.kind].unit}`).join(', ')
                              : 'no alert'
                          }`}
                        </title>
                        <rect x={aheadX + d * CELL} y={laneY(i)} width={CELL - 1} height={LANE_H} rx={1} fill="var(--viz-grid)" opacity={0.45} />
                        {flags.map((f) => (
                          <rect
                            key={f.kind}
                            x={aheadX + d * CELL}
                            y={laneY(i) + 1 + WEATHER_KINDS[f.kind].row * KIND_H}
                            width={CELL - 1}
                            height={KIND_H}
                            fill={WEATHER_KINDS[f.kind].color}
                          />
                        ))}
                      </g>
                    )
                  })}
                  <text
                    x={wide ? aheadX + (ahead ? ahead.dates.length * CELL + 7 : 0) : margin.left}
                    y={wide ? laneY(i) + 10.5 : laneY(i) - 3}
                    fontSize={wide ? 11 : 10}
                    fontWeight={lane.emphasised ? 600 : 400}
                    fill={lane.emphasised ? 'var(--viz-ink)' : 'var(--viz-ink-2)'}
                  >
                    <title>
                      {lane.holdings.length ? `${lane.name}: ${lane.holdings.join('; ')}` : `${lane.name}: none of your holdings operates here`}
                    </title>
                    {shortSite(lane.name)}
                  </text>
                </g>
              ))}
              {marks.map((mark) => {
                const half = markW / 2
                const left = Math.max(margin.left, x(mark.index) - half)
                const right = Math.min(margin.left + plotW, x(mark.index) + half)
                return (
                  <rect
                    key={`${mark.index}-${mark.site}-${mark.kind}`}
                    x={left}
                    y={laneY(laneOf.get(mark.site) ?? 0) + 1 + WEATHER_KINDS[mark.kind].row * KIND_H}
                    width={Math.max(2, right - left)}
                    height={KIND_H}
                    fill={WEATHER_KINDS[mark.kind].color}
                  />
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

          <rect
            x={margin.left}
            y={margin.top - 18}
            width={plotW}
            height={bottom - margin.top + 18}
            fill="transparent"
            tabIndex={0}
            aria-label="Price chart. Use the left and right arrow keys to read values."
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
                    {mark.days > 1 ? `, worst of ${mark.days} alert days (${shortDate(mark.date)})` : ` on ${shortDate(mark.date)}`}
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
// Weather heat grid: sites by day for one measure
// ---------------------------------------------------------------------------
const HeatGrid: React.FC<{
  title: string
  unit: string
  hue: string
  sites: WeatherInsights['sites']
  pick: (day: WeatherDay) => number | null
  floor: number
  threshold: number
  thresholdLabel: string
}> = ({ title, unit, hue, sites, pick, floor, threshold, thresholdLabel }) => {
  const days = sites[0]?.days ?? []
  const shade = (value: number) =>
    Math.round(8 + 92 * Math.max(0, Math.min(1, (value - floor) / (threshold * 1.15 - floor))))
  return (
    <div className="min-w-0">
      <h3 className="text-xs font-semibold text-groww-text-primary">
        {title} <span className="font-normal text-groww-text-secondary">({unit})</span>
      </h3>
      <div className="mt-2">
        <table className="w-full table-fixed border-separate text-[11px] tabular-nums" style={{ borderSpacing: 2 }}>
          <colgroup>
            <col style={{ width: 88 }} />
          </colgroup>
          <thead>
            <tr className="text-[color:var(--viz-muted)]">
              <th className="text-left font-normal pr-1">Site</th>
              {days.map((day) => (
                <th key={day.date} className="font-normal">
                  {new Date(day.date).toLocaleDateString('en-IN', { weekday: 'short' }).slice(0, 2)}
                  <span className="block">{new Date(day.date).getDate()}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sites.map((site) => (
              <tr key={site.name}>
                <th className="text-left font-normal text-groww-text-secondary pr-1 truncate">{site.name}</th>
                {site.days.map((day) => {
                  const value = pick(day)
                  if (value === null) {
                    return <td key={day.date} className="text-center text-[color:var(--viz-muted)]">–</td>
                  }
                  const level = shade(value)
                  const alert = value >= threshold
                  return (
                    <td
                      key={day.date}
                      title={`${site.name}, ${shortDate(day.date)}: ${value} ${unit}${alert ? ` (at or above ${thresholdLabel})` : ''}`}
                      className={`h-7 text-center rounded ${alert ? 'font-bold' : ''}`}
                      style={{
                        background: `color-mix(in oklab, ${hue} ${level}%, var(--viz-surface))`,
                        color: level >= 55 ? '#ffffff' : 'var(--viz-ink)',
                        boxShadow: alert ? 'inset 0 0 0 2px var(--viz-critical)' : undefined,
                      }}
                    >
                      {alert && <span aria-label="alert">▲</span>}
                      {Math.round(value)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1.5 flex items-center gap-1.5 text-[10px] text-[color:var(--viz-muted)]">
        <span>{floor}</span>
        <span
          className="h-1.5 w-16 rounded"
          style={{ background: `linear-gradient(90deg, color-mix(in oklab, ${hue} 8%, var(--viz-surface)), ${hue})` }}
        />
        <span>
          {threshold} = {thresholdLabel}
        </span>
      </p>
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
      // Keep the last good render if a refresh fails.
      setPortfolio((previous) => (previous.status === 'ready' ? previous : { status: 'error', message }))
    } finally {
      setRefreshing(false)
    }
  }, [user?.id, range])

  useEffect(() => {
    loadPortfolio()
  }, [loadPortfolio])

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

  // Default view: the holdings that moved most after the events in view.
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

  // Weather lanes under the price chart: the sites where the user's holdings operate.
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
  const laneStats = lanes.map((lane) => {
    const own = (chartWeather?.marks ?? []).filter((m) => m.site === lane.site)
    return {
      lane,
      kinds: KIND_ORDER.map((kind) => {
        const of = own.filter((m) => m.kind === kind)
        const peak = of.reduce<WeatherMark | null>((best, m) => (best === null || m.value > best.value ? m : best), null)
        return { kind, days: of.reduce((sum, m) => sum + m.days, 0), peak }
      }),
    }
  })

  const sectorWeights = new Map(data?.sectors.map((s) => [s.sector, s]) ?? [])
  const maxSector = Math.max(...(data?.sectors.map((s) => s.weight) ?? [1]))

  return (
    <div id="insights-page" className="viz-root flex flex-col min-h-full">
      <header
        className="sticky top-0 z-20 bg-white border-b border-groww-border-light px-4 sm:px-6 py-3 sm:py-4"
        style={{ minHeight: '60px' }}
      >
        <h1 className="text-lg font-bold text-groww-text-primary">Insights</h1>
        <p className="text-xs text-groww-text-muted mt-0.5">
          {data
            ? `Prices to ${shortDate(data.as_of)}${data.price_source === 'cache' ? ' (saved copy, live feed unavailable)' : ''}`
            : 'Risk exposure, price history and weather outlook'}
        </p>
      </header>

      <div className="flex-1 p-4 sm:p-6 max-w-[1400px] w-full mx-auto flex flex-col gap-4 sm:gap-6">
        {portfolio.status === 'loading' && (
          <p className="py-24 text-center text-sm text-groww-text-secondary">Loading portfolio insights…</p>
        )}
        {portfolio.status === 'error' && (
          <p className="rounded-xl bg-red-50 border border-red-100 text-red-600 text-sm px-4 py-3">{portfolio.message}</p>
        )}

        {data && (
          <>
            {data.portfolio_source === 'sample' && (
              <p className="rounded-xl bg-amber-50 border border-amber-100 text-amber-800 text-xs px-4 py-2.5">
                No holdings with a symbol and quantity were found, so this page shows a sample portfolio. Add
                holdings on the Portfolio page to see your own.
              </p>
            )}

            {/* Headline figures */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
              {[
                { label: 'Portfolio value', value: inr(data.stats.total), note: `${data.stats.holdings} holdings` },
                {
                  label: '1-day 95% VaR',
                  value: data.stats.var_1d === null ? '–' : inr(data.stats.var_1d),
                  note: data.stats.var_1d_pct === null ? 'Not enough history' : `${pct(data.stats.var_1d_pct)} of value`,
                },
                {
                  label: 'Beta to Nifty 50',
                  value: data.stats.beta === null ? '–' : data.stats.beta.toFixed(2),
                  note: 'Last 250 sessions',
                },
                { label: 'Largest position', value: pct(data.stats.largest.weight), note: data.stats.largest.name },
              ].map((tile) => (
                <div key={tile.label} className="bg-white rounded-2xl border border-groww-border-light shadow-card px-4 py-3.5">
                  <p className="text-xs text-groww-text-secondary">{tile.label}</p>
                  <p className="text-xl sm:text-2xl font-semibold text-groww-text-primary mt-1">{tile.value}</p>
                  <p className="text-xs text-groww-text-muted mt-0.5 truncate">{tile.note}</p>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-5 gap-4 sm:gap-6 items-start">
              {/* Sector exposure */}
              <div className="xl:col-span-2">
                <Card id="insights-exposure" title="Sector exposure" subtitle="Share of portfolio value by sector">
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
                        <div className="mt-1 h-3">
                          <div
                            className="h-3 rounded-r transition-opacity duration-150 group-hover:opacity-80"
                            style={{
                              width: `${Math.max(1.5, (sector.weight / maxSector) * 100)}%`,
                              background: 'var(--viz-series-1)',
                            }}
                          />
                        </div>
                        <p className="text-[11px] text-groww-text-muted mt-1 truncate">{sector.holdings.join(', ')}</p>
                      </li>
                    ))}
                  </ul>
                </Card>
              </div>

              {/* Weather outlook */}
              <div className="xl:col-span-3">
                <Card
                  id="insights-weather"
                  title="Weather outlook"
                  subtitle="7-day forecast at sites where listed companies operate, linked to your holdings"
                >
                  {weather.status === 'loading' && <p className="text-sm text-groww-text-secondary">Loading forecast…</p>}
                  {weather.status === 'error' && <p className="text-sm text-red-600">{weather.message}</p>}
                  {weather.status === 'ready' && (
                    <>
                      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs">
                        {weather.data.sites.map((site) => {
                          const exposed = site.sectors.filter((s) => sectorWeights.has(s))
                          const share = exposed.reduce((sum, s) => sum + (sectorWeights.get(s)?.weight ?? 0), 0)
                          const alert = site.flags.length > 0
                          // Holdings with a physical operation at this site, e.g. a refinery.
                          const present = data.positions
                            .filter((p) => site.companies[p.ticker.split('.')[0]])
                            .map((p) => `${p.name} (${site.companies[p.ticker.split('.')[0]]})`)
                          return (
                            <li key={site.name} className="flex items-start gap-2" title={site.relevance}>
                              <span
                                className="mt-0.5 shrink-0 font-bold"
                                style={{ color: alert ? 'var(--viz-critical)' : 'var(--viz-good)' }}
                                aria-hidden
                              >
                                {alert ? '▲' : '✓'}
                              </span>
                              <span className="min-w-0">
                                <span className="font-semibold text-groww-text-primary">{site.name}</span>{' '}
                                <span className={alert ? 'font-semibold text-groww-text-primary' : 'text-groww-text-secondary'}>
                                  {alert ? site.flags.join(', ') : 'no alert'}
                                </span>
                                <span className={`block ${alert && present.length ? 'text-groww-text-primary' : 'text-groww-text-secondary'}`}>
                                  {present.length ? `Your holdings here: ${present.join('; ')}` : 'None of your holdings operates here'}
                                </span>
                                <span className="block text-groww-text-muted">
                                  {exposed.length
                                    ? `${pct(share, 0)} of your portfolio in exposed sectors (${exposed.join(', ')})`
                                    : 'None of your sectors exposed'}
                                </span>
                              </span>
                            </li>
                          )
                        })}
                      </ul>
                      <div className="mt-5 grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(270px, 1fr))' }}>
                        <HeatGrid
                          title="Rain" unit="mm/day" hue="#1c5cab" sites={weather.data.sites}
                          pick={(d) => d.rain_mm} floor={0}
                          threshold={weather.data.thresholds.rain_mm} thresholdLabel="heavy rain"
                        />
                        <HeatGrid
                          title="Wind gusts" unit="km/h" hue="#4a3aa7" sites={weather.data.sites}
                          pick={(d) => d.gust_kmh} floor={0}
                          threshold={weather.data.thresholds.gust_kmh} thresholdLabel="gale"
                        />
                        <HeatGrid
                          title="Max temperature" unit="°C" hue="#b4491c" sites={weather.data.sites}
                          pick={(d) => d.temp_c} floor={15}
                          threshold={weather.data.thresholds.temp_c} thresholdLabel="extreme heat"
                        />
                      </div>
                      <p className="mt-3 text-[11px] text-groww-text-muted">
                        ▲ marks a day at or above the alert threshold. Source: {weather.data.source}.
                      </p>
                    </>
                  )}
                </Card>
              </div>
            </div>

            {/* Filters: scope the price chart and the event table below */}
            <div id="insights-filters" className="flex flex-wrap items-center gap-3 pt-2">
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
              <label className="flex items-center gap-2 text-xs text-groww-text-secondary">
                Events
                <select
                  value={eventType}
                  onChange={(e) => {
                    setEventType(e.target.value)
                    setSelected(null)
                  }}
                  className="rounded-xl border border-groww-border bg-white px-2.5 py-1.5 text-xs text-groww-text-primary outline-none focus:border-groww-green capitalize"
                >
                  <option value="all">All types</option>
                  {eventTypes.map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              {chartWeather && (
                <label className="flex items-center gap-2 text-xs text-groww-text-secondary">
                  Weather
                  <select
                    id="insights-weather-view"
                    value={weatherView}
                    onChange={(e) => setWeatherView(e.target.value as typeof weatherView)}
                    className="rounded-xl border border-groww-border bg-white px-2.5 py-1.5 text-xs text-groww-text-primary outline-none focus:border-groww-green"
                  >
                    <option value="mine">Sites of my holdings</option>
                    <option value="all">All {chartWeather.sites.length} sites</option>
                    <option value="off">Hidden</option>
                  </select>
                </label>
              )}
            </div>

            <div className={`flex flex-col gap-4 sm:gap-6 transition-opacity duration-200 ${refreshing ? 'opacity-60' : ''}`}>
              <Card
                id="insights-prices"
                title="Price history, past events and weather"
                subtitle={`Indexed to 100 at the start of the period. ${
                  selected === null ? 'Showing the holdings that moved most after the events in view.' : ''
                } Pick up to ${MAX_SELECTED} holdings.`}
              >
                <div className="flex flex-wrap gap-2 mb-3" role="group" aria-label="Holdings shown">
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
                        <span
                          className="w-4 h-0.5 rounded"
                          style={{ background: on ? colorOf(series.ticker) : 'var(--viz-axis)' }}
                        />
                        {series.name}
                      </button>
                    )
                  })}
                </div>
                <PriceChart
                  dates={data.prices.dates}
                  lines={lines}
                  events={visibleEvents}
                  activeEventId={activeEventId}
                  weather={chartWeather}
                  lanes={lanes}
                />
                <p className="mt-2 text-[11px] text-groww-text-muted">
                  ▼ marks a past event; the table below lists each one.
                  {data.prices.truncated && ' Only the eight largest holdings can be plotted.'}
                </p>
                {chartWeather === null && (
                  <p className="mt-1 text-[11px] text-groww-text-muted">Weather history is unavailable, so the chart shows prices and events only.</p>
                )}
                {chartWeather && lanes.length > 0 && (
                  <div id="insights-weather-lanes" className="mt-3 pt-3 border-t border-groww-border-light">
                    <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-[11px] text-groww-text-secondary">
                      {KIND_ORDER.map((kind) => {
                        const spec = WEATHER_KINDS[kind]
                        const threshold = { rain: `${chartWeather.thresholds.rain_mm} mm in a day`, wind: `gusts of ${chartWeather.thresholds.gust_kmh} km/h`, heat: `${chartWeather.thresholds.temp_c} °C` }[kind]
                        return (
                          <li key={kind} className="flex items-center gap-2">
                            <span className="relative w-5 rounded-sm" style={{ height: LANE_H, background: 'color-mix(in oklab, var(--viz-grid) 45%, var(--viz-surface))' }} aria-hidden>
                              <span className="absolute inset-x-0" style={{ top: 1 + spec.row * KIND_H, height: KIND_H, background: spec.color }} />
                            </span>
                            <span>
                              <span className="font-medium text-groww-text-primary">{sentence(spec.label)}</span>: {threshold} or more
                            </span>
                          </li>
                        )
                      })}
                    </ul>
                    <div className="mt-3 overflow-x-auto -mx-1">
                      <table className="w-full min-w-[680px] text-xs tabular-nums">
                        <caption className="sr-only">Weather alert days at each site in the period shown</caption>
                        <thead>
                          <tr className="text-left text-groww-text-muted">
                            <th className="font-medium py-1.5 px-1">Site</th>
                            <th className="font-medium py-1.5 px-1">Your holdings there</th>
                            {KIND_ORDER.map((kind) => (
                              <th key={kind} className="font-medium py-1.5 px-1 text-right">{sentence(WEATHER_KINDS[kind].label)} days</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {laneStats.map(({ lane, kinds }) => (
                            <tr key={lane.site} className="border-t border-groww-border-light align-top">
                              <td className={`py-1.5 px-1 whitespace-nowrap ${lane.emphasised ? 'font-semibold text-groww-text-primary' : 'text-groww-text-primary'}`}>
                                {lane.name}
                              </td>
                              <td className="py-1.5 px-1 min-w-[220px] text-groww-text-secondary">
                                {lane.holdings.length ? lane.holdings.join('; ') : 'None'}
                              </td>
                              {kinds.map(({ kind, days, peak }) => (
                                <td key={kind} className="py-1.5 px-1 text-right whitespace-nowrap">
                                  <span className="text-groww-text-primary">{days}</span>
                                  {peak && (
                                    <span className="block text-groww-text-muted">
                                      peak {peak.value} {WEATHER_KINDS[kind].unit}, {shortDate(peak.date)}
                                    </span>
                                  )}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 text-[11px] text-groww-text-muted">
                      {weatherView === 'mine' && myLanes.length === 0 && 'None of your holdings has a mapped site, so all sites are shown. '}
                      Weather history to {shortDate(chartWeather.through)}.
                      {chartWeather.forecast === null && ' The 7-day forecast is unavailable right now.'} Source: {chartWeather.source}.
                    </p>
                  </div>
                )}
              </Card>

              <Card
                id="insights-events"
                title="How holdings moved after each event"
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
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default InsightsPage
