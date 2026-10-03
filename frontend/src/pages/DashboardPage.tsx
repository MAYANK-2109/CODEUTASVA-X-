import React, { useState, useEffect } from 'react'
import Sidebar from '../components/Sidebar'
import PortfolioPage from './PortfolioPage'
import { useAuth } from '../context/AuthContext'
import { fetchNews, type NewsItem } from '../lib/api'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getGreeting(): string {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function relativeTime(iso: string | null): string {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60)    return 'just now'
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

// ---------------------------------------------------------------------------
// DashboardPage shell
// ---------------------------------------------------------------------------
const DashboardPage: React.FC = () => {
  const [activePage, setActivePage] = useState('dashboard')
  const [showPdfModal, setShowPdfModal] = useState(false)

  // Clicking "Add Latest PDF" from the Sidebar navigates to Portfolio and
  // opens the upload modal immediately, regardless of the current page.
  const handleAddPdf = () => {
    setActivePage('portfolio')
    setShowPdfModal(true)
  }

  const renderPage = () => {
    switch (activePage) {
      case 'portfolio':
        return (
          <PortfolioPage
            externalShowModal={showPdfModal}
            onExternalModalClose={() => setShowPdfModal(false)}
          />
        )
      default:
        return <DashboardHome />
    }
  }

  return (
    <div id="dashboard-layout" className="flex h-screen overflow-hidden bg-groww-bg-primary">
      <Sidebar activePage={activePage} onNavigate={setActivePage} onAddPdf={handleAddPdf} />
      <main id="dashboard-main" className="flex-1 overflow-y-auto overflow-x-hidden">
        {renderPage()}
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// News skeleton
// ---------------------------------------------------------------------------
function NewsSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="bg-white rounded-2xl p-4 border border-gray-100 animate-pulse">
          <div className="h-3 bg-gray-100 rounded w-1/4 mb-3" />
          <div className="h-4 bg-gray-200 rounded w-full mb-2" />
          <div className="h-4 bg-gray-200 rounded w-3/4" />
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// News card
// ---------------------------------------------------------------------------
function NewsCard({ item, index }: { item: NewsItem; index: number }) {
  return (
    <a
      href={item.url ?? '#'}
      target="_blank"
      rel="noopener noreferrer"
      id={`news-card-${index}`}
      className="group flex flex-col gap-2 bg-white rounded-2xl p-4 border border-gray-100 shadow-sm hover:shadow-md hover:border-groww-green/30 transition-all duration-200 hover:-translate-y-0.5"
    >
      <div className="flex items-center gap-2">
        {item.source && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold bg-green-50 text-groww-green border border-green-100 shrink-0">
            {item.source}
          </span>
        )}
        {item.published_at && (
          <span className="text-[10px] text-gray-400 ml-auto shrink-0">
            {relativeTime(item.published_at)}
          </span>
        )}
      </div>
      <p className="text-sm font-semibold text-gray-800 leading-snug group-hover:text-groww-green transition-colors line-clamp-2">
        {item.title}
      </p>
      <div className="flex items-center gap-1 text-[11px] text-groww-green font-medium opacity-0 group-hover:opacity-100 transition-opacity">
        <span>Read article</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
        </svg>
      </div>
    </a>
  )
}

// ---------------------------------------------------------------------------
// Dashboard Home — welcome banner + news
// ---------------------------------------------------------------------------
const DashboardHome: React.FC = () => {
  const { user } = useAuth()
  const userInitial = user?.email?.charAt(0).toUpperCase() ?? 'U'
  const userName    = user?.email?.split('@')[0] ?? 'there'
  const greeting    = getGreeting()

  const [news, setNews]       = useState<NewsItem[]>([])
  const [newsLoading, setNL]  = useState(true)
  const [newsError, setNE]    = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setNL(true); setNE(null)
    fetchNews()
      .then(items => { if (!cancelled) { setNews(items); setNL(false) } })
      .catch(err  => { if (!cancelled) { setNE(err.message); setNL(false) } })
    return () => { cancelled = true }
  }, [])

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  })

  return (
    <>
      {/* ── Header ──────────────────────────────────────────────── */}
      <header
        id="dashboard-header"
        className="sticky top-0 z-10 bg-white border-b border-groww-border-light px-6 py-4 flex items-center justify-between"
        style={{ minHeight: '72px' }}
      >
        <div>
          <h1 className="text-lg font-bold text-groww-text-primary">Dashboard</h1>
          <p className="text-xs text-groww-text-muted mt-0.5">{today}</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            id="dashboard-notifications-btn"
            className="relative w-9 h-9 rounded-xl flex items-center justify-center text-groww-text-secondary hover:bg-groww-green-light hover:text-groww-green transition-all duration-200"
            aria-label="Notifications"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
              <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
            </svg>
          </button>
          <button
            id="dashboard-settings-btn"
            className="w-9 h-9 rounded-full flex items-center justify-center text-white font-bold text-sm transition-transform duration-200 hover:scale-105"
            style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)' }}
            aria-label="Profile"
          >
            {userInitial}
          </button>
        </div>
      </header>

      {/* ── Content ─────────────────────────────────────────────── */}
      <div id="dashboard-content" className="p-6 flex flex-col gap-6 max-w-4xl mx-auto w-full">

        {/* Welcome Banner */}
        <div
          id="dashboard-welcome-banner"
          className="relative overflow-hidden rounded-2xl p-6 flex items-center justify-between gap-4"
          style={{
            background: 'linear-gradient(135deg, #00B386 0%, #007A5A 100%)',
            boxShadow: '0 8px 32px rgba(0,179,134,0.25)',
          }}
        >
          {/* decorative circles */}
          <div className="absolute -top-8 -right-8 w-40 h-40 rounded-full opacity-10" style={{ background: '#fff' }} />
          <div className="absolute -bottom-10 -right-20 w-56 h-56 rounded-full opacity-10" style={{ background: '#fff' }} />

          <div className="relative z-10">
            <p className="text-white/70 text-sm font-medium mb-0.5">{greeting},</p>
            <h2 className="text-white text-2xl font-bold capitalize leading-tight mb-2">{userName} 👋</h2>
            <p className="text-white/80 text-sm max-w-xs leading-relaxed">
              Here's what's happening in the markets today. Stay informed and trade smart.
            </p>
          </div>

          <div className="relative z-10 shrink-0 hidden sm:flex flex-col items-end gap-1">
            <div className="flex items-center gap-2 bg-white/20 rounded-xl px-4 py-2">
              <div className="w-2 h-2 rounded-full bg-white animate-pulse" />
              <span className="text-white text-xs font-semibold">Markets live</span>
            </div>
            <p className="text-white/60 text-[11px]">
              {new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
            </p>
          </div>
        </div>

        {/* Market News */}
        <section id="dashboard-news-section">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-base font-bold text-groww-text-primary">Market News</h3>
              <p className="text-xs text-groww-text-muted mt-0.5">Latest headlines from Indian markets</p>
            </div>
            {!newsLoading && news.length > 0 && (
              <span className="text-[11px] font-medium text-groww-green bg-green-50 border border-green-100 px-2.5 py-1 rounded-lg">
                {news.length} articles
              </span>
            )}
          </div>

          {newsLoading ? (
            <NewsSkeleton />
          ) : newsError ? (
            <div id="dashboard-news-error" className="flex flex-col items-center justify-center py-16 text-center gap-3">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center bg-red-50">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#EF4444" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
              </div>
              <p className="text-sm font-semibold text-gray-600">Couldn't load news</p>
              <p className="text-xs text-gray-400 max-w-xs">{newsError}</p>
            </div>
          ) : news.length === 0 ? (
            <div id="dashboard-news-empty" className="flex flex-col items-center justify-center py-16 text-center gap-3">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #E8F5F1, #F0FAF7)' }}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#00B386" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v4"/>
                  <path d="M2 13h10"/><path d="M9 18H2"/><path d="M2 9h3"/>
                </svg>
              </div>
              <p className="text-sm font-semibold text-gray-600">No news available right now</p>
              <p className="text-xs text-gray-400">Check back shortly — the feed refreshes every 5 minutes.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {news.map((item, i) => (
                <NewsCard key={i} item={item} index={i} />
              ))}
            </div>
          )}
        </section>
      </div>
    </>
  )
}

export default DashboardPage
