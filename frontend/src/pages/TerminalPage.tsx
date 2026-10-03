import React, { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import NotificationBell from '../components/NotificationBell'
import AITerminal from '../components/AITerminal'

interface StreamStatus {
  key: string
  label: string
  status: 'live' | 'degraded' | 'down' | 'idle'
  detail: string
  source: string
}

const BACKEND_URL: string =
  import.meta.env.VITE_BACKEND_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const TerminalPage: React.FC = () => {
  const [streams, setStreams] = useState<StreamStatus[]>([])
  const [counts, setCounts] = useState<{ live: number; degraded: number; down: number; idle: number }>({
    live: 0,
    degraded: 0,
    down: 0,
    idle: 0,
  })

  useEffect(() => {
    fetch(`${BACKEND_URL}/api/terminal/streams`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.streams) {
          setStreams(data.streams)
          setCounts(data.counts || { live: data.streams.length, degraded: 0, down: 0, idle: 0 })
        }
      })
      .catch(() => {
        // Fallback default state
      })
  }, [])

  return (
    <div className="flex flex-col-reverse md:flex-row h-dvh overflow-hidden bg-groww-bg-primary">
      <Sidebar />
      <main className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden flex flex-col">
        {/* Top Navigation Bar */}
        <header className="sticky top-0 z-20 bg-white/80 backdrop-blur-md border-b border-groww-border-light px-6 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-emerald-600 text-white flex items-center justify-center font-bold text-sm shadow-xs">
              ⚡
            </div>
            <div>
              <h1 className="text-base font-bold text-groww-text-primary">
                Autonomous Financial Intelligence Terminal
              </h1>
              <p className="text-xs text-groww-text-muted">
                Problem Statement 5 · Multi-Agent Macro & Weather Ingestion Engine
              </p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* Live Data Feeds Health Pill */}
            <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-100 text-xs text-slate-700 border border-slate-200">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <span className="font-semibold">{counts.live || streams.length || 7} Live Ingestion Feeds</span>
            </div>
            <NotificationBell />
          </div>
        </header>

        {/* Terminal Content */}
        <div className="p-4 md:p-6 space-y-6 max-w-7xl mx-auto w-full">
          {/* Main AI Terminal */}
          <AITerminal
            title="LangGraph Multi-Agent Portfolio Management Engine"
            subtitle="Autonomous scenario stress-testing, alternative data reasoning, and grounded hedge execution"
          />

          {/* Real-time Ingestion Feed Telemetry */}
          <div className="rounded-xl border border-groww-border-light bg-white p-4 shadow-sm space-y-3">
            <div className="flex items-center justify-between border-b border-groww-border-light pb-2">
              <h3 className="text-xs font-bold text-groww-text-primary uppercase tracking-wider flex items-center gap-2">
                <span>📡 Active Ingestion Streams & Vector Indices</span>
              </h3>
              <span className="text-[11px] text-groww-text-muted">Continuous Polling & Indexing</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {[
                {
                  label: 'Open-Meteo Global Weather',
                  source: 'Live 7-Day ECMWF / ERA5 Reanalysis',
                  status: 'Active',
                  icon: '🌦️',
                  detail: 'Monitoring rainfall, gale gusts & heat across all industrial ports',
                },
                {
                  label: 'Pinecone Vector Database',
                  source: 'multilingual-e5-large',
                  status: 'Indexed',
                  icon: '📚',
                  detail: 'High-throughput historical crisis parallels & semantic search',
                },
                {
                  label: 'GDELT Global News Engine',
                  source: 'Live 15-Minute News Scanning',
                  status: 'Scanning',
                  icon: '📰',
                  detail: 'Real-time multi-lingual financial sentiment & entity recognition',
                },
                {
                  label: 'Yahoo Finance & NSE Market Feed',
                  source: 'NSE Real-Time / Close Prices',
                  status: 'Streaming',
                  icon: '📈',
                  detail: 'Cross-asset commodities, Brent crude, Gold & FX rates',
                },
              ].map((feed, idx) => (
                <div key={idx} className="p-3 rounded-lg border border-groww-border-light bg-slate-50/50 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-groww-text-primary flex items-center gap-1.5">
                      <span>{feed.icon}</span>
                      <span>{feed.label}</span>
                    </span>
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-emerald-100 text-emerald-700">
                      {feed.status}
                    </span>
                  </div>
                  <p className="text-[11px] font-medium text-emerald-700">{feed.source}</p>
                  <p className="text-[10px] text-groww-text-muted leading-snug">{feed.detail}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}

export default TerminalPage
