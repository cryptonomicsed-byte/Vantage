import React, { useEffect } from 'react'
import { TradingProvider, useTradingStore } from './tradingStore'
import TerminalTopBar from './TerminalTopBar'
import IntelFeed from './IntelFeed'
import ChartWorkspace from './ChartWorkspace'
import ExecutionPanel from './ExecutionPanel'
import ToolDrawer from './ToolDrawer'

// ══════════════════════════════════════════════════════════════════════════════
// TradingTerminal — the unified workspace. Chart is always visible.
// Everything orbits the chart. No tabs. No navigation hell.
//
// GRID:
//   280px | 1fr | 300px  (columns)
//   52px  | 1fr | 0px    (rows — bottom panel collapses to 0 when closed)
// ══════════════════════════════════════════════════════════════════════════════

function TerminalInner() {
  const { state, dispatch } = useTradingStore()

  // Fetch initial data on mount
  useEffect(() => {
    fetchIntel()
    fetchTradingData()
    const intelInterval = setInterval(fetchIntel, 30000)
    const tradingInterval = setInterval(fetchTradingData, 15000)
    return () => { clearInterval(intelInterval); clearInterval(tradingInterval) }
  }, [])

  async function fetchIntel() {
    // /api/intel/* requires X-Agent-Key (PR #39). The global fetch
    // interceptor (installApiKeyInterceptor, wired in main.tsx) attaches it
    // automatically, but setting it explicitly here too means this still
    // works even if that interceptor is ever bypassed or removed.
    const apiKey = localStorage.getItem('vantage_api_key')
    const headers = apiKey ? { 'X-Agent-Key': apiKey } : undefined
    try {
      const [signalsR, intelR] = await Promise.all([
        fetch('/api/intel/signals', { headers }),
        fetch('/api/intel', { headers }),
      ])
      if (signalsR.ok) {
        const d = await signalsR.json()
        dispatch({ type: 'SET_SIGNALS', signals: d.signals || d || [] })
      } else {
        console.error('fetchIntel: /api/intel/signals failed', signalsR.status)
      }
      if (intelR.ok) {
        const d = await intelR.json()
        if (d.whales) dispatch({ type: 'SET_WHALE_TXS', txs: d.whales.transactions || [] })
        if (d.threats) dispatch({ type: 'SET_THREATS', threats: d.threats.active || [] })
        if (d.news) dispatch({ type: 'SET_NEWS', items: d.news.items || [] })
      } else {
        console.error('fetchIntel: /api/intel failed', intelR.status)
      }
    } catch (e) {
      console.error('fetchIntel: request failed', e)
    }
  }

  async function fetchTradingData() {
    const apiKey = localStorage.getItem('vantage_api_key')
    if (!apiKey) return
    try {
      const [posR, portR, walR] = await Promise.all([
        fetch('/api/trading/positions', { headers: { 'X-Agent-Key': apiKey } }),
        fetch('/api/trading/portfolio', { headers: { 'X-Agent-Key': apiKey } }),
        fetch('/api/trading/wallets', { headers: { 'X-Agent-Key': apiKey } }),
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

  return (
    <div style={styles.container}>
      {/* TOP BAR — row 1, col 1-3 */}
      <div style={styles.topBar}>
        <TerminalTopBar />
      </div>

      {/* LEFT PANEL — col 1, row 2 */}
      <div style={styles.leftPanel}>
        <IntelFeed />
      </div>

      {/* CENTER — col 2, row 2 (chart always visible) */}
      <div style={styles.center}>
        <ChartWorkspace />
      </div>

      {/* RIGHT PANEL — col 3, row 2 */}
      <div style={styles.rightPanel}>
        <ExecutionPanel />
      </div>

      {/* BOTTOM PANEL — col 1-3, row 3 (drawer, collapses to 0) */}
      <div style={{
        ...styles.bottomPanel,
        height: state.drawerOpen ? 400 : 0,
        opacity: state.drawerOpen ? 1 : 0,
      }}>
        <ToolDrawer />
      </div>
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
    gridTemplateColumns: '280px 1fr 300px',
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
}
