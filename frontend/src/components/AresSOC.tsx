import React, { useState, useEffect, useCallback } from 'react'
import { Shield, AlertTriangle, Activity, FileText, Terminal, CheckCircle, XCircle, RefreshCw, Lock } from 'lucide-react'

const TABS = [
  { id: 'overview',     label: 'Overview',    icon: Activity },
  { id: 'threat',       label: 'Threat Map',  icon: AlertTriangle },
  { id: 'governance',   label: 'Governance',  icon: Shield },
  { id: 'diagnostics',  label: 'Diagnostics', icon: FileText },
  { id: 'logs',         label: 'Logs',        icon: Terminal },
] as const

type TabId = typeof TABS[number]['id']

interface Toast { message: string; type: 'success' | 'error' }

function timeAgo(iso: string) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = { pending: '#ffaa00', approved: '#39ff14', rejected: '#ff2d4a', ok: '#39ff14', degraded: '#ff2d4a' }
  const c = map[status] || '#6b7280'
  return <span style={{ fontSize: 10, fontWeight: 700, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>{status}</span>
}

// ── Login Screen ─────────────────────────────────────────────────────────────
function LoginScreen({ onAuth }: { onAuth: (key: string) => void }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const attempt = async () => {
    if (!key.trim()) return
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/admin/stats', { headers: { 'X-Admin-Key': key } })
      if (r.ok) {
        sessionStorage.setItem('ares_admin_key', key)
        onAuth(key)
      } else {
        setError('Access denied. Invalid admin key.')
      }
    } catch {
      setError('Connection failed.')
    }
    setLoading(false)
  }

  return (
    <div className="ares-login">
      <div className="ares-login-card">
        <div className="ares-login-title">
          <Lock size={18} style={{ display: 'inline', marginRight: 8, verticalAlign: 'middle' }} />
          ARES SENTINEL CONTROL
        </div>
        <div style={{ color: 'var(--muted)', fontSize: 12, textAlign: 'center', lineHeight: 1.5 }}>
          Restricted access. Administrator credentials required.
        </div>
        <div className="ares-form-group">
          <label className="ares-form-label">Admin Key</label>
          <input
            type="password"
            className="ares-input"
            placeholder="Enter admin key…"
            value={key}
            onChange={e => setKey(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && attempt()}
            autoFocus
          />
        </div>
        {error && <div style={{ color: 'var(--danger)', fontSize: 12, textAlign: 'center' }}>{error}</div>}
        <button className="btn btn-danger" style={{ width: '100%' }} onClick={attempt} disabled={loading || !key.trim()}>
          {loading ? 'Authenticating…' : 'Enter Control Room'}
        </button>
      </div>
    </div>
  )
}

// ── Overview Tab ─────────────────────────────────────────────────────────────
function OverviewTab({ adminFetch }: { adminFetch: (path: string) => Promise<Response> }) {
  const [tel, setTel] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await adminFetch('/api/admin/telemetry')
      if (r.ok) setTel(await r.json())
    } catch {}
    setLoading(false)
  }, [adminFetch])

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t) }, [load])

  if (loading && !tel) return <div style={{ color: 'var(--muted)', fontSize: 13, padding: 20 }}>Loading telemetry…</div>
  if (!tel) return null

  const jq = tel.job_queue as Record<string, number> | null
  const market = tel.market as Record<string, number> | null
  const content = tel.content as Record<string, number> | null
  const sentinel = tel.sentinel as Record<string, unknown> | null
  const health = tel.swarm_health as string
  const hotspots = (sentinel?.error_hotspots as Array<{ error_type: string; count: number }>) || []

  return (
    <div>
      {/* Health gauge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <span className={`ares-health-badge ${health === 'ok' ? 'ares-health-ok' : 'ares-health-degraded'}`}>
          {health === 'ok' ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
          SWARM {health?.toUpperCase()}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /> Refresh</button>
      </div>

      {/* Stat tiles */}
      <div className="ares-stat-grid">
        {[
          { label: 'Active Agents (15m)', value: tel.active_agents_15m as number },
          { label: 'Jobs Active', value: jq?.active ?? 0 },
          { label: 'Dead Letter', value: jq?.dead ?? 0 },
          { label: 'Open Errors', value: (sentinel?.open_error_reports as number) ?? 0 },
          { label: 'Open Tasks', value: market?.open_tasks ?? 0 },
          { label: 'Market Bids (5m)', value: market?.bids_last_5m ?? 0 },
          { label: 'Broadcasts (1h)', value: content?.broadcasts_last_1h ?? 0 },
          { label: 'Active Locks', value: content?.active_broadcast_locks ?? 0 },
        ].map(s => (
          <div key={s.label} className="ares-stat-tile">
            <div className="ares-stat-label">{s.label}</div>
            <div className="ares-stat-value">{s.value}</div>
          </div>
        ))}
      </div>

      {/* Error hotspots */}
      {hotspots.length > 0 && (
        <>
          <div className="ares-section-title">Error Hotspots</div>
          <table className="ares-table">
            <thead><tr><th>Error Type</th><th>Count</th></tr></thead>
            <tbody>
              {hotspots.map((h, i) => (
                <tr key={i}>
                  <td><code style={{ color: 'var(--danger)', fontFamily: 'monospace' }}>{h.error_type}</code></td>
                  <td style={{ color: 'var(--warning)', fontWeight: 700 }}>{h.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

// ── Threat Map Tab ────────────────────────────────────────────────────────────
function ThreatTab({ adminFetch, showToast }: { adminFetch: (path: string, opts?: RequestInit) => Promise<Response>; showToast: (m: string, t?: 'success' | 'error') => void }) {
  const [hits, setHits] = useState<Array<Record<string, unknown>>>([])
  const [anomalies, setAnomalies] = useState<Array<Record<string, unknown>>>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [hR, aR] = await Promise.all([adminFetch('/api/admin/honeypot'), adminFetch('/api/admin/anomaly-profiles')])
      if (hR.ok) setHits(await hR.json())
      if (aR.ok) setAnomalies(await aR.json())
    } catch {}
    setLoading(false)
  }, [adminFetch])

  useEffect(() => { load() }, [load])

  const jail = async (agentId: number, agentName: string) => {
    const r = await adminFetch(`/api/admin/agents/${agentId}/jail-mode`, {
      method: 'POST', body: JSON.stringify({ reason: 'Honeypot trigger — suspicious activity detected' })
    })
    if (r.ok) showToast(`${agentName} jailed.`)
    else showToast(`Failed to jail ${agentName}`, 'error')
  }

  if (loading) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading threat data…</div>

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      {/* Honeypot */}
      <div>
        <div className="ares-section-title">Honeypot Hits ({hits.length})</div>
        <div style={{ maxHeight: '60vh', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
          <table className="ares-table">
            <thead><tr><th>Agent</th><th>Endpoint</th><th>Hits</th><th>Time</th><th></th></tr></thead>
            <tbody>
              {hits.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No honeypot hits</td></tr>}
              {hits.map((h, i) => (
                <tr key={i} className={(h.hit_count as number) > 5 ? 'threat-high' : ''}>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{h.agent_name as string || '—'}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 10, color: 'var(--muted)' }}>{h.endpoint as string || '—'}</td>
                  <td style={{ color: (h.hit_count as number) > 5 ? 'var(--danger)' : 'var(--text)', fontWeight: 700 }}>{Number(h.hit_count)}</td>
                  <td style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(h.hit_at as string)}</td>
                  <td>
                    {!!h.agent_id && (
                      <button className="btn btn-danger btn-sm" onClick={() => jail(h.agent_id as number, h.agent_name as string)}>
                        JAIL
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Anomaly profiles */}
      <div>
        <div className="ares-section-title">Anomaly Profiles</div>
        <div style={{ maxHeight: '60vh', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
          <table className="ares-table">
            <thead><tr><th>Agent</th><th>Requests</th><th>Score</th><th></th></tr></thead>
            <tbody>
              {anomalies.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No anomalies</td></tr>}
              {anomalies.map((a, i) => (
                <tr key={i}>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{a.agent_name as string}</td>
                  <td style={{ fontWeight: 700 }}>{Number(a.request_count)}</td>
                  <td style={{ color: (a.anomaly_score as number) > 1.5 ? 'var(--danger)' : 'var(--warning)' }}>
                    {typeof a.anomaly_score === 'number' ? a.anomaly_score.toFixed(2) : '—'}
                  </td>
                  <td>
                    {!!a.agent_id && (
                      <button className="btn btn-danger btn-sm" onClick={() => jail(a.agent_id as number, a.agent_name as string)}>
                        JAIL
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Governance Tab ────────────────────────────────────────────────────────────
function GovernanceTab({ adminFetch, showToast }: { adminFetch: (path: string, opts?: RequestInit) => Promise<Response>; showToast: (m: string, t?: 'success' | 'error') => void }) {
  const [proposals, setProposals] = useState<Array<Record<string, unknown>>>([])
  const [form, setForm] = useState({ command: 'lock_agent', payload: '', required_approvals: '2' })
  const [loading, setLoading] = useState(true)

  const COMMANDS = ['lock_agent', 'unlock_agent', 'clear_agent_tokens', 'flag_peer']

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await adminFetch('/api/admin/proposals')
      if (r.ok) setProposals(await r.json())
    } catch {}
    setLoading(false)
  }, [adminFetch])

  useEffect(() => { load() }, [load])

  const act = async (id: number, action: 'approve' | 'reject') => {
    const r = await adminFetch(`/api/admin/proposals/${id}/${action}`, { method: 'POST', body: JSON.stringify({}) })
    if (r.ok) { showToast(`Proposal ${action}d`); load() }
    else showToast(`Failed to ${action}`, 'error')
  }

  const submit = async () => {
    let payload: Record<string, unknown> = {}
    try { if (form.payload) payload = JSON.parse(form.payload) } catch {}
    const r = await adminFetch('/api/admin/proposals', {
      method: 'POST',
      body: JSON.stringify({ command: form.command, payload, required_approvals: parseInt(form.required_approvals) || 2 }),
    })
    if (r.ok) { showToast('Proposal created'); setForm(p => ({ ...p, payload: '' })); load() }
    else showToast('Failed to create proposal', 'error')
  }

  return (
    <div>
      {/* Create form */}
      <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 20, marginBottom: 24 }}>
        <div className="ares-section-title" style={{ marginTop: 0 }}>Create Proposal</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div className="ares-form-group">
            <label className="ares-form-label">Command</label>
            <select className="ares-input" value={form.command} onChange={e => setForm(p => ({ ...p, command: e.target.value }))}>
              {COMMANDS.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="ares-form-group">
            <label className="ares-form-label">Required Approvals</label>
            <input className="ares-input" type="number" min={1} value={form.required_approvals} onChange={e => setForm(p => ({ ...p, required_approvals: e.target.value }))} />
          </div>
        </div>
        <div className="ares-form-group">
          <label className="ares-form-label">Payload (JSON)</label>
          <input className="ares-input" placeholder='{"agent_id": 5}' value={form.payload} onChange={e => setForm(p => ({ ...p, payload: e.target.value }))} />
        </div>
        <button className="btn btn-primary btn-sm" onClick={submit}>Submit Proposal</button>
      </div>

      {/* List */}
      <div className="ares-section-title">Pending Proposals ({proposals.length})</div>
      {loading && <div style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</div>}
      {!loading && proposals.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No pending proposals.</div>}
      {proposals.map(p => (
        <div key={p.id as number} className="ares-proposal-card">
          <div className="ares-proposal-header">
            <div>
              <div className="ares-proposal-title">{p.command as string}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
                Proposed by <code>{p.proposed_by as string}</code> · {timeAgo(p.created_at as string)} · {Number(p.votes_for)}/{Number(p.required_approvals)} approvals
              </div>
              {!!(p.payload && p.payload !== '{}') && (
                <div style={{ marginTop: 6, fontFamily: 'monospace', fontSize: 11, color: 'var(--muted)', background: 'rgba(0,0,0,0.3)', padding: '4px 8px', borderRadius: 4 }}>
                  {String(p.payload)}
                </div>
              )}
            </div>
            <div className="ares-proposal-actions">
              <StatusBadge status={p.status as string} />
              {p.status === 'pending' && (
                <>
                  <button className="btn btn-primary btn-sm" onClick={() => act(p.id as number, 'approve')}>
                    <CheckCircle size={12} /> Approve
                  </button>
                  <button className="btn btn-danger btn-sm" onClick={() => act(p.id as number, 'reject')}>
                    <XCircle size={12} /> Reject
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Diagnostics Tab ───────────────────────────────────────────────────────────
function DiagnosticsTab({ adminFetch, showToast }: { adminFetch: (path: string, opts?: RequestInit) => Promise<Response>; showToast: (m: string, t?: 'success' | 'error') => void }) {
  const [errorMap, setErrorMap] = useState<{ hotspots: Array<{ id: number; error_type: string; count: number; last_seen: string }>; total: number } | null>(null)
  const [verifications, setVerifications] = useState<Array<Record<string, unknown>>>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [eR, vR] = await Promise.all([adminFetch('/api/admin/error-map'), adminFetch('/api/admin/skill-verifications?status=pending')])
      if (eR.ok) setErrorMap(await eR.json())
      if (vR.ok) setVerifications(await vR.json())
    } catch {}
    setLoading(false)
  }, [adminFetch])

  useEffect(() => { load() }, [load])

  const resolve = async (id: number) => {
    const r = await adminFetch(`/api/admin/error-map/${id}/resolve`, { method: 'POST', body: JSON.stringify({}) })
    if (r.ok) { showToast('Error resolved'); load() }
    else showToast('Resolve failed', 'error')
  }

  const verAct = async (id: number, action: 'approve' | 'reject') => {
    const r = await adminFetch(`/api/admin/skill-verifications/${id}/${action}`, { method: 'POST', body: JSON.stringify({ score: 1.0 }) })
    if (r.ok) { showToast(`Verification ${action}d`); load() }
    else showToast(`Failed to ${action}`, 'error')
  }

  if (loading) return <div style={{ color: 'var(--muted)', padding: 20, fontSize: 13 }}>Loading…</div>

  return (
    <div>
      {/* Error map */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        <div className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}>
          Error Map {errorMap ? `— ${errorMap.total} open` : ''}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /></button>
      </div>
      <table className="ares-table" style={{ marginBottom: 28 }}>
        <thead><tr><th>Error Type</th><th>Count</th><th>Last Seen</th><th></th></tr></thead>
        <tbody>
          {(!errorMap?.hotspots?.length) && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No open errors</td></tr>}
          {errorMap?.hotspots?.map((h, i) => (
            <tr key={i}>
              <td><code style={{ color: 'var(--danger)', fontFamily: 'monospace' }}>{h.error_type}</code></td>
              <td style={{ color: 'var(--warning)', fontWeight: 700 }}>{h.count}</td>
              <td style={{ fontSize: 11, color: 'var(--muted)' }}>{timeAgo(h.last_seen)}</td>
              <td><button className="btn btn-ghost btn-sm" onClick={() => resolve(h.id)}>Resolve</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Skill verifications */}
      <div className="ares-section-title">Pending Skill Verifications ({verifications.length})</div>
      <table className="ares-table">
        <thead><tr><th>Agent</th><th>Capability</th><th>Proof Type</th><th>Submitted</th><th></th></tr></thead>
        <tbody>
          {verifications.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>No pending verifications</td></tr>}
          {verifications.map(v => (
            <tr key={v.id as number}>
              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{v.agent_name as string}</td>
              <td style={{ color: 'var(--cyan)', fontWeight: 600 }}>{v.capability as string}</td>
              <td style={{ color: 'var(--muted)', fontSize: 11 }}>{v.proof_type as string}</td>
              <td style={{ fontSize: 11, color: 'var(--muted)' }}>{timeAgo(v.submitted_at as string)}</td>
              <td style={{ display: 'flex', gap: 4 }}>
                <button className="btn btn-primary btn-sm" onClick={() => verAct(v.id as number, 'approve')}>✓ Approve</button>
                <button className="btn btn-danger btn-sm" onClick={() => verAct(v.id as number, 'reject')}>✗ Reject</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Logs Tab ──────────────────────────────────────────────────────────────────
function LogsTab({ adminFetch }: { adminFetch: (path: string) => Promise<Response> }) {
  const [logs, setLogs] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await adminFetch('/api/admin/logs?n=100')
      if (r.ok) {
        const data = await r.json()
        setLogs((data.logs || []).slice().reverse())
      }
    } catch {}
    setLoading(false)
  }, [adminFetch])

  useEffect(() => { load() }, [load])

  const classify = (line: string): 'error' | 'warn' | 'normal' => {
    const u = line.toUpperCase()
    if (u.includes('ERROR') || u.includes('EXCEPTION') || u.includes('TRACEBACK')) return 'error'
    if (u.includes('WARN') || u.includes('WARNING')) return 'warn'
    return 'normal'
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span className="ares-section-title" style={{ marginTop: 0, marginBottom: 0 }}>Platform Logs</span>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /> Refresh</button>
      </div>
      <div className="ares-log-list">
        {loading && <div style={{ color: 'var(--muted)' }}>Loading…</div>}
        {!loading && logs.length === 0 && <div style={{ color: 'var(--muted)' }}>No logs available.</div>}
        {logs.map((line, i) => {
          const cls = classify(typeof line === 'string' ? line : JSON.stringify(line))
          const text = typeof line === 'string' ? line : JSON.stringify(line)
          return (
            <div key={i} className={`ares-log-entry ares-log-${cls}`}>{text}</div>
          )
        })}
      </div>
    </div>
  )
}

// ── Root Component ────────────────────────────────────────────────────────────
export default function AresSOC() {
  const [adminKey, setAdminKey] = useState<string>(() => sessionStorage.getItem('ares_admin_key') || '')
  const [activeTab, setActiveTab] = useState<TabId>('overview')
  const [toast, setToast] = useState<Toast | null>(null)

  const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3000)
  }, [])

  const adminFetch = useCallback((path: string, opts: RequestInit = {}) => {
    return fetch(path, {
      ...opts,
      headers: {
        'X-Admin-Key': adminKey,
        'Content-Type': 'application/json',
        ...(opts.headers || {}),
      },
    })
  }, [adminKey])

  const disconnect = () => {
    sessionStorage.removeItem('ares_admin_key')
    setAdminKey('')
  }

  if (!adminKey) return <div className="ares-root"><LoginScreen onAuth={setAdminKey} /></div>

  return (
    <div className="ares-root">
      {/* Toast */}
      {toast && (
        <div className={`ares-toast ares-toast-${toast.type}`}>{toast.message}</div>
      )}

      {/* Header */}
      <div className="ares-header">
        <div className="ares-header-title">
          <Shield size={14} style={{ display: 'inline', marginRight: 8, verticalAlign: 'middle' }} />
          ARES SENTINEL CONTROL
        </div>
        <button className="btn btn-ghost btn-sm" onClick={disconnect} style={{ color: 'var(--muted)' }}>
          <Lock size={12} /> Disconnect
        </button>
      </div>

      {/* Tabs */}
      <div className="ares-tabs">
        {TABS.map(tab => {
          const Icon = tab.icon
          return (
            <button
              key={tab.id}
              className={`ares-tab${activeTab === tab.id ? ' active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon size={13} /> {tab.label}
            </button>
          )
        })}
      </div>

      {/* Content */}
      <div className="ares-content">
        {activeTab === 'overview'    && <OverviewTab adminFetch={adminFetch} />}
        {activeTab === 'threat'      && <ThreatTab adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'governance'  && <GovernanceTab adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'diagnostics' && <DiagnosticsTab adminFetch={adminFetch} showToast={showToast} />}
        {activeTab === 'logs'        && <LogsTab adminFetch={adminFetch} />}
      </div>
    </div>
  )
}
