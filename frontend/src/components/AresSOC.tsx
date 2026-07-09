import React, { useState, useEffect, useCallback } from 'react'
import {
  Shield, AlertTriangle, Activity, FileText, Terminal, CheckCircle, XCircle, RefreshCw, Lock,
  ShieldAlert, Users, ListChecks, ToggleLeft, ToggleRight, Award, Link2, Archive, Plus, Trash2, PlayCircle,
} from 'lucide-react'

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
  const map: Record<string, string> = {
    pending: '#ffaa00', approved: '#39ff14', rejected: '#ff2d4a', ok: '#39ff14', degraded: '#ff2d4a',
    healthy: '#39ff14', congested: '#ff2d4a', live: '#39ff14', neutral: '#ffaa00', long: '#39ff14', pass: '#6b7280',
    clean: '#39ff14', quarantined: '#ff2d4a', running: '#ffaa00', complete: '#39ff14', error: '#ff2d4a',
    open: '#ffaa00', claimed: '#ffaa00', submitted: '#39ff14', strix: '#8b5cf6', regex: '#6b7280',
  }
  const c = map[status.toLowerCase()] || '#6b7280'
  return <span style={{ fontSize: 10, fontWeight: 700, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>{status}</span>
}

// ══════════════════════════════════════════════════════════════════════════════
// TAB CONFIG — Vantage SOC (security) only
// ══════════════════════════════════════════════════════════════════════════════

const SOC_TABS = [
  { id: 'soc-overview',     label: 'SOC Overview',    icon: Shield },
  { id: 'soc-threat',       label: 'Threat Map',      icon: AlertTriangle },
  { id: 'soc-security',     label: 'Security Scans',  icon: ShieldAlert },
  { id: 'soc-agents',       label: 'Agents',          icon: Users },
  { id: 'soc-jobs',         label: 'Job Conductor',   icon: ListChecks },
  { id: 'soc-governance',   label: 'Governance & Audit', icon: FileText },
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

const SocSecurity = ({ adminFetch }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
  const [scans, setScans] = useState<any[]>([]); const [codeScans, setCodeScans] = useState<any[]>([]); const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('')
  const load = useCallback(async () => {
    setLoading(true); try {
      const q = statusFilter ? `?status=${statusFilter}` : ''
      const [sR, cR] = await Promise.all([adminFetch(`/api/admin/security-scans${q}`), adminFetch('/api/admin/code-scans')])
      if (sR.ok) setScans((await sR.json()).scans || []); if (cR.ok) setCodeScans((await cR.json()).scans || [])
    } catch {}; setLoading(false)
  }, [adminFetch, statusFilter])
  useEffect(() => { load() }, [load])
  if (loading && !scans.length && !codeScans.length) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading scan history…</div>
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <span className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}>Security Scans (Parrot · SSTImap · XSStrike · Atomic) — {scans.length}</span>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /></button>
        {['', 'clean', 'quarantined', 'vulnerable', 'flagged'].map(s => (
          <button key={s || 'all'} className={`btn btn-ghost btn-sm ${statusFilter === s ? 'active' : ''}`} style={statusFilter === s ? { borderColor: 'var(--accent)' } : {}} onClick={() => setStatusFilter(s)}>{s || 'all'}</button>
        ))}
      </div>
      <table className="ares-table">
        <thead><tr><th>Agent</th><th>Type</th><th>Ref</th><th>Status</th><th>Normalized</th><th>Findings</th><th>Started</th></tr></thead>
        <tbody>{scans.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No scans</td></tr>}
          {scans.map((s: any) => <tr key={s.id}>
            <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.agent_id ?? '—'}</td>
            <td>{s.artifact_type}</td>
            <td style={{ fontFamily: 'monospace', fontSize: 10, color: 'var(--muted)' }}>{s.artifact_ref || '—'}</td>
            <td><Badge status={s.status} /></td>
            <td>{s.normalized ? 'yes' : 'no'}</td>
            <td style={{ color: (s.findings?.length || 0) > 0 ? 'var(--danger)' : 'var(--muted)' }}>{s.findings?.length || 0}</td>
            <td style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(s.started_at)}</td>
          </tr>)}
        </tbody>
      </table>

      <div className="ares-section-title">Code Scans (Strix / Regex) — {codeScans.length}</div>
      <table className="ares-table">
        <thead><tr><th>Repo</th><th>Engine</th><th>Status</th><th>Findings</th><th>Started</th></tr></thead>
        <tbody>{codeScans.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No code scans</td></tr>}
          {codeScans.map((s: any) => <tr key={s.id}>
            <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.owner}/{s.name}</td>
            <td><Badge status={s.engine} /></td>
            <td><Badge status={s.status} /></td>
            <td style={{ color: (s.findings?.length || 0) > 0 ? 'var(--danger)' : 'var(--muted)' }}>{s.findings?.length || 0}</td>
            <td style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(s.started_at)}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
  )
}

const SocAgents = ({ adminFetch, showToast }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
  const [agents, setAgents] = useState<any[]>([]); const [rateLoad, setRateLoad] = useState<any>(null); const [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    setLoading(true); try {
      const [aR, rR] = await Promise.all([adminFetch('/api/admin/agents'), adminFetch('/api/admin/rate-limit-status')])
      if (aR.ok) setAgents(await aR.json()); if (rR.ok) setRateLoad(await rR.json())
    } catch {}; setLoading(false)
  }, [adminFetch])
  useEffect(() => { load() }, [load])

  const toggleLock = async (a: any) => {
    const action = a.agent_status === 'suspended' ? 'unlock' : 'lock'
    const r = await adminFetch(`/api/admin/agents/${a.id}/${action}`, { method: 'POST', body: '{}' })
    if (r.ok) { showToast(`${a.name} ${action}ed`); load() } else showToast('Action failed', 'error')
  }
  const toggleJail = async (a: any) => {
    const r = a.jail_mode
      ? await adminFetch(`/api/admin/agents/${a.id}/jail-mode`, { method: 'DELETE' })
      : await adminFetch(`/api/admin/agents/${a.id}/jail-mode`, { method: 'POST', body: '{}' })
    if (r.ok) { showToast(`${a.name} ${a.jail_mode ? 'released' : 'jailed'}`); load() } else showToast('Action failed', 'error')
  }
  const setTier = async (a: any, delta: number) => {
    const tier = Math.max(0, Math.min(5, (a.tier || 0) + delta))
    const r = await adminFetch(`/api/admin/agents/${a.id}/tier`, { method: 'PATCH', body: JSON.stringify({ tier }) })
    if (r.ok) load()
  }

  if (loading && !agents.length) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading agents…</div>
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <span className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}>Agents ({agents.length})</span>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /></button>
      </div>
      <div style={{ maxHeight: '50vh', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 20 }}>
        <table className="ares-table">
          <thead><tr><th>Name</th><th>Status</th><th>Tier</th><th>Jail</th><th>Reputation</th><th>Balance</th><th>Actions</th></tr></thead>
          <tbody>{agents.map((a: any) => <tr key={a.id}>
            <td>{a.name}</td>
            <td><Badge status={a.agent_status} /></td>
            <td style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <button className="btn btn-ghost btn-sm" style={{ padding: '0 4px' }} onClick={() => setTier(a, -1)}>−</button>
              {a.tier ?? 0}
              <button className="btn btn-ghost btn-sm" style={{ padding: '0 4px' }} onClick={() => setTier(a, 1)}>+</button>
            </td>
            <td>{a.jail_mode ? <span style={{ color: 'var(--danger)' }}>jailed</span> : '—'}</td>
            <td>{a.reputation ?? '—'}</td>
            <td>{a.token_balance ?? 0}</td>
            <td style={{ display: 'flex', gap: 6 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => toggleLock(a)}>{a.agent_status === 'suspended' ? 'Unlock' : 'Lock'}</button>
              <button className="btn btn-ghost btn-sm" onClick={() => toggleJail(a)}>{a.jail_mode ? 'Release' : 'Jail'}</button>
            </td>
          </tr>)}</tbody>
        </table>
      </div>

      <div className="ares-section-title">Live Rate-Limit Load {rateLoad ? `(${rateLoad.limit}/${rateLoad.window_seconds}s per agent)` : ''}</div>
      <table className="ares-table">
        <thead><tr><th>Agent</th><th>Requests</th><th>% of Limit</th></tr></thead>
        <tbody>{(!rateLoad?.agents?.length) && <tr><td colSpan={3} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No active request load</td></tr>}
          {rateLoad?.agents?.map((r: any) => <tr key={r.agent_id}>
            <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.agent_name}</td>
            <td>{r.requests_in_window} / {r.limit}</td>
            <td style={{ color: r.pct_of_limit > 80 ? 'var(--danger)' : r.pct_of_limit > 50 ? 'var(--warning)' : 'var(--muted)', fontWeight: 700 }}>{r.pct_of_limit}%</td>
          </tr>)}
        </tbody>
      </table>
    </div>
  )
}

const SocJobs = ({ adminFetch }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
  const [overview, setOverview] = useState<any>(null); const [loading, setLoading] = useState(true)
  const load = useCallback(async () => { setLoading(true); try { const r = await adminFetch('/api/admin/jobs-overview'); if (r.ok) setOverview(await r.json()) } catch {}; setLoading(false) }, [adminFetch])
  useEffect(() => { load() }, [load])
  if (loading && !overview) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading job conductor state…</div>
  const jobsByStatus = overview?.jobs_by_status || {}
  const tasksByStatus = overview?.tasks_by_status || {}
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}>Job Conductor</span>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /></button>
      </div>
      <div className="ares-stat-grid">
        {[
          { label: 'Jobs Open', value: jobsByStatus.open ?? 0 },
          { label: 'Jobs Complete', value: jobsByStatus.complete ?? 0 },
          { label: 'Tasks Open', value: tasksByStatus.open ?? 0 },
          { label: 'Tasks Claimed', value: tasksByStatus.claimed ?? 0 },
          { label: 'Tasks Approved', value: tasksByStatus.approved ?? 0 },
          { label: 'Expired Leases', value: overview?.expired_leases ?? 0 },
        ].map(s => (
          <div key={s.label} className="ares-stat-tile">
            <div className="ares-stat-label">{s.label}</div>
            <div className="ares-stat-value" style={s.label === 'Expired Leases' && s.value > 0 ? { color: 'var(--danger)' } : {}}>{s.value}</div>
          </div>
        ))}
      </div>
      <div className="ares-section-title">Recent Jobs</div>
      <table className="ares-table">
        <thead><tr><th>Title</th><th>Type</th><th>Status</th><th>Tasks</th><th>Created</th></tr></thead>
        <tbody>{(!overview?.recent_jobs?.length) && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No jobs yet</td></tr>}
          {overview?.recent_jobs?.map((j: any) => <tr key={j.id}>
            <td>{j.title}</td>
            <td>{j.job_type}</td>
            <td><Badge status={j.status} /></td>
            <td>{j.task_count}</td>
            <td style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(j.created_at)}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
  )
}

const SocGovernance = ({ adminFetch, showToast }: { adminFetch: Function; showToast: (m: string, t?: 'success'|'error') => void }) => {
  const [proposals, setProposals] = useState<any[]>([])
  const [rules, setRules] = useState<any[]>([])
  const [verifications, setVerifications] = useState<any[]>([])
  const [receipts, setReceipts] = useState<any[]>([])
  const [chainStatus, setChainStatus] = useState<any>(null)
  const [snapshots, setSnapshots] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); try {
      const [pR, rR, vR, recR, snR] = await Promise.all([
        adminFetch('/api/admin/proposals'),
        adminFetch('/api/admin/sentinel/rules'),
        adminFetch('/api/admin/skill-verifications?status=pending'),
        adminFetch('/api/admin/receipts?limit=25'),
        adminFetch('/api/admin/snapshots'),
      ])
      if (pR.ok) setProposals(await pR.json())
      if (rR.ok) setRules(await rR.json())
      if (vR.ok) setVerifications(await vR.json())
      if (recR.ok) setReceipts(await recR.json())
      if (snR.ok) setSnapshots(await snR.json())
    } catch {}; setLoading(false)
  }, [adminFetch])
  useEffect(() => { load() }, [load])

  const toggleRule = async (r: any) => { await adminFetch(`/api/admin/sentinel/rules/${r.id}/toggle`, { method: 'PATCH', body: '{}' }); load() }
  const deleteRule = async (r: any) => { if (!window.confirm(`Delete rule "${r.name}"?`)) return; await adminFetch(`/api/admin/sentinel/rules/${r.id}`, { method: 'DELETE' }); load() }
  const approveVer = async (v: any) => { const res = await adminFetch(`/api/admin/skill-verifications/${v.id}/approve`, { method: 'POST', body: '{}' }); if (res.ok) { showToast(`${v.agent_name}'s ${v.capability} approved`); load() } }
  const rejectVer = async (v: any) => { await adminFetch(`/api/admin/skill-verifications/${v.id}/reject`, { method: 'POST', body: '{}' }); load() }
  const verifyChain = async () => { const r = await adminFetch('/api/admin/receipts/verify'); if (r.ok) setChainStatus(await r.json()) }
  const createSnapshot = async () => {
    const label = window.prompt('Label for this snapshot?', `manual-${new Date().toISOString().slice(0, 16)}`)
    if (label === null) return
    const r = await adminFetch('/api/admin/snapshot', { method: 'POST', body: JSON.stringify({ label }) })
    if (r.ok) { showToast('Snapshot created'); load() } else showToast('Snapshot failed', 'error')
  }
  const restoreSnapshot = async (s: any) => {
    if (!window.confirm(`Restore snapshot "${s.label}"? Only capability_versions, swarm_profiles, and agent_sidecars are replayed — everything else in the snapshot is left untouched.`)) return
    const r = await adminFetch(`/api/admin/snapshot/${s.id}/restore`, { method: 'POST', body: '{}' })
    if (r.ok) showToast('Restore complete'); else showToast('Restore failed', 'error')
  }

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

      <div className="ares-section-title">Sentinel Rules ({rules.length})</div>
      <table className="ares-table">
        <thead><tr><th>Name</th><th>Target</th><th>Action</th><th>Enabled</th><th>Last Run</th><th></th></tr></thead>
        <tbody>{rules.length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No rules configured</td></tr>}
          {rules.map((r: any) => <tr key={r.id}>
            <td>{r.name}</td><td>{r.target}</td><td>{r.action}</td>
            <td><button className="btn btn-ghost btn-sm" onClick={() => toggleRule(r)}>{r.enabled ? <ToggleRight size={16} color="var(--accent)" /> : <ToggleLeft size={16} />}</button></td>
            <td style={{ fontSize: 10, color: 'var(--muted)' }}>{r.last_run_at ? timeAgo(r.last_run_at) : 'never'}</td>
            <td><button className="btn btn-ghost btn-sm" onClick={() => deleteRule(r)}><Trash2 size={12} /></button></td>
          </tr>)}
        </tbody>
      </table>

      <div className="ares-section-title">Skill Verifications — Pending ({verifications.length})</div>
      <table className="ares-table">
        <thead><tr><th>Agent</th><th>Capability</th><th>Submitted</th><th></th></tr></thead>
        <tbody>{verifications.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No pending verifications</td></tr>}
          {verifications.map((v: any) => <tr key={v.id}>
            <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{v.agent_name}</td>
            <td><Award size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} />{v.capability}</td>
            <td style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(v.submitted_at)}</td>
            <td style={{ display: 'flex', gap: 6 }}>
              <button className="btn btn-primary btn-sm" onClick={() => approveVer(v)}><CheckCircle size={12} /></button>
              <button className="btn btn-danger btn-sm" onClick={() => rejectVer(v)}><XCircle size={12} /></button>
            </td>
          </tr>)}
        </tbody>
      </table>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <span className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}><Link2 size={13} style={{ verticalAlign: 'middle', marginRight: 4 }} />Audit Receipts ({receipts.length})</span>
        <button className="btn btn-ghost btn-sm" onClick={verifyChain}>Verify Chain Integrity</button>
        {chainStatus && <span style={{ fontSize: 11, color: chainStatus.ok ? 'var(--success)' : 'var(--danger)', fontWeight: 700 }}>
          {chainStatus.ok ? `✓ intact (${chainStatus.checked} checked)` : `✗ broken at id ${chainStatus.broken_at_id}`}
        </span>}
      </div>
      <div style={{ maxHeight: '40vh', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 20 }}>
        <table className="ares-table">
          <thead><tr><th>Agent</th><th>Action</th><th>Severity</th><th>Tier</th></tr></thead>
          <tbody>{receipts.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No receipts yet</td></tr>}
            {receipts.map((r: any) => <tr key={r.id}>
              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.agent_id}</td>
              <td>{r.action}</td>
              <td><Badge status={r.severity?.toLowerCase() === 'critical' ? 'rejected' : r.severity?.toLowerCase() === 'caution' ? 'pending' : 'approved'} /></td>
              <td>{r.tier}</td>
            </tr>)}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <span className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}><Archive size={13} style={{ verticalAlign: 'middle', marginRight: 4 }} />Platform Snapshots ({snapshots.length})</span>
        <button className="btn btn-primary btn-sm" onClick={createSnapshot}><Plus size={12} /> Snapshot Now</button>
      </div>
      <table className="ares-table">
        <thead><tr><th>Label</th><th>Created</th><th>Tables</th><th></th></tr></thead>
        <tbody>{snapshots.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No snapshots yet</td></tr>}
          {snapshots.map((s: any) => <tr key={s.id}>
            <td>{s.label || `snapshot-${s.id}`}</td>
            <td style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(s.created_at)}</td>
            <td style={{ fontSize: 10, color: 'var(--muted)' }}>{(s.tables_list?.length ?? 0)} tables</td>
            <td><button className="btn btn-ghost btn-sm" onClick={() => restoreSnapshot(s)}><PlayCircle size={12} /> Restore</button></td>
          </tr>)}
        </tbody>
      </table>
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

interface LogEntry { ts: string; level: string; logger: string; msg: string }

const SocLogs = ({ adminFetch }: { adminFetch: (path: string) => Promise<Response> }) => {
  const [logs, setLogs] = useState<LogEntry[]>([]); const [loading, setLoading] = useState(true)
  const load = useCallback(async () => { setLoading(true); try { const r = await adminFetch('/api/admin/logs?n=100'); if (r.ok) { const d = await r.json(); setLogs((d.logs || []).slice().reverse()) } } catch {}; setLoading(false) }, [adminFetch])
  useEffect(() => { load() }, [load])
  const classify = (level: string): 'error' | 'warn' | 'normal' => {
    const u = (level || '').toUpperCase(); if (u.includes('ERROR') || u.includes('CRITICAL')) return 'error'; if (u.includes('WARN')) return 'warn'; return 'normal'
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
        {logs.map((entry, i) => (
          <div key={i} className={`ares-log-entry ares-log-${classify(entry.level)}`}>
            {entry.ts} [{entry.level}] {entry.logger}: {entry.msg}
          </div>
        ))}
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
    <div className="ares-root">
      {/* Header */}
      <div className="ares-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ fontSize: 18, fontWeight: 700 }}>🛡️ SENTINEL CONTROL</div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="ares-tabs">
        {SOC_TABS.map(t => (
          <button key={t.id} className={`ares-tab ${activeTab === t.id ? 'active' : ''}`} onClick={() => setActiveTab(t.id)}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="ares-content">
        {activeTab === 'soc-overview' && <SocOverview adminFetch={adminFetch} />}
        {activeTab === 'soc-threat' && <SocThreat adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-security' && <SocSecurity adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-agents' && <SocAgents adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-jobs' && <SocJobs adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-governance' && <SocGovernance adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-diagnostics' && <SocDiagnostics adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'soc-logs' && <SocLogs adminFetch={adminFetch} />}
      </div>

      {/* Toast */}
      {toast && <div className={`ares-toast ares-toast-${toast.type}`}>{toast.message}</div>}
    </div>
  )
}
