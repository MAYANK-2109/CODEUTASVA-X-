import React, { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'

// ── SVG Icon Components ────────────────────────────────────────────────────────

const IconLock = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
)

const IconZap = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
  </svg>
)

const IconBarChart = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10" />
    <line x1="12" y1="20" x2="12" y2="4" />
    <line x1="6" y1="20" x2="6" y2="14" />
  </svg>
)
//

const IconEye = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
)

const IconEyeOff = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
    <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </svg>
)

const IconAlertCircle = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
)

const IconCheckCircle = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5">
    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
    <polyline points="22 4 12 14.01 9 11.01" />
  </svg>
)

const IconSpinner = () => (
  <svg
    className="animate-spin"
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.5"
    strokeLinecap="round"
  >
    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
  </svg>
)

// ── Brand logo mark ────────────────────────────────────────────────────────────
const BrandLogo = ({ size = 36 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 36 36" fill="none">
    <path d="M6 18C6 11.373 11.373 6 18 6s12 5.373 12 12-5.373 12-12 12" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
    <path d="M18 18l8-8M18 18v-8" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="18" cy="18" r="3" fill="white" />
  </svg>
)

// ── Feature list items ─────────────────────────────────────────────────────────
const features = [
  { Icon: IconLock, text: 'Secure & encrypted' },
  { Icon: IconZap, text: 'Real-time updates' },
  { Icon: IconBarChart, text: 'Clean analytics' },
]

// ── Reusable label ─────────────────────────────────────────────────────────────
const FieldLabel = ({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) => (
  <label
    htmlFor={htmlFor}
    className="text-xs font-semibold text-groww-text-secondary uppercase tracking-wide"
  >
    {children}
  </label>
)

// ── Main Page ──────────────────────────────────────────────────────────────────
const LoginPage: React.FC = () => {
  const { session, loading } = useAuth()

  const [isSignUp, setIsSignUp] = useState(false)
  const [authLoading, setAuthLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [showPassword, setShowPassword] = useState(false)

  // Login fields
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  // Sign-up extra fields
  const [fullName, setFullName] = useState('')
  const [age, setAge] = useState('')

  if (!loading && session) {
    return <Navigate to="/dashboard" replace />
  }

  const resetForm = () => {
    setEmail('')
    setPassword('')
    setFullName('')
    setAge('')
    setError(null)
    setSuccessMsg(null)
    setShowPassword(false)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSuccessMsg(null)

    // Client-side validation for sign-up
    if (isSignUp) {
      if (!fullName.trim()) {
        setError('Please enter your full name.')
        return
      }
      const ageNum = parseInt(age, 10)
      if (!age || isNaN(ageNum) || ageNum < 18 || ageNum > 120) {
        setError('Please enter a valid age (18 or above).')
        return
      }
    }

    setAuthLoading(true)

    try {
      if (isSignUp) {
        const { error } = await supabase.auth.signUp({
          email,
          password,
          options: {
            data: {
              full_name: fullName.trim(),
              age: parseInt(age, 10),
            },
          },
        })
        if (error) throw error
        setSuccessMsg('Account created! Check your email to confirm your account.')
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'An unexpected error occurred.'
      setError(message)
    } finally {
      setAuthLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-groww-bg-primary flex">

      {/* ── Left panel: branding ──────────────────────────────────── */}
      <div
        className="hidden lg:flex lg:w-1/2 flex-col items-center justify-center p-12 relative overflow-hidden"
        style={{ background: 'linear-gradient(135deg, #00B386 0%, #00956E 50%, #007A5A 100%)' }}
      >
        {/* Decorative circles */}
        <div className="absolute top-[-80px] right-[-80px] w-64 h-64 rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }} />
        <div className="absolute bottom-[-60px] left-[-60px] w-48 h-48 rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }} />
        <div className="absolute top-1/2 left-[-100px] w-80 h-80 rounded-full" style={{ background: 'rgba(255,255,255,0.04)', transform: 'translateY(-50%)' }} />

        <div className="relative z-10 text-white text-center max-w-sm">
          {/* Logo */}
          <div className="mb-8 flex justify-center">
            <div
              className="w-16 h-16 rounded-2xl flex items-center justify-center"
              style={{ background: 'rgba(255,255,255,0.18)', backdropFilter: 'blur(12px)' }}
            >
              <BrandLogo size={36} />
            </div>
          </div>

          <h1 className="text-4xl font-bold mb-4 leading-tight">
            Grow your<br />
            <span style={{ color: 'rgba(255,255,255,0.82)' }}>financial future</span>
          </h1>
          <p className="text-base leading-relaxed" style={{ color: 'rgba(255,255,255,0.7)' }}>
            Simple, transparent, and powerful tools to help you achieve your goals.
          </p>

          {/* Feature pills */}
          <div className="mt-10 flex flex-col gap-3">
            {features.map(({ Icon, text }) => (
              <div
                key={text}
                className="flex items-center gap-3 px-4 py-3 rounded-xl text-left"
                style={{ background: 'rgba(255,255,255,0.12)', backdropFilter: 'blur(8px)' }}
              >
                <span style={{ color: 'rgba(255,255,255,0.9)' }}><Icon /></span>
                <span className="text-sm font-medium" style={{ color: 'rgba(255,255,255,0.9)' }}>
                  {text}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Right panel: auth form ────────────────────────────────── */}
      <div className="flex-1 flex items-center justify-center p-6 overflow-y-auto">
        <div className="w-full max-w-md py-8">

          {/* Mobile logo */}
          <div className="lg:hidden flex justify-center mb-8">
            <div
              className="w-12 h-12 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #00B386, #00956E)' }}
            >
              <BrandLogo size={28} />
            </div>
          </div>

          <div className="bg-white rounded-2xl border border-groww-border-light p-8" style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)' }}>

            {/* Header */}
            <div className="mb-7">
              <h2 className="text-2xl font-bold text-groww-text-primary mb-1">
                {isSignUp ? 'Create account' : 'Welcome back'}
              </h2>
              <p className="text-sm text-groww-text-secondary">
                {isSignUp
                  ? 'Fill in the details below to get started'
                  : 'Sign in to access your dashboard'}
              </p>
            </div>

            <form onSubmit={handleSubmit} id="auth-form" className="flex flex-col gap-4">

              {/* ── Sign-up only: Full name ── */}
              {isSignUp && (
                <div className="flex flex-col gap-1.5">
                  <FieldLabel htmlFor="name-input">Full name</FieldLabel>
                  <input
                    id="name-input"
                    type="text"
                    autoComplete="name"
                    required
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="John Doe"
                    className="auth-input"
                  />
                </div>
              )}

              {/* ── Email ── */}
              <div className="flex flex-col gap-1.5">
                <FieldLabel htmlFor="email-input">Email address</FieldLabel>
                <input
                  id="email-input"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="auth-input"
                />
              </div>

              {/* ── Sign-up only: Age ── */}
              {isSignUp && (
                <div className="flex flex-col gap-1.5">
                  <FieldLabel htmlFor="age-input">Age</FieldLabel>
                  <input
                    id="age-input"
                    type="number"
                    min={18}
                    max={120}
                    required
                    value={age}
                    onChange={(e) => setAge(e.target.value)}
                    placeholder="e.g. 25"
                    className="auth-input"
                  />
                </div>
              )}

              {/* ── Password ── */}
              <div className="flex flex-col gap-1.5">
                <FieldLabel htmlFor="password-input">Password</FieldLabel>
                <div className="relative">
                  <input
                    id="password-input"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete={isSignUp ? 'new-password' : 'current-password'}
                    required
                    minLength={6}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Minimum 6 characters"
                    className="auth-input pr-11"
                  />
                  <button
                    id="toggle-password-btn"
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-groww-text-muted hover:text-groww-text-secondary transition-colors"
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? <IconEyeOff /> : <IconEye />}
                  </button>
                </div>
              </div>

              {/* ── Error ── */}
              {error && (
                <div
                  id="auth-error"
                  className="flex items-start gap-2.5 px-3.5 py-3 rounded-xl text-sm"
                  style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#DC2626' }}
                >
                  <IconAlertCircle />
                  <span>{error}</span>
                </div>
              )}

              {/* ── Success ── */}
              {successMsg && (
                <div
                  id="auth-success"
                  className="flex items-start gap-2.5 px-3.5 py-3 rounded-xl text-sm"
                  style={{ background: '#F0FAF7', border: '1px solid #6EE7B7', color: '#059669' }}
                >
                  <IconCheckCircle />
                  <span>{successMsg}</span>
                </div>
              )}

              {/* ── Submit ── */}
              <button
                id="auth-submit-btn"
                type="submit"
                disabled={authLoading}
                className="btn-primary mt-1"
              >
                {authLoading ? (
                  <>
                    <IconSpinner />
                    {isSignUp ? 'Creating account...' : 'Signing in...'}
                  </>
                ) : (
                  isSignUp ? 'Create account' : 'Sign in'
                )}
              </button>
            </form>

            {/* ── Toggle mode ── */}
            <div className="mt-6 text-center">
              <p className="text-sm text-groww-text-secondary">
                {isSignUp ? 'Already have an account?' : "Don't have an account?"}{' '}
                <button
                  id="toggle-auth-mode-btn"
                  type="button"
                  onClick={() => {
                    setIsSignUp(!isSignUp)
                    resetForm()
                  }}
                  className="text-groww-green font-semibold hover:text-groww-green-dark transition-colors"
                >
                  {isSignUp ? 'Sign in' : 'Sign up'}
                </button>
              </p>
            </div>
          </div>

          <p className="mt-6 text-center text-xs text-groww-text-muted">
            By continuing, you agree to our Terms of Service and Privacy Policy.
          </p>
        </div>
      </div>
    </div>
  )
}

export default LoginPage
