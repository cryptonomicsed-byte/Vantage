import React, { useEffect } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { TradingProvider, useTradingStore, Signal, AlphaMover, WhaleTx, Threat, DebateSummary } from './tradingStore'
import TerminalTopBar from './TerminalTopBar'
import IntelFeed from './IntelFeed'
import ChartWorkspace from './ChartWorkspace'
import ExecutionPanel from './ExecutionPanel'
import ToolDrawer from './ToolDrawer'
import AlertModal from './AlertModal'

// ══════════════════════════════════════════════════════════════════════════════
// TradingTerminal — the unified workspace. Chart is always visible.
// Left (intel) and right (execution) panels collapse so just the chart shows.
//
// GRID:
//   [left] | 1fr | [right]   (columns; left/right → 0 when collapsed)
//   52px  | 1fr | 0px        (rows — bottom drawer collapses to 0 when closed)
// ══════════════════════════════════════════════════════════════════════════════

function apiHeaders(): Record<string, string> | undefined {
  const key = localStorage.getItem('vantage_api_key')
  return key ? { 'X-Agent-Key': key } : undefined
}

// Normalize a raw signal (shapes vary by source: radar, stix, ingest, etc.)
// into the Signal shape IntelFeed/ChartWorkspace render.
function normalizeSignal(raw: any, i: number): Signal {
  const change = raw.change_6h ?? raw.change_24h ?? raw.price_change_pct ?? raw.change ?? 0
  let direction: Signal['direction'] = raw.direction
  if (!direction) {
    direction = change > 0 ? 'BULLISH' : change < 0 ? 'BEARISH' : 'NEUTRAL'
  } else {
    direction = String(direction).toUpperCase() as Signal['direction']
  }
  // Conviction arrives on mixed scales (0-1 from ingest, 0-8 from radar score).
  const rawConv = Number(raw.conviction ?? raw.confidence ?? raw.score ?? 0)
  const conviction = rawConv <= 1 ? rawConv : Math.min(1, rawConv / 8)
  // Timestamp: ISO string, or unix seconds (ts), or ms.
  let timestamp = raw.timestamp
  if (!timestamp && raw.ts) timestamp = new Date(raw.ts * 1000).toISOString()
  if (!timestamp) timestamp = new Date().toISOString()
  const src = String(raw.source || '')
  const type = String(raw.type || '')
  const isThreat = /stix|vuln|threat|poison|sanction|rug/i.test(src + type)
  return {
    id: raw.id || `${raw.symbol || 'sig'}-${raw.ts || i}`,
    symbol: (raw.symbol || raw.pair || '—').toUpperCase(),
    direction,
    conviction,
    source: src || 'unknown',
    timeframe: raw.timeframe,
    timestamp,
    reasoning: raw.reasoning || raw.detail || (raw.type ? `${raw.type}${raw.score ? ` · score ${raw.score}` : ''}` : ''),
    is_anomaly: raw.is_anomaly ?? isThreat,
    is_predictive: raw.is_predictive ?? src === 'predictor',
    tags: raw.tags,
  }
}

function TerminalInner() {
  const { state, dispatch } = useTradingStore()

  useEffect(() => {
    fetchIntel()
    fetchTradingData()
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {})
    }
    const intelInterval = setInterval(fetchIntel, 30000)
    const tradingInterval = setInterval(fetchTradingData, 15000)
    const alertInterval = setInterval(checkPriceAlerts, 20000)
    return () => { clearInterval(intelInterval); clearInterval(tradingInterval); clearInterval(alertInterval) }
  }, [])

  // Poll live prices for any saved price alerts; fire a browser notification
  // and drop the alert when its threshold is crossed.
  async function checkPriceAlerts() {
    let alerts: any[]
    try { alerts = JSON.parse(localStorage.getItem('vantage_price_alerts') || '[]') } catch { return }
    if (!alerts.length) return
    const pairs = [...new Set(alerts.map(a => a.pair))]
    const prices: Record<string, number> = {}
    await Promise.all(pairs.map(async pair => {
      const sym = String(pair).split('/')[0]
      try {
        const r = await fetch(`/api/trading/markets/${sym}/price`)
        if (r.ok) { const d = await r.json(); if (d.price) prices[pair] = d.price }
      } catch {}
    }))
    const remaining = alerts.filter(a => {
      const p = prices[a.pair]
      if (!p) return true
      const crossed = a.direction === 'above' ? p >= a.price : p <= a.price
      if (crossed && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        new Notification('Vantage price alert', { body: `${a.pair} crossed ${a.direction} $${a.price} (now $${p})` })
      }
      return !crossed
    })
    if (remaining.length !== alerts.length) {
      localStorage.setItem('vantage_price_alerts', JSON.stringify(remaining))
    }
  }

  async function fetchIntel() {
    const headers = apiHeaders()
    try {
      const [signalsR, alphaR, whalesR, debateR] = await Promise.all([
        fetch('/api/intel/signals', { headers }),
        fetch('/api/intel/alpha', { headers }),
        fetch('/api/intel/whales', { headers }),
        fetch('/api/agents/debates', { headers }),
      ])

      // Signals — normalize mixed shapes, and derive threats from them.
      if (signalsR.ok) {
        const d = await signalsR.json()
        const raw: any[] = d.signals || (Array.isArray(d) ? d : [])
        const signals = raw.map(normalizeSignal)
        dispatch({ type: 'SET_SIGNALS', signals })

        const threats: Threat[] = signals
          .filter(s => s.is_anomaly)
          .map(s => ({
            id: s.id,
            name: `${s.symbol} — ${s.source}`,
            type: 'signal',
            conviction: s.conviction,
            impact: s.conviction > 0.66 ? 'high' : s.conviction > 0.33 ? 'medium' : 'low',
            related_events: s.reasoning ? [s.reasoning] : undefined,
            active: true,
            timestamp: s.timestamp,
          }))
        dispatch({ type: 'SET_THREATS', threats })
      } else {
        console.error('fetchIntel: /api/intel/signals', signalsR.status)
      }

      // Alpha movers
      if (alphaR.ok) {
        const d = await alphaR.json()
        const items: any[] = d.items || (Array.isArray(d) ? d : [])
        const movers: AlphaMover[] = items.map(m => ({
          symbol: (m.symbol || '—').toUpperCase(),
          change_pct: Number(m.change_pct ?? m.change_24h ?? 0),
          volume: Number(m.volume ?? m.volume_24h ?? 0),
          source: m.signal || 'movers',
        }))
        dispatch({ type: 'SET_ALPHA_MOVERS', movers })
      }

      // Whale transactions (BTC mempool)
      if (whalesR.ok) {
        const d = await whalesR.json()
        const txs: any[] = d.transactions || (Array.isArray(d) ? d : [])
        const whales: WhaleTx[] = txs.map(t => ({
          hash: t.txid || t.hash || '',
          symbol: 'BTC',
          amount: Number(t.value_btc ?? t.amount ?? 0),
          amount_usd: Number(t.value_usd ?? t.amount_usd ?? 0),
          direction: t.direction || 'inflow',
          exchange: t.exchange || 'mempool',
          timestamp: t.timestamp || new Date().toISOString(),
        }))
        dispatch({ type: 'SET_WHALE_TXS', txs: whales })
      }

      // Debates
      if (debateR.ok) {
        const d = await debateR.json()
        const rows: any[] = d.debates || d.items || (Array.isArray(d) ? d : [])
        const debates: DebateSummary[] = rows.map((x: any) => ({
          id: String(x.id ?? x.broadcast_id ?? ''),
          topic: x.topic || x.title || 'Debate',
          consensus: x.consensus || x.status || 'ongoing',
          agents: Array.isArray(x.agents)
            ? x.agents.map((a: any) => ({ name: a.name || a.agent_name || 'agent', stance: a.stance || a.position || '' }))
            : [],
          conviction: Number(x.conviction ?? 0.5),
          timestamp: x.timestamp || x.created_at || new Date().toISOString(),
        }))
        dispatch({ type: 'SET_DEBATES', debates })
      }
    } catch (e) {
      console.error('fetchIntel failed', e)
    }
  }

  async function fetchTradingData() {
    const headers = apiHeaders()
    if (!headers) return
    try {
      const [posR, portR, walR] = await Promise.all([
        fetch('/api/trading/positions', { headers }),
        fetch('/api/trading/portfolio', { headers }),
        fetch('/api/trading/wallets', { headers }),
      ])
      if (posR.ok) {
        const d = await posR.json()
        dispatch({ type: 'SET_POSITIONS', positions: d.positions || [] })
      }
      if (portR.ok) {
        const d = await portR.json()
        dispatch({ type: 'SET_PORTFOLIO', portfolio: d })
      }
      if (walR.ok) {
        const d = await walR.json()
        dispatch({ type: 'SET_WALLETS', wallets: d.wallets || [] })
      }
    } catch {}
  }

  const leftW = state.leftCollapsed ? '0px' : '280px'
  const rightW = state.rightCollapsed ? '0px' : '300px'

  return (
    <div style={{ ...styles.container, gridTemplateColumns: `${leftW} 1fr ${rightW}` }}>
      {/* TOP BAR */}
      <div style={styles.topBar}>
        <TerminalTopBar />
      </div>

      {/* LEFT PANEL */}
      <div style={{ ...styles.leftPanel, width: state.leftCollapsed ? 0 : undefined }}>
        <IntelFeed />
      </div>

      {/* CENTER — chart always visible, with collapse toggles */}
      <div style={styles.center}>
        <button
          style={{ ...styles.collapseBtn, left: 4 }}
          onClick={() => dispatch({ type: 'TOGGLE_LEFT_PANEL' })}
          title={state.leftCollapsed ? 'Show intel panel' : 'Hide intel panel'}
        >
          {state.leftCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
        <button
          style={{ ...styles.collapseBtn, right: 4 }}
          onClick={() => dispatch({ type: 'TOGGLE_RIGHT_PANEL' })}
          title={state.rightCollapsed ? 'Show execution panel' : 'Hide execution panel'}
        >
          {state.rightCollapsed ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
        </button>
        <ChartWorkspace />
      </div>

      {/* RIGHT PANEL */}
      <div style={{ ...styles.rightPanel, width: state.rightCollapsed ? 0 : undefined }}>
        <ExecutionPanel />
      </div>

      {/* BOTTOM DRAWER */}
      <div style={{
        ...styles.bottomPanel,
        height: state.drawerOpen ? 400 : 0,
        opacity: state.drawerOpen ? 1 : 0,
      }}>
        <ToolDrawer />
      </div>

      {/* Alert / notification modal (overlay) */}
      {state.alertModalOpen && <AlertModal />}
    </div>
  )
}

export default function TradingTerminal() {
  return (
    <TradingProvider>
      <TerminalInner />
    </TradingProvider>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'grid',
    gridTemplateRows: '52px 1fr 0px',
    height: 'calc(100vh - 120px)',
    width: '100%',
    overflow: 'hidden',
    background: '#0a0a14',
    color: '#e0e0e0',
    fontFamily: 'Inter, -apple-system, system-ui, sans-serif',
    fontSize: 13,
    borderRadius: 12,
    border: '1px solid rgba(255,255,255,0.08)',
    transition: 'grid-template-columns 0.25s cubic-bezier(0.4,0,0.2,1)',
    position: 'relative',
  },
  topBar: {
    gridColumn: '1 / -1',
    gridRow: 1,
    borderBottom: '1px solid rgba(255,255,255,0.08)',
    display: 'flex',
    alignItems: 'center',
    padding: '0 16px',
    background: 'rgba(10,10,20,0.95)',
    backdropFilter: 'blur(12px)',
    zIndex: 10,
  },
  leftPanel: {
    gridColumn: 1,
    gridRow: 2,
    borderRight: '1px solid rgba(255,255,255,0.08)',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
    background: 'rgba(10,10,20,0.7)',
  },
  center: {
    gridColumn: 2,
    gridRow: 2,
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
    position: 'relative',
  },
  rightPanel: {
    gridColumn: 3,
    gridRow: 2,
    borderLeft: '1px solid rgba(255,255,255,0.08)',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
    background: 'rgba(10,10,20,0.7)',
  },
  bottomPanel: {
    gridColumn: '1 / -1',
    gridRow: 3,
    borderTop: '1px solid rgba(255,255,255,0.08)',
    overflow: 'hidden',
    transition: 'height 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease',
    background: 'rgba(10,10,20,0.95)',
    backdropFilter: 'blur(12px)',
  },
  collapseBtn: {
    position: 'absolute',
    top: '50%',
    transform: 'translateY(-50%)',
    zIndex: 20,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 22,
    height: 44,
    background: 'rgba(20,20,42,0.9)',
    border: '1px solid rgba(255,255,255,0.12)',
    borderRadius: 6,
    color: '#9ca3af',
    cursor: 'pointer',
    backdropFilter: 'blur(8px)',
  },
}
