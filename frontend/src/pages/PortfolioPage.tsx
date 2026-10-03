import React, { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import { useAuth } from '../context/AuthContext'
import { fetchNews, fetchPortfolio } from '../lib/api'
import type { NewsItem, Portfolio } from '../lib/api'

type Loadable<T> =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; data: T }

const PRICE_REFRESH_MS = 60_000

const formatMoney = (value: number, currency: string) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(value)

const formatTimeAgo = (iso: string) => {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000))
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

const greeting = () => {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

const SectionCard: React.FC<{
  id: string
  title: string
  note?: string
  children: React.ReactNode
}> = ({ id, title, note, children }) => (
  <section
    id={id}
    className="bg-white rounded-2xl border border-groww-border-light shadow-card"
  >
    <div className="flex items-baseline justify-between gap-4 px-6 py-4 border-b border-groww-border-light">
      <h2 className="text-base font-bold text-groww-text-primary">{title}</h2>
      {note && <span className="text-xs text-groww-text-muted">{note}</span>}
    </div>
    {children}
  </section>
)

const Message: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="px-6 py-8 text-sm text-groww-text-secondary text-center">{children}</p>
)

const PortfolioPage: React.FC = () => {
  const { user } = useAuth()
  const [portfolio, setPortfolio] = useState<Loadable<Portfolio>>({ status: 'loading' })
  const [news, setNews] = useState<Loadable<NewsItem[]>>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false

    const loadPortfolio = () =>
      fetchPortfolio()
        .then((data) => !cancelled && setPortfolio({ status: 'ready', data }))
        // Keep showing the last good prices if a refresh fails.
        .catch(() => !cancelled && setPortfolio((prev) => (prev.status === 'ready' ? prev : { status: 'error' })))

    loadPortfolio()
    fetchNews()
      .then((data) => !cancelled && setNews({ status: 'ready', data }))
      .catch(() => !cancelled && setNews({ status: 'error' }))

    const timer = setInterval(loadPortfolio, PRICE_REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const userName = (user?.email ?? 'there').split('@')[0]

  return (
    <div id="portfolio-layout" className="flex h-screen overflow-hidden bg-groww-bg-primary">
      <Sidebar />

      <main id="portfolio-main" className="flex-1 overflow-y-auto overflow-x-hidden">
        <div className="max-w-6xl mx-auto p-6 flex flex-col gap-6">
          {/* Welcome */}
          <header
            id="portfolio-welcome"
            className="rounded-2xl px-6 py-6 text-white"
            style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)' }}
          >
            <p className="text-sm opacity-90">
              {new Date().toLocaleDateString('en-IN', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
            </p>
            <h1 className="text-2xl font-bold mt-1">
              {greeting()}, <span className="capitalize">{userName}</span>
            </h1>
            <p className="text-sm opacity-90 mt-1">
              Welcome back. Here is the latest on your portfolio.
            </p>
          </header>

          {/* News */}
          <SectionCard id="portfolio-news" title="News" note="Headlines on your holdings">
            {news.status === 'loading' && <Message>Loading news…</Message>}
            {news.status === 'error' && (
              <Message>Could not load news. Check that the backend is running.</Message>
            )}
            {news.status === 'ready' && news.data.length === 0 && (
              <Message>No recent headlines available right now.</Message>
            )}
            {news.status === 'ready' && news.data.length > 0 && (
              <ul className="divide-y divide-groww-border-light">
                {news.data.map((item) => (
                  <li key={item.url ?? item.title}>
                    <a
                      href={item.url ?? undefined}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block px-6 py-3 hover:bg-groww-green-pale transition-colors duration-200"
                    >
                      <p className="text-sm font-medium text-groww-text-primary">{item.title}</p>
                      <p className="text-xs text-groww-text-muted mt-1">
                        {[item.source, item.published_at && formatTimeAgo(item.published_at)]
                          .filter(Boolean)
                          .join(' · ')}
                      </p>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          {/* Holdings */}
          <SectionCard
            id="portfolio-holdings"
            title="Holdings"
            note={
              portfolio.status === 'ready'
                ? portfolio.data.prices_as_of
                  ? `Prices updated ${new Date(portfolio.data.prices_as_of).toLocaleTimeString('en-IN')}`
                  : 'Live prices unavailable'
                : undefined
            }
          >
            {portfolio.status === 'loading' && <Message>Loading holdings…</Message>}
            {portfolio.status === 'error' && (
              <Message>Could not load holdings. Check that the backend is running.</Message>
            )}
            {portfolio.status === 'ready' && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-groww-text-muted text-right">
                      <th className="px-6 py-3 font-medium text-left">Stock</th>
                      <th className="px-6 py-3 font-medium">No. of shares</th>
                      <th className="px-6 py-3 font-medium">Total avg. price</th>
                      <th className="px-6 py-3 font-medium">Current total price</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-groww-border-light border-t border-groww-border-light">
                    {portfolio.data.holdings.map((h) => {
                      const change = h.current_value === null ? null : h.current_value - h.invested_value
                      return (
                        <tr key={h.ticker} className="text-right tabular-nums">
                          <td className="px-6 py-3 text-left">
                            <p className="font-semibold text-groww-text-primary">{h.name}</p>
                            <p className="text-xs text-groww-text-muted">{h.ticker.replace('.NS', '')}</p>
                          </td>
                          <td className="px-6 py-3 text-groww-text-primary">{h.shares}</td>
                          <td className="px-6 py-3">
                            <p className="text-groww-text-primary">
                              {formatMoney(h.invested_value, portfolio.data.currency)}
                            </p>
                            <p className="text-xs text-groww-text-muted">
                              {formatMoney(h.avg_price, portfolio.data.currency)} / share
                            </p>
                          </td>
                          <td className="px-6 py-3">
                            {h.current_value === null || h.current_price === null || change === null ? (
                              <span className="text-groww-text-muted" title="Live price unavailable">—</span>
                            ) : (
                              <>
                                <p className={`font-semibold ${change >= 0 ? 'text-groww-green' : 'text-red-500'}`}>
                                  {formatMoney(h.current_value, portfolio.data.currency)}
                                </p>
                                <p className="text-xs text-groww-text-muted">
                                  {formatMoney(h.current_price, portfolio.data.currency)} / share
                                </p>
                              </>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                  <tfoot>
                    <tr className="text-right tabular-nums font-bold text-groww-text-primary border-t border-groww-border">
                      <td className="px-6 py-4 text-left" colSpan={2}>Total</td>
                      <td className="px-6 py-4">
                        {formatMoney(portfolio.data.totals.invested_value, portfolio.data.currency)}
                      </td>
                      <td className="px-6 py-4">
                        {portfolio.data.totals.current_value === null
                          ? '—'
                          : formatMoney(portfolio.data.totals.current_value, portfolio.data.currency)}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </SectionCard>
        </div>
      </main>
    </div>
  )
}

export default PortfolioPage
