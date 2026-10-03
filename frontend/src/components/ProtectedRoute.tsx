import React from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

interface ProtectedRouteProps {
  children: React.ReactNode
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const { session, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen bg-groww-bg-primary flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          {/* Animated Groww-style loader */}
          <div className="relative w-12 h-12">
            <div className="absolute inset-0 rounded-full border-2 border-groww-green-light"></div>
            <div
              className="absolute inset-0 rounded-full border-2 border-transparent border-t-groww-green"
              style={{ animation: 'spin 0.8s linear infinite' }}
            ></div>
          </div>
          <p className="text-groww-text-muted text-sm font-medium">Loading…</p>
        </div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }

  if (!session) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

export default ProtectedRoute
