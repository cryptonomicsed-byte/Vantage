import React, { useState, useCallback } from 'react'
import { Wallet, TrendingUp, TrendingDown, Shield, BookOpen, PieChart, Activity } from 'lucide-react'
import { useTradingStore } from './tradingStore'

// ══════════════════════════════════════════════════════════════════════════════
// ExecutionPanel — right panel. Risk slider always visible. Quick trade,
// positions, portfolio, wallet selector.
// ══════════════════════════════════════════════════════════════════════════════

function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(Number(n))) return '—'
  const v = Number(n)
  if (Math.abs(v) >= 1_000_000) return '$' + (v / 1_000_000).toFixed(2) + 'M'
  if (Math.abs(v) >= 1_000) return '$' + (v / 1_000).toFixed(2) + 'K'
  return '$' + v.toFixed(2)
}

export default function ExecutionPanel() {
  const { state, dispatch, tradingApi, toggleDrawer } = useTradingStore()
  const [quickTradeSide, setQuickTradeSide] = useState<'buy' | 'sell'>('buy')
  const [quickTradeSize, setQuickTradeSize] = useState('')
  const [tradeMsg, setTradeMsg] = useState('')

  const portfolio = state.portfolio
  const activeWallet = state.wallets.find(w => w.id === state.activeWalletId)

  const handleQuickTrade = useCallback(async () => {
    const apiKey = localStorage.getItem('vantage_api_key')
    if (!apiKey) { setTradeMsg('Connect API key first'); return }
    const size = parseFloat(quickTradeSize)
    if (!size || size <= 0) { setTradeMsg('Enter valid size'); return }

    try {
      const r = await tradingApi('/orders', {
        method: 'POST',
        body: JSON.stringify({
          order_type: 'market',
          side: quickTradeSide.toUpperCase(),
          symbol: state.activePair,
          chain: activeWallet?.chain || 'solana',
          quantity: size,
          trigger_reason: 'manual_quick_trade',
        }),
      })
      if (r.ok) {
        setTradeMsg(`Order placed: ${quickTradeSide.toUpperCase()} ${size} ${state.activePair}`)
        setQuickTradeSize('')
      } else {
        const d = await r.json().catch(() => ({}))
        setTradeMsg(d.detail || 'Order failed')
      }
    } catch {
      setTradeMsg('Network error')
    }
  }, [quickTradeSide, quickTradeSize, state.activePair, activeWallet, tradingApi])

  return (
    <div style={styles.container}>
      {/* Risk Slider — ALWAYS VISIBLE */}
      <div style={styles.riskSection}>
        <div style={styles.riskHeader}>
          <Shield size={14} style={{ color: '#ffaa00' }} />
          <span style={styles.sectionTitle}>Risk Level</span>
          <span style={{ ...styles.riskValue, color: state.riskLevel < 0.3 ? '#39ff14' : state.riskLevel < 0.7 ? '#ffaa00' : '#ff2d4a' }}>
            {state.riskLevel < 0.3 ? 'Conservative' : state.riskLevel < 0.7 ? 'Moderate' : 'Aggressive'}
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={state.riskLevel}
          onChange={e => dispatch({ type: 'SET_RISK_LEVEL', level: parseFloat(e.target.value) })}
          style={styles.riskSlider}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: '#6b7280', marginTop: 2 }}>
          <span>Conservative</span>
          <span>Aggressive</span>
        </div>
      </div>

      {/* Quick Trade */}
      <div style={styles.tradeSection}>
        <div style={styles.sectionHeader}>
          <span style={styles.sectionTitle}>Quick Trade</span>
          <span style={{ fontSize: 10, color: '#6b7280' }}>{state.activePair}</span>
        </div>
        <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
          <button
            style={{
              ...styles.tradeBtn,
              background: quickTradeSide === 'buy' ? 'rgba(57,255,20,0.15)' : 'rgba(255,255,255,0.05)',
              color: quickTradeSide === 'buy' ? '#39ff14' : '#6b7280',
              borderColor: quickTradeSide === 'buy' ? 'rgba(57,255,20,0.3)' : 'transparent',
            }}
            onClick={() => setQuickTradeSide('buy')}
          >
            <TrendingUp size={13} /> Buy
          </button>
          <button
            style={{
              ...styles.tradeBtn,
              background: quickTradeSide === 'sell' ? 'rgba(255,45,74,0.15)' : 'rgba(255,255,255,0.05)',
              color: quickTradeSide === 'sell' ? '#ff2d4a' : '#6b7280',
              borderColor: quickTradeSide === 'sell' ? 'rgba(255,45,74,0.3)' : 'transparent',
            }}
            onClick={() => setQuickTradeSide('sell')}
          >
            <TrendingDown size={13} /> Sell
          </button>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            style={styles.tradeInput}
            type="number"
            placeholder="Size..."
            value={quickTradeSize}
            onChange={e => setQuickTradeSize(e.target.value)}
          />
          <button
            style={{
              ...styles.tradeExecuteBtn,
              background: quickTradeSide === 'buy' ? '#39ff14' : '#ff2d4a',
              color: '#0a0a14',
            }}
            onClick={handleQuickTrade}
          >
            {quickTradeSide === 'buy' ? 'BUY' : 'SELL'}
          </button>
        </div>
        {tradeMsg && <div style={{ fontSize: 10, color: '#ffaa00', marginTop: 4 }}>{tradeMsg}</div>}
      </div>

      {/* Wallet Selector */}
      <div style={styles.walletSection}>
        <div style={styles.sectionHeader}>
          <Wallet size={13} style={{ color: '#8a4bff' }} />
          <span style={styles.sectionTitle}>Wallet</span>
        </div>
        {state.wallets.length === 0 ? (
          <div style={{ fontSize: 11, color: '#6b7280' }}>No wallets connected</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {state.wallets.map(w => (
              <button
                key={w.id}
                style={{
                  ...styles.walletBtn,
                  background: w.id === state.activeWalletId ? 'rgba(138,75,255,0.12)' : 'transparent',
                  borderColor: w.id === state.activeWalletId ? 'rgba(138,75,255,0.3)' : 'rgba(255,255,255,0.06)',
                }}
                onClick={() => dispatch({ type: 'SET_ACTIVE_WALLET', id: w.id })}
              >
                <span style={styles.walletLabel}>{w.label}</span>
                <span style={styles.walletChain}>{w.chain}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Positions */}
      <div style={styles.positionsSection}>
        <div style={styles.sectionHeader}>
          <PieChart size={13} style={{ color: '#00f5ff' }} />
          <span style={styles.sectionTitle}>Positions ({state.positions.length})</span>
        </div>
        <div style={{ maxHeight: 150, overflowY: 'auto' }}>
          {state.positions.length === 0 ? (
            <div style={{ fontSize: 11, color: '#6b7280' }}>No open positions</div>
          ) : (
            state.positions.map((p, i) => (
              <div key={i} style={styles.positionItem}>
                <span style={styles.posSymbol}>{p.symbol}</span>
                <span style={{ ...styles.posPnl, color: p.unrealized_pnl_usd >= 0 ? '#39ff14' : '#ff2d4a' }}>
                  {p.unrealized_pnl_usd >= 0 ? '+' : ''}{p.unrealized_pnl_pct.toFixed(1)}%
                </span>
                <span style={styles.posValue}>{fmtUsd(p.market_value_usd)}</span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Portfolio Summary */}
      <div style={styles.portfolioSection}>
        <div style={styles.sectionHeader}>
          <TrendingUp size={13} style={{ color: '#39ff14' }} />
          <span style={styles.sectionTitle}>Portfolio</span>
        </div>
        {portfolio ? (
          <div style={styles.portfolioStats}>
            <div style={styles.portfolioStat}>
              <span style={styles.statLabel}>Total</span>
              <span style={styles.statValue}>{fmtUsd(portfolio.total_market_value_usd)}</span>
            </div>
            <div style={styles.portfolioStat}>
              <span style={styles.statLabel}>Today</span>
              <span style={{ ...styles.statValue, color: portfolio.total_pnl_usd >= 0 ? '#39ff14' : '#ff2d4a' }}>
                {portfolio.total_pnl_usd >= 0 ? '+' : ''}{fmtUsd(portfolio.total_pnl_usd)}
              </span>
            </div>
            <div style={styles.portfolioStat}>
              <span style={styles.statLabel}>Win Rate</span>
              <span style={styles.statValue}>{portfolio.win_rate_pct?.toFixed(0) || '—'}%</span>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 11, color: '#6b7280' }}>Connect API key to view portfolio</div>
        )}
      </div>

      {/* Bottom Actions */}
      <div style={styles.bottomActions}>
        <button style={styles.actionBtn} onClick={() => toggleDrawer('journal')}>
          <BookOpen size={12} /> Journal
        </button>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    padding: '0',
    overflowY: 'auto',
  },
  riskSection: {
    padding: '12px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  riskHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    marginBottom: 8,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: 700,
    color: '#e0e0e0',
    textTransform: 'uppercase' as const,
    letterSpacing: 0.5,
  },
  riskValue: {
    marginLeft: 'auto',
    fontSize: 10,
    fontWeight: 700,
  },
  riskSlider: {
    width: '100%',
    height: 4,
    appearance: 'none' as const,
    WebkitAppearance: 'none' as const,
    background: 'linear-gradient(to right, #39ff14, #ffaa00, #ff2d4a)',
    borderRadius: 2,
    outline: 'none',
    cursor: 'pointer',
  },
  tradeSection: {
    padding: '12px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    marginBottom: 8,
  },
  tradeBtn: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    padding: '6px 0',
    border: '1px solid transparent',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 600,
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  },
  tradeInput: {
    flex: 1,
    padding: '7px 10px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 6,
    color: '#e0e0e0',
    fontSize: 12,
    outline: 'none',
    fontFamily: 'inherit',
  },
  tradeExecuteBtn: {
    padding: '7px 16px',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 700,
    fontFamily: 'inherit',
    letterSpacing: 0.5,
  },
  walletSection: {
    padding: '12px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  walletBtn: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '6px 10px',
    background: 'transparent',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 6,
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  },
  walletLabel: {
    fontSize: 12,
    color: '#e0e0e0',
    fontWeight: 600,
  },
  walletChain: {
    fontSize: 10,
    color: '#6b7280',
  },
  positionsSection: {
    padding: '12px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  positionItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '4px 0',
    borderBottom: '1px solid rgba(255,255,255,0.03)',
  },
  posSymbol: {
    fontSize: 12,
    fontWeight: 600,
    fontFamily: 'monospace',
    color: '#e0e0e0',
    minWidth: 50,
  },
  posPnl: {
    fontSize: 12,
    fontWeight: 700,
    fontFamily: 'monospace',
  },
  posValue: {
    marginLeft: 'auto',
    fontSize: 11,
    color: '#9ca3af',
  },
  portfolioSection: {
    padding: '12px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  portfolioStats: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr 1fr',
    gap: 8,
  },
  portfolioStat: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  statLabel: {
    fontSize: 9,
    color: '#6b7280',
    textTransform: 'uppercase' as const,
    letterSpacing: 0.5,
  },
  statValue: {
    fontSize: 14,
    fontWeight: 700,
    color: '#e0e0e0',
    fontFamily: 'monospace',
  },
  bottomActions: {
    padding: '12px 14px',
    marginTop: 'auto',
  },
  actionBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    width: '100%',
    padding: '8px 0',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6,
    color: '#9ca3af',
    cursor: 'pointer',
    fontSize: 12,
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  },
  freqtradeSection: {
    padding: '12px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  freqtradeStatus: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    background: 'rgba(0,245,255,0.04)',
    border: '1px solid rgba(0,245,255,0.1)',
    borderRadius: 8,
    padding: '8px 10px',
  },
  freqtradeRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '2px 0',
  },
}
