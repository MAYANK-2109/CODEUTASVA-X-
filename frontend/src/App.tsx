import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import ProtectedRoute from './components/ProtectedRoute'
import ChatWidget from './components/ChatWidget'
import Sidebar from './components/Sidebar'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import PortfolioPage from './pages/PortfolioPage'
import InsightsPage from './pages/InsightsPage'
import TerminalPage from './pages/TerminalPage'
import AccountSettingsPage from './pages/AccountSettingsPage'

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public routes */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected routes */}
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <DashboardPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/terminal"
            element={
              <ProtectedRoute>
                <TerminalPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/portfolio"
            element={
              <ProtectedRoute>
                <div className="flex flex-col-reverse md:flex-row h-dvh overflow-hidden bg-groww-bg-primary">
                  <Sidebar />
                  <main className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden">
                    <PortfolioPage />
                  </main>
                </div>
              </ProtectedRoute>
            }
          />
          <Route
            path="/insights"
            element={
              <ProtectedRoute>
                <div className="flex flex-col-reverse md:flex-row h-dvh overflow-hidden bg-groww-bg-primary">
                  <Sidebar />
                  <main className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden">
                    <InsightsPage />
                  </main>
                </div>
              </ProtectedRoute>
            }
          />
          <Route
            path="/settings"
            element={
              <ProtectedRoute>
                <AccountSettingsPage />
              </ProtectedRoute>
            }
          />

          {/* Redirect root to dashboard */}
          <Route path="/" element={<Navigate to="/dashboard" replace />} />

          {/* Catch-all → dashboard */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
        <ChatWidget />
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
