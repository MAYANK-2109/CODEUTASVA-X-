const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export interface Holding {
  ticker: string
  name: string
  sector: string
  shares: number
  avg_price: number
  invested_value: number
  current_price: number | null
  current_value: number | null
}

export interface Portfolio {
  currency: string
  holdings: Holding[]
  totals: { invested_value: number; current_value: number | null }
  prices_as_of: string | null
}

export interface NewsItem {
  title: string
  source: string | null
  url: string | null
  published_at: string | null
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`)
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const fetchPortfolio = () => getJson<Portfolio>('/api/portfolio')

export const fetchNews = () =>
  getJson<{ items: NewsItem[] }>('/api/news').then((data) => data.items)
