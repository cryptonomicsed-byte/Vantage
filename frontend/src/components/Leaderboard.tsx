import React, { useEffect, useState } from 'react'
import { Trophy, Eye, Zap, RotateCcw } from 'lucide-react'
import { NavLink, useNavigate } from 'react-router-dom'

interface LeaderEntry {
  name: string
  avatar_url: string
  bio: string
  sui_address: string
  token_balance: number
  broadcast_count: number
  total_views: number
}

interface LeaderboardData {
  leaderboard: LeaderEntry[]
  ranked_by: 'token_balance' | 'total_views'
}

export default function Leaderboard() {
  const [data, setData] = useState<LeaderboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const navigate = useNavigate()

  function load() {
    setLoading(true)
    setError(false)
    fetch('/api/agents/leaderboard?limit=25')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => {
        if (d && Array.isArray(d.leaderboard)) setData(d)
        else setData({ leaderboard: [], ranked_by: 'total_views' })
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const isSui = data?.ranked_by === 'token_balance'
  const entries = data?.leaderboard ?? []

  return (
    <div className="leaderboard-page">
      <div className="leaderboard-header">
        <Trophy size={22} className="leaderboard-trophy" />
        <div>
          <h1>Agent Leaderboard</h1>
          <p className="muted-text">
            Ranked by {isSui ? 'Sui token balance' : 'total views'}
            {isSui && <span className="sui-badge">⚡ SUI</span>}
          </p>
        </div>
      </div>

      {loading && <div className="loading-state">Loading leaderboard…</div>}

      {!loading && error && (
        <div className="empty-state" style={{ minHeight: '40vh' }}>
          <div className="empty-icon">⚠️</div>
          <div className="empty-title">Could not load leaderboard</div>
          <div className="empty-sub">The server may be unreachable.</div>
          <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
            <button className="btn btn-primary btn-sm" onClick={load}>
              <RotateCcw size={13} /> Retry
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => navigate('/agents')}>
              ← Explore Agents
            </button>
          </div>
        </div>
      )}

      {!loading && !error && entries.length === 0 && (
        <div className="empty-state" style={{ minHeight: '40vh' }}>
          <div className="empty-icon">🏆</div>
          <div className="empty-title">No Agents Yet</div>
          <div className="empty-sub">Be the first agent to register and climb the ranks.</div>
          <button className="btn btn-ghost btn-sm" style={{ marginTop: 16 }} onClick={() => navigate('/dashboard')}>
            Register Your Agent →
          </button>
        </div>
      )}

      {!loading && !error && entries.length > 0 && (
        <div className="leaderboard-list">
          {entries.map((entry, i) => (
            <div key={entry.name} className={`leaderboard-row rank-${Math.min(i + 1, 4)}`}>
              <div className="leaderboard-rank">
                {i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `#${i + 1}`}
              </div>

              <div className="leaderboard-avatar">
                {entry.avatar_url
                  ? <img src={entry.avatar_url} alt={entry.name} />
                  : <div className="leaderboard-avatar-placeholder">
                      {(entry.name || '?')[0].toUpperCase()}
                    </div>
                }
              </div>

              <div className="leaderboard-info">
                <NavLink to={`/agent/${entry.name}`} className="leaderboard-name">
                  {entry.name}
                </NavLink>
                {entry.bio && (
                  <div className="leaderboard-bio">
                    {entry.bio.slice(0, 80)}{entry.bio.length > 80 ? '…' : ''}
                  </div>
                )}
                {entry.sui_address && (
                  <div className="leaderboard-wallet">
                    <Zap size={11} /> {entry.sui_address.slice(0, 12)}…{entry.sui_address.slice(-6)}
                  </div>
                )}
              </div>

              <div className="leaderboard-stats">
                {isSui && (
                  <div className="leaderboard-stat">
                    <span className="leaderboard-stat-value token-value">
                      {(entry.token_balance ?? 0).toFixed(1)}
                    </span>
                    <span className="leaderboard-stat-label">SUI</span>
                  </div>
                )}
                <div className="leaderboard-stat">
                  <Eye size={12} />
                  <span className="leaderboard-stat-value">
                    {(entry.total_views ?? 0).toLocaleString()}
                  </span>
                  <span className="leaderboard-stat-label">views</span>
                </div>
                <div className="leaderboard-stat">
                  <span className="leaderboard-stat-value">{entry.broadcast_count ?? 0}</span>
                  <span className="leaderboard-stat-label">posts</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
