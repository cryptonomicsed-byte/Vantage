import React, { useState, useEffect, useCallback } from 'react'
import { TrendingUp, BarChart3, Zap, Activity, Database, Radio, Layers, Droplets, Waves, DollarSign, History, Waypoints, ListOrdered, Share2, Flame, Brain } from 'lucide-react'
import Top5Degen from './Top5Degen'
import MoneyFlowGraph from '../MoneyFlowGraph'
import { TokenLink, WalletLink } from './EntityProfileCard'
import AgentIntel from './AgentIntel'

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
function useAresApi(path: string, interval = 60000) {
  const [data, setData] = useState<any>(() => {
    try {
      const raw = localStorage.getItem(cacheKeyFor(path))
      return raw ? JSON.parse(raw) : null
    } catch { return null }
  })
  const [loading, setLoading] = useState(data === null)
  const load = useCallback(async () => {
    try {
      const r = await fetch(path)
      if (r.ok) {
        const json = await r.json()
        setData(json)
        try { localStorage.setItem(cacheKeyFor(path), JSON.stringify(json)) } catch { /* quota — non-fatal */ }
      }
    } catch { /* offline — keep showing last-known data */ }
    setLoading(false)
  }, [path])
  useEffect(() => { load(); const t = setInterval(load, interval); return () => clearInterval(t) }, [load, interval])
  return { data, loading, refresh: load }
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
  const intel = useAresApi('/api/intel', 60000)
  const i = intel.data
  const chains = i?.health?.chains || {}
  const arbOpps = i?.arbitrage?.opportunities || []
  const fusion = i?.anomalies?.fusion || {}

  return (
    <div>
      <div className="ares-section-title" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span>Market Intelligence</span>
        <Badge status={Object.keys(chains).length > 0 ? 'live' : 'pending'} />
        {intel.loading && <span style={{ color: 'var(--muted)', fontSize: 11 }}>refreshing…</span>}
      </div>

      {/* BTC Consensus */}
      {fusion.btc_consensus && (
        <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 20, marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>BTC Consensus Price</div>
          <div style={{ fontSize: 32, fontWeight: 700, color: '#39ff14', fontFamily: 'monospace' }}>${Number(fusion.btc_consensus).toLocaleString()}</div>
          <div style={{ display: 'flex', gap: 24, marginTop: 8, fontSize: 12, color: 'var(--muted)' }}>
            <span>ETH: ${Number(fusion.eth||0).toLocaleString()}</span>
            <span>SOL: ${Number(fusion.sol||0).toFixed(2)}</span>
            <span>Sources: {fusion.sources}</span>
          </div>
        </div>
      )}

      {/* Stat grid */}
      <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
        <div className="ares-stat-tile"><div className="ares-stat-label">Chains</div><div className="ares-stat-value">{Object.keys(chains).length}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Arbitrage</div><div className="ares-stat-value" style={{ color: 'var(--warning)' }}>{arbOpps.length}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Anomalies</div><div className="ares-stat-value">{(i?.anomalies?.anomalies||[]).length}</div></div>
      </div>

      {/* Chain health */}
      <div className="ares-section-title">Chain Health ({Object.keys(chains).length})</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8, marginBottom: 16 }}>
        {Object.entries(chains).map(([k, v]: [string, any]) => (
          <div key={k} style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px' }}>
            <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'capitalize' }}>{k}</div>
            <Badge status={v.health || '?'} />
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>{Object.entries(v).filter(([k2]) => k2 !== 'health').map(([k2, v2]) => `${k2}=${v2}`).join(', ')}</div>
          </div>
        ))}
      </div>

      {/* Top arbitrage */}
      {arbOpps.length > 0 && (
        <>
          <div className="ares-section-title">Top Arbitrage ({arbOpps.length})</div>
          <table className="ares-table">
            <thead><tr><th>Route</th><th>Pair</th><th>Spread</th></tr></thead>
            <tbody>
              {arbOpps.slice(0, 5).map((o: any, i: number) => (
                <tr key={i}><td>{o.route}</td><td>{o.pair && o.pair.includes('/') ? <TokenLink symbol={o.pair.split('/')[0]} /> : o.pair}</td><td style={{ color: 'var(--warning)', fontWeight: 700 }}>{o.spread_pct?.toFixed(1)}%</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function AresIntel() {
  const { data, loading } = useAresApi('/api/intel', 30000)
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading intel…</div>
  return <pre style={{ fontSize: 11, color: 'var(--muted)', maxHeight: '70vh', overflow: 'auto', background: 'rgba(0,0,0,0.3)', padding: 16, borderRadius: 8, fontFamily: 'monospace' }}>{JSON.stringify(data, null, 2)}</pre>
}

function AresArbitrage() {
  const { data, loading } = useAresApi('/api/intel', 30000)
  const opps = data?.arbitrage?.opportunities?.filter((o: any) => o.spread_pct > 0.5 && o.spread_pct < 1000) || []
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading…</div>
  return (
    <div>
      <div className="ares-section-title">{opps.length} Arbitrage Opportunities</div>
      <table className="ares-table">
        <thead><tr><th>Route</th><th>Pair</th><th>Spread</th><th>Buy</th><th>Sell</th></tr></thead>
        <tbody>
          {opps.map((o: any, i: number) => (
            <tr key={i} className={o.spread_pct > 3 ? 'threat-high' : ''}>
              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{o.route}</td>
              <td>{o.pair && o.pair.includes('/') ? <TokenLink symbol={o.pair.split('/')[0]} /> : o.pair}</td>
              <td style={{ color: o.spread_pct > 3 ? 'var(--danger)' : 'var(--warning)', fontWeight: 700 }}>{o.spread_pct?.toFixed(1)}%</td>
              <td>${o.buy_price?.toFixed(2)}</td>
              <td>${o.sell_price?.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
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
            {Object.entries(v).filter(([k2]) => k2 !== 'health').map(([k2, v2]) => (
              <div key={k2} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', padding: '2px 0' }}>
                <span>{k2}</span>
                <span style={{ color: 'var(--text)', fontFamily: 'monospace' }}>{String(v2)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function AresSentiment() {
  const { data, loading } = useAresApi('/api/intel', 60000)
  const sent = data?.sentiment?.sentiment || {}
  const indicators = data?.sentiment?.indicators || []
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading…</div>
  return (
    <div>
      <div className="ares-section-title">Market Sentiment</div>
      <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
        {Object.entries(sent).map(([k, v]) => (
          <div key={k} className="ares-stat-tile">
            <div className="ares-stat-label">{k.replace(/_/g, ' ')}</div>
            <div className="ares-stat-value">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</div>
          </div>
        ))}
      </div>
      {indicators.length > 0 && (
        <>
          <div className="ares-section-title">Signals</div>
          {indicators.map((i: string, idx: number) => (
            <div key={idx} style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', marginBottom: 4, fontSize: 12, color: 'var(--warning)' }}>{i}</div>
          ))}
        </>
      )}
    </div>
  )
}

function AresSources() {
  const { data, loading } = useAresApi('/api/intel/sources', 60000)
  const endpoints = data?.endpoints || {}
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
      <div className="ares-section-title">{Object.keys(endpoints).length} Data Sources</div>
      {Object.entries(groups).map(([group, sources]) => sources.length > 0 && (
        <div key={group} style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>{group} ({sources.length})</div>
          <table className="ares-table">
            <thead><tr><th>Name</th><th>Chain</th></tr></thead>
            <tbody>
              {sources.map(([k, v]) => (
                <tr key={k}><td style={{ fontFamily: 'monospace', fontSize: 11 }}>{k}</td><td>{v.chain}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

function AresAlpha() {
  const { data, loading } = useAresApi('/api/alpha', 30000)
  const items = data?.items || []
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading…</div>
  return (
    <div>
      <div className="ares-section-title">{items.length} Active Signals</div>
      <table className="ares-table">
        <thead><tr><th>Token</th><th>Conviction</th><th>Price</th><th>Volume 24h</th></tr></thead>
        <tbody>
          {items.map((i: any, idx: number) => (
            <tr key={idx}>
              <td style={{ fontWeight: 600 }}>{i.symbol ? <TokenLink symbol={i.symbol} ca={i.ca || i.address} chain={i.chain} /> : '?'}</td>
              <td style={{ color: (i.conviction||0) > 3 ? 'var(--danger)' : 'var(--warning)', fontWeight: 700 }}>{(i.conviction||0).toFixed(2)}</td>
              <td style={{ fontFamily: 'monospace' }}>${(i.price||0).toFixed(8)}</td>
              <td style={{ fontFamily: 'monospace' }}>${(i.volume_24h||0).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// MARKET INTEL SHELL — internal sub-tab bar over the relocated intel tabs
// ══════════════════════════════════════════════════════════════════════════════

function AresYields() {
  const { data, loading } = useAresApi('/api/intel/yields', 120000)
  const pools = data?.pools || []
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading yields…</div>
  return (
    <div>
      <div className="ares-section-title">{pools.length} DeFi Yield Pools <span style={{ fontSize: 11, color: 'var(--muted)' }}>(DefiLlama · TVL ≥ $1M)</span></div>
      <table className="ares-table">
        <thead><tr><th>Pool</th><th>Project</th><th>Chain</th><th>APY</th><th>TVL</th></tr></thead>
        <tbody>
          {pools.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No pools loaded.</td></tr>}
          {pools.map((p: any, i: number) => (
            <tr key={i}>
              <td style={{ fontWeight: 600 }}><TokenLink symbol={p.pool} chain={p.chain} />{p.stablecoin && <span style={{ fontSize: 9, color: 'var(--cyan)', marginLeft: 4 }}>STABLE</span>}</td>
              <td style={{ fontSize: 11 }}>{p.project}</td>
              <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.chain}</td>
              <td style={{ color: 'var(--green)', fontWeight: 700 }}>{p.apy?.toFixed(1)}%</td>
              <td style={{ fontFamily: 'monospace' }}>${(p.tvl_usd / 1e6).toFixed(1)}M</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AresDex() {
  const [q, setQ] = useState('SOL')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async (query: string) => {
    setLoading(true)
    try { const r = await fetch(`/api/intel/dex?q=${encodeURIComponent(query)}`); if (r.ok) setData(await r.json()) } catch {}
    setLoading(false)
  }, [])
  useEffect(() => { load('SOL') }, [load])
  const pairs = data?.pairs || []
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <input className="ares-input" placeholder="Token (e.g. SOL, PEPE, WIF)" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && load(q)} style={{ maxWidth: 240 }} />
        <button className="btn btn-primary btn-sm" onClick={() => load(q)}>Search</button>
      </div>
      <div className="ares-section-title">{pairs.length} DEX Pairs {loading && <span style={{ fontSize: 11, color: 'var(--muted)' }}>loading…</span>}</div>
      <table className="ares-table">
        <thead><tr><th>Pair</th><th>DEX</th><th>Chain</th><th>Price</th><th>Liquidity</th><th>Vol 24h</th><th>24h</th></tr></thead>
        <tbody>
          {pairs.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No pairs.</td></tr>}
          {pairs.map((p: any, i: number) => (
            <tr key={i}>
              <td style={{ fontWeight: 600 }}>
                {p.pair && p.pair.includes('/')
                  ? <><TokenLink symbol={p.pair.split('/')[0]} ca={p.base_address} chain={p.chain} />/{p.pair.split('/')[1]}</>
                  : <TokenLink symbol={p.pair} ca={p.base_address} chain={p.chain} />}
              </td>
              <td style={{ fontSize: 11 }}>{p.dex}</td>
              <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.chain}</td>
              <td style={{ fontFamily: 'monospace' }}>{p.price_usd != null ? '$' + Number(p.price_usd).toPrecision(4) : '—'}</td>
              <td style={{ fontFamily: 'monospace' }}>{p.liquidity_usd ? '$' + (p.liquidity_usd / 1e3).toFixed(0) + 'K' : '—'}</td>
              <td style={{ fontFamily: 'monospace' }}>{p.volume_24h ? '$' + (p.volume_24h / 1e3).toFixed(0) + 'K' : '—'}</td>
              <td style={{ color: (p.change_24h || 0) >= 0 ? 'var(--green)' : 'var(--danger)', fontWeight: 700 }}>{p.change_24h != null ? p.change_24h.toFixed(1) + '%' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AresAllTokens() {
  const [network, setNetwork] = useState('solana')
  const [kind, setKind] = useState<'trending' | 'new'>('trending')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/intel/dex/pools?network=${encodeURIComponent(network)}&kind=${kind}&limit=50`)
      if (r.ok) setData(await r.json())
    } catch {}
    setLoading(false)
  }, [network, kind])
  useEffect(() => { load() }, [load])
  const pools = data?.pools || []
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <select className="ares-input" value={network} onChange={e => setNetwork(e.target.value)} style={{ maxWidth: 160 }}>
          <option value="solana">Solana</option>
          <option value="eth">Ethereum</option>
          <option value="base">Base</option>
        </select>
        <button className={`btn btn-sm ${kind === 'trending' ? 'btn-primary' : ''}`} onClick={() => setKind('trending')}>Trending</button>
        <button className={`btn btn-sm ${kind === 'new' ? 'btn-primary' : ''}`} onClick={() => setKind('new')}>New</button>
        <button className="btn btn-ghost btn-sm" onClick={load}>Refresh</button>
      </div>
      <div className="ares-section-title">
        {pools.length} pools — every pair currently trading, not just top-ranked tokens {loading && <span style={{ fontSize: 11, color: 'var(--muted)' }}>loading…</span>}
      </div>
      <div className="dash-panel" style={{ maxHeight: 560, overflowY: 'auto', padding: 0 }}>
        <table className="ares-table">
          <thead><tr><th>Pair</th><th>Price</th><th>Liquidity</th><th>Vol 24h</th><th>24h</th><th>Buys/Sells 24h</th><th>Created</th></tr></thead>
          <tbody>
            {pools.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No pools.</td></tr>}
            {pools.map((p: any, i: number) => (
              <tr key={i}>
                <td style={{ fontWeight: 600 }}>
                  {p.pair && p.pair.includes('/')
                    ? <><TokenLink symbol={p.pair.split('/')[0]} ca={p.base_address} chain={network} />/{p.pair.split('/')[1]}</>
                    : <TokenLink symbol={p.pair} ca={p.base_address} chain={network} />}
                </td>
                <td style={{ fontFamily: 'monospace' }}>{p.price_usd != null ? '$' + Number(p.price_usd).toPrecision(4) : '—'}</td>
                <td style={{ fontFamily: 'monospace' }}>{p.liquidity_usd ? '$' + (p.liquidity_usd / 1e3).toFixed(0) + 'K' : '—'}</td>
                <td style={{ fontFamily: 'monospace' }}>{p.volume_24h ? '$' + (p.volume_24h / 1e3).toFixed(0) + 'K' : '—'}</td>
                <td style={{ color: (p.change_24h_pct || 0) >= 0 ? 'var(--green)' : 'var(--danger)', fontWeight: 700 }}>{p.change_24h_pct != null ? p.change_24h_pct.toFixed(1) + '%' : '—'}</td>
                <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.buys_24h ?? '—'} / {p.sells_24h ?? '—'}</td>
                <td style={{ fontSize: 10, color: 'var(--muted)' }}>{p.created_at ? new Date(p.created_at).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function AresWhales() {
  const { data, loading } = useAresApi('/api/intel/whales', 30000)
  const txs = data?.transactions || []
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading whale activity…</div>
  return (
    <div>
      <div className="ares-section-title">Largest Recent BTC Transactions <span style={{ fontSize: 11, color: 'var(--muted)' }}>(mempool.space)</span></div>
      <table className="ares-table">
        <thead><tr><th>Tx</th><th>Value (BTC)</th><th>Fee (sat)</th><th>Size (vB)</th></tr></thead>
        <tbody>
          {txs.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No mempool data.</td></tr>}
          {txs.map((t: any, i: number) => (
            <tr key={i} className={(t.value_btc || 0) > 10 ? 'threat-high' : ''}>
              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{t.txid}</td>
              <td style={{ fontFamily: 'monospace', fontWeight: 700, color: (t.value_btc || 0) > 10 ? 'var(--warning)' : 'var(--text)' }}>{t.value_btc}</td>
              <td style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{t.fee_sat?.toLocaleString()}</td>
              <td style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{t.size_vb}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AresFx() {
  const [base, setBase] = useState('USD')
  const { data, loading, refresh } = useAresApi(`/api/intel/fx?base=${base}`, 300000)
  const rates = data?.rates || {}
  const majors = ['EUR', 'GBP', 'JPY', 'CNY', 'CAD', 'AUD', 'CHF', 'INR', 'BRL', 'MXN', 'KRW', 'SGD']
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading FX rates…</div>
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <input className="ares-input" placeholder="Base (e.g. USD)" value={base} onChange={e => setBase(e.target.value.toUpperCase())} onKeyDown={e => e.key === 'Enter' && refresh()} style={{ maxWidth: 120 }} />
        <button className="btn btn-primary btn-sm" onClick={refresh}>Refresh</button>
      </div>
      <div className="ares-section-title">{base} Exchange Rates <span style={{ fontSize: 11, color: 'var(--muted)' }}>(ExchangeRate-API)</span></div>
      {Object.keys(rates).length === 0 ? (
        <div style={{ color: 'var(--muted)', padding: 20 }}>No rates loaded — check the base currency code.</div>
      ) : (
        <div className="ares-stat-grid">
          {majors.filter(c => rates[c] != null).map(c => (
            <div key={c} className="ares-stat-tile">
              <div className="ares-stat-label">{c}</div>
              <div className="ares-stat-value">{Number(rates[c]).toFixed(4)}</div>
            </div>
          ))}
        </div>
      )}
      {data?.updated && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 12 }}>Updated: {data.updated}</div>}
    </div>
  )
}

function AresBacktest() {
  const [symbol, setSymbol] = useState('BTC')
  const [days, setDays] = useState(90)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const run = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/intel/backtest?symbol=${encodeURIComponent(symbol)}&days=${days}`)
      if (r.ok) setData(await r.json())
    } catch {}
    setLoading(false)
  }, [symbol, days])
  useEffect(() => { run() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <input className="ares-input" placeholder="Symbol (e.g. BTC)" value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} style={{ maxWidth: 140 }} />
        <input className="ares-input" type="number" min={30} max={365} value={days} onChange={e => setDays(Number(e.target.value))} style={{ maxWidth: 100 }} />
        <button className="btn btn-primary btn-sm" onClick={run} disabled={loading}>{loading ? 'Running…' : 'Run Backtest'}</button>
      </div>
      {data?.error ? (
        <div style={{ color: 'var(--muted)', padding: 20 }}>Not enough history for {data.symbol} over {days} days.</div>
      ) : data ? (
        <div>
          <div className="ares-section-title">{data.strategy} vs Buy &amp; Hold — {data.symbol} ({data.days}d)</div>
          <div className="ares-stat-grid">
            <div className="ares-stat-tile"><div className="ares-stat-label">Strategy Return</div><div className="ares-stat-value" style={{ color: data.strategy_return_pct >= 0 ? 'var(--green)' : 'var(--danger)' }}>{data.strategy_return_pct}%</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Buy &amp; Hold Return</div><div className="ares-stat-value">{data.buy_hold_return_pct}%</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Trades</div><div className="ares-stat-value">{data.trades}</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Win Rate</div><div className="ares-stat-value">{data.win_rate_pct}%</div></div>
          </div>
          <div style={{ marginTop: 12, fontSize: 12, color: data.beat_buy_hold ? 'var(--green)' : 'var(--muted)' }}>
            {data.beat_buy_hold ? 'Strategy beat buy-and-hold over this period.' : 'Buy-and-hold beat the strategy over this period.'}
          </div>
        </div>
      ) : (
        <div style={{ color: 'var(--muted)', padding: 20 }}>{loading ? 'Running…' : 'Run a backtest.'}</div>
      )}
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
              {result.first_buyers.map((b: any, i: number) => (
                <div key={i} style={{ fontSize: 12, padding: '4px 0' }}><WalletLink address={b.wallet} chain="solana" /></div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function AresTrace() {
  const [mode, setMode] = useState<'wallet' | 'token'>('wallet')
  const [chain, setChain] = useState('bitcoin')
  const [addressInput, setAddressInput] = useState('')
  const [path, setPath] = useState<TraceHop[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  const runTrace = useCallback(async (hop: TraceHop) => {
    setLoading(true)
    try {
      const r = await fetch(`/api/intel/trace/${hop.chain}/${encodeURIComponent(hop.address)}?limit=15`)
      setData(r.ok ? await r.json() : null)
    } catch { setData(null) }
    setLoading(false)
  }, [])

  function submit() {
    if (!addressInput.trim()) return
    const hop = { chain, address: addressInput.trim() }
    setPath([hop])
    runTrace(hop)
  }

  function pivot(address: string) {
    const activeChain = path[path.length - 1]?.chain || chain
    const hop = { chain: activeChain, address }
    setPath(prev => [...prev, hop].slice(-8))
    setAddressInput(address)
    runTrace(hop)
  }

  function jumpTo(i: number) {
    const truncated = path.slice(0, i + 1)
    const hop = truncated[truncated.length - 1]
    setPath(truncated)
    setAddressInput(hop.address)
    setChain(hop.chain)
    runTrace(hop)
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        <button className={`btn btn-sm ${mode === 'wallet' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMode('wallet')}>By Wallet</button>
        <button className={`btn btn-sm ${mode === 'token' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMode('token')}>By Token</button>
      </div>

      {mode === 'token' ? <TraceByToken /> : <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select className="ares-input" value={chain} onChange={e => setChain(e.target.value)} style={{ maxWidth: 120 }}>
          <option value="bitcoin">Bitcoin</option>
          <option value="solana">Solana</option>
        </select>
        <input
          className="ares-input"
          placeholder="Wallet address"
          value={addressInput}
          onChange={e => setAddressInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()}
          style={{ minWidth: 280, flex: 1 }}
        />
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={loading}>{loading ? 'Tracing…' : 'Trace'}</button>
      </div>

      {path.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
          {path.map((h, i) => (
            <React.Fragment key={i}>
              {i > 0 && <span style={{ color: 'var(--muted)' }}>→</span>}
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => jumpTo(i)}
                style={{ fontFamily: 'monospace', fontSize: 11, opacity: i === path.length - 1 ? 1 : 0.6 }}
              >
                {h.address.slice(0, 6)}…{h.address.slice(-4)}
              </button>
            </React.Fragment>
          ))}
        </div>
      )}

      {path.length === 0 && (
        <div style={{ color: 'var(--muted)', padding: 20 }}>
          Enter a Bitcoin or Solana address to see its balance and recent in/out transactions — click any counterparty address below to pivot the trace onto it.
        </div>
      )}

      {data && data.supported === false && (
        <div style={{ color: 'var(--muted)', padding: 20 }}>{data.reason}</div>
      )}

      {data && data.supported && (
        <div>
          <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
            <div className="ares-stat-tile"><div className="ares-stat-label">Balance</div><div className="ares-stat-value">{data.balance?.amount} {data.balance?.unit}</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Tx Count</div><div className="ares-stat-value">{data.tx_count}</div></div>
            <div className="ares-stat-tile"><div className="ares-stat-label">Source</div><div className="ares-stat-value" style={{ fontSize: 12 }}>{data.source}</div></div>
          </div>
          {(data.transactions || []).length === 0 ? (
            <div style={{ color: 'var(--muted)', padding: 20 }}>{data.reason || 'No recent transactions.'}</div>
          ) : (
            <table className="ares-table">
              <thead><tr><th>Direction</th><th>Amount</th><th>Fee</th><th>Counterparties</th><th>SPL Tokens</th></tr></thead>
              <tbody>
                {data.transactions.map((t: any, i: number) => (
                  <tr key={i}>
                    <td><Badge status={t.direction === 'in' ? 'live' : 'pending'} /> {t.direction}</td>
                    <td style={{ fontFamily: 'monospace', fontWeight: 700 }}>{t.amount}</td>
                    <td style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{t.fee}</td>
                    <td>
                      {(t.counterparties || []).map((c: any, j: number) => (
                        <button
                          key={j}
                          className="btn btn-ghost btn-sm"
                          onClick={() => pivot(c.address)}
                          title={`${c.role} · ${c.amount}`}
                          style={{ fontFamily: 'monospace', fontSize: 10, marginRight: 4, marginBottom: 2 }}
                        >
                          {c.address.slice(0, 6)}…{c.address.slice(-4)}
                        </button>
                      ))}
                    </td>
                    <td>
                      {(t.token_transfers || []).map((tt: any, k: number) => (
                        <div key={k} style={{ marginBottom: 4 }}>
                          <span style={{ fontFamily: 'monospace', fontSize: 10, color: tt.direction === 'in' ? 'var(--green)' : 'var(--danger)' }}>
                            {tt.direction === 'in' ? '+' : '−'}{tt.amount} <span style={{ color: 'var(--muted)' }}>{tt.mint.slice(0, 4)}…{tt.mint.slice(-4)}</span>
                          </span>
                          {(tt.counterparties || []).map((c: any, j: number) => (
                            <button
                              key={j}
                              className="btn btn-ghost btn-sm"
                              onClick={() => pivot(c.address)}
                              title={`${c.role} · ${c.amount}`}
                              style={{ fontFamily: 'monospace', fontSize: 10, marginLeft: 4 }}
                            >
                              {c.address.slice(0, 6)}…{c.address.slice(-4)}
                            </button>
                          ))}
                        </div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      </>}
    </div>
  )
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
      {tab === 'top5' && <Top5Degen />}
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
    </div>
  )
}
