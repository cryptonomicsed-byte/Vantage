import React, { createContext, useContext, useState, useCallback, useRef, useEffect, ReactNode } from 'react'

// ══════════════════════════════════════════════════════════════════════════════
// Trading Terminal State — single source of truth for the unified terminal.
// Uses React Context + useReducer pattern (no external deps needed).
// ══════════════════════════════════════════════════════════════════════════════

export type FeedFilter = 'all' | 'signals' | 'alpha' | 'whales' | 'threats' | 'news' | 'debate'
export type ToolType = 'backtest' | 'arbitrage' | 'debate' | 'yields' | 'dex' | 'sim' | 'stress' | 'risk' | 'swarm' | 'custom' | 'journal' | 'chains' | 'eco' | 'flows' | 'retro'
export type PinePanelMode = 'nl' | 'code' | 'signal' | 'backtest'

export interface Signal {
  id: string
  symbol: string
  direction: 'BUY' | 'SELL' | 'LONG' | 'SHORT' | 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  conviction: number
  source: string
  timeframe?: string
  timestamp: string
  reasoning?: string
  is_anomaly?: boolean
  is_predictive?: boolean
  tags?: string[]
}

export interface AlphaMover {
  symbol: string
  change_pct: number
  volume: number
  source: string
  sparkline?: number[]
}

export interface WhaleTx {
  hash: string
  symbol: string
  amount: number
  amount_usd: number
  direction: 'inflow' | 'outflow'
  exchange?: string
  timestamp: string
}

export interface Threat {
  id: string
  name: string
  type: string
  conviction: number
  impact: string
  related_events?: string[]
  active: boolean
  timestamp: string
}

export interface NewsItem {
  title: string
  source: string
  sentiment: 'positive' | 'negative' | 'neutral'
  confidence: number
  url?: string
  timestamp: string
  symbols?: string[]
}

export interface DebateSummary {
  id: string
  topic: string
  consensus: string
  agents: { name: string; stance: string }[]
  conviction: number
  timestamp: string
}

export interface Position {
  symbol: string
  net_quantity: number
  avg_cost: number
  live_price: number
  market_value_usd: number
  unrealized_pnl_usd: number
  unrealized_pnl_pct: number
  realized_pnl_usd: number
}

export interface Order {
  id: number
  order_type: string
  side: string
  symbol: string
  chain: string
  quantity: number
  price: number | null
  filled_quantity: number
  avg_fill_price: number | null
  status: string
  tx_hash: string
  created_at: string
  executed_at: string | null
}

export interface PortfolioSummary {
  positions: Position[]
  total_market_value_usd: number
  total_unrealized_pnl_usd: number
  total_realized_pnl_usd: number
  total_pnl_usd: number
  open_positions: number
  filled_orders: number
  win_rate_pct: number
}

export interface Wallet {
  id: number
  label: string
  chain: string
  address: string
  exchange: string
  balances: { token: string; balance: number; value_usd: number }[]
}

export interface PineScript {
  id: number
  name: string
  code: string
  description: string
  category: string
  created_at: string
}

interface TradingState {
  activePair: string
  activeTimeframe: string
  chartTimestamp: number | null
  signals: Signal[]
  alphaMovers: AlphaMover[]
  whaleTransactions: WhaleTx[]
  activeThreats: Threat[]
  newsItems: NewsItem[]
  debateSummaries: DebateSummary[]
  feedFilter: FeedFilter
  riskLevel: number
  positions: Position[]
  orders: Order[]
  portfolio: PortfolioSummary | null
  wallets: Wallet[]
  activeWalletId: number | null
  drawerOpen: boolean
  drawerTool: ToolType | null
  pinePanelOpen: boolean
  pineMode: PinePanelMode
  activePineScripts: PineScript[]
  alertModalOpen: boolean
  searchQuery: string
  sourcesStatus: Record<string, { active: boolean; lastCall: string; errorRate: number }>
}

type TradingAction =
  | { type: 'SET_PAIR'; pair: string }
  | { type: 'SET_TIMEFRAME'; tf: string }
  | { type: 'NAVIGATE_TO'; pair: string; tf: string; timestamp: number }
  | { type: 'SET_SIGNALS'; signals: Signal[] }
  | { type: 'SET_ALPHA_MOVERS'; movers: AlphaMover[] }
  | { type: 'SET_WHALE_TXS'; txs: WhaleTx[] }
  | { type: 'SET_THREATS'; threats: Threat[] }
  | { type: 'SET_NEWS'; items: NewsItem[] }
  | { type: 'SET_DEBATES'; debates: DebateSummary[] }
  | { type: 'SET_FEED_FILTER'; filter: FeedFilter }
  | { type: 'SET_RISK_LEVEL'; level: number }
  | { type: 'SET_POSITIONS'; positions: Position[] }
  | { type: 'SET_ORDERS'; orders: Order[] }
  | { type: 'SET_PORTFOLIO'; portfolio: PortfolioSummary | null }
  | { type: 'SET_WALLETS'; wallets: Wallet[] }
  | { type: 'SET_ACTIVE_WALLET'; id: number }
  | { type: 'TOGGLE_DRAWER'; tool?: ToolType }
  | { type: 'CLOSE_DRAWER' }
  | { type: 'TOGGLE_PINE_PANEL' }
  | { type: 'SET_PINE_MODE'; mode: PinePanelMode }
  | { type: 'SET_PINE_SCRIPTS'; scripts: PineScript[] }
  | { type: 'TOGGLE_ALERT_MODAL' }
  | { type: 'SET_SEARCH'; query: string }

const initialState: TradingState = {
  activePair: 'BTC/USDT',
  activeTimeframe: '1h',
  chartTimestamp: null,
  signals: [],
  alphaMovers: [],
  whaleTransactions: [],
  activeThreats: [],
  newsItems: [],
  debateSummaries: [],
  feedFilter: 'all',
  riskLevel: 0.5,
  positions: [],
  orders: [],
  portfolio: null,
  wallets: [],
  activeWalletId: null,
  drawerOpen: false,
  drawerTool: null,
  pinePanelOpen: false,
  pineMode: 'nl',
  activePineScripts: [],
  alertModalOpen: false,
  searchQuery: '',
  sourcesStatus: {},
}

function tradingReducer(state: TradingState, action: TradingAction): TradingState {
  switch (action.type) {
    case 'SET_PAIR':
      return { ...state, activePair: action.pair, chartTimestamp: null }
    case 'SET_TIMEFRAME':
      return { ...state, activeTimeframe: action.tf }
    case 'NAVIGATE_TO':
      return { ...state, activePair: action.pair, activeTimeframe: action.tf, chartTimestamp: action.timestamp }
    case 'SET_SIGNALS':
      return { ...state, signals: action.signals }
    case 'SET_ALPHA_MOVERS':
      return { ...state, alphaMovers: action.movers }
    case 'SET_WHALE_TXS':
      return { ...state, whaleTransactions: action.txs }
    case 'SET_THREATS':
      return { ...state, activeThreats: action.threats }
    case 'SET_NEWS':
      return { ...state, newsItems: action.items }
    case 'SET_DEBATES':
      return { ...state, debateSummaries: action.debates }
    case 'SET_FEED_FILTER':
      return { ...state, feedFilter: action.filter }
    case 'SET_RISK_LEVEL':
      return { ...state, riskLevel: action.level }
    case 'SET_POSITIONS':
      return { ...state, positions: action.positions }
    case 'SET_ORDERS':
      return { ...state, orders: action.orders }
    case 'SET_PORTFOLIO':
      return { ...state, portfolio: action.portfolio }
    case 'SET_WALLETS':
      return { ...state, wallets: action.wallets }
    case 'SET_ACTIVE_WALLET':
      return { ...state, activeWalletId: action.id }
    case 'TOGGLE_DRAWER':
      if (state.drawerOpen && state.drawerTool === action.tool) {
        return { ...state, drawerOpen: false, drawerTool: null }
      }
      return { ...state, drawerOpen: true, drawerTool: action.tool || state.drawerTool || 'backtest' }
    case 'CLOSE_DRAWER':
      return { ...state, drawerOpen: false, drawerTool: null }
    case 'TOGGLE_PINE_PANEL':
      return { ...state, pinePanelOpen: !state.pinePanelOpen }
    case 'SET_PINE_MODE':
      return { ...state, pineMode: action.mode }
    case 'SET_PINE_SCRIPTS':
      return { ...state, activePineScripts: action.scripts }
    case 'TOGGLE_ALERT_MODAL':
      return { ...state, alertModalOpen: !state.alertModalOpen }
    case 'SET_SEARCH':
      return { ...state, searchQuery: action.query }
    default:
      return state
  }
}

interface TradingContextType {
  state: TradingState
  dispatch: React.Dispatch<TradingAction>
  navigateTo: (pair: string, tf?: string, timestamp?: number) => void
  toggleDrawer: (tool?: ToolType) => void
  agentKey: () => string
  tradingApi: (path: string, opts?: RequestInit) => Promise<Response>
}

const TradingContext = createContext<TradingContextType | null>(null)

export function useTradingStore() {
  const ctx = useContext(TradingContext)
  if (!ctx) throw new Error('useTradingStore must be inside TradingProvider')
  return ctx
}

export function TradingProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = React.useReducer(tradingReducer, initialState)

  const navigateTo = useCallback((pair: string, tf?: string, timestamp?: number) => {
    dispatch({ type: 'NAVIGATE_TO', pair, tf: tf || state.activeTimeframe, timestamp: timestamp || Date.now() / 1000 })
  }, [state.activeTimeframe])

  const toggleDrawer = useCallback((tool?: ToolType) => {
    dispatch({ type: 'TOGGLE_DRAWER', tool })
  }, [])

  const agentKey = useCallback(() => localStorage.getItem('vantage_api_key') || '', [])

  const tradingApi = useCallback(async (path: string, opts: RequestInit = {}): Promise<Response> => {
    return fetch(`/api/trading${path}`, {
      ...opts,
      headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json', ...(opts.headers || {}) },
    })
  }, [agentKey])

  return (
    <TradingContext.Provider value={{ state, dispatch, navigateTo, toggleDrawer, agentKey, tradingApi }}>
      {children}
    </TradingContext.Provider>
  )
}
