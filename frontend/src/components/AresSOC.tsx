import React, { useState, useEffect, useCallback } from 'react'
import { Shield, AlertTriangle, Activity, FileText, Terminal, CheckCircle, XCircle, RefreshCw, Lock, TrendingUp, BarChart3, Zap, Wallet, Globe, Brain, Search, Server, Layers, Database, ArrowUpDown, PieChart, Radio } from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────
type Toast = { message: string; type: 'success' | 'error' } | null

function timeAgo(iso: string): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function fmt(n: number | null | undefined, d = '—'): string {
  if (n === null || n === undefined) return d
  if (typeof n !== 'number') return String(n)
  if (Math.abs(n) > 1_000_000) return '$' + (n / 1_000_000).toFixed(1) + 'M'
  if (Math.abs(n) > 1_000) return '$' + (n / 1_000).toFixed(1) + 'K'
  return '$' + n.toFixed(2)
}

// ── Shared helpers ───────────────────────────────────────────────────────────
function Badge({ status }: { status: string }) {
  const map: Record<string, string> = { pending: '#ffaa00', approved: '#39ff14', rejected: '#ff2d4a', ok: '#39ff14', degraded: '#ff2d4a', healthy: '#39ff14', congested: '#ff2d4a', live: '#39ff14', neutral: '#ffaa00', long: '#39ff14', pass: '#6b7280' }
  const c = map[status.toLowerCase()] || '#6b7280'
  return <span style={{ fontSize: 10, fontWeight: 700, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>{status}</span>
}

// ══════════════════════════════════════════════════════════════════════════════
// TAB CONFIG — merges Vantage SOC + Ares Intelligence
// ══════════════════════════════════════════════════════════════════════════════

const SOC_TABS = [
  { id: 'soc-overview',     label: 'SOC Overview',    icon: Shield, group: 'vantage' },
  { id: 'soc-threat',       label: 'Threat Map',      icon: AlertTriangle, group: 'vantage' },
  { id: 'soc-governance',   label: 'Governance',      icon: FileText, group: 'vantage' },
  { id: 'soc-diagnostics',  label: 'Diagnostics',     icon: Activity, group: 'vantage' },
  { id: 'soc-logs',         label: 'Logs',            icon: Terminal, group: 'vantage' },
]

const ARES_TABS = [
  { id: 'ares-overview',    label: 'Overview',        icon: Radio, group: 'ares' },
  { id: 'ares-intel',       label: 'Intel Scan',      icon: BarChart3, group: 'ares' },
  { id: 'ares-arbitrage',   label: 'Arbitrage',       icon: TrendingUp, group: 'ares' },
  { id: 'ares-debate',      label: 'Debate',          icon: Brain, group: 'ares' },
  { id: 'ares-health',      label: 'Health',          icon: Activity, group: 'ares' },
  { id: 'ares-sentiment',   label: 'Sentiment',       icon: Zap, group: 'ares' },
  { id: 'ares-sources',     label: 'Sources',         icon: Database, group: 'ares' },
  { id: 'ares-alpha',       label: 'Alpha',           icon: TrendingUp, group: 'ares' },
]

type TabId = string

// ══════════════════════════════════════════════════════════════════════════════
// DATA FETCHING HOOK
// ══════════════════════════════════════════════════════════════════════════════

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
// ARES INTELLIGENCE TABS
// ══════════════════════════════════════════════════════════════════════════════

function AresOverview() {
  const intel = useAresApi('/api/intel', 60000)
  const debate = useAresApi('/api/debate', 60000)
  const i = intel.data
  const d = debate.data?.debates || []
  const chains = i?.health?.chains || {}
  const arbOpps = i?.arbitrage?.opportunities || []
  const fusion = i?.anomalies?.fusion || {}

  return (
    <div>
      <div className="ares-section-title" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span>ARES Intelligence</span>
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
    const chain = v.chain || ''
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
// VANTAGE SOC TABS (preserved from original)
// ══════════════════════════════════════════════════════════════════════════════

function SocOverview({ adminFetch }: { adminFetch: (path: string) => Promise<Response> }) {
  const [tel, setTel] = useState<Record<string, any> | null>(null)
  const [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    setLoading(true)
    try { const r = await adminFetch('/api/admin/telemetry'); if (r.ok) setTel(await r.json()) } catch {}
    setLoading(false)
  }, [adminFetch])
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t) }, [load])
  if (loading && !tel) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading telemetry…</div>
  if (!tel) return null
  const health = tel.swarm_health as string
  const hotspots = (tel.sentinel?.error_hotspots as Array<any>) || []
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <span className={`ares-health-badge ${health === 'ok' ? 'ares-health-ok' : 'ares-health-degraded'}`}>
          {health === 'ok' ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
          SWARM {health?.toUpperCase()}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /> Refresh</button>
      </div>
      <div className="ares-stat-grid">
        {[
          { label: 'Active Agents (15m)', value: tel.active_agents_15m },
          { label: 'Jobs Active', value: tel.job_queue?.active ?? 0 },
          { label: 'Dead Letter', value: tel.job_queue?.dead ?? 0 },
          { label: 'Open Errors', value: tel.sentinel?.open_error_reports ?? 0 },
          { label: 'Broadcasts (1h)', value: tel.content?.broadcasts_last_1h ?? 0 },
          { label: 'Active Locks', value: tel.content?.active_broadcast_locks ?? 0 },
        ].map(s => (
          <div key={s.label} className="ares-stat-tile">
            <div className="ares-stat-label">{s.label}</div>
            <div className="ares-stat-value">{s.value}</div>
          </div>
        ))}
      </div>
      {hotspots.length > 0 && (
        <>
          <div className="ares-section-title">Error Hotspots</div>
          <table className="ares-table">
            <thead><tr><th>Error Type</th><th>Count</th></tr></thead>
            <tbody>{hotspots.map((h: any, i: number) => (
              <tr key={i}><td><code style={{ color: 'var(--danger)' }}>{h.error_type}</code></td><td style={{ color: 'var(--warning)', fontWeight: 700 }}>{h.count}</td></tr>
            ))}</tbody>
          </table>
        </>
      )}
    </div>
  )
}

const SocThreat = ({ adminFetch, showToast }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
  const [hits, setHits] = useState<any[]>([]); const [anomalies, setAnomalies] = useState<any[]>([]); const [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    setLoading(true); try {
      const [hR, aR] = await Promise.all([adminFetch('/api/admin/honeypot'), adminFetch('/api/admin/anomaly-profiles')])
      if (hR.ok) setHits(await hR.json()); if (aR.ok) setAnomalies(await aR.json())
    } catch {}; setLoading(false)
  }, [adminFetch])
  useEffect(() => { load() }, [load])
  if (loading) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading threat data…</div>
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      <div>
        <div className="ares-section-title">Honeypot Hits ({hits.length})</div>
        <div style={{ maxHeight: '60vh', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
          <table className="ares-table"><thead><tr><th>Agent</th><th>Endpoint</th><th>Hits</th><th>Time</th></tr></thead>
            <tbody>{hits.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No honeypot hits</td></tr>}
              {hits.map((h, i) => <tr key={i} className={(h.hit_count as number) > 5 ? 'threat-high' : ''}>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{h.agent_name as string || '—'}</td>
                <td style={{ fontFamily: 'monospace', fontSize: 10, color: 'var(--muted)' }}>{h.endpoint as string || '—'}</td>
                <td style={{ color: (h.hit_count as number) > 5 ? 'var(--danger)' : 'var(--text)', fontWeight: 700 }}>{Number(h.hit_count)}</td>
                <td style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(h.hit_at as string)}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </div>
      <div>
        <div className="ares-section-title">Anomaly Profiles</div>
        <div style={{ maxHeight: '60vh', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
          <table className="ares-table"><thead><tr><th>Agent</th><th>Requests</th><th>Score</th></tr></thead>
            <tbody>{anomalies.length === 0 && <tr><td colSpan={3} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No anomalies</td></tr>}
              {anomalies.map((a, i) => <tr key={i}>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{a.agent_name}</td>
                <td style={{ fontWeight: 700 }}>{Number(a.request_count)}</td>
                <td style={{ color: (a.anomaly_score as number) > 1.5 ? 'var(--danger)' : 'var(--warning)' }}>{(a.anomaly_score as number)?.toFixed(2)}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

const SocGovernance = ({ adminFetch, showToast }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
  const [proposals, setProposals] = useState<any[]>([]); const [loading, setLoading] = useState(true)
  const load = useCallback(async () => { setLoading(true); try { const r = await adminFetch('/api/admin/proposals'); if (r.ok) setProposals(await r.json()) } catch {}; setLoading(false) }, [adminFetch])
  useEffect(() => { load() }, [load])
  return (
    <div>
      <div className="ares-section-title">Proposals ({proposals.length})</div>
      {loading && <div style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</div>}
      {!loading && proposals.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No pending proposals.</div>}
      {proposals.map(p => (
        <div key={p.id as number} className="ares-proposal-card">
          <div className="ares-proposal-header">
            <div><div className="ares-proposal-title">{p.command as string}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>by <code>{p.proposed_by as string}</code> · {timeAgo(p.created_at as string)} · {Number(p.votes_for)}/{Number(p.required_approvals)} approvals</div>
            </div>
            <div className="ares-proposal-actions"><Badge status={p.status as string} />
              {p.status === 'pending' && <><button className="btn btn-primary btn-sm" onClick={() => { adminFetch(`/api/admin/proposals/${p.id}/approve`, { method: 'POST', body: '{}' }); load() }}><CheckCircle size={12} /> Approve</button>
                <button className="btn btn-danger btn-sm" onClick={() => { adminFetch(`/api/admin/proposals/${p.id}/reject`, { method: 'POST', body: '{}' }); load() }}><XCircle size={12} /> Reject</button></>}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

const SocDiagnostics = ({ adminFetch, showToast }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
  const [errorMap, setErrorMap] = useState<any>(null); const [loading, setLoading] = useState(true)
  const load = useCallback(async () => { setLoading(true); try { const r = await adminFetch('/api/admin/error-map'); if (r.ok) setErrorMap(await r.json()) } catch {}; setLoading(false) }, [adminFetch])
  useEffect(() => { load() }, [load])
  if (loading) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading…</div>
  return (
    <div>
      <div className="ares-section-title">Error Map {errorMap ? `— ${errorMap.total} open` : ''}</div>
      <table className="ares-table">
        <thead><tr><th>Error Type</th><th>Count</th><th>Last Seen</th></tr></thead>
        <tbody>{(!errorMap?.hotspots?.length) && <tr><td colSpan={3} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No open errors</td></tr>}
          {errorMap?.hotspots?.map((h: any, i: number) => <tr key={i}><td><code style={{ color: 'var(--danger)' }}>{h.error_type}</code></td><td style={{ color: 'var(--warning)', fontWeight: 700 }}>{h.count}</td><td style={{ fontSize: 11, color: 'var(--muted)' }}>{timeAgo(h.last_seen)}</td></tr>)}
        </tbody>
      </table>
    </div>
  )
}

const SocLogs = ({ adminFetch }: { adminFetch: (path: string) => Promise<Response> }) => {
  const [logs, setLogs] = useState<string[]>([]); const [loading, setLoading] = useState(true)
  const load = useCallback(async () => { setLoading(true); try { const r = await adminFetch('/api/admin/logs?n=100'); if (r.ok) { const d = await r.json(); setLogs((d.logs || []).slice().reverse()) } } catch {}; setLoading(false) }, [adminFetch])
  useEffect(() => { load() }, [load])
  const classify = (line: string): 'error' | 'warn' | 'normal' => {
    const u = line.toUpperCase(); if (u.includes('ERROR') || u.includes('EXCEPTION')) return 'error'; if (u.includes('WARN')) return 'warn'; return 'normal'
  }
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}>Platform Logs</span>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /></button>
      </div>
      <div className="ares-log-list">
        {loading && <div style={{ color: 'var(--muted)' }}>Loading…</div>}
        {!loading && logs.length === 0 && <div style={{ color: 'var(--muted)' }}>No logs.</div>}
        {logs.map((line, i) => <div key={i} className={`ares-log-entry ares-log-${classify(line)}`}>{line}</div>)}
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// LOGIN SCREEN
// ══════════════════════════════════════════════════════════════════════════════

function LoginScreen({ onAuth }: { onAuth: (key: string) => void }) {
  const [key, setKey] = useState(''); const [error, setError] = useState(''); const [loading, setLoading] = useState(false)
  const attempt = async () => {
    if (!key.trim()) return; setLoading(true); setError('')
    try {
      const r = await fetch('/api/admin/stats', { headers: { 'X-Admin-Key': key } })
      if (r.ok) { sessionStorage.setItem('ares_admin_key', key); onAuth(key) } else setError('Access denied.')
    } catch { setError('Connection failed.') }; setLoading(false)
  }
  return (
    <div className="ares-login">
      <div className="ares-login-card">
        <div className="ares-login-title"><Lock size={18} style={{ display: 'inline', marginRight: 8, verticalAlign: 'middle' }} />ARES SENTINEL CONTROL</div>
        <div style={{ color: 'var(--muted)', fontSize: 12, textAlign: 'center', lineHeight: 1.5 }}>Restricted access. Administrator credentials required.</div>
        <div className="ares-form-group"><label className="ares-form-label">Admin Key</label>
          <input type="password" className="ares-input" placeholder="Enter admin key…" value={key} onChange={e => setKey(e.target.value)} onKeyDown={e => e.key === 'Enter' && attempt()} autoFocus /></div>
        {error && <div style={{ color: 'var(--danger)', fontSize: 12, textAlign: 'center' }}>{error}</div>}
        <button className="btn btn-danger" style={{ width: '100%' }} onClick={attempt} disabled={loading || !key.trim()}>{loading ? 'Authenticating…' : 'Enter Control Room'}</button>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// ROOT COMPONENT
// ══════════════════════════════════════════════════════════════════════════════

export default function AresSOC() {
  const [adminKey, setAdminKey] = useState(() => sessionStorage.getItem('ares_admin_key') || '')
  const [activeTab, setActiveTab] = useState<TabId>('ares-overview')
  const [group, setGroup] = useState<'ares' | 'soc'>('ares')
  const [toast, setToast] = useState<Toast>(null)
  const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => { setToast({ message, type }); setTimeout(() => setToast(null), 3000) }, [])
  const adminFetch = useCallback((path: string, opts: RequestInit = {}) => fetch(path, { ...opts, headers: { 'X-Admin-Key': adminKey, 'Content-Type': 'application/json', ...(opts.headers || {}) } }), [adminKey])

  if (!adminKey) return <LoginScreen onAuth={setAdminKey} />

  const tabs = group === 'ares' ? ARES_TABS : SOC_TABS

  return (
    <div className="ares-soc">
      {/* Header */}
      <div className="ares-soc-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ fontSize: 18, fontWeight: 700 }}>{group === 'ares' ? '🦅 ARES INTELLIGENCE' : '🛡️ SENTINEL CONTROL'}</div>
          <div style={{ display: 'flex', gap: 4, background: 'rgba(255,255,255,0.05)', borderRadius: 8, padding: 2 }}>
            <button className={`btn btn-${group === 'ares' ? 'primary' : 'ghost'} btn-sm`} onClick={() => setGroup('ares')}>Intelligence</button>
            <button className={`btn btn-${group === 'soc' ? 'primary' : 'ghost'} btn-sm`} onClick={() => setGroup('soc')}>Security</button>
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="ares-soc-tabs">
        {tabs.map(t => (
          <button key={t.id} className={`ares-soc-tab ${activeTab === t.id ? 'active' : ''}`} onClick={() => setActiveTab(t.id)}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="ares-soc-content">
        {activeTab === 'ares-overview' && <AresOverview />}
        {activeTab === 'ares-intel' && <AresIntel />}
        {activeTab === 'ares-arbitrage' && <AresArbitrage />}
        {activeTab === 'ares-debate' && <AresDebate />}
        {activeTab === 'ares-health' && <AresHealth />}
        {activeTab === 'ares-sentiment' && <AresSentiment />}
        {activeTab === 'ares-sources' && <AresSources />}
        {activeTab === 'ares-alpha' && <AresAlpha />}
        {activeTab === 'soc-overview' && <SocOverview adminFetch={adminFetch} />}
        {activeTab === 'soc-threat' && <SocThreat adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-governance' && <SocGovernance adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-diagnostics' && <SocDiagnostics adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-logs' && <SocLogs adminFetch={adminFetch} />}
      </div>

      {/* Toast */}
      {toast && <div className={`ares-toast ares-toast-${toast.type}`}>{toast.message}</div>}
    </div>
  )
}
