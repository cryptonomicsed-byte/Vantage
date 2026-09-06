import React, { useCallback, useEffect, useRef, useState } from 'react'

interface Delegation {
  id: number
  task_id?: number
  task_title?: string
  from_agent_name?: string
  to_agent_name?: string
  status: 'pending' | 'accepted' | 'completed' | 'rejected'
  instructions?: string
  deadline?: string | null
  created_at: string
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'rgba(255,255,255,0.35)',
  accepted: '#60a5fa',
  completed: '#3cc878',
  rejected: '#ef4444',
}

const STATUS_BG: Record<string, string> = {
  pending: 'rgba(255,255,255,0.06)',
  accepted: 'rgba(96,165,250,0.12)',
  completed: 'rgba(60,200,120,0.12)',
  rejected: 'rgba(239,68,68,0.12)',
}

function truncate(s?: string, n = 80): string {
  if (!s) return ''
  return s.length <= n ? s : s.slice(0, n) + '…'
}

function DelegationCard({
  delegation,
  mode,
  onAction,
}: {
  delegation: Delegation
  mode: 'sent' | 'received'
  onAction: () => void
}) {
  const [acting, setActing] = useState(false)

  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const headers: Record<string, string> = apiKey ? { 'X-Agent-Key': apiKey } : {}

  async function doAction(action: 'accept' | 'complete') {
    setActing(true)
    try {
      await fetch(`/api/delegations/${delegation.id}/${action}`, { method: 'POST', headers })
      onAction()
    } catch { /* ignore */ } finally {
      setActing(false)
    }
  }

  const otherAgent = mode === 'sent' ? delegation.to_agent_name : delegation.from_agent_name
  const statusColor = STATUS_COLOR[delegation.status] || 'var(--muted)'
  const statusBg = STATUS_BG[delegation.status] || 'transparent'

  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
      borderRadius: 8, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {truncate(delegation.task_title || `Task #${delegation.task_id}`, 50)}
        </span>
        <span style={{
          fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
          padding: '2px 6px', borderRadius: 4,
          color: statusColor, background: statusBg,
        }}>
          {delegation.status}
        </span>
      </div>

      <div style={{ fontSize: 11, color: 'var(--muted)' }}>
        {mode === 'sent' ? 'To: ' : 'From: '}
        <span style={{ color: '#a78bfa' }}>{otherAgent || '—'}</span>
      </div>

      {delegation.instructions && (
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.5)', fontStyle: 'italic' }}>
          {truncate(delegation.instructions)}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>
        {delegation.deadline && (
          <span>Due: {new Date(delegation.deadline).toLocaleDateString()}</span>
        )}
        <span style={{ marginLeft: 'auto' }}>{new Date(delegation.created_at).toLocaleString()}</span>
      </div>

      {mode === 'received' && delegation.status === 'pending' && (
        <button
          className="btn btn-sm btn-primary"
          disabled={acting}
          onClick={() => doAction('accept')}
          style={{ alignSelf: 'flex-start', fontSize: 11 }}
        >
          {acting ? '…' : 'Accept'}
        </button>
      )}
      {mode === 'received' && delegation.status === 'accepted' && (
        <button
          className="btn btn-sm"
          disabled={acting}
          onClick={() => doAction('complete')}
          style={{ alignSelf: 'flex-start', fontSize: 11, color: '#3cc878', borderColor: '#3cc878' }}
        >
          {acting ? '…' : 'Complete'}
        </button>
      )}
    </div>
  )
}

export default function DelegationPanel() {
  const [sent, setSent] = useState<Delegation[]>([])
  const [received, setReceived] = useState<Delegation[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'sent' | 'received'>('received')
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const headers: Record<string, string> = apiKey ? { 'X-Agent-Key': apiKey } : {}

  const load = useCallback(() => {
    if (!apiKey) { setLoading(false); return }
    fetch('/api/agents/me/delegations', { headers })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) {
          setSent(Array.isArray(d.sent) ? d.sent : [])
          setReceived(Array.isArray(d.received) ? d.received : [])
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [apiKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load()
    intervalRef.current = setInterval(load, 15_000)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [load])

  const active = tab === 'sent' ? sent : received

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
        {(['received', 'sent'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              fontSize: 12, fontWeight: tab === t ? 700 : 400,
              padding: '4px 14px', borderRadius: 6, border: 'none', cursor: 'pointer',
              background: tab === t ? 'rgba(138,75,255,0.18)' : 'transparent',
              color: tab === t ? 'var(--text)' : 'var(--muted)',
              borderBottom: tab === t ? '2px solid var(--purple)' : '2px solid transparent',
            }}
          >
            {t === 'received' ? 'Received' : 'Sent'}
            {t === 'received' && received.length > 0 && (
              <span style={{ marginLeft: 6, fontSize: 9, background: 'var(--purple)', color: '#fff', padding: '1px 5px', borderRadius: 8 }}>
                {received.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '24px 0' }}>Loading…</div>
      ) : active.length === 0 ? (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '32px 0' }}>
          {tab === 'received'
            ? 'No delegations received yet — other agents can assign tasks to you.'
            : 'No delegations sent yet — delegate tasks to other agents from the task board.'}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {active.map(d => (
            <DelegationCard key={d.id} delegation={d} mode={tab} onAction={load} />
          ))}
        </div>
      )}
    </div>
  )
}
