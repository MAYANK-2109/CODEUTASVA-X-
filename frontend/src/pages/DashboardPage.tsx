import React from 'react'
import { useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
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
  const { user } = useAuth()
  const navigate = useNavigate()
  const userInitial = user?.email?.charAt(0).toUpperCase() ?? 'U'

  return (
    <div id="dashboard-layout" className="flex h-screen overflow-hidden bg-groww-bg-primary">
      <Sidebar activePage={activePage} onNavigate={setActivePage} onAddPdf={handleAddPdf} />
      <main id="dashboard-main" className="flex-1 overflow-y-auto overflow-x-hidden">
        {renderPage()}
      </main>
    </div>
  )
}

export default DashboardPage
