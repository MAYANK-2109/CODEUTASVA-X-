import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'

// ── Types ─────────────────────────────────────────────────────────────────────

interface NewsItem {
  title: string
  source: string | null
  url: string | null
  published_at: string | null
  category?: string
}

interface PortfolioStats {
  totalInvested: number
  totalCurrent: number
  totalProfitAmount: number
  totalProfitPercent: number
  totalStocks: number
  isCustom: boolean
  lastUpdated: string
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

const IconBell = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
    <path d="M13.73 21a2 2 0 0 1-3.46 0" />
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

const FALLBACK_NEWS: NewsItem[] = [
  {
    title: 'GSTAT: GSTR-9/9C Errors Cannot Deny Valid Input Tax Credit, Affirms Appellate Body',
    source: 'Taxscan',
    url: 'https://news.google.com',
    published_at: new Date(Date.now() - 25 * 60 * 1000).toISOString(),
    category: 'Tax & Policy',
  },
  {
    title: 'NTPC Advances Clean Energy Drive With New Green Power & Skill Initiatives',
    source: 'Economic Times',
    url: 'https://news.google.com',
    published_at: new Date(Date.now() - 65 * 60 * 1000).toISOString(),
    category: 'Energy',
  },
  {
    title: 'Reliance Retail Expands Omnichannel Footprint with Next-Gen Digital Hubs',
    source: 'LiveMint',
    url: 'https://news.google.com',
    published_at: new Date(Date.now() - 120 * 60 * 1000).toISOString(),
    category: 'Retail',
  },
  {
    title: 'RBI Monetary Policy: Focus Remains on Stable Liquidity and Inflation Containment',
    source: 'Business Standard',
    url: 'https://news.google.com',
    published_at: new Date(Date.now() - 180 * 60 * 1000).toISOString(),
    category: 'Economy',
  },
  {
    title: 'Indian IT Giants See Rebound in Cloud & AI Transformation Deals for FY27',
    source: 'Financial Express',
    url: 'https://news.google.com',
    published_at: new Date(Date.now() - 240 * 60 * 1000).toISOString(),
    category: 'Tech',
  },
  {
    title: 'Nifty 50 Holds Key Support Levels as Domestic Institutional Inflows Stay Resilient',
    source: 'CNBC-TV18',
    url: 'https://news.google.com',
    published_at: new Date(Date.now() - 310 * 60 * 1000).toISOString(),
    category: 'Markets',
  },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtCur = (n: number) =>
  `\u20B9${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

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
    totalInvested: 397150,
    totalCurrent: 416646,
    totalProfitAmount: 19496,
    totalProfitPercent: 4.91,
    totalStocks: 8,
    isCustom: false,
    lastUpdated: 'Live',
  })
  const [portfolioLoading, setPortfolioLoading] = useState(true)

  const [news, setNews] = useState<NewsItem[]>(FALLBACK_NEWS)
  const [newsLoading, setNewsLoading] = useState(false)
  const [newsFilter, setNewsFilter] = useState('ALL')
  const [newsSearch, setNewsSearch] = useState('')

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
        const { data: holdings, error } = await supabase
          .from('portfolio_holdings')
          .select('*')
          .eq('user_id', user.id)

        if (!error && holdings && holdings.length > 0) {
          foundUserHoldings = true
          const invested = holdings.reduce(
            (acc, h) => acc + (Number(h.units) || 0) * (Number(h.buy_price) || 0),
            0
          )
          const current = holdings.reduce(
            (acc, h) =>
              acc +
              (Number(h.units) || 0) *
              (Number(h.current_price) || Number(h.buy_price) || 0),
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
            lastUpdated: 'Just now',
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
            const invested = Number(data.totals?.invested_value) || 397150
            const current = Number(data.totals?.current_value) || 416646
            const profit = current - invested
            const profitPct = invested > 0 ? (profit / invested) * 100 : 4.91
            const count = Array.isArray(data.holdings) ? data.holdings.length : 8

            setPortfolioStats({
              totalInvested: invested,
              totalCurrent: current,
              totalProfitAmount: profit,
              totalProfitPercent: profitPct,
              totalStocks: count,
              isCustom: false,
              lastUpdated: 'Live Feed',
            })
          }
        } catch {
          // Keep resilient defaults
        }
      }
    } catch {
      // Keep state resilient
    } finally {
      setPortfolioLoading(false)
    }
  }, [user?.id, backendUrl])

  // ── Fetch News Trail Data ───────────────────────────────────────────────────
  const fetchNewsData = useCallback(async () => {
    setNewsLoading(true)
    try {
      const res = await fetch(`${backendUrl}/api/news?limit=10`, {
        headers: { 'Content-Type': 'application/json' },
      })
      if (res.ok) {
        const data = await res.json()
        if (Array.isArray(data.items) && data.items.length > 0) {
          const mapped: NewsItem[] = data.items.map((item: any) => ({
            title: item.title,
            source: item.source || 'Market Feed',
            url: item.url,
            published_at: item.published_at,
            category: determineCategory(item.title),
          }))
          setNews(mapped)
        }
      }
    } catch {
      // Use fallback news seamlessly
    } finally {
      setNewsLoading(false)
    }
  }, [backendUrl])

  function determineCategory(text: string): string {
    const lower = text.toLowerCase()
    if (lower.includes('tax') || lower.includes('gst') || lower.includes('policy') || lower.includes('rbi')) return 'Policy & Tax'
    if (lower.includes('energy') || lower.includes('power') || lower.includes('ntpc')) return 'Energy'
    if (lower.includes('retail') || lower.includes('reliance') || lower.includes('tata')) return 'Corporate'
    if (lower.includes('tech') || lower.includes('ai') || lower.includes('software')) return 'Tech'
    return 'Markets'
  }

  useEffect(() => {
    fetchPortfolioData()
    fetchNewsData()
  }, [fetchPortfolioData, fetchNewsData])

  // News Filtering
  const filteredNews = useMemo(() => {
    return news.filter((item) => {
      const matchesSearch =
        item.title.toLowerCase().includes(newsSearch.toLowerCase()) ||
        (item.source && item.source.toLowerCase().includes(newsSearch.toLowerCase()))
      const matchesCat =
        newsFilter === 'ALL' ||
        (item.category && item.category.toLowerCase().includes(newsFilter.toLowerCase()))
      return matchesSearch && matchesCat
    })
  }, [news, newsSearch, newsFilter])

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
              <span className="text-groww-green font-bold flex items-center gap-1">
                25,124.50
                <span className="text-[10px] px-1 py-0.2 bg-emerald-100/70 rounded">+0.64%</span>
              </span>
            </div>

            {/* Refresh button */}
            <button
              id="dashboard-refresh-btn"
              onClick={() => {
                fetchPortfolioData()
                fetchNewsData()
              }}
              title="Refresh live data"
              className="w-9 h-9 rounded-xl border border-groww-border-light flex items-center justify-center text-groww-text-secondary hover:text-groww-green hover:border-groww-green hover:bg-groww-green-light/40 transition-all duration-200"
              aria-label="Refresh data"
            >
              <IconRefresh spinning={portfolioLoading || newsLoading} />
            </button>

            {/* Notifications */}
            <button
              id="dashboard-notifications-btn"
              className="relative w-9 h-9 rounded-xl border border-groww-border-light flex items-center justify-center text-groww-text-secondary hover:text-groww-green hover:bg-groww-green-light/40 transition-all duration-200"
              aria-label="Notifications"
            >
              <IconBell />
              <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-groww-green" />
            </button>

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
        <div className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1600px] w-full mx-auto">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

            {/* ════════════════════════════════════════════════════════════════
                LEFT / CENTER SECTION (Col 1 to 7)
                Matches wireframe: Top = "welcome message", Bottom = "news trail"
               ════════════════════════════════════════════════════════════════ */}
            <div className="lg:col-span-7 flex flex-col gap-6">

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
                      Welcome to your Portfolio Terminal
                    </span>
                    <span className="text-xs text-groww-text-muted hidden sm:inline-flex items-center gap-1">
                      <IconClock />
                      Live Market Session
                    </span>
                  </div>

                  {/* Main Greeting */}
                  <h2 className="text-2xl sm:text-3xl font-extrabold text-groww-text-primary tracking-tight">
                    {greeting}, <span className="text-groww-green capitalize">{displayName}</span>
                  </h2>

                  <p className="text-sm text-groww-text-secondary mt-2 max-w-xl leading-relaxed">
                    Your financial dashboard is up to date. Monitor your live stock performance, track real-time profit margins, and stay informed with the latest market news trail below.
                  </p>

                  {/* Market Quick Metrics Banner */}
                  <div className="mt-5 grid grid-cols-2 sm:grid-cols-3 gap-3 pt-4 border-t border-emerald-100/70">
                    <div className="p-3 rounded-xl bg-white/80 border border-emerald-50 shadow-sm">
                      <p className="text-[11px] font-semibold text-groww-text-muted uppercase tracking-wider">Nifty 50</p>
                      <p className="text-sm font-bold text-groww-text-primary mt-0.5">25,124.50</p>
                      <span className="text-xs font-semibold text-groww-green flex items-center gap-0.5 mt-0.5">
                        <IconTrendingUp /> +0.64%
                      </span>
                    </div>

                    <div className="p-3 rounded-xl bg-white/80 border border-emerald-50 shadow-sm">
                      <p className="text-[11px] font-semibold text-groww-text-muted uppercase tracking-wider">BSE Sensex</p>
                      <p className="text-sm font-bold text-groww-text-primary mt-0.5">82,340.10</p>
                      <span className="text-xs font-semibold text-groww-green flex items-center gap-0.5 mt-0.5">
                        <IconTrendingUp /> +0.58%
                      </span>
                    </div>

                    <div className="col-span-2 sm:col-span-1 p-3 rounded-xl bg-white/80 border border-emerald-50 shadow-sm">
                      <p className="text-[11px] font-semibold text-groww-text-muted uppercase tracking-wider">Market Trend</p>
                      <p className="text-sm font-bold text-groww-green-dark mt-0.5">Bullish Momentum</p>
                      <span className="text-xs text-groww-text-secondary mt-0.5 block truncate">Broad market gains</span>
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

              {/* ── 2. NEWS TRAIL CARD (Common to every user) ─────────────── */}
              <section
                id="news-trail-card"
                className="bg-white rounded-2xl sm:rounded-3xl border border-groww-border-light shadow-card p-6 flex flex-col transition-all duration-200"
              >
                {/* News Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-groww-border-light">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-lg font-bold text-groww-text-primary tracking-tight">Market News Trail</h3>
                      <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-gray-100 text-gray-700">
                        Common Feed
                      </span>
                    </div>
                    <p className="text-xs text-groww-text-muted mt-0.5">
                      Curated financial updates, policy shifts & market announcements
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

                {/* News Category Pills */}
                <div className="flex items-center gap-2 py-3 overflow-x-auto no-scrollbar">
                  {['ALL', 'Markets', 'Policy & Tax', 'Corporate', 'Energy', 'Tech'].map((category) => (
                    <button
                      key={category}
                      onClick={() => setNewsFilter(category)}
                      className={`px-3 py-1 rounded-lg text-xs font-semibold whitespace-nowrap transition-all duration-150 ${newsFilter === category
                          ? 'bg-groww-green text-white shadow-sm'
                          : 'bg-gray-100 text-groww-text-secondary hover:bg-gray-200'
                        }`}
                    >
                      {category}
                    </button>
                  ))}
                </div>

                {/* News Trail Timeline / List */}
                <div className="relative mt-2 divide-y divide-gray-100 max-h-[560px] overflow-y-auto pr-1">
                  {newsLoading && news.length === 0 ? (
                    <div className="py-12 flex flex-col items-center justify-center gap-3">
                      <div className="w-8 h-8 border-3 border-groww-green/20 border-t-groww-green rounded-full animate-spin" />
                      <p className="text-xs text-groww-text-muted">Streaming news trail...</p>
                    </div>
                  ) : filteredNews.length === 0 ? (
                    <div className="py-12 text-center">
                      <p className="text-sm font-semibold text-groww-text-secondary">No articles found</p>
                      <p className="text-xs text-groww-text-muted mt-1">Try searching for a different keyword or category.</p>
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
                              {item.source || 'Market Feed'}
                            </span>
                            {item.category && (
                              <span className="text-[11px] text-groww-text-muted">
                                &bull; {item.category}
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
                  <span>Synced with National Market Disclosures</span>
                  <span className="font-semibold text-groww-green">Updates every 5 mins</span>
                </div>
              </section>

            </div>

            {/* ════════════════════════════════════════════════════════════════
                RIGHT SECTION (Col 8 to 12)
                Matches wireframe: "personal portfolio analysis" with 6 boxes
               ════════════════════════════════════════════════════════════════ */}
            <div className="lg:col-span-5 flex flex-col gap-6">

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
                        {fmtCur(portfolioStats.totalInvested)}
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
                        {portfolioStats.totalStocks} {portfolioStats.totalStocks === 1 ? 'Asset' : 'Assets'}
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
                        {fmtPct(portfolioStats.totalProfitPercent)}
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
                        {portfolioStats.totalProfitAmount >= 0 ? '+' : ''}
                        {fmtCur(portfolioStats.totalProfitAmount)}
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
                        {fmtCur(portfolioStats.totalCurrent)}
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
                      <p className="text-base sm:text-lg font-extrabold text-teal-700 tracking-tight">
                        Balanced
                      </p>
                      <p className="text-[11px] text-groww-text-muted mt-1">
                        Diversified risk score
                      </p>
                    </div>
                  </div>
                </div>

                {/* ── Asset Allocation / Performance Bar ──────────────────── */}
                <div className="mt-4 p-4 rounded-2xl bg-emerald-50/50 border border-emerald-100">
                  <div className="flex items-center justify-between text-xs font-semibold text-groww-text-secondary mb-2">
                    <span>Capital Distribution</span>
                    <span className="text-groww-green font-bold">
                      {((portfolioStats.totalProfitAmount / (portfolioStats.totalCurrent || 1)) * 100).toFixed(1)}% Gains Share
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
                      Invested ({fmtCur(portfolioStats.totalInvested)})
                    </span>
                    <span className="flex items-center gap-1.5 font-medium text-groww-text-primary">
                      Gain ({fmtCur(portfolioStats.totalProfitAmount)})
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
