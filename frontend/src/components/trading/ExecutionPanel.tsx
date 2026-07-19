import React, { useState, useCallback, useEffect } from 'react'
import { Wallet, TrendingUp, TrendingDown, Shield, BookOpen, PieChart, Activity, Brain, Plus, X, Power, ToggleLeft, ToggleRight } from 'lucide-react'
import { useTradingStore } from './tradingStore'
import GenerateWalletModal from './GenerateWalletModal'

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
  // activePair is a display pair like "BTC/USDT" — not resolvable to a
  // Solana mint, so it can't drive real execution. Orders/positions store
  // the mint directly in `symbol` for solana-chain rows (see
  // trading.py execute_live_order), so this field is the actual trade
  // target: paste a CA, or it prefills from a clicked position below.
  const [mint, setMint] = useState('')
  const [strategyId, setStrategyId] = useState('')
  const [strategies, setStrategies] = useState<any[]>([])
  const [tradeMsg, setTradeMsg] = useState('')
  const [tradeMsgOk, setTradeMsgOk] = useState(true)
  const [busy, setBusy] = useState(false)
  const [showGenerateWallet, setShowGenerateWallet] = useState(false)

  async function refreshWallets() {
    try {
      const r = await tradingApi('/wallets')
      if (r.ok) {
        const d = await r.json()
        dispatch({ type: 'SET_WALLETS', wallets: Array.isArray(d) ? d : (d.wallets || []) })
      }
    } catch { /* best-effort */ }
  }

  const portfolio = state.portfolio
  const activeWallet = state.wallets.find(w => w.id === state.activeWalletId)
  const [sourcePerf, setSourcePerf] = useState<any[]>([])

  useEffect(() => {
    if (!state.activeWalletId) return
    (async () => {
      try {
        const r = await tradingApi('/strategies')
        if (r.ok) {
          // Show every strategy, not just armed+live — same as
          // EntityProfileCard's TradePanel: picking a not-yet-armed one
          // surfaces a clear backend error at trade time instead of just
          // silently disappearing from the picker.
          setStrategies(await r.json())
        }
      } catch { /* best-effort */ }
    })()
  }, [state.activeWalletId, tradingApi])

  // Real learning signal: does trading source X actually make money? Backed
  // by trade_outcome_learner.py evaluating every executed buy at +1h/+24h —
  // not a heuristic, actual pnl on this agent's own trades.
  useEffect(() => {
    (async () => {
      try {
        const r = await tradingApi('/source-performance')
        if (r.ok) setSourcePerf((await r.json()).sources || [])
      } catch { /* best-effort */ }
    })()
    const iv = setInterval(async () => {
      try {
        const r = await tradingApi('/source-performance')
        if (r.ok) setSourcePerf((await r.json()).sources || [])
      } catch {}
    }, 60000)
    return () => clearInterval(iv)
  }, [tradingApi])

  // Auto-Trading Daemons — the fail-closed arm switches for the standalone
  // daemons that create AND execute their own orders (degen_alpha_fusion,
  // ares_pumpfun_trader, ares_jupiter_signer). Unlike the strategy armed/live
  // gate above, nothing here had a UI-controlled off switch until now — a
  // daemon traded the instant its systemd service was running. Backed by
  // /api/trading/daemon-settings/{key} (same table+endpoints the wallet
  // pickers below already use), value "1" enabled / anything else disabled.
  const AUTO_TRADE_DAEMONS = [
    { key: 'degen_alpha_fusion_trading_enabled', label: 'Degen Alpha Fusion', hint: 'Moonshot sniping on trending pools' },
    { key: 'pumpfun_trader_trading_enabled', label: 'Pumpfun Trader', hint: 'Buys pumpfun signals from degen_alpha_fusion/ogun_degen' },
    { key: 'jupiter_signer_trading_enabled', label: 'Jupiter Signer', hint: 'Signs + submits queued moonshot swaps' },
    { key: 'hyperliquid_trader_trading_enabled', label: 'Hyperliquid Trader', hint: 'Perp market_open/market_close via the HL SDK — real signing, wallet currently unfunded' },
    { key: 'base_trader_trading_enabled', label: 'Base Trader', hint: '1inch swaps on Base — real EIP-1559 signing + broadcast, needs ETH for gas' },
    { key: 'sui_trader_trading_enabled', label: 'Sui Trader', hint: 'Real signing via pysui + multi-protocol swaps via Cetus\'s official SDK (Node bridge), needs SUI for gas' },
    { key: 'polymarket_trader_trading_enabled', label: 'Polymarket Trader', hint: 'Real EIP-712 CLOB orders via py-clob-client, needs USDC.e on Polygon' },
    { key: 'solana_engine_trading_enabled', label: 'Solana Engine', hint: 'General Solana order executor — routes through the same real execute-live signer as Pumpfun Trader' },
  ]
  const [daemonSettings, setDaemonSettings] = useState<Record<string, { value: string | null }>>({})
  const [daemonBusy, setDaemonBusy] = useState<Record<string, boolean>>({})

  const loadDaemonSettings = useCallback(async () => {
    try {
      const r = await tradingApi('/daemon-settings')
      if (r.ok) setDaemonSettings(await r.json())
    } catch { /* best-effort */ }
  }, [tradingApi])
  useEffect(() => { loadDaemonSettings() }, [loadDaemonSettings])

  const toggleDaemonTrading = useCallback(async (key: string, currentlyOn: boolean) => {
    setDaemonBusy(b => ({ ...b, [key]: true }))
    try {
      const r = await tradingApi(`/daemon-settings/${key}`, {
        method: 'PUT',
        body: JSON.stringify({ value: currentlyOn ? '0' : '1' }),
      })
      if (r.ok) await loadDaemonSettings()
    } catch { /* best-effort */ }
    setDaemonBusy(b => ({ ...b, [key]: false }))
  }, [tradingApi, loadDaemonSettings])

  const handleQuickTrade = useCallback(async () => {
    const apiKey = localStorage.getItem('vantage_api_key')
    if (!apiKey) { setTradeMsgOk(false); setTradeMsg('Connect API key first'); return }
    if (!activeWallet) { setTradeMsgOk(false); setTradeMsg('Select a wallet first'); return }
    if (!mint.trim()) { setTradeMsgOk(false); setTradeMsg('Enter a token contract address (mint)'); return }
    const size = parseFloat(quickTradeSize)
    if (!size || size <= 0) { setTradeMsgOk(false); setTradeMsg('Enter valid size'); return }

    setBusy(true)
    try {
      const r = await tradingApi('/quick-trade', {
        method: 'POST',
        body: JSON.stringify({
          mint: mint.trim(),
          side: quickTradeSide,
          wallet_id: activeWallet.id,
          quantity: size,
          strategy_id: strategyId ? Number(strategyId) : undefined,
          trigger_reason: strategyId ? 'strategy_terminal' : 'manual_terminal',
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (r.ok) {
        setTradeMsgOk(true)
        setTradeMsg(`Submitted — tx ${String(d.tx_hash || '').slice(0, 10)}…`)
        setQuickTradeSize('')
      } else {
        setTradeMsgOk(false)
        setTradeMsg(typeof d.detail === 'string' ? d.detail : (d.detail?.error?.detail || d.detail?.error || 'Order failed'))
      }
    } catch {
      setTradeMsgOk(false)
      setTradeMsg('Network error')
    }
    setBusy(false)
  }, [quickTradeSide, quickTradeSize, mint, strategyId, activeWallet, tradingApi])

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
        <input
          style={{ ...styles.tradeInput, width: '100%', marginBottom: 6 }}
          type="text"
          placeholder="Token contract address (mint)..."
          value={mint}
          onChange={e => setMint(e.target.value)}
        />
        {strategies.length > 0 && (
          <select
            value={strategyId}
            onChange={e => setStrategyId(e.target.value)}
            style={{ ...styles.tradeInput, width: '100%', marginBottom: 6 }}
          >
            <option value="">Manual (no strategy)</option>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name} {s.armed && s.live ? '● live' : s.armed ? '○ armed, paper' : '○ not armed'}</option>)}
          </select>
        )}
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            style={styles.tradeInput}
            type="number"
            placeholder="Size (SOL to buy, tokens to sell)..."
            value={quickTradeSize}
            onChange={e => setQuickTradeSize(e.target.value)}
          />
          <button
            style={{
              ...styles.tradeExecuteBtn,
              background: busy ? 'rgba(255,255,255,0.1)' : (quickTradeSide === 'buy' ? '#39ff14' : '#ff2d4a'),
              color: busy ? '#6b7280' : '#0a0a14',
              cursor: busy ? 'wait' : 'pointer',
            }}
            onClick={handleQuickTrade}
            disabled={busy}
          >
            {busy ? '…' : (quickTradeSide === 'buy' ? 'BUY' : 'SELL')}
          </button>
        </div>
        {tradeMsg && <div style={{ fontSize: 10, color: tradeMsgOk ? '#39ff14' : '#ff2d4a', marginTop: 4, wordBreak: 'break-all' }}>{tradeMsg}</div>}
      </div>

      {/* Wallet Selector */}
      <div style={styles.walletSection}>
        <div style={styles.sectionHeader}>
          <Wallet size={13} style={{ color: '#8a4bff' }} />
          <span style={styles.sectionTitle}>Wallet</span>
          <button onClick={() => setShowGenerateWallet(true)}
            style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 3, padding: '2px 6px', background: 'rgba(138,75,255,0.15)', border: '1px solid rgba(138,75,255,0.3)', borderRadius: 5, color: '#c4b5fd', fontSize: 10, cursor: 'pointer' }}>
            <Plus size={10} /> Generate
          </button>
        </div>
        {showGenerateWallet && (
          <GenerateWalletModal onClose={() => setShowGenerateWallet(false)} onCreated={refreshWallets} tradingApi={tradingApi} />
        )}
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
              <div
                key={i}
                style={{ ...styles.positionItem, cursor: 'pointer' }}
                title="Click to load into Quick Trade"
                onClick={() => setMint(p.symbol)}
              >
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

      {/* Auto-Trading Daemons — fail-closed arm switches. Off by default;
          a daemon here does nothing (not even paper-fill) until toggled on. */}
      <div style={styles.portfolioSection}>
        <div style={styles.sectionHeader}>
          <Power size={13} style={{ color: '#ff2d4a' }} />
          <span style={styles.sectionTitle}>Auto-Trading Daemons</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {AUTO_TRADE_DAEMONS.map(d => {
            const on = daemonSettings[d.key]?.value === '1'
            return (
              <div key={d.key} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
                <button
                  style={{ padding: 0, background: 'none', border: 'none', cursor: daemonBusy[d.key] ? 'default' : 'pointer' }}
                  disabled={!!daemonBusy[d.key]}
                  onClick={() => toggleDaemonTrading(d.key, on)}
                  title={on ? 'Disable live trading' : 'Enable live trading'}
                >
                  {on ? <ToggleRight size={20} color="#39ff14" /> : <ToggleLeft size={20} color="#6b7280" />}
                </button>
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  <span style={{ color: '#e0e0e0' }}>{d.label}</span>
                  <span style={{ color: '#6b7280', fontSize: 9 }}>{d.hint}</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Source Performance — real pnl by strategy/trigger source, not a heuristic */}
      <div style={styles.portfolioSection}>
        <div style={styles.sectionHeader}>
          <Brain size={13} style={{ color: '#8a4bff' }} />
          <span style={styles.sectionTitle}>Learning: Source Performance</span>
        </div>
        {sourcePerf.length === 0 ? (
          <div style={{ fontSize: 11, color: '#6b7280' }}>No evaluated trades yet — real pnl appears here 1h+ after your first live buy.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {sourcePerf.map((s, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, padding: '3px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <span style={{ color: '#e0e0e0' }}>{s.source} <span style={{ color: '#6b7280', fontSize: 9 }}>({s.window}, n={s.n_trades})</span></span>
                <span style={{ color: s.avg_pnl_pct >= 0 ? '#39ff14' : '#ff2d4a', fontWeight: 700, fontFamily: 'monospace' }}>
                  {s.avg_pnl_pct >= 0 ? '+' : ''}{s.avg_pnl_pct.toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
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
