import React, { useState, useEffect, useCallback } from 'react'
import QuantBacktestPanel from './analytics/QuantBacktestPanel'
import WhalesPanel from './analytics/WhalesPanel'
import FxPanel from './analytics/FxPanel'
import SourcesLivePanel from './analytics/SourcesLivePanel'
import { TrendingUp, BarChart3, Zap, Activity, Database, Radio, Layers, Droplets, Waves, DollarSign, History, GitBranch as Waypoints, ListOrdered, Share2, Flame, Brain, Scale, UserCog, PlusCircle } from 'lucide-react'
import Top5Degen from './Top5Degen'
import MoneyFlowGraph from '../MoneyFlowGraph'
import CouncilSection from '../CouncilSection'
import ShadowAccountSection from '../ShadowAccountSection'
import { TokenLink, WalletLink } from './EntityProfileCard'
import AgentIntel from './AgentIntel'
import { LabelChips } from './EarlyRunnerIntel'
import OverviewPanel from './analytics/OverviewPanel'
import ArbitragePanel from './analytics/ArbitragePanel'
import AlphaPanel from './analytics/AlphaPanel'
import YieldsPanel from './analytics/YieldsPanel'
import DexPanel from './analytics/DexPanel'
import AllTokensPanel from './analytics/AllTokensPanel'
import TracePanel from './analytics/TracePanel'
import AddTrackerPanel from './analytics/AddTrackerPanel'

// ══════════════════════════════════════════════════════════════════════════════
// Analytics — the deep-dive lenses behind Trading's default Dashboard tab. These
// read public, unauthenticated endpoints (/api/intel, /api/alpha, /api/intel/fx,
// /api/intel/backtest, /api/intel/trace/{chain}/{address}, /api/intel/health,
// /api/intel/sources) via plain fetch — exactly as they did inside AresSOC, with
// zero auth. The chart + Pine editor live on the Dashboard tab now (see
// trading/TradingDashboard.tsx), not here.
// ══════════════════════════════════════════════════════════════════════════════

// ── Shared helpers (self-contained copy; SOC console keeps its own) ────────────
function Badge({ status }: { status: string }) {
  const map: Record<string, string> = { pending: '#ffaa00', approved: '#39ff14', rejected: '#ff2d4a', ok: '#39ff14', degraded: '#ff2d4a', healthy: '#39ff14', congested: '#ff2d4a', live: '#39ff14', neutral: '#ffaa00', long: '#39ff14', pass: '#6b7280' }
  const c = map[status.toLowerCase()] || '#6b7280'
  return <span style={{ fontSize: 10, fontWeight: 700, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>{status}</span>
}

// ── Data fetching hook ─────────────────────────────────────────────────────────
// Seeds from a localStorage cache so switching tabs and coming back shows the
// last-known-good payload immediately instead of a blank "Loading…" flash —
// this component used to start every mount from data=null with no memory of
// what was there a moment ago, which is what "info was here, I came back and
// it's gone" was actually caused by.
function cacheKeyFor(path: string) { return `vantage_cache_intel_${path}` }
// Feed status: useAresApi keeps showing the last cached body when a fetch
// fails, which made a dead feed look live. Every mounted feed reports here and
// <FeedStatus/> names the ones serving stale data.
type FeedState = { path: string; interval: number; lastOk: number | null; error: string | null }
const FEEDS = new Map<string, FeedState>()
const FEED_EVENT = 'ares-feed-status'
function reportFeed(s: FeedState) { FEEDS.set(s.path, s); try { window.dispatchEvent(new Event(FEED_EVENT)) } catch { /* */ } }

function useAresApi(path: string, interval = 60000) {
  const [data, setData] = useState<any>(() => {
    try {
      const raw = localStorage.getItem(cacheKeyFor(path))
      return raw ? JSON.parse(raw) : null
    } catch { return null }
  })
  const [loading, setLoading] = useState(data === null)
  const load = useCallback(async () => {
    const prev = FEEDS.get(path)
    try {
      const r = await fetch(path)
      if (r.ok) {
        const json = await r.json()
        setData(json)
        try { localStorage.setItem(cacheKeyFor(path), JSON.stringify(json)) } catch { /* quota — non-fatal */ }
        reportFeed({ path, interval, lastOk: Date.now(), error: null })
      } else {
        reportFeed({ path, interval, lastOk: prev?.lastOk ?? null, error: `HTTP ${r.status}` })
      }
    } catch (e: any) {
      reportFeed({ path, interval, lastOk: prev?.lastOk ?? null, error: e?.message || 'network error' })
    }
    setLoading(false)
  }, [path, interval])
  useEffect(() => {
    load(); const t = setInterval(load, interval)
    return () => { clearInterval(t); FEEDS.delete(path); try { window.dispatchEvent(new Event(FEED_EVENT)) } catch { /* */ } }
  }, [load, interval, path])
  return { data, loading, refresh: load }
}

function FeedStatus() {
  const [, tick] = useState(0)
  useEffect(() => {
    const on = () => tick(x => x + 1)
    window.addEventListener(FEED_EVENT, on); const t = setInterval(on, 30000)
    return () => { window.removeEventListener(FEED_EVENT, on); clearInterval(t) }
  }, [])
  const now = Date.now()
  const bad = [...FEEDS.values()].filter(f => f.error || (f.lastOk != null && now - f.lastOk > 3 * f.interval))
  if (!bad.length) return null
  return (
    <div style={{ border: '1px solid #f59e0b55', background: 'rgba(245,158,11,0.06)', borderRadius: 8, padding: '8px 12px', marginBottom: 12, fontSize: 12 }}>
      {bad.map(f => (
        <div key={f.path}>
          <b style={{ color: '#f59e0b' }}>Stale feed</b> <code>{f.path.split('?')[0]}</code>
          {' — '}{f.error ? `last request failed (${f.error})` : 'no fresh data'}
          {'; '}{f.lastOk ? `showing data from ${Math.round((now - f.lastOk) / 60000)}m ago` : 'showing cached data from an earlier visit'}
        </div>
      ))}
    </div>
  )
}


// Watchlist is the one intel endpoint group that's agent-authenticated (add/
// remove/refresh mutate shared state) — mirrors Portfolio.tsx's localStorage
// 'vantage_api_key' → X-Agent-Key pattern.
function agentKey(): string {
  return localStorage.getItem('vantage_api_key') || ''
}

async function intelAuthApi(path: string, opts: RequestInit = {}): Promise<Response> {
  return fetch(`/api/intel${path}`, {
    ...opts,
    headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
}

// ══════════════════════════════════════════════════════════════════════════════
// INTELLIGENCE TABS
// ══════════════════════════════════════════════════════════════════════════════

function AresOverview() {
  return <OverviewPanel />
}

function AresIntel() {
  const { data, loading } = useAresApi('/api/intel', 30000)
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading intel…</div>
  return <pre style={{ fontSize: 11, color: 'var(--muted)', maxHeight: '70vh', overflow: 'auto', background: 'rgba(0,0,0,0.3)', padding: 16, borderRadius: 8, fontFamily: 'monospace' }}>{JSON.stringify(data, null, 2)}</pre>
}

function AresArbitrage() {
  return <ArbitragePanel />
}

function AresHealth() {
  const { data, loading } = useAresApi('/api/intel/health', 30000)
  const chains = data?.chains || {}
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading…</div>
  return (
    <div>
      <div className="ares-section-title">{Object.keys(chains).length} Chains Monitored</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: 12 }}>
        {Object.entries(chains).map(([k, v]: [string, any]) => (
          <div key={k} style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 14, textTransform: 'capitalize' }}>{k}</span>
              <Badge status={v.health || '?'} />
            </div>
            {Object.entries(v).filter(([k2]) => k2 !== 'health' && k2 !== 'error').map(([k2, v2]) => (
              <div key={k2} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', padding: '2px 0' }}>
                <span>{k2}</span>
                <span style={{ color: 'var(--text)', fontFamily: 'monospace' }} title={v2 == null ? 'not measured by this probe' : undefined}>{v2 == null ? '—' : typeof v2 === 'number' ? v2.toLocaleString() : String(v2)}</span>
              </div>
            ))}
            {v.error && <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 4 }}>{String(v.error)}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

function AresSentiment() {
  const { data, loading } = useAresApi('/api/intel', 60000)
  const sent = data?.sentiment?.sentiment || {}
  const indicators: string[] = data?.sentiment?.indicators || []
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading…</div>
  const fg = typeof sent.fear_greed === 'number' ? sent.fear_greed : null
  const tile = (label: string, value: React.ReactNode, color?: string, sub?: string) => (
    <div className="ares-stat-tile">
      <div className="ares-stat-label">{label}</div>
      <div className="ares-stat-value" style={{ color, textTransform: 'capitalize' }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
  return (
    <div>
      <div className="ares-section-title">Market Sentiment</div>
      <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
        {tile('Overall', sent.overall || '—', sentColor(sent.overall), sent.overall === 'mixed' ? 'price action and positioning disagree' : undefined)}
        {tile('Price trend', sent.price_trend || '—', sentColor(sent.price_trend), 'avg 24h move ±1% / breadth 60–40 band')}
        {tile('Positioning', sent.positioning || '—', sent.positioning === 'greed' ? 'var(--success)' : sent.positioning === 'fear' ? 'var(--danger)' : undefined, 'Fear & Greed index')}
        {tile('Fear & Greed', fg ?? '—', undefined, sent.mood)}
        {tile('Top-100 green', sent.gainers_pct != null ? `${sent.gainers_pct}%` : '—')}
        {tile('Avg 24h move', sent.avg_change_24h != null ? `${sent.avg_change_24h > 0 ? '+' : ''}${sent.avg_change_24h}%` : '—', (sent.avg_change_24h || 0) >= 0 ? 'var(--success)' : 'var(--danger)')}
        {tile('BTC dominance', sent.btc_dominance != null ? `${Number(sent.btc_dominance).toFixed(1)}%` : '—')}
      </div>
      {fg !== null && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ position: 'relative', height: 10, borderRadius: 5, background: 'linear-gradient(90deg,#ef4444,#f59e0b 45%,#22c55e)' }}>
            <div style={{ position: 'absolute', left: `calc(${fg}% - 6px)`, top: -3, width: 12, height: 16, borderRadius: 3, background: '#fff', boxShadow: '0 0 6px #000' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
            <span>extreme fear</span><span>neutral</span><span>extreme greed</span>
          </div>
        </div>
      )}
      {indicators.length > 0 && (
        <>
          <div className="ares-section-title">Signals</div>
          {indicators.map((s, idx) => (
            <div key={idx} style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', marginBottom: 4, fontSize: 12, color: 'var(--warning)' }}>{s}</div>
          ))}
        </>
      )}
    </div>
  )
}

function sentColor(s?: string): string | undefined {
  return s === 'bullish' ? 'var(--success)' : s === 'bearish' ? 'var(--danger)' : s === 'mixed' ? 'var(--warning)' : undefined
}

function AresSources() {
  const { data, loading } = useAresApi('/api/intel/sources', 60000)
  // /sources-registry is the richer, self-contained source of truth (real
  // category + integration status per source, 15 named entries) -- /sources
  // itself is just a thin proxy to the ares-bridge RPC endpoint list and
  // has neither field. Cross-referenced by name below rather than switching
  // the primary endpoint outright, so a bridge outage still shows whatever
  // /sources itself returns.
  const { data: registryData } = useAresApi('/api/intel/sources-registry', 300000)
  const endpoints = data?.endpoints || {}
  const registry: { name: string; category: string; integrated: boolean }[] = registryData?.sources || []
  const registryByKey = new Map(registry.map(s => [s.name.toLowerCase().replace(/[^a-z0-9]/g, ''), s]))
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading…</div>
  const groups: Record<string, [string, any][]> = {
    'Chain RPC': [], 'Exchanges': [], 'DEX/DeFi': [], 'Finance/FX': [], 'Other': [],
  }
  Object.entries(endpoints).forEach(([k, v]: [string, any]) => {
    if (['base','solana','polygon','sui','hyperliquid'].includes(k)) groups['Chain RPC'].push([k, v])
    else if (['coingecko','gemini','kraken','kucoin','okx','wazirx','coinlore','coinpaprika'].includes(k)) groups['Exchanges'].push([k, v])
    else if (['geckoterminal','dexscreener','defillama','pyth'].includes(k)) groups['DEX/DeFi'].push([k, v])
    else if (['yahoo','currency_rates','exchangerate','nbp','nexchange'].includes(k)) groups['Finance/FX'].push([k, v])
    else groups['Other'].push([k, v])
  })
  return (
    <div>
      <SourcesLivePanel />
      <div className="ares-section-title" style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <span>{Object.keys(endpoints).length} Data Sources</span>
        {registry.length > 0 && (
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>
            {registry.filter(s => s.integrated).length}/{registry.length} confirmed integrated (sources-registry)
          </span>
        )}
      </div>
      {Object.entries(groups).map(([group, sources]) => sources.length > 0 && (
        <div key={group} style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>{group} ({sources.length})</div>
          <table className="ares-table">
            <thead><tr><th>Name</th><th>Chain</th><th>Status</th></tr></thead>
            <tbody>
              {sources.map(([k, v]) => {
                const reg = registryByKey.get(k.toLowerCase().replace(/[^a-z0-9]/g, ''))
                return (
                  <tr key={k}>
                    <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{k}</td>
                    <td>{v.chain}</td>
                    <td>
                      {reg ? (
                        <span style={{ fontSize: 10, color: reg.integrated ? 'var(--green)' : 'var(--muted)' }}>
                          {reg.integrated ? '✓ integrated' : 'listed, not integrated'}
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, color: 'var(--muted)' }}>—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

function AresAlpha() {
  return <AlphaPanel />
}

// ══════════════════════════════════════════════════════════════════════════════
// MARKET INTEL SHELL — internal sub-tab bar over the relocated intel tabs
// ══════════════════════════════════════════════════════════════════════════════

function AresYields() {
  return <YieldsPanel />
}

function AresDex() {
  return <DexPanel />
}

function AresAllTokens() {
  return <AllTokensPanel />
}

function AresWhales() {
  // confirmed block whales + mempool (analytics/WhalesPanel.tsx)
  return <WhalesPanel />
}

function AresFx() {
  // converter, local-currency crypto prices, full table (analytics/FxPanel.tsx)
  return <FxPanel />
}

function AresBacktest() {
  const [symbol, setSymbol] = useState('BTC')
  const [days, setDays] = useState(90)
  const [fast, setFast] = useState(10)
  const [slow, setSlow] = useState(30)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const run = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/intel/backtest?symbol=${encodeURIComponent(symbol)}&days=${days}&fast=${fast}&slow=${slow}`)
      if (r.ok) setData(await r.json())
    } catch {}
    setLoading(false)
  }, [symbol, days, fast, slow])
  useEffect(() => { run() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="ares-input" placeholder="Symbol (e.g. BTC)" value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} style={{ maxWidth: 140 }} />
        <input className="ares-input" type="number" min={30} max={365} value={days} onChange={e => setDays(Number(e.target.value))} style={{ maxWidth: 100 }} title="days of history" />
        <input className="ares-input" type="number" min={2} max={100} value={fast} onChange={e => setFast(Number(e.target.value))} style={{ maxWidth: 70 }} title="fast SMA window" />
        <span style={{ color: 'var(--muted)', fontSize: 11 }}>/</span>
        <input className="ares-input" type="number" min={3} max={200} value={slow} onChange={e => setSlow(Number(e.target.value))} style={{ maxWidth: 70 }} title="slow SMA window" />
        <button className="btn btn-primary btn-sm" onClick={run} disabled={loading}>{loading ? 'Running…' : 'Run Backtest'}</button>
      </div>
      {data?.error ? (
        <div style={{ color: 'var(--muted)', padding: 20 }}>{data.error === 'insufficient data' ? `Not enough history for ${data.symbol} over ${days} days.` : data.error}</div>
      ) : data ? (
        <div>
          <div className="ares-section-title">{data.strategy} vs Buy &amp; Hold — {data.symbol} ({data.days}d, {data.data_points} closes{data.price_src ? ` · ${data.price_src === 'ohlc' ? 'Kraken/Binance' : 'CoinGecko'}` : ''})</div>
          <div className="ares-stat-grid">
            <div className="ares-stat-tile"><div className="ares-stat-label">Strategy Return</div><div className="ares-stat-value" style={{ color: data.strategy_return_pct >= 0 ? 'var(--green)' : 'var(--danger)' }}>{data.strategy_return_pct}%</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Buy &amp; Hold Return</div><div className="ares-stat-value">{data.buy_hold_return_pct}%</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Trades</div><div className="ares-stat-value">{data.trades}</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Win Rate</div><div className="ares-stat-value">{data.win_rate_pct}%</div></div>
            <div className="ares-stat-tile" title="worst peak-to-trough decline of the strategy's own equity curve -- can be deep even when total return beats buy-and-hold">
              <div className="ares-stat-label">Max Drawdown</div><div className="ares-stat-value" style={{ color: 'var(--danger)' }}>{data.max_drawdown_pct}%</div>
            </div>
          </div>
          <div style={{ marginTop: 12, fontSize: 12, color: data.beat_buy_hold ? 'var(--green)' : 'var(--muted)' }}>
            {data.beat_buy_hold ? 'Strategy beat buy-and-hold over this period.' : 'Buy-and-hold beat the strategy over this period.'}
          </div>
        </div>
      ) : (
        <div style={{ color: 'var(--muted)', padding: 20 }}>{loading ? 'Running…' : 'Run a backtest.'}</div>
      )}
      <QuantBacktestPanel />
    </div>
  )
}

// ── Wallet fund-flow trace: bitcoin + solana only (see backend/market_sources.py
// address_lookup for why), one hop per call — clicking a counterparty pivots the
// trace onto it, there is no automatic multi-hop crawling. ─────────────────────
type TraceHop = { chain: string; address: string }

// ── Trace by Token — runs pumpfun_wallet_intel.py's own deployer/top-holder/
// top-trader/first-buyer extraction on demand for any token, instead of
// waiting for its background daemon cycle to reach it. Every wallet found
// is persisted the same way the daemon does (token_wallet_roles +
// tracked_wallets), so it's a real node in the money-flow graph
// immediately — this is the actual "high signal token → back-track
// investors → find wallets to track" loop, just triggerable on demand.
function FirstBuyersStatus({ t, n }: { t?: any; n: number }) {
  if (!t) return null
  const txt = t.from === 'stored_trace'
    ? `origin trace (${t.source || 'rpc'}${t.reached_origin ? ', reached the create tx' : ', partial'})`
    : t.from === 'quick_walk' ? 'quick walk (no origin trace yet)'
    : t.status === 'pending' || t.status === 'tracing' || t.status === 'walking'
      ? `queued for the origin trace (${t.status}) -- walking to the create tx on the free RPC can take minutes; trace again later`
    : t.status === 'failed' ? `origin trace failed: ${t.last_error || 'unknown'} -- it will retry`
    : t.status === 'unavailable' ? 'origin tracer unavailable' : ''
  if (!txt) return null
  return <div style={{ fontSize: 11, color: n ? 'var(--muted)' : '#ffaa00', marginBottom: 6 }}>{txt}</div>
}

function TraceByToken() {
  const [mint, setMint] = useState('')
  const [symbol, setSymbol] = useState('')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function run() {
    if (!mint.trim()) return
    setLoading(true); setError(''); setResult(null)
    try {
      const key = localStorage.getItem('vantage_api_key') || ''
      const r = await fetch(`/api/intel/pumpfun/trace-token/${encodeURIComponent(mint.trim())}?symbol=${encodeURIComponent(symbol.trim())}`, {
        method: 'POST', headers: { 'X-Agent-Key': key },
      })
      const d = await r.json()
      if (r.ok) setResult(d)
      else setError(d.detail || 'Trace failed')
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setLoading(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="ares-input" placeholder="Token contract address (mint)" value={mint}
          onChange={e => setMint(e.target.value)} onKeyDown={e => e.key === 'Enter' && run()}
          style={{ minWidth: 320, flex: 1, fontFamily: 'monospace' }} />
        <input className="ares-input" placeholder="Symbol (optional)" value={symbol}
          onChange={e => setSymbol(e.target.value)} style={{ maxWidth: 140 }} />
        <button className="btn btn-primary btn-sm" onClick={run} disabled={loading}>{loading ? 'Tracing… (real Helius/Birdeye calls, ~5-10s)' : 'Trace Token'}</button>
      </div>

      {!result && !loading && (
        <div style={{ color: 'var(--muted)', padding: 20 }}>
          Paste any token's contract address — pulls its real deployer, top holders, top traders, and first buyers via Helius/Birdeye, and persists every wallet found into the money-flow graph (same pipeline pumpfun_wallet_intel.py's background daemon already runs for tokens in Top5/must-buy-20 — this just runs it immediately for a token you pick).
        </div>
      )}
      {error && <div style={{ color: 'var(--danger)', padding: 12 }}>{error}</div>}

      {result && (
        <div>
          <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
            <div className="ares-stat-tile"><div className="ares-stat-label">Wallets Tracked</div><div className="ares-stat-value">{result.wallets_tracked}</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Top Holders</div><div className="ares-stat-value">{result.top_holders.length}</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Top Traders</div><div className="ares-stat-value">{result.top_traders.length}</div></div>
            {result.concentrated && <div className="ares-stat-tile"><div className="ares-stat-label">⚠️ Concentration</div><div className="ares-stat-value" style={{ color: 'var(--danger)' }}>Top 5 holders own 20%+</div></div>}
          </div>

          {result.deployer && (
            <div style={{ marginBottom: 12, fontSize: 12 }}>Deployer: <WalletLink address={result.deployer} chain="solana" /></div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Top Holders</div>
              {result.top_holders.map((h: any, i: number) => (
                <div key={i} style={{ fontSize: 12, padding: '4px 0', display: 'flex', justifyContent: 'space-between' }}>
                  <WalletLink address={h.wallet} chain="solana" />
                  <span style={{ color: 'var(--muted)', fontFamily: 'monospace' }}>{h.amount?.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                </div>
              ))}
            </div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Top Traders</div>
              {result.top_traders.map((t: any, i: number) => (
                <div key={i} style={{ fontSize: 12, padding: '4px 0', display: 'flex', justifyContent: 'space-between' }}>
                  <WalletLink address={t.wallet} chain="solana" />
                  <span style={{ color: 'var(--muted)' }}>{t.txn_count} txns</span>
                </div>
              ))}
            </div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>First Buyers</div>
              <FirstBuyersStatus t={result.first_buyers_trace} n={result.first_buyers.length} />
              {result.first_buyers.map((b: any, i: number) => (
                <div key={i} style={{ fontSize: 12, padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                    <span>{b.rank ? <span style={{ color: 'var(--muted)', marginRight: 4 }}>#{b.rank}</span> : null}<WalletLink address={b.wallet} chain="solana" />{b.is_major ? <span title="Largest or earliest entry" style={{ marginLeft: 4, color: '#ffaa00' }}>★</span> : null}</span>
                    {typeof b.sol_spent === 'number' && <span style={{ color: 'var(--muted)' }}>{b.sol_spent.toFixed(2)} SOL</span>}
                  </div>
                  {(b.labels?.length > 0) && <div style={{ marginTop: 2 }}><LabelChips labels={b.labels} /></div>}
                  {b.funded_by && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>funded by <WalletLink address={b.funded_by} chain="solana" /> {b.funder_labels?.length > 0 && <LabelChips labels={b.funder_labels} />}</div>}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function AresTrace() {
  return <TracePanel tokenMode={<TraceByToken />} />
}

// ── Unified ingestion — paste a Twitter/X link or handle, Telegram link or
// handle, Solana wallet address, or Polymarket/EVM wallet address; the
// backend auto-detects and routes to the real existing tracker for that
// domain (see backend/routers/intel.py POST /api/intel/ingest). Falls back
// to an explicit type selector only when detection is genuinely ambiguous
// (a bare @handle could be Twitter or Telegram). ───────────────────────────
function AresIngest() {
  return <AddTrackerPanel />
}

const ADDRESS_TYPE_LABELS: Record<string, string> = {
  wallet: 'Wallet', exchange: 'Exchange', contract: 'Contract (CA)', smart_wallet: 'Smart Wallet',
}
const ADDRESS_TYPE_COLORS: Record<string, string> = {
  wallet: '#00f5ff', exchange: '#f59e0b', contract: '#a855f7', smart_wallet: '#4ade80',
}
function TypeBadge({ type }: { type: string }) {
  const color = ADDRESS_TYPE_COLORS[type] || '#8892a6'
  return (
    <span style={{ fontSize: 10, fontWeight: 700, color, border: `1px solid ${color}`, borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap' }}>
      {ADDRESS_TYPE_LABELS[type] || type}
    </span>
  )
}


// AresCharts (native OHLC + Pine editor) moved to TradingDashboard.tsx, which
// is now the default Trading tab — kept out of this file to avoid two
// disconnected chart surfaces existing side by side.

const INTEL_TABS = [
  { id: 'top5', label: 'Top 5', icon: Flame },
  { id: 'ingest', label: 'Add Tracker', icon: PlusCircle },
  { id: 'overview',  label: 'Overview',  icon: Radio },
  { id: 'trace',     label: 'Trace',     icon: Waypoints },
  { id: 'arbitrage', label: 'Arbitrage', icon: TrendingUp },
  { id: 'alpha',     label: 'Alpha',     icon: TrendingUp },
  { id: 'yields',    label: 'Yields',    icon: Layers },
  { id: 'dex',       label: 'DEX',       icon: Droplets },
  { id: 'alltokens', label: 'All Tokens', icon: ListOrdered },
  { id: 'whales',    label: 'Whales',    icon: Waves },
  { id: 'moneyflow', label: 'Money Flow', icon: Share2 },
  { id: 'fx',        label: 'FX',        icon: DollarSign },
  { id: 'backtest',  label: 'Backtest',  icon: History },
  { id: 'sentiment', label: 'Sentiment', icon: Zap },
  { id: 'health',    label: 'Health',    icon: Activity },
  { id: 'sources',   label: 'Sources',   icon: Database },
  { id: 'intel',     label: 'Raw Intel', icon: BarChart3 },
  { id: 'agent-intel', label: 'Agent Intel', icon: Brain },
  { id: 'council', label: 'Council', icon: Scale },
  { id: 'shadow-account', label: 'Shadow Account', icon: UserCog },
]

export default function MarketIntel() {
  const [tab, setTab] = useState('overview')
  return (
    <div>
      <div className="top-nav-tabs" style={{ flexWrap: 'wrap', borderBottom: '1px solid var(--border)', paddingBottom: 8, marginBottom: 20 }}>
        {INTEL_TABS.map(t => (
          <button key={t.id} type="button" className={`top-nav-tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>
      <FeedStatus />
      {tab === 'top5' && <Top5Degen />}
      {tab === 'ingest' && <AresIngest />}
      {tab === 'overview' && <AresOverview />}
      {tab === 'trace' && <AresTrace />}
      {tab === 'arbitrage' && <AresArbitrage />}
      {tab === 'alpha' && <AresAlpha />}
      {tab === 'yields' && <AresYields />}
      {tab === 'dex' && <AresDex />}
      {tab === 'alltokens' && <AresAllTokens />}
      {tab === 'whales' && <AresWhales />}
      {tab === 'moneyflow' && <MoneyFlowGraph />}
      {tab === 'fx' && <AresFx />}
      {tab === 'backtest' && <AresBacktest />}
      {tab === 'sentiment' && <AresSentiment />}
      {tab === 'health' && <AresHealth />}
      {tab === 'sources' && <AresSources />}
      {tab === 'intel' && <AresIntel />}
      {tab === 'agent-intel' && <AgentIntel />}
      {tab === 'council' && <CouncilSection />}
      {tab === 'shadow-account' && <ShadowAccountSection />}
    </div>
  )
}
