import React, { useEffect, useState, useCallback } from 'react'
import { Users, UserPlus, UserMinus, TrendingUp, RefreshCw } from 'lucide-react'

interface Leader {
  leader_id: string
  leader_name: string
  subscribed_at: string
  recent_pnl?: number
  win_rate?: number
  total_trades?: number
}

const cardStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid rgba(255,255,255,0.06)',
  borderRadius: 8,
  padding: 14,
  marginBottom: 10,
}

function apiKey() { return localStorage.getItem('vantage_api_key') || '' }

export default function CopyTradingPanel() {
  const [leaders, setLeaders] = useState<Leader[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [unsubbing, setUnsubbing] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    fetch('/api/copytrade/leaders', { headers: { 'X-Agent-Key': apiKey() } })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setLeaders(Array.isArray(d) ? d : (d.leaders || [])))
      .catch(() => setError('Failed to load your leaders'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  async function unsubscribe(leaderId: string) {
    setUnsubbing(leaderId)
    try {
      const r = await fetch(`/api/copytrade/subscribe/${leaderId}`, {
        method: 'DELETE',
        headers: { 'X-Agent-Key': apiKey() },
      })
      if (r.ok) {
        setLeaders(prev => prev.filter(l => l.leader_id !== leaderId))
      } else {
        setError('Failed to unsubscribe')
      }
    } catch {
      setError('Failed to unsubscribe')
    } finally {
      setUnsubbing(null)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Users size={18} color="#a78bfa" />
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>My Leaders</h2>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>{leaders.length} subscribed</span>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={load}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: '#ef4444', padding: '8px 12px', background: 'rgba(239,68,68,0.08)', borderRadius: 6, marginBottom: 12 }}>
          {error}
        </div>
      )}

      {loading && <div className="loading-state">Loading your copy-trade leaders…</div>}

      {!loading && leaders.length === 0 && !error && (
        <div style={{ ...cardStyle, textAlign: 'center', padding: 32 }}>
          <UserPlus size={28} color="var(--muted)" style={{ marginBottom: 10 }} />
          <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 6 }}>No leaders yet</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            Go to the Leaderboard and click "Copy" next to an agent to start copy-trading their moves.
          </div>
        </div>
      )}

      {!loading && leaders.map(leader => (
        <div key={leader.leader_id} style={cardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{leader.leader_name || leader.leader_id}</div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                {leader.recent_pnl !== undefined && (
                  <span style={{ fontSize: 11, color: leader.recent_pnl >= 0 ? '#22c55e' : '#ef4444', display: 'flex', alignItems: 'center', gap: 3 }}>
                    <TrendingUp size={10} />
                    {leader.recent_pnl >= 0 ? '+' : ''}{leader.recent_pnl.toFixed(2)}% PnL
                  </span>
                )}
                {leader.win_rate !== undefined && (
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>{leader.win_rate.toFixed(0)}% win rate</span>
                )}
                {leader.total_trades !== undefined && (
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>{leader.total_trades} trades</span>
                )}
                {leader.subscribed_at && (
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                    since {new Date(leader.subscribed_at).toLocaleDateString()}
                  </span>
                )}
              </div>
            </div>
            <button
              className="btn btn-danger btn-sm"
              onClick={() => unsubscribe(leader.leader_id)}
              disabled={unsubbing === leader.leader_id}
              style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 5 }}
            >
              <UserMinus size={12} />
              {unsubbing === leader.leader_id ? 'Removing…' : 'Unsubscribe'}
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
