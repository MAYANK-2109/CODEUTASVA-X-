import React from 'react'

export interface IconProps {
  className?: string
  size?: number | string
  style?: React.CSSProperties
}

// ── Alert & Status Vector Icons ──────────────────────────────────────────────

export const IconCritical: React.FC<IconProps> = ({ className = 'w-3 h-3', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="currentColor"
    aria-hidden="true"
  >
    <path
      fillRule="evenodd"
      d="M8 1.5a1.2 1.2 0 0 1 1.04.6l6 10.4a1.2 1.2 0 0 1-1.04 1.8H2a1.2 1.2 0 0 1-1.04-1.8l6-10.4A1.2 1.2 0 0 1 8 1.5zm0 3.75a.65.65 0 0 0-.65.65v3.2a.65.65 0 0 0 1.3 0v-3.2A.65.65 0 0 0 8 5.25zm0 5.75a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5z"
      clipRule="evenodd"
    />
  </svg>
)

export const IconWarning: React.FC<IconProps> = ({ className = 'w-3 h-3', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="currentColor"
    aria-hidden="true"
  >
    <path
      fillRule="evenodd"
      d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 3.5a.65.65 0 0 0-.65.65v3.6a.65.65 0 0 0 1.3 0v-3.6A.65.65 0 0 0 8 4.5zm0 6a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5z"
      clipRule="evenodd"
    />
  </svg>
)

export const IconInfo: React.FC<IconProps> = ({ className = 'w-3 h-3', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    aria-hidden="true"
  >
    <circle cx="8" cy="8" r="6" />
    <path d="M8 7v4.5M8 4.75h.01" strokeLinecap="round" />
  </svg>
)

// ── Stream Feed Status Icons ────────────────────────────────────────────────

export const IconLive: React.FC<IconProps> = ({ className = 'w-2.5 h-2.5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="currentColor"
    aria-hidden="true"
  >
    <circle cx="8" cy="8" r="5" />
  </svg>
)

export const IconDegraded: React.FC<IconProps> = ({ className = 'w-2.5 h-2.5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="currentColor"
    aria-hidden="true"
  >
    <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.75" />
    <path d="M8 2a6 6 0 0 0 0 12V2z" />
  </svg>
)

export const IconDown: React.FC<IconProps> = ({ className = 'w-2.5 h-2.5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="currentColor"
    aria-hidden="true"
  >
    <path d="M8 2.5L14 13.5H2L8 2.5Z" />
  </svg>
)

export const IconIdle: React.FC<IconProps> = ({ className = 'w-2.5 h-2.5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    aria-hidden="true"
  >
    <circle cx="8" cy="8" r="5" />
  </svg>
)

// ── Action & Confirmation Icons ─────────────────────────────────────────────

export const IconCheck: React.FC<IconProps> = ({ className = 'w-3.5 h-3.5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="3.5 8.5 6.5 11.5 12.5 4.5" />
  </svg>
)

export const IconCross: React.FC<IconProps> = ({ className = 'w-3.5 h-3.5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <line x1="4" y1="4" x2="12" y2="12" />
    <line x1="12" y1="4" x2="4" y2="12" />
  </svg>
)

// ── Financial Directional Vectors ───────────────────────────────────────────

export const IconTriangleUp: React.FC<IconProps> = ({ className = 'w-2.5 h-2.5 inline-block shrink-0', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 12 12"
    fill="currentColor"
    aria-hidden="true"
  >
    <path d="M6 2L10.5 9.5H1.5L6 2Z" />
  </svg>
)

export const IconTriangleDown: React.FC<IconProps> = ({ className = 'w-2.5 h-2.5 inline-block shrink-0', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 12 12"
    fill="currentColor"
    aria-hidden="true"
  >
    <path d="M6 10L1.5 2.5H10.5L6 10Z" />
  </svg>
)

export const IconTrendingUp: React.FC<IconProps> = ({ className = 'w-5 h-5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
    <polyline points="17 6 23 6 23 12" />
  </svg>
)

export const IconTrendingDown: React.FC<IconProps> = ({ className = 'w-5 h-5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" />
    <polyline points="17 18 23 18 23 12" />
  </svg>
)

// ── Financial Metric Category Vectors (Replaces Emoji Placeholders) ─────────

export const IconWallet: React.FC<IconProps> = ({ className = 'w-5 h-5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
    <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
    <path d="M18 12a2 2 0 0 0 0 4h4v-4z" />
  </svg>
)

export const IconChartBar: React.FC<IconProps> = ({ className = 'w-5 h-5', size, style }) => (
  <svg
    className={className}
    width={size}
    height={size}
    style={style}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <line x1="18" y1="20" x2="18" y2="10" />
    <line x1="12" y1="20" x2="12" y2="4" />
    <line x1="6" y1="20" x2="6" y2="14" />
    <line x1="2" y1="20" x2="22" y2="20" />
  </svg>
)
