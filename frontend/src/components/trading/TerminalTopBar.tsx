import React, { useState, useRef, useEffect, useCallback } from 'react'
import { ChevronDown, FileText, Bell, Mic, Search, X } from 'lucide-react'
import { useTradingStore } from './tradingStore'

// ══════════════════════════════════════════════════════════════════════════════
// TerminalTopBar — pair selector, timeframes, Pine, alerts, voice, search.
// Also shows live BTC/ETH/SOL ticker prices + source health indicator.
// Always visible at the top of the terminal.
// ══════════════════════════════════════════════════════════════════════════════

const PAIRS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'ADA/USDT', 'JUP/USDT', 'DOGE/USDT', 'XRP/USDT', 'MATIC/USDT']
const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1D', '1W']
const TICKER_SYMBOLS = ['BTC', 'ETH', 'SOL']

export default function TerminalTopBar() {
  const { state, dispatch, toggleDrawer } = useTradingStore()
  const [pairOpen, setPairOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [ticker, setTicker] = useState<Record<string, { price: number; change: number }>>({})
  const [sourcesOnline, setSourcesOnline] = useState<number>(0)
  const [sourcesTotal, setSourcesTotal] = useState<number>(0)
  const pairRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (pairRef.current && !pairRef.current.contains(e.target as Node)) setPairOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  // Fetch live ticker prices
  const fetchTicker = useCallback(async () => {
    try {
      const results = await Promise.all(
        TICKER_SYMBOLS.map(sym =>
          fetch(`/api/trading/markets/${sym}/price`).then(r => r.ok ? r.json() : null).catch(() => null)
        )
      )
      const t: Record<string, { price: number; change: number }> = {}
      results.forEach((r, i) => {
        if (r?.price) t[TICKER_SYMBOLS[i]] = { price: r.price, change: 0 }
      })
      setTicker(t)
    } catch {}
  }, [])

  // Fetch source health
  const fetchSources = useCallback(async () => {
    try {
      const r = await fetch('/api/intel/sources-registry')
      if (r.ok) {
        const d = await r.json()
        setSourcesTotal(d.total || 0)
        setSourcesOnline(d.integrated || 0)
      }
    } catch {}
  }, [])

  useEffect(() => {
    fetchTicker()
    fetchSources()
    const t = setInterval(fetchTicker, 15000)
    const s = setInterval(fetchSources, 60000)
    return () => { clearInterval(t); clearInterval(s) }
  }, [fetchTicker, fetchSources])

  const filteredPairs = PAIRS.filter(p => p.toLowerCase().includes(searchQuery.toLowerCase()))

  return (
    <div style={styles.topBarInner}>
      {/* Pair Selector */}
      <div ref={pairRef} style={styles.pairSelector}>
        <button style={styles.pairButton} onClick={() => setPairOpen(!pairOpen)}>
          <span style={styles.pairSymbol}>{state.activePair}</span>
          <ChevronDown size={14} style={{ opacity: 0.5 }} />
        </button>
        {pairOpen && (
          <div style={styles.pairDropdown}>
            <div style={styles.pairSearch}>
              <Search size={12} style={{ opacity: 0.4 }} />
              <input
                autoFocus
                style={styles.pairSearchInput}
                placeholder="Search pairs..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
            </div>
            {filteredPairs.map(p => (
              <button
                key={p}
                style={{
                  ...styles.pairOption,
                  background: p === state.activePair ? 'rgba(0,245,255,0.1)' : 'transparent',
                  color: p === state.activePair ? '#00f5ff' : '#e0e0e0',
                }}
                onClick={() => { dispatch({ type: 'SET_PAIR', pair: p }); setPairOpen(false); setSearchQuery('') }}
              >
                {p}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Timeframes */}
      <div style={styles.timeframes}>
        {TIMEFRAMES.map(tf => (
          <button
            key={tf}
            style={{
              ...styles.tfButton,
              background: tf === state.activeTimeframe ? 'rgba(0,245,255,0.15)' : 'transparent',
              color: tf === state.activeTimeframe ? '#00f5ff' : '#6b7280',
              fontWeight: tf === state.activeTimeframe ? 700 : 400,
            }}
            onClick={() => dispatch({ type: 'SET_TIMEFRAME', tf })}
          >
            {tf}
          </button>
        ))}
      </div>

      {/* Live Ticker */}
      <div style={styles.ticker}>
        {TICKER_SYMBOLS.map(sym => {
          const t = ticker[sym]
          const changeColor = t?.change ? (t.change >= 0 ? '#39ff14' : '#ff2d4a') : '#6b7280'
          return (
            <div key={sym} style={styles.tickerItem}>
              <span style={{ fontWeight: 700, fontSize: 11, color: '#9ca3af' }}>{sym}</span>
              <span style={{ fontSize: 11, fontFamily: 'monospace', color: changeColor, fontWeight: 600 }}>
                {t ? '$' + (t.price >= 1000 ? t.price.toLocaleString() : t.price >= 1 ? t.price.toFixed(2) : t.price.toFixed(6)) : '—'}
              </span>
            </div>
          )
        })}
      </div>

      {/* Market Pulse — fear/greed + breadth */}
      {state.pulse && (() => {
        const fg = state.pulse.fear_greed
        const c = fg >= 60 ? '#39ff14' : fg >= 45 ? '#ffaa00' : '#ff2d4a'
        return (
          <div
            style={styles.pulse}
            title={`Market breadth: ${state.pulse.gainers_pct}% green · avg 24h ${state.pulse.avg_change_24h}%`}
          >
            <span style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: 0.5 }}>F&G</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: c, fontFamily: 'monospace' }}>{fg}</span>
            <span style={{ fontSize: 10, color: c, textTransform: 'capitalize' }}>{state.pulse.mood || state.pulse.overall}</span>
          </div>
        )
      })()}

      {/* Source health indicator */}
      {sourcesTotal > 0 && (
        <div
          title={`${sourcesOnline}/${sourcesTotal} data sources active`}
          style={{
            ...styles.sourceDot,
            background: sourcesOnline >= sourcesTotal * 0.8 ? '#39ff14' : sourcesOnline >= sourcesTotal * 0.5 ? '#ffaa00' : '#ff2d4a',
          }}
        />
      )}

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Action Buttons */}
      <div style={styles.actions}>
        <button
          style={{
            ...styles.actionButton,
            background: state.pinePanelOpen ? 'rgba(0,245,255,0.12)' : 'rgba(255,255,255,0.05)',
            color: state.pinePanelOpen ? '#00f5ff' : '#9ca3af',
            borderColor: state.pinePanelOpen ? 'rgba(0,245,255,0.3)' : 'rgba(255,255,255,0.08)',
          }}
          onClick={() => dispatch({ type: 'TOGGLE_PINE_PANEL' })}
          title="Pine Script"
        >
          <FileText size={16} />
          <span style={styles.actionLabel}>Pine</span>
        </button>

        <button
          style={styles.actionButton}
          onClick={() => dispatch({ type: 'TOGGLE_ALERT_MODAL' })}
          title="Alerts"
        >
          <Bell size={16} />
        </button>

        <button style={styles.actionButton} title="Voice mode">
          <Mic size={16} />
        </button>

        <div style={styles.searchBox}>
          <Search size={13} style={{ opacity: 0.4 }} />
          <input
            style={styles.searchInput}
            placeholder="Search..."
            value={state.searchQuery}
            onChange={e => dispatch({ type: 'SET_SEARCH', query: e.target.value })}
          />
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  topBarInner: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    width: '100%',
    height: '100%',
  },
  pairSelector: {
    position: 'relative',
  },
  pairButton: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 6,
    padding: '6px 12px',
    color: '#e0e0e0',
    cursor: 'pointer',
    fontSize: 14,
    fontWeight: 700,
    fontFamily: 'inherit',
  },
  pairSymbol: {
    fontFamily: 'monospace',
    letterSpacing: 0.5,
  },
  pairDropdown: {
    position: 'absolute',
    top: '100%',
    left: 0,
    marginTop: 4,
    background: '#14142a',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    padding: 4,
    minWidth: 180,
    maxHeight: 300,
    overflowY: 'auto',
    zIndex: 100,
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
  },
  pairSearch: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 8px',
    borderBottom: '1px solid rgba(255,255,255,0.08)',
    marginBottom: 2,
  },
  pairSearchInput: {
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#e0e0e0',
    fontSize: 12,
    width: '100%',
    fontFamily: 'inherit',
  },
  pairOption: {
    display: 'block',
    width: '100%',
    textAlign: 'left',
    padding: '6px 8px',
    border: 'none',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
    fontFamily: 'inherit',
  },
  timeframes: {
    display: 'flex',
    gap: 2,
    background: 'rgba(255,255,255,0.03)',
    borderRadius: 6,
    padding: 2,
  },
  tfButton: {
    padding: '4px 10px',
    border: 'none',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
    fontFamily: 'monospace',
    letterSpacing: 0.5,
    transition: 'all 0.15s',
  },
  actions: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  actionButton: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    padding: '6px 10px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6,
    color: '#9ca3af',
    cursor: 'pointer',
    fontSize: 12,
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  },
  actionLabel: {
    fontSize: 12,
  },
  searchBox: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '5px 10px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6,
    marginLeft: 4,
  },
  searchInput: {
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#e0e0e0',
    fontSize: 12,
    width: 140,
    fontFamily: 'inherit',
  },
  ticker: {
    display: 'flex',
    gap: 16,
    alignItems: 'center',
  },
  tickerItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  sourceDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    flexShrink: 0,
    marginLeft: 8,
    boxShadow: '0 0 6px currentColor',
  },
  pulse: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    padding: '3px 10px',
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6,
  },
}
