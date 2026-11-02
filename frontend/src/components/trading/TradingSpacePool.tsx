import React, { useState, useEffect, useCallback } from 'react'

interface Pool {
  id: string
  name: string
  token: string
  symbol: string
  target_amount: number
  quorum_pct: number
  committed_count: number
  total_committed: number
  status: string
  created_at: string
}

interface Props {
  slug: string
  spaceId: string
  authHeaders: Record<string, string>
}

export default function TradingSpacePool({ slug, spaceId, authHeaders }: Props) {
  const formHeaders = { ...authHeaders, 'Content-Type': 'application/x-www-form-urlencoded' }
  const [pools, setPools] = useState<Pool[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newToken, setNewToken] = useState('')
  const [newSymbol, setNewSymbol] = useState('')
  const [newTarget, setNewTarget] = useState('')
  const [newQuorum, setNewQuorum] = useState(51)
  const [creating, setCreating] = useState(false)
  const [commitPoolId, setCommitPoolId] = useState<string | null>(null)
  const [commitAmount, setCommitAmount] = useState('')
  const [committing, setCommitting] = useState(false)

  const base = '/api/guilds/' + slug + '/trading-spaces/' + spaceId + '/pools'

  const loadPools = useCallback(async () => {
    try {
      const r = await fetch(base, { headers: authHeaders })
      if (r.ok) setPools((await r.json()).pools || [])
    } finally { setLoading(false) }
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadPools()
    const t = setInterval(loadPools, 20000)
    return () => clearInterval(t)
  }, [loadPools])

  async function createPool() {
    if (!newName.trim() || !newToken.trim()) return
    setCreating(true)
    try {
      const body = new URLSearchParams({ name: newName, token: newToken, symbol: newSymbol, target_amount: newTarget, quorum_pct: String(newQuorum) })
      const r = await fetch(base, { method: 'POST', headers: formHeaders, body: body.toString() })
      if (r.ok) { setNewName(''); setNewToken(''); setNewSymbol(''); setNewTarget(''); setNewQuorum(51); setShowCreate(false); loadPools() }
    } finally { setCreating(false) }
  }

  async function commitToPool(poolId: string) {
    if (!commitAmount) return
    setCommitting(true)
    try {
      const body = new URLSearchParams({ amount: commitAmount })
      await fetch(base + '/' + poolId + '/commit', { method: 'POST', headers: formHeaders, body: body.toString() })
      setCommitPoolId(null); setCommitAmount(''); loadPools()
    } finally { setCommitting(false) }
  }

  function statusColor(s: string): string {
    if (s === 'quorum_reached') return '#3cc878'
    if (s === 'executing') return 'var(--cyan)'
    if (s === 'closed') return 'var(--muted)'
    return '#f59e0b'
  }

  function progressPct(pool: Pool): number {
    if (!pool.target_amount) return 0
    return Math.min(100, Math.round((pool.total_committed / pool.target_amount) * 100))
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading pools…</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Liquidity Pools</div>
        <button className=btn btn-sm onClick={() => setShowCreate(!showCreate)} style={{ fontSize: 11 }}>{showCreate ? 'Cancel' : '+ New Pool'}</button>
      </div>

      {showCreate && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>New Pool</div>
          <input value={newName} onChange={e => setNewName(e.target.value)} placeholder=Pool name style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          <div style={{ display: 'flex', gap: 10 }}>
            <input value={newToken} onChange={e => setNewToken(e.target.value)} placeholder=Token address style={{ flex: 2, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
            <input value={newSymbol} onChange={e => setNewSymbol(e.target.value)} placeholder=Symbol style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <input value={newTarget} onChange={e => setNewTarget(e.target.value)} placeholder=Target amount type=number style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' as const }}>Quorum %:</span>
              <input type=number min={1} max={100} value={newQuorum} onChange={e => setNewQuorum(Number(e.target.value))} style={{ width: 70, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 8px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
            </div>
          </div>
          <button className=btn btn-sm onClick={createPool} disabled={creating || !newName.trim() || !newToken.trim()} style={{ alignSelf: 'flex-start', fontSize: 11 }}>{creating ? 'Creating…' : 'Create Pool'}</button>
        </div>
      )}

      {pools.length === 0 && !showCreate && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No pools yet. Create one to start coordinating capital.</div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {pools.map(pool => {
          const pct = progressPct(pool)
          const sc = statusColor(pool.status)
          return (
            <div key={pool.id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', marginBottom: 3 }}>{pool.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>{pool.token.slice(0, 20)}{pool.token.length > 20 ? '…' : ''}</div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: sc + '22', color: sc, textTransform: 'uppercase' as const, letterSpacing: '0.05em' }}>
                    {pool.status === 'quorum_reached' ? 'Quorum' : pool.status}
                  </span>
                  {pool.symbol && <span style={{ fontSize: 11, color: 'var(--cyan)' }}>{pool.symbol}</span>}
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 10 }}>
                {[
                  { label: 'Target', value: pool.target_amount ? pool.target_amount.toLocaleString() : '—' },
                  { label: 'Committed', value: pool.total_committed ? pool.total_committed.toLocaleString() : '0' },
                  { label: 'Participants', value: String(pool.committed_count || 0) },
                ].map(s => (
                  <div key={s.label} style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px' }}>
                    <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' as const, letterSpacing: '0.06em', marginBottom: 3 }}>{s.label}</div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>{s.value}</div>
                  </div>
                ))}
              </div>

              {pool.target_amount > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>
                    <span>Progress</span><span>{pct}%</span>
                  </div>
                  <div style={{ height: 4, background: 'rgba(255,255,255,0.06)', borderRadius: 2, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: pct + '%', background: pool.status === 'quorum_reached' ? '#3cc878' : 'var(--cyan)', borderRadius: 2, transition: 'width 0.3s' }} />
                  </div>
                  <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 3 }}>Quorum at {pool.quorum_pct}%</div>
                </div>
              )}

              {pool.status !== 'closed' && pool.status !== 'executing' && (
                commitPoolId === pool.id ? (
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <input value={commitAmount} onChange={e => setCommitAmount(e.target.value)} placeholder=Amount type=number style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
                    <button className=btn btn-sm onClick={() => commitToPool(pool.id)} disabled={committing || !commitAmount} style={{ fontSize: 11 }}>{committing ? '…' : 'Commit'}</button>
                    <button className=btn btn-sm onClick={() => setCommitPoolId(null)} style={{ fontSize: 11 }}>Cancel</button>
                  </div>
                ) : (
                  <button className=btn btn-sm onClick={() => setCommitPoolId(pool.id)} style={{ fontSize: 11, background: 'rgba(0,200,180,0.08)', border: '1px solid rgba(0,200,180,0.25)', color: 'var(--cyan)' }}>
                    + Commit Capital
                  </button>
                )
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
