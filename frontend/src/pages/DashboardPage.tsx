import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { isLeftover, savedPrice } from '../lib/holdings'
import NotificationBell from '../components/NotificationBell'
import { RiskOverviewTiles, RiskAlertList, RiskStreamsCard } from '../components/RiskTerminal'
import { Skeleton } from '../components/Skeleton'
import { IconTriangleUp, IconTriangleDown } from '../components/Icons'
import { AgentGraphCard, MarketRiskRow } from '../components/DashboardIntel'

// ── Types ─────────────────────────────────────────────────────────────────────

interface NewsItem {
  title: string
  source: string | null
  url: string | null
  published_at: string | null
  // The sectors this headline is about, and any holdings it names.
  sectors: string[]
  holdings: string[]
}

interface NewsSector {
  sector: string
  holdings: string[]
}

interface HoldingRow {
  id: string
  name: string
  symbol: string
  type: string          // stock | etf | mf | bond | other
  units: number
  buyPrice: number
  livePrice: number | null
  history: number[]     // last ~30 closing prices, oldest first
}

interface PortfolioStats {
  totalInvested: number
  totalCurrent: number
  totalProfitAmount: number
  totalProfitPercent: number
  totalStocks: number
  isCustom: boolean
  lastUpdated: string
  // Holdings with no market price; they are valued at cost in totalCurrent.
  unpricedCount: number
  // False until real figures have loaded; the page then shows a dash, never a stand-in number.
  available: boolean
  health: { label: string; detail: string } | null
}

interface MarketIndex {
  key: string
  label: string
  value: number
  change_1d_pct: number
  change_1m_pct: number
}

interface MarketOverview {
  indices: MarketIndex[]
  trend: { label: string; detail: string } | null
  as_of: string | null
}

// Concentration, from the effective number of holdings (1 / sum of squared weights).
function portfolioHealth(positions: { name: string; value: number }[]) {
  const total = positions.reduce((sum, p) => sum + p.value, 0)
  if (!(total > 0)) return null
  const effective = 1 / positions.reduce((sum, p) => sum + (p.value / total) ** 2, 0)
  const largest = positions.reduce((a, b) => (b.value > a.value ? b : a))
  const label = effective < 3 ? 'Concentrated' : effective < 6 ? 'Moderately diversified' : 'Diversified'
  return { label, detail: `Largest holding is ${((largest.value / total) * 100).toFixed(0)}% (${largest.name})` }
}

// ── Mini sparkline (96×48, matches reference image style) ──────────────────────

const MiniSparkline: React.FC<{ values: number[]; positive: boolean }> = ({ values, positive }) => {
  const W = 96, H = 48, padX = 4, padY = 6
  if (values.length < 2) {
    return (
      <svg width={W} height={H} aria-hidden style={{ display: 'block' }}>
        <rect x={0} y={0} width={W} height={H} rx={6} fill={positive ? 'rgba(0,179,134,0.06)' : 'rgba(239,68,68,0.06)'} />
        <line x1={padX} y1={H / 2} x2={W - padX} y2={H / 2} stroke="#e5e7eb" strokeWidth="1.5" strokeDasharray="3 2" />
      </svg>
    )
  }
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const px = (i: number) => padX + (i / (values.length - 1)) * (W - padX * 2)
  const py = (v: number) => H - padY - ((v - lo) / span) * (H - padY * 2)
  let d = ''
  // Smooth curve via cubic bezier
  values.forEach((v, i) => {
    if (i === 0) { d += `M${px(i).toFixed(1)},${py(v).toFixed(1)}` }
    else {
      const cpX = (px(i) + px(i - 1)) / 2
      d += ` C${cpX.toFixed(1)},${py(values[i - 1]).toFixed(1)} ${cpX.toFixed(1)},${py(v).toFixed(1)} ${px(i).toFixed(1)},${py(v).toFixed(1)}`
    }
  })
  const fillD = `${d} L${px(values.length - 1).toFixed(1)},${H} L${px(0).toFixed(1)},${H} Z`
  const color = positive ? '#00B386' : '#EF4444'
  const fillColor = positive ? 'rgba(0,179,134,0.13)' : 'rgba(239,68,68,0.11)'
  const dotX = px(values.length - 1)
  const dotY = py(values[values.length - 1])
  return (
    <svg width={W} height={H} aria-label="price sparkline" style={{ display: 'block', overflow: 'visible' }}>
      <rect x={0} y={0} width={W} height={H} rx={6} fill={fillColor} />
      <path d={fillD} fill={fillColor} />
      <path d={d} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={dotX} cy={dotY} r="3.5" fill={color} stroke="white" strokeWidth="1.5" />
    </svg>
  )
}

// ── Avatar initials helper ──────────────────────────────────────────────────────

const AVATAR_GRADIENTS = [
  'linear-gradient(135deg,#F97316,#EA580C)',
  'linear-gradient(135deg,#3B82F6,#1D4ED8)',
  'linear-gradient(135deg,#10B981,#047857)',
  'linear-gradient(135deg,#8B5CF6,#6D28D9)',
  'linear-gradient(135deg,#EF4444,#B91C1C)',
  'linear-gradient(135deg,#F59E0B,#D97706)',
  'linear-gradient(135deg,#EC4899,#BE185D)',
  'linear-gradient(135deg,#14B8A6,#0F766E)',
  'linear-gradient(135deg,#6366F1,#4338CA)',
  'linear-gradient(135deg,#84CC16,#4D7C0F)',
]

function stockInitials(name: string): string {
  const words = name.trim().split(/[\s\-_]+/).filter(Boolean)
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

// ── SVG Icon Components (Strictly NO emojis) ──────────────────────────────────

const IconWallet = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
    <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
    <path d="M18 12a2 2 0 0 0 0 4h4v-4z" />
  </svg>
)

const IconBarChart = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10" />
    <line x1="12" y1="20" x2="12" y2="4" />
    <line x1="6" y1="20" x2="6" y2="14" />
  </svg>
)

const IconTrendingUp = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
    <polyline points="17 6 23 6 23 12" />
  </svg>
)

const IconPercent = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="19" y1="5" x2="5" y2="19" />
    <circle cx="6.5" cy="6.5" r="2.5" />
    <circle cx="17.5" cy="17.5" r="2.5" />
  </svg>
)

const IconCurrencyRupee = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 3h12" />
    <path d="M6 8h12" />
    <path d="M6 13l8.5 8" />
    <path d="M6 13h3a4 4 0 0 0 0-8" />
  </svg>
)

const IconShieldCheck = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <polyline points="9 12 11 14 15 10" />
  </svg>
)

const IconExternalLink = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    <polyline points="15 3 21 3 21 9" />
    <line x1="10" y1="14" x2="21" y2="3" />
  </svg>
)

const IconArrowRight = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
)

const IconRefresh = ({ spinning = false }: { spinning?: boolean }) => (
  <svg
    className={spinning ? 'animate-spin' : ''}
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <polyline points="23 4 23 10 17 10" />
    <polyline points="1 20 1 14 7 14" />
    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
  </svg>
)

const IconSearch = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="8" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
)

const IconUpload = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </svg>
)

const IconClock = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <polyline points="12 6 12 12 16 14" />
  </svg>
)

const IconSparkle = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
  </svg>
)

// ── Fallback news dataset (guarantees instant display if backend is sleeping) ───

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtCur = (n: number) =>
  `\u20B9${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const fmtNum = (n: number) =>
  n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const fmtPct = (n: number) => {
  const prefix = n >= 0 ? '+' : ''
  return `${prefix}${n.toFixed(2)}%`
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return 'Recently'
  try {
    const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000)
    if (diff < 60) return 'Just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return `${Math.floor(diff / 86400)}d ago`
  } catch {
    return 'Recently'
  }
}

// ── Main Dashboard Component ──────────────────────────────────────────────────

const DashboardPage: React.FC = () => {
  const { user } = useAuth()
  const navigate = useNavigate()

  // State
  const [portfolioStats, setPortfolioStats] = useState<PortfolioStats>({
    totalInvested: 0,
    totalCurrent: 0,
    totalProfitAmount: 0,
    totalProfitPercent: 0,
    totalStocks: 0,
    isCustom: false,
    lastUpdated: '',
    unpricedCount: 0,
    available: false,
    health: null,
  })
  const [market, setMarket] = useState<MarketOverview | null>(null)
  const [marketFailed, setMarketFailed] = useState(false)
  const [portfolioLoading, setPortfolioLoading] = useState(true)
  const [holdingRows, setHoldingRows] = useState<HoldingRow[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  const [news, setNews] = useState<NewsItem[]>([])
  const [newsLoading, setNewsLoading] = useState(true)
  const [newsFilter, setNewsFilter] = useState('ALL')
  const [newsFailed, setNewsFailed] = useState(false)
  const [newsScope, setNewsScope] = useState<{
    source: 'user' | 'sample'
    windowDays: number
    sectors: NewsSector[]
    unmapped: string[]
  } | null>(null)
  const [newsSearch, setNewsSearch] = useState('')
  // Bumped by the refresh button so the risk overview reloads with everything else.
  const [refreshKey, setRefreshKey] = useState(0)

  // Backend URL resolution
  const backendUrl =
    (import.meta as any).env?.VITE_BACKEND_URL ||
    (import.meta as any).env?.VITE_API_URL ||
    'https://codeutasva-x.onrender.com'

  // User details
  const userEmail = user?.email || 'investor@example.com'
  const savedFirstName = localStorage.getItem(`user_${userEmail}_firstName`)
  const metadataName = (user?.user_metadata?.full_name as string) || ''
  const displayName =
    metadataName || savedFirstName || userEmail.split('@')[0] || 'Investor'
  const userInitial = (displayName.charAt(0) || 'U').toUpperCase()

  // Time of day greeting
  const greeting = useMemo(() => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }, [])

  // ── Fetch Personal Portfolio Data ───────────────────────────────────────────
  const fetchPortfolioData = useCallback(async () => {
    setPortfolioLoading(true)
    try {
      let foundUserHoldings = false

      // 1. Try Supabase personal holdings for this user
      if (user?.id) {
        const { data: saved, error } = await supabase
          .from('portfolio_holdings')
          .select('*')
          .eq('user_id', user.id)
        // Header rows saved by an old import are not holdings.
        const holdings = (saved ?? []).filter((h) => !isLeftover(h))

        if (!error && holdings.length > 0) {
          foundUserHoldings = true

          // Latest market prices, matched by symbol, ISIN or name. A holding
          // the backend cannot price keeps the price saved with it.
          let live: Record<string, number> = {}
          try {
            const res = await fetch(`${backendUrl}/api/prices`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                holdings: holdings.map((h) => ({
                  key: String(h.id),
                  symbol: h.symbol ?? '',
                  isin: h.isin ?? '',
                  name: h.name ?? '',
                })),
              }),
            })
            if (res.ok) live = (await res.json()).prices ?? {}
          } catch {
            // Saved prices are used below.
          }
          const liveCount = holdings.filter((h) => live[String(h.id)] != null).length
          const priceOf = (h: (typeof holdings)[number]) => live[String(h.id)] ?? savedPrice(h)
          const unpricedCount = holdings.filter((h) => priceOf(h) == null).length

          const invested = holdings.reduce(
            (acc, h) => acc + (Number(h.units) || 0) * (Number(h.buy_price) || 0),
            0
          )
          const current = holdings.reduce(
            (acc, h) =>
              acc +
              (Number(h.units) || 0) * (priceOf(h) ?? (Number(h.buy_price) || 0)),
            0
          )
          const profit = current - invested
          const profitPct = invested > 0 ? (profit / invested) * 100 : 0

          setPortfolioStats({
            totalInvested: invested,
            totalCurrent: current,
            totalProfitAmount: profit,
            totalProfitPercent: profitPct,
            totalStocks: holdings.length,
            isCustom: true,
            lastUpdated: liveCount > 0 ? `${liveCount} of ${holdings.length} live` : 'Saved prices',
            unpricedCount,
            available: true,
            health: portfolioHealth(
              holdings.map((h) => ({
                name: h.name,
                value: (Number(h.units) || 0) * (priceOf(h) ?? (Number(h.buy_price) || 0)),
              }))
            ),
          })
        }
      }

      // 2. If user has not uploaded personal holdings yet, load from backend portfolio API
      if (!foundUserHoldings) {
        try {
          const res = await fetch(`${backendUrl}/api/portfolio`, {
            headers: { 'Content-Type': 'application/json' },
          })
          if (res.ok) {
            const data = await res.json()
            const sample: { name: string; invested_value: number; current_value: number | null }[] =
              Array.isArray(data.holdings) ? data.holdings : []
            if (sample.length > 0) {
              // A sample holding without a live price is valued at cost and counted as unpriced.
              const invested = sample.reduce((sum, h) => sum + (Number(h.invested_value) || 0), 0)
              const current = sample.reduce(
                (sum, h) => sum + (h.current_value ?? (Number(h.invested_value) || 0)),
                0
              )
              const profit = current - invested
              setPortfolioStats({
                totalInvested: invested,
                totalCurrent: current,
                totalProfitAmount: profit,
                totalProfitPercent: invested > 0 ? (profit / invested) * 100 : 0,
                totalStocks: sample.length,
                isCustom: false,
                lastUpdated: 'Sample portfolio',
                unpricedCount: sample.filter((h) => h.current_value == null).length,
                available: true,
                health: portfolioHealth(
                  sample.map((h) => ({ name: h.name, value: h.current_value ?? (Number(h.invested_value) || 0) }))
                ),
              })
            }
          }
        } catch {
          // Figures stay unavailable and the page shows a dash.
        }
      }
    } catch {
      // Figures stay unavailable and the page shows a dash.
    } finally {
      setPortfolioLoading(false)
    }
  }, [user?.id, backendUrl])

  // ── Fetch per-holding price history (sparklines) ────────────────────────────
  const fetchHoldingHistory = useCallback(async () => {
    if (!user?.id) return
    setHistoryLoading(true)
    try {
      const { data: saved, error } = await supabase
        .from('portfolio_holdings')
        .select('*')
        .eq('user_id', user.id)
      const holdings = (saved ?? []).filter((h: any) => !isLeftover(h))
      if (error || holdings.length === 0) return

      // Fetch live prices
      let live: Record<string, number> = {}
      try {
        const res = await fetch(`${backendUrl}/api/prices`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            holdings: holdings.map((h: any) => ({
              key: String(h.id), symbol: h.symbol ?? '', isin: h.isin ?? '', name: h.name ?? '',
            })),
          }),
        })
        if (res.ok) live = (await res.json()).prices ?? {}
      } catch { /* use buy prices */ }

      // Fetch sparkline history from backend insights endpoint
      let priceHistory: Record<string, number[]> = {}
      try {
        const res = await fetch(`${backendUrl}/api/insights/portfolio`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            holdings: holdings.map((h: any) => ({
              name: h.name, symbol: h.symbol, isin: h.isin, units: h.units, buy_price: h.buy_price, type: h.type,
            })),
            range: '1y',
          }),
        })
        if (res.ok) {
          const json = await res.json()
          const series: { ticker: string; values: (number | null)[] }[] = json?.prices?.series ?? []
          series.forEach((s) => {
            const nums = s.values.filter((v): v is number => v !== null)
            // Take last 30 points
            priceHistory[s.ticker] = nums.slice(-30)
          })
        }
      } catch { /* sparkline stays empty */ }

      const classify = (h: any): string => {
        const t = (h.type ?? '').toLowerCase()
        if (t.includes('etf')) return 'ETF'
        if (t.includes('mf') || t.includes('mutual') || t.includes('fund')) return 'MF'
        if (t.includes('bond') || t.includes('debt') || t.includes('ncd')) return 'Bond'
        return 'Stock'
      }

      const rows: HoldingRow[] = holdings.map((h: any) => {
        const ticker = (h.symbol ?? '').toUpperCase()
        return {
          id: String(h.id),
          name: h.name ?? ticker,
          symbol: ticker,
          type: classify(h),
          units: Number(h.units) || 0,
          buyPrice: Number(h.buy_price) || 0,
          livePrice: live[String(h.id)] ?? null,
          history: priceHistory[ticker] ?? priceHistory[`${ticker}.NS`] ?? priceHistory[`${ticker}.BO`] ?? [],
        }
      })
      setHoldingRows(rows)
    } catch { /* silent */ } finally {
      setHistoryLoading(false)
    }
  }, [user?.id, backendUrl])

  // ── Fetch News Trail Data: only headlines about the sectors the user holds ──
  const fetchNewsData = useCallback(async () => {
    if (!user?.id) return
    setNewsLoading(true)
    try {
      const { data: saved, error } = await supabase
        .from('portfolio_holdings')
        .select('name, symbol, isin, units, buy_price, type')
        .eq('user_id', user.id)
      const holdings = error ? null : (saved ?? []).filter((h) => !isLeftover(h))
      const res = await fetch(`${backendUrl}/api/news`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holdings, limit: 30 }),
      })
      if (!res.ok) throw new Error(`Request failed (${res.status})`)
      const data = await res.json()
      setNews(
        (Array.isArray(data.items) ? data.items : []).map((item: NewsItem) => ({
          title: item.title,
          source: item.source,
          url: item.url,
          published_at: item.published_at,
          sectors: item.sectors ?? [],
          holdings: item.holdings ?? [],
        }))
      )
      setNewsScope({
        source: data.portfolio_source,
        windowDays: data.window_days,
        sectors: Array.isArray(data.sectors) ? data.sectors : [],
        unmapped: Array.isArray(data.unmapped) ? data.unmapped : [],
      })
      setNewsFailed(false)
    } catch {
      // The list keeps whatever was last fetched; with nothing fetched it says so.
      setNewsFailed(true)
    } finally {
      setNewsLoading(false)
    }
  }, [user?.id, backendUrl])

  // ── Fetch index levels and market trend ─────────────────────────────────────
  const fetchMarketData = useCallback(async () => {
    try {
      const res = await fetch(`${backendUrl}/api/market/overview`)
      if (!res.ok) throw new Error(`Request failed (${res.status})`)
      setMarket(await res.json())
      setMarketFailed(false)
    } catch {
      setMarketFailed(true)
    }
  }, [backendUrl])

  const nifty = market?.indices.find((i) => i.key === 'nifty')
  const sensex = market?.indices.find((i) => i.key === 'sensex')
  const marketPending = marketFailed ? 'Unavailable' : 'Loading…'
  // A skeleton stands in for any portfolio figure that has not loaded.
  const shown = (text: string, widthClass = 'w-16') => (portfolioStats.available ? text : <Skeleton className={`h-4 inline-block align-middle ${widthClass}`} />)

  useEffect(() => {
    fetchPortfolioData()
    fetchNewsData()
    fetchMarketData()
    fetchHoldingHistory()
  }, [fetchPortfolioData, fetchNewsData, fetchHoldingHistory])

  // One filter per sector held, in the order the backend ranks them, with its headline count.
  const newsSectors = useMemo(
    () =>
      (newsScope?.sectors ?? []).map((entry) => ({
        ...entry,
        count: news.filter((item) => item.sectors.includes(entry.sector)).length,
      })),
    [news, newsScope]
  )
  // A sector that drops out after a refresh falls back to all headlines.
  const activeNewsFilter = newsSectors.some((entry) => entry.sector === newsFilter) ? newsFilter : 'ALL'

  const filteredNews = useMemo(() => {
    const search = newsSearch.toLowerCase()
    return news.filter(
      (item) =>
        (item.title.toLowerCase().includes(search) || (item.source ?? '').toLowerCase().includes(search)) &&
        (activeNewsFilter === 'ALL' || item.sectors.includes(activeNewsFilter))
    )
  }, [news, newsSearch, activeNewsFilter])

  return (
    <div id="dashboard-layout" className="flex flex-col-reverse md:flex-row h-dvh overflow-hidden bg-groww-bg-primary font-inter">
      {/* ── Left Sidebar ──────────────────────────────────────────────────── */}
      <Sidebar activePage="dashboard" />

      {/* ── Main Content Area ─────────────────────────────────────────────── */}
      <main id="dashboard-main" className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden flex flex-col">
        {/* Top Header Bar */}
        <header
          id="dashboard-header"
          className="sticky top-0 z-20 bg-white/95 backdrop-blur-md border-b border-groww-border-light px-4 sm:px-6 py-3 sm:py-3.5 flex items-center justify-between gap-3"
          style={{ minHeight: '60px' }}
        >
          {/* Header Left: Page Title & Date */}
          <div className="flex items-center gap-4">
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold text-groww-text-primary tracking-tight">Dashboard</h1>
                <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 text-groww-green border border-emerald-100">
                  <span className="w-1.5 h-1.5 rounded-full bg-groww-green animate-pulse" />
                  Live Terminal
                </span>
              </div>
              <p className="text-xs text-groww-text-muted mt-0.5">
                {new Date().toLocaleDateString('en-US', {
                  weekday: 'long',
                  year: 'numeric',
                  month: 'short',
                  day: 'numeric',
                })}
              </p>
            </div>
          </div>

          {/* Header Right: Status & Actions */}
          <div className="flex items-center gap-2 sm:gap-3 shrink-0">
            {/* Quick Live Index Ticker */}
            <div className="hidden md:flex items-center gap-3 px-3 py-1.5 rounded-xl bg-groww-bg-primary border border-groww-border-light text-xs font-medium">
              <span className="text-groww-text-secondary font-semibold">NIFTY 50</span>
              {nifty ? (
                <span id="dashboard-nifty" className="text-groww-text-primary font-bold flex items-center gap-1.5">
                  {fmtNum(nifty.value)}
                  <span
                    className={`text-[10px] px-1 rounded ${nifty.change_1d_pct >= 0 ? 'bg-emerald-100/70 text-groww-green' : 'bg-red-50 text-red-500'}`}
                  >
                    {fmtPct(nifty.change_1d_pct)}
                  </span>
                </span>
              ) : (
                <span className="text-groww-text-muted">{marketPending}</span>
              )}
            </div>

            {/* Refresh button */}
            <button
              id="dashboard-refresh-btn"
              onClick={() => {
                fetchPortfolioData()
                fetchNewsData()
                fetchMarketData()
                setRefreshKey((key) => key + 1)
              }}
              title="Refresh live data"
              className="w-9 h-9 rounded-xl border border-groww-border-light flex items-center justify-center text-groww-text-secondary hover:text-groww-green hover:border-groww-green hover:bg-groww-green-light/40 transition-all duration-200"
              aria-label="Refresh data"
            >
              <IconRefresh spinning={portfolioLoading || newsLoading} />
            </button>

            {/* Notifications: live alerts on the user's holdings */}
            <NotificationBell />

            {/* User Profile Avatar */}
            <button
              id="dashboard-settings-btn"
              onClick={() => navigate('/settings')}
              className="w-9 h-9 rounded-full flex items-center justify-center text-white font-bold text-sm transition-transform duration-200 hover:scale-105 shadow-sm"
              style={{ background: 'linear-gradient(135deg, #00B386 0%, #007A5A 100%)' }}
              aria-label="Account Settings"
              title={`${displayName} - Account Settings`}
            >
              {userInitial}
            </button>
          </div>
        </header>

        {/* ── Dashboard Body Grid (Matching Reference Wireframe Layout) ────── */}
        <div className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1600px] w-full mx-auto flex flex-col gap-6">
          {/* Risk first: what could be lost, where exposure sits, and active alerts summary */}
          <RiskOverviewTiles backendUrl={backendUrl} refreshKey={refreshKey} />

          {/* Interactive price and weather chart beside the risk exposure breakdown */}
          <MarketRiskRow backendUrl={backendUrl} refreshKey={refreshKey} />

          {/* Live multi-agent execution graph with the agents' trade and hedge recommendation */}
          <AgentGraphCard backendUrl={backendUrl} />

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

            {/* ════════════════════════════════════════════════════════════════
                LEFT / CENTER SECTION (Col 1 to 7)
                Matches wireframe: Top = "alerts & welcome", Bottom = "news trail"
               ════════════════════════════════════════════════════════════════ */}
            <div className="lg:col-span-7 flex flex-col gap-6">

              {/* ── 0. WHAT NEEDS ATTENTION (Alerts) ───────────────────────── */}
              <RiskAlertList />

              {/* ── 1. WELCOME MESSAGE CARD ───────────────────────────────── */}
              <section
                id="welcome-card"
                className="relative overflow-hidden rounded-2xl sm:rounded-3xl p-6 sm:p-7 border border-emerald-100/80 transition-all duration-300"
                style={{
                  background: 'linear-gradient(135deg, #FFFFFF 0%, #F5FCF9 60%, #EBF8F4 100%)',
                  boxShadow: '0 4px 20px -2px rgba(0, 179, 134, 0.08), 0 2px 6px -1px rgba(0, 0, 0, 0.02)',
                }}
              >
                {/* Decorative background shapes */}
                <div
                  className="absolute -right-8 -top-8 w-40 h-40 rounded-full pointer-events-none opacity-40 blur-2xl"
                  style={{ background: 'radial-gradient(circle, #00B386 0%, transparent 70%)' }}
                />

                <div className="relative z-10">
                  {/* Top Badge & Time */}
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-100/70 text-groww-green-dark border border-emerald-200/60">
                      <IconSparkle />
                      Market today
                    </span>
                    <span className="text-xs text-groww-text-muted hidden sm:inline-flex items-center gap-1">
                      <IconClock />
                      {market?.as_of
                        ? `Index close of ${new Date(market.as_of).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}`
                        : `Market data ${marketPending.toLowerCase()}`}
                    </span>
                  </div>

                  {/* Main Greeting */}
                  <h2 className="text-xl sm:text-2xl font-extrabold text-groww-text-primary tracking-tight">
                    {greeting}, <span className="text-groww-green capitalize">{displayName}</span>
                  </h2>

                  {/* Market Quick Metrics Banner */}
                  <div className="mt-5 grid grid-cols-2 sm:grid-cols-3 gap-3 pt-4 border-t border-emerald-100/70">
                    <div className="p-3 rounded-xl bg-white/80 border border-emerald-50 shadow-sm">
                      <p className="text-[11px] font-semibold text-groww-text-muted uppercase tracking-wider">Nifty 50</p>
                      <p className="text-sm font-bold text-groww-text-primary mt-0.5">{nifty ? fmtNum(nifty.value) : '—'}</p>
                      {nifty ? (
                        <span className={`text-xs font-semibold mt-0.5 inline-flex items-center gap-1 ${nifty.change_1d_pct >= 0 ? 'text-groww-green' : 'text-red-500'}`}>
                          {nifty.change_1d_pct >= 0 ? <IconTriangleUp className="w-2 h-2 shrink-0" /> : <IconTriangleDown className="w-2 h-2 shrink-0" />}
                          <span>{fmtPct(nifty.change_1d_pct)} on the day</span>
                        </span>
                      ) : (
                        <span className="text-xs text-groww-text-muted mt-0.5 block">{marketPending}</span>
                      )}
                    </div>

                    <div className="p-3 rounded-xl bg-white/80 border border-emerald-50 shadow-sm">
                      <p className="text-[11px] font-semibold text-groww-text-muted uppercase tracking-wider">BSE Sensex</p>
                      <p className="text-sm font-bold text-groww-text-primary mt-0.5">{sensex ? fmtNum(sensex.value) : '—'}</p>
                      {sensex ? (
                        <span className={`text-xs font-semibold mt-0.5 inline-flex items-center gap-1 ${sensex.change_1d_pct >= 0 ? 'text-groww-green' : 'text-red-500'}`}>
                          {sensex.change_1d_pct >= 0 ? <IconTriangleUp className="w-2 h-2 shrink-0" /> : <IconTriangleDown className="w-2 h-2 shrink-0" />}
                          <span>{fmtPct(sensex.change_1d_pct)} on the day</span>
                        </span>
                      ) : (
                        <span className="text-xs text-groww-text-muted mt-0.5 block">{marketPending}</span>
                      )}
                    </div>

                    <div className="col-span-2 sm:col-span-1 p-3 rounded-xl bg-white/80 border border-emerald-50 shadow-sm">
                      <p className="text-[11px] font-semibold text-groww-text-muted uppercase tracking-wider">Market Trend</p>
                      <p
                        id="dashboard-market-trend"
                        className={`text-sm font-bold mt-0.5 ${
                          market?.trend?.label === 'Uptrend'
                            ? 'text-groww-green-dark'
                            : market?.trend?.label === 'Downtrend'
                              ? 'text-red-600'
                              : 'text-groww-text-primary'
                        }`}
                      >
                        {market?.trend?.label ?? '—'}
                      </p>
                      <span className="text-xs text-groww-text-secondary mt-0.5 block truncate">
                        {market?.trend?.detail ?? marketPending}
                      </span>
                    </div>
                  </div>

                  {/* Action buttons */}
                  <div className="mt-5 flex flex-wrap items-center gap-3">
                    <button
                      id="welcome-view-portfolio-btn"
                      onClick={() => navigate('/portfolio')}
                      className="px-4 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-white flex items-center gap-2 transition-all duration-200 hover:-translate-y-0.5 active:translate-y-0 shadow-sm"
                      style={{ background: 'linear-gradient(135deg, #00B386 0%, #007A5A 100%)' }}
                    >
                      <span>Explore Portfolio Details</span>
                      <IconArrowRight />
                    </button>

                    <button
                      id="welcome-upload-statement-btn"
                      onClick={() => navigate('/portfolio')}
                      className="px-4 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-groww-text-primary bg-white border border-groww-border hover:border-groww-green hover:text-groww-green transition-all duration-200 flex items-center gap-2"
                    >
                      <IconUpload />
                      <span>Upload Statement PDF</span>
                    </button>
                  </div>
                </div>
              </section>

              {/* ── 2. NEWS TRAIL CARD (headlines about the sectors the user holds) ── */}
              <section
                id="news-trail-card"
                className="bg-white rounded-2xl sm:rounded-3xl border border-groww-border-light shadow-card p-6 flex flex-col transition-all duration-200"
              >
                {/* News Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-groww-border-light">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-lg font-bold text-groww-text-primary tracking-tight">Market News Trail</h3>
                      <span id="news-scope" className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-gray-100 text-gray-700">
                        {newsScope?.source === 'sample' ? 'Sample portfolio' : 'Your sectors'}
                      </span>
                    </div>
                    <p className="text-xs text-groww-text-muted mt-0.5">
                      {newsScope?.source === 'sample'
                        ? 'No stock holdings saved yet, so this shows the sectors of the sample portfolio'
                        : 'Only headlines about the sectors your stocks are in'}
                    </p>
                  </div>

                  {/* News Trail Search & Category Filter */}
                  <div className="flex items-center gap-2">
                    <div className="relative">
                      <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-groww-text-muted">
                        <IconSearch />
                      </span>
                      <input
                        id="news-search-input"
                        type="text"
                        value={newsSearch}
                        onChange={(e) => setNewsSearch(e.target.value)}
                        placeholder="Search headlines..."
                        className="pl-8 pr-3 py-1.5 text-xs rounded-xl border border-groww-border bg-groww-bg-primary text-groww-text-primary placeholder-groww-text-muted outline-none focus:border-groww-green w-36 sm:w-44 transition-all"
                      />
                    </div>

                    <button
                      id="news-refresh-btn"
                      onClick={fetchNewsData}
                      title="Reload news trail"
                      className="p-2 rounded-xl border border-groww-border hover:border-groww-green hover:text-groww-green text-groww-text-secondary transition-colors"
                      aria-label="Reload news"
                    >
                      <IconRefresh spinning={newsLoading} />
                    </button>
                  </div>
                </div>

                {/* One pill per sector held; hovering shows which holdings put it there */}
                <div id="news-sector-filters" className="flex items-center gap-2 pt-3 pb-2 overflow-x-auto no-scrollbar">
                  {[{ sector: 'ALL', holdings: [] as string[], count: news.length }, ...newsSectors].map((entry) => (
                    <button
                      key={entry.sector}
                      onClick={() => setNewsFilter(entry.sector)}
                      aria-pressed={activeNewsFilter === entry.sector}
                      title={entry.holdings.length ? `Because you hold ${entry.holdings.join(', ')}` : undefined}
                      className={`px-3 py-1 rounded-lg text-xs font-semibold whitespace-nowrap transition-all duration-150 ${activeNewsFilter === entry.sector
                          ? 'bg-groww-green text-white shadow-sm'
                          : 'bg-gray-100 text-groww-text-secondary hover:bg-gray-200'
                        }`}
                    >
                      {entry.sector === 'ALL' ? 'All' : entry.sector}{' '}
                      <span className="font-normal opacity-80">{entry.count}</span>
                    </button>
                  ))}
                </div>
                {/* Why each sector is here: the holdings behind it */}
                {newsScope && newsScope.sectors.length > 0 && (
                  <p id="news-sector-basis" className="pb-2 text-[11px] text-groww-text-muted leading-relaxed">
                    {(activeNewsFilter === 'ALL'
                      ? newsScope.sectors
                      : newsScope.sectors.filter((entry) => entry.sector === activeNewsFilter)
                    )
                      .map((entry) => `${entry.sector}: ${entry.holdings.join(', ')}`)
                      .join(' · ')}
                    {newsScope.unmapped.length > 0 &&
                      ` · No sector found for ${newsScope.unmapped.join(', ')}, so no news is shown for ${
                        newsScope.unmapped.length === 1 ? 'it' : 'them'
                      }.`}
                  </p>
                )}

                {/* News Trail Timeline / List */}
                <div className="relative mt-2 divide-y divide-gray-100 max-h-[560px] overflow-y-auto pr-1">
                  {newsLoading && news.length === 0 ? (
                    <div className="flex flex-col">
                      {Array.from({ length: 6 }).map((_, i) => (
                        <div key={i} className="py-3.5 px-2.5 flex items-start gap-3.5">
                          <div className="flex flex-col items-center shrink-0 mt-1">
                            <Skeleton className="w-2.5 h-2.5 rounded-full bg-emerald-100" />
                            {i !== 5 && <Skeleton className="w-0.5 h-10 my-1 bg-emerald-50" />}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-2">
                              <Skeleton className="w-12 h-4" />
                              <Skeleton className="w-16 h-4" />
                            </div>
                            <Skeleton className="w-3/4 h-4 mb-1" />
                            <Skeleton className="w-1/2 h-4" />
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : filteredNews.length === 0 ? (
                    <div className="py-12 text-center">
                      <p className="text-sm font-semibold text-groww-text-secondary">
                        {news.length > 0
                          ? 'No articles found'
                          : newsFailed
                            ? 'News is unavailable right now'
                            : 'No news on your sectors'}
                      </p>
                      <p className="text-xs text-groww-text-muted mt-1">
                        {news.length > 0
                          ? 'Try a different keyword or sector.'
                          : newsFailed
                            ? 'The news feed could not be reached. Use refresh to try again.'
                            : newsScope && newsScope.sectors.length === 0
                              ? 'No sector could be found for your holdings, so there is nothing to search for.'
                              : `Nothing published in the last ${newsScope?.windowDays ?? 7} days is about the sectors you hold.`}
                      </p>
                    </div>
                  ) : (
                    filteredNews.map((item, index) => (
                      <article
                        key={`${item.title}-${index}`}
                        className="group py-3.5 px-2.5 rounded-xl hover:bg-gray-50/80 transition-all duration-150 flex items-start gap-3.5"
                      >
                        {/* Trail indicator node */}
                        <div className="flex flex-col items-center shrink-0 mt-1">
                          <span className="w-2.5 h-2.5 rounded-full bg-groww-green group-hover:scale-125 transition-transform" />
                          <span className="w-0.5 h-8 bg-emerald-100 my-1 group-last:hidden" />
                        </div>

                        {/* Article Content */}
                        <div className="flex-1 min-w-0">
                          {/* Metadata row */}
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-emerald-50 text-groww-green-dark border border-emerald-100">
                              {item.source || 'News'}
                            </span>
                            <span className="text-[11px] font-semibold text-groww-text-secondary">
                              {item.sectors.join(', ')}
                            </span>
                            {item.holdings.length > 0 && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-100">
                                Names {item.holdings.join(', ')}
                              </span>
                            )}
                            <span className="text-[11px] text-groww-text-muted flex items-center gap-1 ml-auto">
                              <IconClock />
                              {timeAgo(item.published_at)}
                            </span>
                          </div>

                          {/* Article Title */}
                          <h4 className="text-sm font-bold text-groww-text-primary group-hover:text-groww-green transition-colors leading-snug">
                            {item.url ? (
                              <a
                                href={item.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-baseline gap-1 hover:underline"
                              >
                                <span>{item.title}</span>
                                <span className="inline-block shrink-0 text-groww-text-muted group-hover:text-groww-green">
                                  <IconExternalLink />
                                </span>
                              </a>
                            ) : (
                              item.title
                            )}
                          </h4>
                        </div>
                      </article>
                    ))
                  )}
                </div>

                {/* Footer Note */}
                <div className="mt-4 pt-3 border-t border-groww-border-light flex items-center justify-between text-xs text-groww-text-muted">
                  <span>Source: Google News</span>
                  <span>
                    {news.length} headline{news.length === 1 ? '' : 's'} from the last {newsScope?.windowDays ?? 7} days
                    {newsFailed && news.length > 0 ? ' · last refresh failed' : ''}
                  </span>
                </div>
              </section>

            </div>

            {/* ════════════════════════════════════════════════════════════════
                RIGHT SECTION (Col 8 to 12)
                Matches wireframe: "data streams" and "personal portfolio analysis"
               ════════════════════════════════════════════════════════════════ */}
            <div className="lg:col-span-5 flex flex-col gap-6">

              {/* ── DATA STREAMS STATUS ──────────────────────────────────── */}
              <RiskStreamsCard backendUrl={backendUrl} />

              <section
                id="personal-portfolio-card"
                className="bg-white rounded-2xl sm:rounded-3xl border border-groww-border-light shadow-card p-6 flex flex-col transition-all duration-200"
              >
                {/* Portfolio Card Header */}
                <div className="flex items-center justify-between pb-4 border-b border-groww-border-light">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-lg font-bold text-groww-text-primary tracking-tight">
                        Personal Portfolio Analysis
                      </h3>
                    </div>
                    <p className="text-xs text-groww-text-muted mt-0.5">
                      {portfolioStats.isCustom
                        ? 'Live data linked to your account'
                        : 'Benchmark portfolio overview'}
                    </p>
                  </div>

                  <button
                    id="portfolio-manage-link-btn"
                    onClick={() => navigate('/portfolio')}
                    className="text-xs font-semibold text-groww-green hover:text-groww-green-dark flex items-center gap-1 py-1 px-2.5 rounded-lg hover:bg-groww-green-light transition-colors"
                  >
                    <span>Manage</span>
                    <IconArrowRight />
                  </button>
                </div>

                {/* Account identification pill */}
                <div className="my-4 px-3.5 py-2.5 rounded-xl bg-groww-bg-primary border border-groww-border-light flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2 truncate">
                    <span className="w-2 h-2 rounded-full bg-groww-green shrink-0" />
                    <span className="text-groww-text-secondary font-medium truncate">
                      Account: <strong className="text-groww-text-primary">{userEmail}</strong>
                    </span>
                  </div>
                  <span className="px-2 py-0.5 rounded font-bold text-[10px] bg-emerald-100 text-groww-green-dark shrink-0">
                    {portfolioStats.isCustom ? 'Verified Active' : 'Sample Model'}
                  </span>
                </div>

                {/* ── THE 6 METRIC BOXES (2-column x 3-row grid) ─────────── */}
                <div
                  id="portfolio-metrics-grid"
                  className="grid grid-cols-2 gap-3.5 my-2"
                >
                  {/* BOX 1: Total Amount Invested */}
                  <div
                    id="metric-total-invested"
                    className="p-4 rounded-2xl bg-groww-bg-primary border border-groww-border-light hover:border-emerald-200 transition-all duration-200 flex flex-col justify-between group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-groww-text-secondary">
                        Total Invested
                      </span>
                      <div className="w-8 h-8 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0">
                        <IconWallet />
                      </div>
                    </div>
                    <div>
                      <p className="text-base sm:text-lg font-extrabold text-groww-text-primary tracking-tight">
                        {shown(fmtCur(portfolioStats.totalInvested))}
                      </p>
                      <p className="text-[11px] text-groww-text-muted mt-1">
                        Principal capital
                      </p>
                    </div>
                  </div>

                  {/* BOX 2: Total Stocks */}
                  <div
                    id="metric-total-stocks"
                    className="p-4 rounded-2xl bg-groww-bg-primary border border-groww-border-light hover:border-emerald-200 transition-all duration-200 flex flex-col justify-between group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-groww-text-secondary">
                        Total Stocks
                      </span>
                      <div className="w-8 h-8 rounded-xl bg-purple-50 text-purple-600 flex items-center justify-center shrink-0">
                        <IconBarChart />
                      </div>
                    </div>
                    <div>
                      <p className="text-base sm:text-lg font-extrabold text-groww-text-primary tracking-tight">
                        {shown(`${portfolioStats.totalStocks} ${portfolioStats.totalStocks === 1 ? 'Asset' : 'Assets'}`)}
                      </p>
                      <p className="text-[11px] text-groww-text-muted mt-1">
                        Active holdings
                      </p>
                    </div>
                  </div>

                  {/* BOX 3: Total Profit Percent */}
                  <div
                    id="metric-profit-percent"
                    className="p-4 rounded-2xl bg-groww-bg-primary border border-groww-border-light hover:border-emerald-200 transition-all duration-200 flex flex-col justify-between group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-groww-text-secondary">
                        Profit %
                      </span>
                      <div className="w-8 h-8 rounded-xl bg-emerald-50 text-groww-green flex items-center justify-center shrink-0">
                        <IconPercent />
                      </div>
                    </div>
                    <div>
                      <p className={`text-base sm:text-lg font-extrabold tracking-tight ${portfolioStats.totalProfitPercent >= 0 ? 'text-groww-green' : 'text-red-500'
                        }`}>
                        {shown(fmtPct(portfolioStats.totalProfitPercent))}
                      </p>
                      <p className="text-[11px] text-groww-text-muted mt-1">
                        Total return rate (ROI)
                      </p>
                    </div>
                  </div>

                  {/* BOX 4: Total Profit Amount */}
                  <div
                    id="metric-profit-amount"
                    className="p-4 rounded-2xl bg-groww-bg-primary border border-groww-border-light hover:border-emerald-200 transition-all duration-200 flex flex-col justify-between group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-groww-text-secondary">
                        Total Profit
                      </span>
                      <div className="w-8 h-8 rounded-xl bg-emerald-50 text-groww-green flex items-center justify-center shrink-0">
                        <IconTrendingUp />
                      </div>
                    </div>
                    <div>
                      <p className={`text-base sm:text-lg font-extrabold tracking-tight ${portfolioStats.totalProfitAmount >= 0 ? 'text-groww-green' : 'text-red-500'
                        }`}>
                        {shown(`${portfolioStats.totalProfitAmount >= 0 ? '+' : ''}${fmtCur(portfolioStats.totalProfitAmount)}`)}
                      </p>
                      <p className="text-[11px] text-groww-text-muted mt-1">
                        Unrealized profit
                      </p>
                    </div>
                  </div>

                  {/* BOX 5: Current Portfolio Value */}
                  <div
                    id="metric-current-value"
                    className="p-4 rounded-2xl bg-groww-bg-primary border border-groww-border-light hover:border-emerald-200 transition-all duration-200 flex flex-col justify-between group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-groww-text-secondary">
                        Current Value
                      </span>
                      <div className="w-8 h-8 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center shrink-0">
                        <IconCurrencyRupee />
                      </div>
                    </div>
                    <div>
                      <p className="text-base sm:text-lg font-extrabold text-groww-text-primary tracking-tight">
                        {shown(fmtCur(portfolioStats.totalCurrent))}
                      </p>
                      <p className="text-[11px] text-groww-text-muted mt-1">
                        Live market valuation
                      </p>
                    </div>
                  </div>

                  {/* BOX 6: Portfolio Health & Status */}
                  <div
                    id="metric-portfolio-status"
                    className="p-4 rounded-2xl bg-groww-bg-primary border border-groww-border-light hover:border-emerald-200 transition-all duration-200 flex flex-col justify-between group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-groww-text-secondary">
                        Portfolio Health
                      </span>
                      <div className="w-8 h-8 rounded-xl bg-teal-50 text-teal-600 flex items-center justify-center shrink-0">
                        <IconShieldCheck />
                      </div>
                    </div>
                    <div>
                      <p
                        id="dashboard-portfolio-health"
                        className={`text-base sm:text-lg font-extrabold tracking-tight ${
                          portfolioStats.health?.label === 'Concentrated' ? 'text-amber-600' : 'text-teal-700'
                        }`}
                      >
                        {portfolioStats.health?.label ?? '—'}
                      </p>
                      <p className="text-[11px] text-groww-text-muted mt-1">
                        {portfolioStats.health?.detail ?? 'Needs priced holdings'}
                      </p>
                    </div>
                  </div>
                </div>

                {portfolioStats.unpricedCount > 0 && (
                  <p id="dashboard-unpriced-note" className="mt-3 px-3 py-2 rounded-xl bg-gray-50 border border-gray-100 text-[11px] text-groww-text-secondary">
                    {portfolioStats.unpricedCount} holding{portfolioStats.unpricedCount !== 1 ? 's have' : ' has'} no market
                    price and {portfolioStats.unpricedCount !== 1 ? 'are' : 'is'} counted at cost in the current value.
                  </p>
                )}

                {/* ── Holdings Breakdown with Sparklines ──────────────────── */}
                {holdingRows.length > 0 && (
                  <div id="dashboard-holdings-breakdown" className="mt-5">
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="text-sm font-bold text-groww-text-primary tracking-tight">Holdings Breakdown</h4>
                      {historyLoading && (
                        <span className="text-[10px] text-groww-text-muted flex items-center gap-1">
                          <span className="w-3 h-3 border border-groww-green/30 border-t-groww-green rounded-full animate-spin inline-block" />
                          Updating charts…
                        </span>
                      )}
                    </div>

                    <div className="overflow-x-auto" style={{ marginLeft: '-4px', marginRight: '-4px' }}>
                      <table className="w-full text-xs" style={{ borderCollapse: 'separate', borderSpacing: '0 6px', minWidth: 560 }}>
                        <thead>
                          <tr>
                            <th className="pl-2 pr-3 pb-1 text-left font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">Stock</th>
                            <th className="px-2 pb-1 text-left font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">Type</th>
                            <th className="px-2 pb-1 text-right font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">Units</th>
                            <th className="px-2 pb-1 text-right font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">Buy Price</th>
                            <th className="px-2 pb-1 text-right font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">Live Rate</th>
                            <th className="px-2 pb-1 text-center font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">30d Trend</th>
                            <th className="px-2 pb-1 text-right font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">Invested</th>
                            <th className="pr-2 pb-1 text-right font-semibold text-groww-text-muted text-[11px] uppercase tracking-wide">P&amp;L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {holdingRows.map((row, idx) => {
                            const invested = row.units * row.buyPrice
                            const livePrice = row.livePrice
                            const currentVal = livePrice != null ? row.units * livePrice : null
                            const pnlAmt = currentVal != null ? currentVal - invested : null
                            const pnlPct = livePrice != null && row.buyPrice > 0
                              ? ((livePrice - row.buyPrice) / row.buyPrice) * 100
                              : null
                            const isUp = pnlPct == null ? true : pnlPct >= 0
                            const trendIsUp = row.history.length >= 2 ? row.history[row.history.length - 1] >= row.history[0] : true

                            const typeBadgeStyle: Record<string, { bg: string; color: string; border: string }> = {
                              ETF:       { bg: '#EDE9FE', color: '#6D28D9', border: '#DDD6FE' },
                              MF:        { bg: '#DBEAFE', color: '#1D4ED8', border: '#BFDBFE' },
                              Bond:      { bg: '#FEF3C7', color: '#B45309', border: '#FDE68A' },
                              Commodity: { bg: '#FFF7ED', color: '#C2410C', border: '#FED7AA' },
                              Stock:     { bg: '#ECFDF5', color: '#047857', border: '#A7F3D0' },
                            }
                            const ts = typeBadgeStyle[row.type] ?? typeBadgeStyle.Stock
                            const initials = stockInitials(row.name)
                            const avatarGrad = AVATAR_GRADIENTS[idx % AVATAR_GRADIENTS.length]

                            return (
                              <tr
                                key={row.id}
                                className="group transition-colors"
                                style={{ background: 'white' }}
                                onMouseEnter={e => (e.currentTarget.style.background = '#F0FDF8')}
                                onMouseLeave={e => (e.currentTarget.style.background = 'white')}
                              >
                                {/* Avatar + Name + Symbol */}
                                <td className="pl-2 pr-3 py-2.5" style={{ borderRadius: '12px 0 0 12px' }}>
                                  <div className="flex items-center gap-2.5 min-w-0">
                                    <div
                                      className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 text-white font-bold text-[11px] tracking-wide shadow-sm"
                                      style={{ background: avatarGrad }}
                                    >
                                      {initials}
                                    </div>
                                    <div className="min-w-0">
                                      <p className="font-bold text-groww-text-primary text-[12px] leading-tight truncate max-w-[100px]" title={row.name}>
                                        {row.name}
                                      </p>
                                      <p className="text-[10px] text-groww-text-muted font-medium tracking-wide">{row.symbol || '—'}</p>
                                    </div>
                                  </div>
                                </td>

                                {/* Type badge */}
                                <td className="px-2 py-2.5">
                                  <span
                                    className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider"
                                    style={{ background: ts.bg, color: ts.color, border: `1px solid ${ts.border}` }}
                                  >
                                    {row.type}
                                  </span>
                                </td>

                                {/* Units */}
                                <td className="px-2 py-2.5 text-right">
                                  <span className="font-semibold text-groww-text-primary">{row.units.toLocaleString('en-IN')}</span>
                                </td>

                                {/* Buy Price */}
                                <td className="px-2 py-2.5 text-right">
                                  <span className="font-semibold text-groww-text-primary">{fmtCur(row.buyPrice)}</span>
                                </td>

                                {/* Live Rate */}
                                <td className="px-2 py-2.5 text-right">
                                  {livePrice != null ? (
                                    <>
                                      <span className="font-bold text-groww-text-primary">{fmtCur(livePrice)}</span>
                                    </>
                                  ) : (
                                    <>
                                      <span className="font-bold text-groww-text-primary">{fmtCur(row.buyPrice)}</span>
                                      <span className="block text-[9px] text-groww-text-muted font-medium">Saved</span>
                                    </>
                                  )}
                                </td>

                                {/* Sparkline — between Live Rate and Invested */}
                                <td className="px-2 py-1.5 text-center">
                                  <MiniSparkline values={row.history} positive={trendIsUp} />
                                </td>

                                {/* Invested */}
                                <td className="px-2 py-2.5 text-right">
                                  <span className="font-semibold text-groww-text-primary">{fmtCur(invested)}</span>
                                </td>

                                {/* P&L */}
                                <td className="pr-2 py-2.5 text-right" style={{ borderRadius: '0 12px 12px 0' }}>
                                  {pnlAmt != null && pnlPct != null ? (
                                    <>
                                      <span className={`font-bold text-[12px] ${isUp ? 'text-groww-green' : 'text-red-500'}`}>
                                        {pnlAmt >= 0 ? '+' : ''}{fmtCur(pnlAmt)}
                                      </span>
                                      <span className={`flex items-center justify-end gap-0.5 text-[10px] font-semibold mt-0.5 ${isUp ? 'text-groww-green' : 'text-red-500'}`}>
                                        <svg width="8" height="8" viewBox="0 0 8 8" fill="currentColor">
                                          {isUp
                                            ? <polygon points="4,1 7,7 1,7" />
                                            : <polygon points="4,7 7,1 1,1" />
                                          }
                                        </svg>
                                        {Math.abs(pnlPct).toFixed(2)}%
                                      </span>
                                    </>
                                  ) : (
                                    <span className="text-groww-text-muted">—</span>
                                  )}
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* ── Asset Allocation / Performance Bar ──────────────────── */}
                <div className="mt-4 p-4 rounded-2xl bg-emerald-50/50 border border-emerald-100">
                  <div className="flex items-center justify-between text-xs font-semibold text-groww-text-secondary mb-2">
                    <span>Capital Distribution</span>
                    <span className="text-groww-green font-bold">
                      {shown(`${((portfolioStats.totalProfitAmount / (portfolioStats.totalCurrent || 1)) * 100).toFixed(1)}% Gains Share`)}
                    </span>
                  </div>

                  {/* Progress bar */}
                  <div className="w-full h-2.5 rounded-full bg-gray-200 overflow-hidden flex">
                    <div
                      className="h-full bg-groww-green rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.min(
                          100,
                          Math.max(
                            5,
                            (portfolioStats.totalInvested / (portfolioStats.totalCurrent || 1)) * 100
                          )
                        )}%`,
                      }}
                    />
                  </div>

                  <div className="flex items-center justify-between text-[11px] text-groww-text-muted mt-2">
                    <span className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-groww-green inline-block" />
                      Invested ({shown(fmtCur(portfolioStats.totalInvested))})
                    </span>
                    <span className="flex items-center gap-1.5 font-medium text-groww-text-primary">
                      Gain ({shown(fmtCur(portfolioStats.totalProfitAmount))})
                    </span>
                  </div>
                </div>

                {/* ── Direct Action CTA ───────────────────────────────────── */}
                <div className="mt-5 flex flex-col gap-2.5">
                  <button
                    id="portfolio-full-details-btn"
                    onClick={() => navigate('/portfolio')}
                    className="w-full py-3 px-4 rounded-xl text-xs sm:text-sm font-semibold text-white transition-all duration-200 flex items-center justify-center gap-2 hover:-translate-y-0.5 active:translate-y-0 shadow-sm"
                    style={{ background: 'linear-gradient(135deg, #00B386 0%, #007A5A 100%)' }}
                  >
                    <span>Open Full Portfolio & Holdings</span>
                    <IconArrowRight />
                  </button>

                  {!portfolioStats.isCustom && (
                    <button
                      id="portfolio-upload-cas-btn"
                      onClick={() => navigate('/portfolio')}
                      className="w-full py-2.5 px-4 rounded-xl text-xs font-semibold text-groww-green bg-groww-green-light hover:bg-emerald-100 transition-colors flex items-center justify-center gap-1.5"
                    >
                      <IconUpload />
                      <span>Upload your CAS Statement to see personal stocks</span>
                    </button>
                  )}
                </div>
              </section>

            </div>

          </div>
        </div>
      </main>
    </div>
  )
}

export default DashboardPage
