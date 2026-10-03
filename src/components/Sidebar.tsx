import React, { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { useNavigate } from 'react-router-dom'

interface NavItem {
  id: string
  label: string
  icon: React.ReactNode
}

const navItems: NavItem[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="7"/>
        <rect x="14" y="3" width="7" height="7"/>
        <rect x="14" y="14" width="7" height="7"/>
        <rect x="3" y="14" width="7" height="7"/>
      </svg>
    ),
  },
  {
    id: 'explore',
    label: 'Explore',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8"/>
        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
    ),
  },
  {
    id: 'portfolio',
    label: 'Portfolio',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="20" x2="18" y2="10"/>
        <line x1="12" y1="20" x2="12" y2="4"/>
        <line x1="6" y1="20" x2="6" y2="14"/>
      </svg>
    ),
  },
  {
    id: 'watchlist',
    label: 'Watchlist',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
      </svg>
    ),
  },
  {
    id: 'orders',
    label: 'Orders',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="16" y1="13" x2="8" y2="13"/>
        <line x1="16" y1="17" x2="8" y2="17"/>
        <polyline points="10 9 9 9 8 9"/>
      </svg>
    ),
  },
]

const Sidebar: React.FC = () => {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const [activeItem, setActiveItem] = useState('dashboard')

  const userInitial = user?.email?.charAt(0).toUpperCase() ?? 'U'
  const userEmail = user?.email ?? 'user@example.com'
  const userName = userEmail.split('@')[0]

  return (
    <aside
      id="sidebar"
      className="relative flex flex-col h-screen bg-groww-bg-sidebar border-r border-groww-border-light transition-all duration-300 ease-in-out shrink-0"
      style={{
        width: collapsed ? '72px' : '240px',
        boxShadow: '2px 0 20px rgba(0, 179, 134, 0.05)',
      }}
    >
      {/* ── Top: User Profile ───────────────────────────────────── */}
      <div
        className="flex items-center gap-3 p-4 border-b border-groww-border-light cursor-pointer hover:bg-gray-50 transition-colors"
        style={{ minHeight: '72px' }}
        onClick={() => navigate('/settings')}
      >
        {/* Avatar / profile icon */}
        <div
          id="sidebar-user-avatar"
          className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-sm select-none transition-transform duration-200 hover:scale-105"
          style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)' }}
          title={userName}
        >
          {userInitial}
        </div>

        {/* Name + email — only when expanded */}
        <div
          className="flex-1 min-w-0 overflow-hidden transition-all duration-300"
          style={{ opacity: collapsed ? 0 : 1, width: collapsed ? 0 : 'auto' }}
        >
          <p className="text-sm font-semibold text-groww-text-primary truncate capitalize">
            {userName}
          </p>
          <p className="text-xs text-groww-text-muted truncate">Settings</p>
        </div>
      </div>

      {/* ── Navigation ──────────────────────────────────────────── */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-3 flex flex-col gap-1">
        {navItems.map((item) =>
          collapsed ? (
            /* Collapsed: icon-only with tooltip */
            <div key={item.id} className="relative group">
              <button
                id={`sidebar-nav-${item.id}`}
                onClick={() => setActiveItem(item.id)}
                className={`nav-item-collapsed w-full ${activeItem === item.id ? 'active' : ''}`}
                aria-label={item.label}
              >
                {item.icon}
              </button>
              {/* Tooltip */}
              <span
                className="absolute left-14 top-1/2 -translate-y-1/2 z-50 px-2.5 py-1.5 rounded-lg text-xs font-medium text-white whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150"
                style={{ background: '#1A1A2E' }}
              >
                {item.label}
                <span
                  className="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent"
                  style={{ borderRightColor: '#1A1A2E' }}
                />
              </span>
            </div>
          ) : (
            /* Expanded: icon + label */
            <button
              key={item.id}
              id={`sidebar-nav-${item.id}`}
              onClick={() => setActiveItem(item.id)}
              className={`nav-item w-full text-left ${activeItem === item.id ? 'active' : ''}`}
            >
              <span className="shrink-0">{item.icon}</span>
              <span className="truncate">{item.label}</span>
            </button>
          )
        )}
      </nav>

      {/* ── Bottom: Sign out ────────────────────────────────────── */}
      <div className="px-3 pb-4 border-t border-groww-border-light pt-4">
        {collapsed ? (
          <div className="relative group">
            <button
              id="sidebar-signout-btn"
              onClick={signOut}
              className="nav-item-collapsed w-full text-red-400 hover:text-red-500 hover:bg-red-50"
              aria-label="Sign out"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
                <polyline points="16 17 21 12 16 7"/>
                <line x1="21" y1="12" x2="9" y2="12"/>
              </svg>
            </button>
            <span
              className="absolute left-14 top-1/2 -translate-y-1/2 z-50 px-2.5 py-1.5 rounded-lg text-xs font-medium text-white whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150"
              style={{ background: '#1A1A2E' }}
            >
              Sign out
            </span>
          </div>
        ) : (
          <button
            id="sidebar-signout-btn"
            onClick={signOut}
            className="nav-item w-full text-left text-red-400 hover:text-red-500 hover:bg-red-50"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
              <polyline points="16 17 21 12 16 7"/>
              <line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
            <span className="truncate">Sign out</span>
          </button>
        )}
      </div>

      {/* ── Collapse / Expand toggle button ─────────────────────── */}
      <button
        id="sidebar-collapse-btn"
        onClick={() => setCollapsed(!collapsed)}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className="absolute -right-3.5 top-[86px] z-20 w-7 h-7 rounded-full bg-white border border-groww-border flex items-center justify-center shadow-card transition-all duration-200 hover:border-groww-green hover:text-groww-green text-groww-text-secondary"
        style={{ cursor: 'pointer' }}
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ transform: collapsed ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.3s' }}
        >
          <polyline points="15 18 9 12 15 6"/>
        </svg>
      </button>
    </aside>
  )
}

export default Sidebar
