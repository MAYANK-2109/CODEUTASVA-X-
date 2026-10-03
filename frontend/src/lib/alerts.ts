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

export type SolutionAction = 'hedge' | 'trim' | 'rebalance' | 'watch' | 'hold' | 'review'

// What to do about an alert: one sized action, the steps, and the figures behind it.
export interface Solution {
  action: SolutionAction
  headline: string
  steps: string[]
  figures: { label: string; value: string }[]
  alternative: string | null
  // The rule that picked the action, with the numbers it compared.
  why: string
}

// One step of an alert's evidence trail: what was found, and how.
export interface TrailStep {
  title: string
  finding: string
  method: string | null
}

// One holding as the impact model scores it for the coming sessions.
export interface HoldingRisk {
  name: string
  weight: number
  fall: number
  elevated: boolean
  downside: number
  usual_downside: number
  downside_amount: number
}

export interface RiskModel {
  name: string
  auc: number
  tested_on: string
  trained_on: string
  auc_price_only?: number
  fall_size?: number
  horizon_sessions?: number
  rate_when_elevated?: number
  rate_otherwise?: number
}

export interface Alert {
  id: string
  severity: Severity
  category: string
  holding: string | null
  title: string
  detail: string
  recommendation: string
  solution: Solution
  basis: string
  hedge: AlertHedge | null
  trail: TrailStep[]
}

export interface AlertsResult {
  alerts: Alert[]
  portfolio_source: 'user' | 'sample'
  prices_as_of: string | null
  generated_at: string
  unavailable: string[]
  risk_ranking: HoldingRisk[]
  risk_model: RiskModel | null
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
