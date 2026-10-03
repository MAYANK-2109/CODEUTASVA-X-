import React, { useState } from 'react'
import Sidebar from '../components/Sidebar'
import PortfolioPage from './PortfolioPage'
import { useAuth } from '../context/AuthContext'

const DashboardPage: React.FC = () => {
  const [activePage, setActivePage] = useState('dashboard')

  const renderPage = () => {
    switch (activePage) {
      case 'portfolio':
        return <PortfolioPage />
      default:
        return <DashboardHome />
    }
  }

  return (
    <div id="dashboard-layout" className="flex h-screen overflow-hidden bg-groww-bg-primary">
      {/* Left Sidebar */}
      <Sidebar />

      {/* Main content area */}
      <main
        id="dashboard-main"
        className="flex-1 overflow-y-auto overflow-x-hidden"
      >
        {/* Top header bar */}
        <header
          id="dashboard-header"
          className="sticky top-0 z-10 bg-white border-b border-groww-border-light px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between"
          style={{ minHeight: '64px' }}
        >
          <div>
            <h1 className="text-lg font-bold text-groww-text-primary">Dashboard</h1>
            <p className="text-xs text-groww-text-muted mt-0.5">
              {new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
            </p>
          </div>

          {/* Right header actions */}
          <div className="flex items-center gap-3">
            {/* Notification bell */}
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

            {/* Settings */}
            <button
              id="dashboard-settings-btn"
              onClick={() => navigate('/settings')}
              className="w-9 h-9 rounded-full flex items-center justify-center text-white font-bold text-sm transition-transform duration-200 hover:scale-105"
              style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)' }}
              aria-label="Settings"
            >
              {userInitial}
            </button>
          </div>
        </header>

        {/* Blank dashboard body */}
        <div
          id="dashboard-content"
          className="flex flex-col items-center justify-center min-h-[calc(100vh-72px)] p-8"
        >
          {/* Empty state illustration */}
          <div className="flex flex-col items-center text-center max-w-sm">
            {/* Icon */}
            <div
              className="w-20 h-20 rounded-2xl flex items-center justify-center mb-6"
              style={{ background: 'linear-gradient(135deg, #E8F5F1, #F0FAF7)' }}
            >
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#00B386" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7"/>
                <rect x="14" y="3" width="7" height="7"/>
                <rect x="14" y="14" width="7" height="7"/>
                <rect x="3" y="14" width="7" height="7"/>
              </svg>
            </div>

            <h2 className="text-xl font-bold text-groww-text-primary mb-2">
              Your dashboard is ready
            </h2>
            <p className="text-sm text-groww-text-secondary leading-relaxed">
              You're successfully logged in. Use the navigation sidebar on the left to get started.
            </p>

            {/* Green divider dot */}
            <div className="mt-6 flex items-center gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-groww-green"></div>
              <span className="text-xs text-groww-text-muted font-medium">All systems operational</span>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}

const DashboardHome: React.FC = () => {
  const { user } = useAuth()
  const userInitial = user?.email?.charAt(0).toUpperCase() ?? 'U'

  return (
    <>
      <header
        id="dashboard-header"
        className="sticky top-0 z-10 bg-white border-b border-groww-border-light px-6 py-4 flex items-center justify-between"
        style={{ minHeight: '72px' }}
      >
        <div>
          <h1 className="text-lg font-bold text-groww-text-primary">Dashboard</h1>
          <p className="text-xs text-groww-text-muted mt-0.5">
            {new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
          </p>
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

      <div id="dashboard-content" className="flex flex-col items-center justify-center min-h-[calc(100vh-72px)] p-8">
        <div className="flex flex-col items-center text-center max-w-sm">
          <div
            className="w-20 h-20 rounded-2xl flex items-center justify-center mb-6"
            style={{ background: 'linear-gradient(135deg, #E8F5F1, #F0FAF7)' }}
          >
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#00B386" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="7" height="7"/>
              <rect x="14" y="3" width="7" height="7"/>
              <rect x="14" y="14" width="7" height="7"/>
              <rect x="3" y="14" width="7" height="7"/>
            </svg>
          </div>
          <h2 className="text-xl font-bold text-groww-text-primary mb-2">Your dashboard is ready</h2>
          <p className="text-sm text-groww-text-secondary leading-relaxed">
            You're successfully logged in. Use the navigation sidebar on the left to get started.
          </p>
          <div className="mt-6 flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-groww-green"></div>
            <span className="text-xs text-groww-text-muted font-medium">All systems operational</span>
          </div>
        </div>
      </div>
    </>
  )
}

export default DashboardPage
