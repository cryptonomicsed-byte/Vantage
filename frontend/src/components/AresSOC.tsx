import React, { useState, useEffect, useCallback } from 'react'
import { Shield, AlertTriangle, Activity, FileText, Terminal, CheckCircle, XCircle, RefreshCw, Lock } from 'lucide-react'

// ══════════════════════════════════════════════════════════════════════════════
// ARES Sentinel Control — admin-only SOC (security operations) console at /ares.
// Market-intelligence and trading have been promoted to the main-app Trading
// section (see components/TradingSection.tsx); this console is security-only.
// ══════════════════════════════════════════════════════════════════════════════

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

// ── Shared helpers ───────────────────────────────────────────────────────────
function Badge({ status }: { status: string }) {
  const map: Record<string, string> = { pending: '#ffaa00', approved: '#39ff14', rejected: '#ff2d4a', ok: '#39ff14', degraded: '#ff2d4a', healthy: '#39ff14', congested: '#ff2d4a', live: '#39ff14', neutral: '#ffaa00', long: '#39ff14', pass: '#6b7280' }
  const c = map[status.toLowerCase()] || '#6b7280'
  return <span style={{ fontSize: 10, fontWeight: 700, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>{status}</span>
}

// ══════════════════════════════════════════════════════════════════════════════
// TAB CONFIG — Vantage SOC (security) only
// ══════════════════════════════════════════════════════════════════════════════

const SOC_TABS = [
  { id: 'soc-overview',     label: 'SOC Overview',    icon: Shield },
  { id: 'soc-threat',       label: 'Threat Map',      icon: AlertTriangle },
  { id: 'soc-governance',   label: 'Governance',      icon: FileText },
  { id: 'soc-diagnostics',  label: 'Diagnostics',     icon: Activity },
  { id: 'soc-logs',         label: 'Logs',            icon: Terminal },
]

type TabId = string

// ══════════════════════════════════════════════════════════════════════════════
// VANTAGE SOC TABS
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

const SocThreat = ({ adminFetch }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
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

const SocGovernance = ({ adminFetch }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
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

const SocDiagnostics = ({ adminFetch }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
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
// ROOT COMPONENT — security operations console only
// ══════════════════════════════════════════════════════════════════════════════

export default function AresSOC() {
  const [adminKey, setAdminKey] = useState(() => sessionStorage.getItem('ares_admin_key') || '')
  const [activeTab, setActiveTab] = useState<TabId>('soc-overview')
  const [toast, setToast] = useState<Toast>(null)
  const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => { setToast({ message, type }); setTimeout(() => setToast(null), 3000) }, [])
  const adminFetch = useCallback((path: string, opts: RequestInit = {}) => fetch(path, { ...opts, headers: { 'X-Admin-Key': adminKey, 'Content-Type': 'application/json', ...(opts.headers || {}) } }), [adminKey])

  if (!adminKey) return <LoginScreen onAuth={setAdminKey} />

  return (
    <div className="ares-soc">
      {/* Header */}
      <div className="ares-soc-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ fontSize: 18, fontWeight: 700 }}>🛡️ SENTINEL CONTROL</div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="ares-soc-tabs">
        {SOC_TABS.map(t => (
          <button key={t.id} className={`ares-soc-tab ${activeTab === t.id ? 'active' : ''}`} onClick={() => setActiveTab(t.id)}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="ares-soc-content">
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
