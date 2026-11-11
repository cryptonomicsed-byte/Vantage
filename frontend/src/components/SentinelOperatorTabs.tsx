import React, { useCallback, useEffect, useState } from 'react'
import { CheckCircle, XCircle, RefreshCw, PlayCircle, Square, RotateCw } from 'lucide-react'
import { getHumanSession } from '../utils/humanSession'

// Sentinel operator surfaces. These authenticate as a named human operator
// (X-Human-Session), never with the shared admin key: a toggle needs to know
// WHO asked and WHO approved, and the requester can never approve their own.

type Toast = (m: string, t?: 'success' | 'error') => void

function opFetch(path: string, opts: RequestInit = {}) {
  const session = getHumanSession()
  return fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(session ? { 'X-Human-Session': session } : {}), ...(opts.headers || {}) },
  })
}

async function detail(r: Response, fallback: string) {
  const d = await r.json().catch(() => ({}))
  return typeof d.detail === 'string' ? d.detail : fallback
}

function NeedsOperator({ status }: { status: number | null }) {
  if (status === 401 || status === 403) {
    return <div className="ares-card" style={{ padding: 16, fontSize: 12, color: 'var(--muted)' }}>
      Sign in as a human account that holds an instance operator role to use this tab.
    </div>
  }
  if (status === 503) {
    return <div className="ares-card" style={{ padding: 16, fontSize: 12, color: 'var(--muted)' }}>
      Not configured on this instance.
    </div>
  }
  return null
}

export function SocDaemons({ showToast }: { showToast: Toast }) {
  const [rows, setRows] = useState<any[]>([])
  const [status, setStatus] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await opFetch('/api/admin/daemons'); setStatus(r.status)
      if (r.ok) setRows((await r.json()).daemons || [])
    } catch { /* best-effort */ }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  const act = async (unit: string, action: 'start' | 'stop' | 'restart') => {
    const r = await opFetch(`/api/admin/daemons/${unit}/${action}`, { method: 'POST', body: '{}' })
    if (r.status === 202) showToast(`${action} ${unit}: sign-off filed for a second operator`)
    else if (r.ok) showToast(`${unit} ${action}ped`)
    else showToast(await detail(r, `Could not ${action} ${unit}`), 'error')
    load()
  }

  if (status && !(status >= 200 && status < 300)) return <NeedsOperator status={status} />
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>Stop is immediate. Start and restart file a sign-off card.</div>
        <button className="btn btn-sm" onClick={load} disabled={loading}><RefreshCw size={12} /> Refresh</button>
      </div>
      <table className="ares-table">
        <thead><tr><th>Unit</th><th>State</th><th>Restarts</th><th></th></tr></thead>
        <tbody>
          {rows.map(d => (
            <tr key={d.unit}>
              <td>{d.name}{d.denylisted && <span style={{ color: 'var(--danger)', marginLeft: 6 }}>deny-listed</span>}</td>
              <td>{d.active}/{d.sub}{d.crash_looping && <span style={{ color: 'var(--danger)', marginLeft: 6 }}>crash-looping</span>}</td>
              <td>{d.n_restarts}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button className="btn btn-sm" disabled={d.running || d.denylisted} onClick={() => act(d.unit, 'start')}><PlayCircle size={12} /> Start</button>{' '}
                <button className="btn btn-danger btn-sm" disabled={!d.running} onClick={() => act(d.unit, 'stop')}><Square size={12} /> Stop</button>{' '}
                <button className="btn btn-sm" disabled={d.denylisted} onClick={() => act(d.unit, 'restart')}><RotateCw size={12} /> Restart</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function SocSignoffs({ showToast }: { showToast: Toast }) {
  const [cards, setCards] = useState<any[]>([])
  const [me, setMe] = useState<any>(null)
  const [status, setStatus] = useState<number | null>(null)
  const [filter, setFilter] = useState('pending')
  const load = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([opFetch('/api/sentinel/me'), opFetch(`/api/sentinel/signoffs?status=${filter}`)])
      setStatus(r.status)
      if (m.ok) setMe(await m.json())
      if (r.ok) setCards((await r.json()).cards || [])
    } catch { /* best-effort */ }
  }, [filter])
  useEffect(() => { load() }, [load])

  const decide = async (id: string, verb: 'approve' | 'deny') => {
    const r = await opFetch(`/api/sentinel/signoffs/${id}/${verb}`, { method: 'POST', body: '{}' })
    if (r.ok) {
      const d = await r.json()
      showToast(`Card ${d.status}${d.status === 'halted' ? `: ${d.result}` : ''}`, d.status === 'approved' || d.status === 'denied' ? 'success' : 'error')
    } else showToast(await detail(r, `Could not ${verb}`), 'error')
    load()
  }

  if (status && !(status >= 200 && status < 300)) return <NeedsOperator status={status} />
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
        {['pending', 'approved', 'denied', 'halted', 'expired'].map(s => (
          <button key={s} className={`btn btn-sm ${filter === s ? 'btn-primary' : ''}`} onClick={() => setFilter(s)}>{s}</button>
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--muted)' }}>
          {me ? `You: ${me.display_name || me.human_id} (${me.role})${me.may_approve_trading ? '' : ' — may not approve'}` : ''}
        </span>
      </div>
      {cards.length === 0 && <div style={{ fontSize: 12, color: 'var(--muted)' }}>No {filter} cards.</div>}
      {cards.map(c => {
        const mine = me && c.requested_by_human === me.human_id
        return (
          <div key={c.id} className="ares-card" style={{ padding: 12, marginBottom: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <div><b>{c.summary || c.kind}</b> <span style={{ fontSize: 11, color: 'var(--muted)' }}>{c.kind} · risk {c.risk}</span></div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>requested by #{c.requested_by_human} · expires {c.expires_at}</div>
            </div>
            {c.result && <div style={{ fontSize: 11, marginTop: 6 }}>{c.result}</div>}
            {c.status === 'pending' && (
              <div style={{ marginTop: 8 }}>
                <button className="btn btn-primary btn-sm" disabled={!!mine || !me?.may_approve_trading} title={mine ? 'You requested this card' : ''}
                  onClick={() => decide(c.id, 'approve')}><CheckCircle size={12} /> Allow</button>{' '}
                <button className="btn btn-danger btn-sm" disabled={!!mine} onClick={() => decide(c.id, 'deny')}><XCircle size={12} /> Deny</button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function SocPeers({ showToast }: { showToast: Toast }) {
  const [peers, setPeers] = useState<any[]>([])
  const [status, setStatus] = useState<number | null>(null)
  const load = useCallback(async () => {
    try {
      const r = await opFetch('/api/sentinel/federation/peers'); setStatus(r.status)
      if (r.ok) setPeers((await r.json()).peers || [])
    } catch { /* best-effort */ }
  }, [])
  useEffect(() => { load() }, [load])
  const set = async (id: number, verb: 'admit' | 'revoke') => {
    const r = await opFetch(`/api/sentinel/federation/peers/${id}/${verb}`, { method: 'POST', body: '{}' })
    showToast(r.ok ? `Peer ${verb === 'admit' ? 'admitted' : 'revoked'}` : await detail(r, `Could not ${verb}`), r.ok ? 'success' : 'error')
    load()
  }
  if (status && !(status >= 200 && status < 300)) return <NeedsOperator status={status} />
  return (
    <table className="ares-table">
      <thead><tr><th>Peer</th><th>Found via</th><th>Key pinned</th><th>Admitted</th><th></th></tr></thead>
      <tbody>
        {peers.map(p => (
          <tr key={p.id}>
            <td>{p.name || p.url}<div style={{ fontSize: 11, color: 'var(--muted)' }}>{p.url}</div></td>
            <td>{p.discovered_via}</td>
            <td>{p.nostr_pubkey ? 'yes' : 'no'}</td>
            <td>{p.admitted ? `yes (${p.admitted_by})` : 'no'}</td>
            <td>{p.admitted
              ? <button className="btn btn-danger btn-sm" onClick={() => set(p.id, 'revoke')}>Revoke</button>
              : <button className="btn btn-sm" disabled={!p.nostr_pubkey} onClick={() => set(p.id, 'admit')}>Admit</button>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
