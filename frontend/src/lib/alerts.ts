// The latest alerts, shared between the bell (which fetches them) and the
// dashboard's risk overview (which summarises them).
import { useSyncExternalStore } from 'react'

export type Severity = 'critical' | 'warning' | 'info'

export interface AlertHedge {
  action: 'hedge' | 'monitor' | 'none'
  expected_loss: number
  priced_in: number | null
  instrument: string | null
  notional: number | null
  cost: number | null
  confidence: string
  drill: boolean
}

export interface Alert {
  id: string
  severity: Severity
  category: string
  holding: string | null
  title: string
  detail: string
  recommendation: string
  basis: string
  hedge: AlertHedge | null
}

export interface AlertsResult {
  alerts: Alert[]
  portfolio_source: 'user' | 'sample'
  prices_as_of: string | null
  generated_at: string
  unavailable: string[]
  risk_model: { auc: number; tested_on: string; trained_on: string } | null
}

interface AlertsState {
  result: AlertsResult | null
  failed: boolean
  loading: boolean
}

let state: AlertsState = { result: null, failed: false, loading: true }
const listeners = new Set<() => void>()

export const publishAlerts = (change: Partial<AlertsState>) => {
  state = { ...state, ...change }
  listeners.forEach((listener) => listener())
}

const subscribe = (listener: () => void) => {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export const useAlerts = () => useSyncExternalStore(subscribe, () => state)

// Lets another part of the page open the bell's panel.
const OPEN_EVENT = 'alerts:open'
export const openAlerts = () => window.dispatchEvent(new Event(OPEN_EVENT))
export const onOpenAlerts = (handler: () => void) => {
  window.addEventListener(OPEN_EVENT, handler)
  return () => window.removeEventListener(OPEN_EVENT, handler)
}
