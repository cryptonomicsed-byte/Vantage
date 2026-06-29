import React, { useState, useEffect, useCallback } from 'react'
import { TrendingUp, BarChart3, Zap, Brain, Activity, Database, Radio, RefreshCw } from 'lucide-react'

// ══════════════════════════════════════════════════════════════════════════════
// Market Intelligence — public market-data tabs relocated out of the admin (ARES)
// console into the main-app Trading section. These read public, unauthenticated
// endpoints (/api/intel, /api/alpha, /api/debate, /api/health, /api/rpc) via plain
// fetch — exactly as they did inside AresSOC, with zero auth.
// ══════════════════════════════════════════════════════════════════════════════

// ── Shared helpers (self-contained copy; SOC console keeps its own) ────────────
function Badge({ status }: { status: string }) {
  const map: Record<string, string> = { pending: '#ffaa00', approved: '#39ff14', rejected: '#ff2d4a', ok: '#39ff14', degraded: '#ff2d4a', healthy: '#39ff14', congested: '#ff2d4a', live: '#39ff14', neutral: '#ffaa00', long: '#39ff14', pass: '#6b7280' }
  const c = map[status.toLowerCase()] || '#6b7280'
  return <span style={{ fontSize: 10, fontWeight: 700, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>{status}</span>
}

// ── Data fetching hook ─────────────────────────────────────────────────────────
function useAresApi(path: string, interval = 60000) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    try {
      const r = await fetch(path)
      if (r.ok) setData(await r.json())
    } catch {}
    setLoading(false)
  }, [path])
  useEffect(() => { load(); const t = setInterval(load, interval); return () => clearInterval(t) }, [load, interval])
  return { data, loading, refresh: load }
}

// ══════════════════════════════════════════════════════════════════════════════
// INTELLIGENCE TABS
// ══════════════════════════════════════════════════════════════════════════════

function AresOverview() {
  const intel = useAresApi('/api/intel', 60000)
  const debate = useAresApi('/api/debate', 60000)
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
        <div className="ares-stat-tile"><div className="ares-stat-label">Debate</div><div className="ares-stat-value">{debate.data?.consensus || '—'}</div></div>
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
                <tr key={i}><td>{o.route}</td><td>{o.pair}</td><td style={{ color: 'var(--warning)', fontWeight: 700 }}>{o.spread_pct?.toFixed(1)}%</td></tr>
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
              <td>{o.pair}</td>
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

function AresDebate() {
  const { data, loading, refresh } = useAresApi('/api/debate', 60000)
  const debates = data?.debates || []
  if (loading && !data) return <div style={{ color: 'var(--muted)', padding: 20 }}>Loading debate…</div>
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <div className="ares-section-title" style={{ margin: 0 }}>Multi-Agent Debate</div>
        <Badge status={data?.consensus || '?'} />
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>Score: {data?.consensus_score}</span>
        <button className="btn btn-ghost btn-sm" onClick={refresh}><RefreshCw size={12} /></button>
      </div>
      <table className="ares-table">
        <thead><tr><th>Agent</th><th>Role</th><th>Verdict</th><th>Confidence</th><th>Reasoning</th></tr></thead>
        <tbody>
          {debates.map((d: any, i: number) => (
            <tr key={i}>
              <td style={{ fontWeight: 600 }}>{d.agent}</td>
              <td style={{ fontSize: 11, color: 'var(--muted)' }}>{d.perspective}</td>
              <td><Badge status={d.verdict || '?'} /></td>
              <td style={{ fontWeight: 700 }}>{d.confidence}%</td>
              <td style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.reasoning}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AresHealth() {
  const { data, loading } = useAresApi('/api/health', 30000)
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
  const { data, loading } = useAresApi('/api/rpc', 60000)
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
              <td style={{ fontWeight: 600 }}>{i.symbol || '?'}</td>
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

const INTEL_TABS = [
  { id: 'overview',  label: 'Overview',  icon: Radio },
  { id: 'arbitrage', label: 'Arbitrage', icon: TrendingUp },
  { id: 'alpha',     label: 'Alpha',     icon: TrendingUp },
  { id: 'sentiment', label: 'Sentiment', icon: Zap },
  { id: 'debate',    label: 'Debate',    icon: Brain },
  { id: 'health',    label: 'Health',    icon: Activity },
  { id: 'sources',   label: 'Sources',   icon: Database },
  { id: 'intel',     label: 'Raw Intel', icon: BarChart3 },
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
      {tab === 'overview' && <AresOverview />}
      {tab === 'arbitrage' && <AresArbitrage />}
      {tab === 'alpha' && <AresAlpha />}
      {tab === 'sentiment' && <AresSentiment />}
      {tab === 'debate' && <AresDebate />}
      {tab === 'health' && <AresHealth />}
      {tab === 'sources' && <AresSources />}
      {tab === 'intel' && <AresIntel />}
    </div>
  )
}
