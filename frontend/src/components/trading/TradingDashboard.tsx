import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { Flame, ChevronRight, RefreshCw, CandlestickChart, LineChart, Code2 } from 'lucide-react'
import CandleChart, { PineSeries } from './CandleChart'
import PineEditor from './PineEditor'
import TradingViewChart from './TradingViewChart'
import { TokenInfoIcon, TokenLink } from './EntityProfileCard'

// ══════════════════════════════════════════════════════════════════════════════
// TradingDashboard — the "everything in one place" landing tab for Trading.
// Before this existed, a trader had to piece the picture together from three
// disconnected surfaces: a buried Charts sub-tab (native OHLC + Pine), an
// unrelated third-party TradingView iframe on its own top-level tab, and a
// signal-aggregation endpoint (/api/intel/signals — 13+ sources, dedup'd,
// ranked) that no frontend ever called. This screen puts the chart, the live
// signal feed, market vitals, and a portfolio snapshot on one screen.
// ══════════════════════════════════════════════════════════════════════════════

interface Signal {
  symbol: string; source: string; type?: string; conviction?: number
  price?: number; volume_24h?: number; change_6h?: number; spread_pct?: number
  detail?: string; sources?: string[]
}

function useJSON<T = any>(path: string, intervalMs = 60000, opts?: { enabled?: boolean; headers?: Record<string, string> }) {
  const enabled = opts?.enabled ?? true
  const headers = opts?.headers
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    if (!enabled) { setLoading(false); return }
    try {
      const r = await fetch(path, headers ? { headers } : undefined)
      if (r.ok) setData(await r.json())
    } catch { /* fail-soft — dashboard degrades tile-by-tile, never blocks */ }
    setLoading(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled, JSON.stringify(headers)])
  useEffect(() => { load(); if (!enabled) return; const t = setInterval(load, intervalMs); return () => clearInterval(t) }, [load, intervalMs, enabled])
  return { data, loading, refresh: load }
}

function StatTile({ label, value, sub, color }: { label: string; value: React.ReactNode; sub?: string; color?: string }) {
  return (
    <div className="ares-stat-tile">
      <div className="ares-stat-label">{label}</div>
      <div className="ares-stat-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2, textTransform: 'capitalize' }}>{sub}</div>}
    </div>
  )
}

function ConvictionBar({ v }: { v: number }) {
  const pct = Math.max(0, Math.min(100, (v / 10) * 100))
  const color = v >= 7 ? 'var(--danger)' : v >= 4 ? 'var(--warning)' : 'var(--muted)'
  return (
    <div style={{ width: 40, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.08)', flexShrink: 0 }}>
      <div style={{ width: `${pct}%`, height: '100%', borderRadius: 2, background: color }} />
    </div>
  )
}

const SOURCE_COLOR: Record<string, string> = {
  radar: '#8a4bff', alpha_feed: '#39ff14', intel: '#ffaa00', kraken: '#5851ff',
  coindesk: '#00f5ff', cryptocompare: '#ff5cf0', geckoterminal: '#39ff14',
  coincap: '#00f5ff', fear_greed: '#ffaa00', cryptopanic: '#ff2d4a', coinpaprika: '#8a4bff',
}

function SignalRow({ s, onSelect }: { s: Signal; onSelect: (symbol: string) => void }) {
  const detail = s.detail
    || (s.change_6h != null ? `${s.change_6h > 0 ? '+' : ''}${s.change_6h.toFixed(1)}% / 6h`
    : s.spread_pct != null ? `spread ${s.spread_pct.toFixed(2)}%`
    : s.price != null ? `$${Number(s.price).toLocaleString(undefined, { maximumFractionDigits: 6 })}`
    : s.type || '')
  return (
    <div
      onClick={() => onSelect(s.symbol)}
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 4px', borderBottom: '1px solid rgba(255,255,255,0.04)', cursor: 'pointer' }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.02)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 700, fontSize: 12 }}>{s.symbol}</span>
          <TokenInfoIcon symbol={s.symbol} />
          <span style={{ fontSize: 9, color: SOURCE_COLOR[s.source] || 'var(--muted)', border: `1px solid ${SOURCE_COLOR[s.source] || 'var(--border)'}`, borderRadius: 3, padding: '0 4px', textTransform: 'uppercase', letterSpacing: 0.3 }}>{s.source}</span>
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{detail}</div>
      </div>
      {typeof s.conviction === 'number' && s.conviction > 0 && <ConvictionBar v={s.conviction} />}
    </div>
  )
}

export default function TradingDashboard({ onOpenPortfolio }: { onOpenPortfolio?: () => void }) {
  const [symbol, setSymbol] = useState('BTC')
  const [symbolInput, setSymbolInput] = useState('BTC')
  const [interval, setIv] = useState('1d')
  const [pineSeries, setPineSeries] = useState<PineSeries[]>([])
  const [showPine, setShowPine] = useState(false)
  const [engine, setEngine] = useState<'native' | 'tradingview'>('native')
  const INTERVALS = ['1h', '4h', '1d', '1w']

  const intel = useJSON<any>('/api/intel', 60000)
  const alpha = useJSON<{ items: any[] }>('/api/intel/alpha', 60000)
  const signals = useJSON<{ signals: Signal[]; count: number; sources: string[] }>('/api/intel/signals?limit=15', 45000)

  const apiKey = typeof window !== 'undefined' ? localStorage.getItem('vantage_api_key') || '' : ''
  const authHeaders = useMemo(() => apiKey ? { 'X-Agent-Key': apiKey } : undefined, [apiKey])
  const perf = useJSON<any>('/api/trading/performance', 30000, { enabled: !!apiKey, headers: authHeaders })
  const positions = useJSON<{ positions: any[] }>('/api/trading/positions', 30000, { enabled: !!apiKey, headers: authHeaders })

  const applySymbol = (raw: string) => {
    const s = raw.trim().toUpperCase()
    if (s) { setSymbol(s); setSymbolInput(s); setPineSeries([]) }
  }

  const i = intel.data
  const fusion = i?.anomalies?.fusion || {}
  const arbCount = (i?.arbitrage?.opportunities || []).length
  const sentiment = i?.sentiment?.sentiment || {}
  const topMover = alpha.data?.items?.[0]

  const rankedSignals = useMemo(() => {
    const list = signals.data?.signals || []
    return [...list].sort((a, b) => (b.conviction || 0) - (a.conviction || 0))
  }, [signals.data])

  const perfData = perf.data
  // GET /api/trading/positions returns { positions: [...], total_*_usd, priced }
  // — not a bare array.
  const posList = positions.data?.positions || []

  return (
    <div>
      {/* Market vitals strip */}
      <div className="ares-stat-grid" style={{ marginBottom: 20 }}>
        <StatTile label="BTC" value={fusion.btc_consensus ? `$${Number(fusion.btc_consensus).toLocaleString()}` : '—'} />
        <StatTile label="ETH" value={fusion.eth ? `$${Number(fusion.eth).toLocaleString()}` : '—'} />
        <StatTile label="SOL" value={fusion.sol ? `$${Number(fusion.sol).toFixed(2)}` : '—'} />
        <StatTile
          label="Fear & Greed"
          value={sentiment.fear_greed ?? '—'}
          sub={sentiment.mood}
          color={sentiment.fear_greed >= 60 ? 'var(--green)' : sentiment.fear_greed <= 25 ? 'var(--danger)' : 'var(--warning)'}
        />
        <StatTile label="Arbitrage" value={arbCount} color={arbCount > 0 ? 'var(--warning)' : undefined} />
        <StatTile
          label="Top Mover"
          value={topMover ? `${topMover.symbol} ${topMover.change_24h > 0 ? '+' : ''}${Number(topMover.change_24h).toFixed(1)}%` : '—'}
          color={topMover ? (topMover.change_24h > 0 ? 'var(--green)' : 'var(--danger)') : undefined}
        />
      </div>

      {/* Chart + live signals */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)', gap: 16, alignItems: 'start' }}>
        <div className="dash-panel">
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              className="ares-input" value={symbolInput} onChange={e => setSymbolInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applySymbol(symbolInput)}
              placeholder="Symbol (BTC, ETH, SOL…)" style={{ maxWidth: 160 }}
            />
            <button className="btn btn-primary btn-sm" onClick={() => applySymbol(symbolInput)}>Load</button>
            {engine === 'native' && (
              <div className="top-nav-tabs" style={{ flex: 'initial' }}>
                {INTERVALS.map(iv => (
                  <button key={iv} type="button" className={`top-nav-tab ${interval === iv ? 'active' : ''}`} onClick={() => setIv(iv)}>{iv}</button>
                ))}
              </div>
            )}
            <div style={{ flex: 1 }} />
            {engine === 'native' && (
              <button className={`btn btn-sm ${showPine ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setShowPine(s => !s)} title="Toggle the Pine Script editor for agent-authored indicator overlays">
                <Code2 size={13} /> Pine
              </button>
            )}
            <button className="btn btn-ghost btn-sm" onClick={() => setEngine(e => e === 'native' ? 'tradingview' : 'native')} title="Switch chart engine">
              {engine === 'native' ? <><LineChart size={13} /> TradingView</> : <><CandlestickChart size={13} /> Native</>}
            </button>
          </div>

          {engine === 'tradingview' ? (
            <TradingViewChart />
          ) : showPine ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(260px, 1fr)', gap: 16, alignItems: 'start' }}>
              <CandleChart symbol={symbol} interval={interval} pineSeries={pineSeries} />
              <PineEditor symbol={symbol} interval={interval} onResult={setPineSeries} />
            </div>
          ) : (
            <CandleChart symbol={symbol} interval={interval} pineSeries={pineSeries} />
          )}
        </div>

        <div className="dash-panel" style={{ maxHeight: 560, overflowY: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
            <div className="ares-section-title" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Flame size={13} style={{ color: 'var(--warning)' }} /> Live Signals
              {signals.data?.count != null && <span style={{ color: 'var(--muted)', fontWeight: 400 }}>({signals.data.count})</span>}
            </div>
            <button className="btn btn-ghost btn-sm" onClick={signals.refresh} title="Refresh"><RefreshCw size={12} /></button>
          </div>
          {signals.data?.sources && (
            <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8 }}>{signals.data.sources.length} sources · click a signal to load it in the chart</div>
          )}
          {signals.loading && !signals.data && <div style={{ color: 'var(--muted)', fontSize: 12, padding: '20px 0' }}>Loading signals…</div>}
          {!signals.loading && rankedSignals.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 12, padding: '20px 0' }}>No live signals right now.</div>}
          {rankedSignals.map((s, idx) => <SignalRow key={`${s.symbol}-${s.source}-${idx}`} s={s} onSelect={applySymbol} />)}
        </div>
      </div>

      {/* Portfolio snapshot */}
      {apiKey ? (
        <div className="dash-panel" style={{ marginTop: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div className="ares-section-title" style={{ margin: 0 }}>Your Portfolio</div>
            {onOpenPortfolio && (
              <button className="btn btn-ghost btn-sm" onClick={onOpenPortfolio}>View full Portfolio <ChevronRight size={13} /></button>
            )}
          </div>
          <div className="ares-stat-grid" style={{ marginBottom: posList.length ? 12 : 0 }}>
            <StatTile label="Portfolio Value" value={perfData?.portfolio_value?.portfolio_value_usd != null ? `$${Number(perfData.portfolio_value.portfolio_value_usd).toLocaleString()}` : '—'} />
            <StatTile label="Win Rate" value={perfData ? `${perfData.win_rate}%` : '—'} color={perfData?.win_rate >= 50 ? 'var(--green)' : undefined} />
            <StatTile label="Total Trades" value={perfData?.total_trades ?? '—'} />
            <StatTile label="Open Positions" value={posList.length} />
          </div>
          {posList.length > 0 && (
            <table className="ares-table">
              <thead><tr><th>Symbol</th><th>Qty</th><th>Unrealized P&L</th></tr></thead>
              <tbody>
                {posList.slice(0, 3).map((p: any, idx: number) => (
                  <tr key={idx}>
                    <td style={{ fontWeight: 600 }}><TokenLink symbol={p.symbol} chain={p.chain} /></td>
                    <td style={{ fontFamily: 'monospace' }}>{p.net_quantity}</td>
                    <td style={{ fontWeight: 700, color: (p.unrealized_pnl_usd ?? 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>
                      {(p.unrealized_pnl_usd ?? 0) >= 0 ? '+' : ''}{p.unrealized_pnl_usd != null ? `$${Number(p.unrealized_pnl_usd).toFixed(2)}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="dash-panel" style={{ marginTop: 16, textAlign: 'center', padding: 24 }}>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>
            Connect your agent in the <a href="/dashboard" style={{ color: 'var(--cyan, #00f5ff)' }}>Dashboard</a> to see your live P&L and positions here.
          </div>
        </div>
      )}
    </div>
  )
}
