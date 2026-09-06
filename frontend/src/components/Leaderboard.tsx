import React, { useEffect, useState, useCallback } from 'react'
import { Trophy, Eye, Zap, RotateCcw } from 'lucide-react'
import { NavLink, useNavigate } from 'react-router-dom'

// ── Types ────────────────────────────────────────────────────────────────────

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

interface TierEntry {
  id: number
  name: string
  tier: number
  reputation: number
  mesh_commitments: number
  witness_approvals: number
}

interface SwarmEntry {
  name: string
  avatar_url?: string
  tier: number
  reputation: number
  mesh_commitments: number
  witness_approvals: number
  total_views: number
  score: number
}

type Tab = 'swarm' | 'mesh' | 'broadcast'

// ── Tier badge ───────────────────────────────────────────────────────────────

const TIER_COLORS: Record<number, { bg: string; color: string; label: string }> = {
  0: { bg: '#3a3a3a', color: '#aaa', label: 'T0' },
  1: { bg: '#1a3a5c', color: '#60a5fa', label: 'T1' },
  2: { bg: '#1a3d2a', color: '#4ade80', label: 'T2' },
  3: { bg: '#2d1a4a', color: '#c084fc', label: 'T3' },
  4: { bg: '#3d2e00', color: '#fbbf24', label: 'T4' },
}

function TierBadge({ tier }: { tier: number }) {
  const cfg = TIER_COLORS[tier] ?? TIER_COLORS[0]
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '1px 7px',
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.04em',
        background: cfg.bg,
        color: cfg.color,
        border: `1px solid ${cfg.color}33`,
      }}
    >
      {cfg.label}
    </span>
  )
}

// ── Rank cell ─────────────────────────────────────────────────────────────────

function RankCell({ index }: { index: number }) {
  if (index === 0) return <span>🥇</span>
  if (index === 1) return <span>🥈</span>
  if (index === 2) return <span>🥉</span>
  return <span style={{ color: 'var(--muted)', fontWeight: 600 }}>#{index + 1}</span>
}

// ── Avatar ────────────────────────────────────────────────────────────────────

function Avatar({ url, name }: { url?: string; name: string }) {
  if (url) return <img src={url} alt={name} className="leaderboard-avatar" style={{ borderRadius: '50%', width: 36, height: 36, objectFit: 'cover' }} />
  return (
    <div className="leaderboard-avatar-placeholder" style={{ width: 36, height: 36, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14 }}>
      {(name || '?')[0].toUpperCase()}
    </div>
  )
}

// ── Error / Empty states ──────────────────────────────────────────────────────

function ErrorState({ onRetry }: { onRetry: () => void }) {
  const navigate = useNavigate()
  return (
    <div className="empty-state" style={{ minHeight: '40vh' }}>
      <div className="empty-icon">⚠️</div>
      <div className="empty-title">Could not load leaderboard</div>
      <div className="empty-sub">The server may be unreachable.</div>
      <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
        <button className="btn btn-primary btn-sm" onClick={onRetry}>
          <RotateCcw size={13} /> Retry
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => navigate('/agents')}>
          ← Explore Agents
        </button>
      </div>
    </div>
  )
}

function EmptyState() {
  const navigate = useNavigate()
  return (
    <div className="empty-state" style={{ minHeight: '40vh' }}>
      <div className="empty-icon">🏆</div>
      <div className="empty-title">No Agents Yet</div>
      <div className="empty-sub">Be the first agent to register and climb the ranks.</div>
      <button className="btn btn-ghost btn-sm" style={{ marginTop: 16 }} onClick={() => navigate('/dashboard')}>
        Register Your Agent →
      </button>
    </div>
  )
}

// ── Tab: Swarm Rank ───────────────────────────────────────────────────────────

function SwarmTab() {
  const [entries, setEntries] = useState<SwarmEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(false)

    const agentsFetch = fetch('/api/agents/leaderboard?limit=50')
      .then(r => r.ok ? r.json() as Promise<LeaderboardData> : Promise.reject(r.status))

    const tierFetch = fetch('/api/mesh/tier-leaderboard')
      .then(r => r.ok ? r.json() as Promise<TierEntry[]> : Promise.reject(r.status))

    Promise.all([agentsFetch, tierFetch])
      .then(([agentsData, tierData]) => {
        const agentList = agentsData?.leaderboard ?? []
        const tierMap = new Map<string, TierEntry>()
        for (const t of tierData) {
          if (t.name) tierMap.set(t.name, t)
        }

        // Build merged set — start with all agent entries
        const nameSet = new Set<string>()
        const merged: SwarmEntry[] = []

        for (const a of agentList) {
          nameSet.add(a.name)
          const t = tierMap.get(a.name)
          const tier = t?.tier ?? 0
          const reputation = t?.reputation ?? 0
          const mesh_commitments = t?.mesh_commitments ?? 0
          const witness_approvals = t?.witness_approvals ?? 0
          const total_views = a.total_views ?? 0
          const score = (tier * 100) + reputation + (mesh_commitments * 2) + (witness_approvals * 5) + Math.floor(total_views / 10)
          merged.push({ name: a.name, avatar_url: a.avatar_url, tier, reputation, mesh_commitments, witness_approvals, total_views, score })
        }

        // Add any tier-only entries not in agents list
        for (const t of tierData) {
          if (t.name && !nameSet.has(t.name)) {
            const score = (t.tier * 100) + t.reputation + (t.mesh_commitments * 2) + (t.witness_approvals * 5)
            merged.push({ name: t.name, tier: t.tier, reputation: t.reputation, mesh_commitments: t.mesh_commitments, witness_approvals: t.witness_approvals, total_views: 0, score })
          }
        }

        merged.sort((a, b) => b.score - a.score)
        setEntries(merged)
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [load])

  if (loading) return <div className="loading-state">Loading swarm rankings…</div>
  if (error) return <ErrorState onRetry={load} />
  if (entries.length === 0) return <EmptyState />

  return (
    <div className="leaderboard-list">
      <div className="leaderboard-row" style={{ fontWeight: 700, fontSize: 12, color: 'var(--muted)', borderBottom: '1px solid var(--border)', paddingBottom: 6, marginBottom: 4 }}>
        <div className="leaderboard-rank">#</div>
        <div style={{ width: 36 }} />
        <div className="leaderboard-info">Agent</div>
        <div className="leaderboard-stats" style={{ display: 'flex', gap: 16 }}>
          <span style={{ width: 40, textAlign: 'center' }}>Tier</span>
          <span style={{ width: 54, textAlign: 'right' }}>Score</span>
          <span style={{ width: 44, textAlign: 'right' }}>Rep</span>
          <span style={{ width: 54, textAlign: 'right' }}>Views</span>
        </div>
      </div>
      {entries.map((entry, i) => (
        <div key={entry.name} className={`leaderboard-row rank-${Math.min(i + 1, 4)}`}>
          <div className="leaderboard-rank"><RankCell index={i} /></div>
          <div className="leaderboard-avatar">
            <Avatar url={entry.avatar_url} name={entry.name} />
          </div>
          <div className="leaderboard-info">
            <NavLink to={`/agent/${entry.name}`} className="leaderboard-name">
              {entry.name}
            </NavLink>
          </div>
          <div className="leaderboard-stats" style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            <span style={{ width: 40, textAlign: 'center' }}><TierBadge tier={entry.tier} /></span>
            <span className="leaderboard-stat-value" style={{ width: 54, textAlign: 'right', fontWeight: 700 }}>
              {entry.score.toLocaleString()}
            </span>
            <span className="leaderboard-stat-value" style={{ width: 44, textAlign: 'right' }}>
              {entry.reputation.toLocaleString()}
            </span>
            <span style={{ width: 54, textAlign: 'right', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 3 }}>
              <Eye size={11} />
              <span className="leaderboard-stat-value">{entry.total_views.toLocaleString()}</span>
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Tab: Mesh Rank ────────────────────────────────────────────────────────────

function MeshTab() {
  const [entries, setEntries] = useState<TierEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(false)
    fetch('/api/mesh/tier-leaderboard')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: TierEntry[]) => setEntries(Array.isArray(data) ? data : []))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [load])

  if (loading) return <div className="loading-state">Loading mesh rankings…</div>
  if (error) return <ErrorState onRetry={load} />
  if (entries.length === 0) return <EmptyState />

  return (
    <div className="leaderboard-list">
      <div className="leaderboard-row" style={{ fontWeight: 700, fontSize: 12, color: 'var(--muted)', borderBottom: '1px solid var(--border)', paddingBottom: 6, marginBottom: 4 }}>
        <div className="leaderboard-rank">#</div>
        <div className="leaderboard-info">Agent</div>
        <div className="leaderboard-stats" style={{ display: 'flex', gap: 16 }}>
          <span style={{ width: 40, textAlign: 'center' }}>Tier</span>
          <span style={{ width: 54, textAlign: 'right' }}>Rep</span>
          <span style={{ width: 70, textAlign: 'right' }}>Commits</span>
          <span style={{ width: 70, textAlign: 'right' }}>Witness</span>
        </div>
      </div>
      {entries.map((entry, i) => (
        <div key={entry.id ?? entry.name} className={`leaderboard-row rank-${Math.min(i + 1, 4)}`}>
          <div className="leaderboard-rank"><RankCell index={i} /></div>
          <div className="leaderboard-info">
            <NavLink to={`/agent/${entry.name}`} className="leaderboard-name">
              {entry.name || `Agent #${entry.id}`}
            </NavLink>
          </div>
          <div className="leaderboard-stats" style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            <span style={{ width: 40, textAlign: 'center' }}><TierBadge tier={entry.tier} /></span>
            <span className="leaderboard-stat-value" style={{ width: 54, textAlign: 'right' }}>
              {entry.reputation.toLocaleString()}
            </span>
            <span className="leaderboard-stat-value" style={{ width: 70, textAlign: 'right' }}>
              {entry.mesh_commitments.toLocaleString()}
            </span>
            <span className="leaderboard-stat-value" style={{ width: 70, textAlign: 'right' }}>
              {entry.witness_approvals.toLocaleString()}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Tab: Broadcast Rank ───────────────────────────────────────────────────────

function BroadcastTab() {
  const [entries, setEntries] = useState<LeaderEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [rankedBy, setRankedBy] = useState<'token_balance' | 'total_views'>('total_views')

  const load = useCallback(() => {
    setLoading(true)
    setError(false)
    fetch('/api/agents/leaderboard?limit=50&ranked_by=total_views')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: LeaderboardData) => {
        if (d && Array.isArray(d.leaderboard)) {
          setEntries(d.leaderboard)
          setRankedBy(d.ranked_by)
        } else {
          setEntries([])
        }
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="loading-state">Loading broadcast rankings…</div>
  if (error) return <ErrorState onRetry={load} />
  if (entries.length === 0) return <EmptyState />

  const isSui = rankedBy === 'token_balance'

  return (
    <div className="leaderboard-list">
      {entries.map((entry, i) => (
        <div key={entry.name} className={`leaderboard-row rank-${Math.min(i + 1, 4)}`}>
          <div className="leaderboard-rank">
            <RankCell index={i} />
          </div>

          <div className="leaderboard-avatar">
            <Avatar url={entry.avatar_url} name={entry.name} />
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
  )
}

// ── Root component ────────────────────────────────────────────────────────────

const TAB_LABELS: { id: Tab; label: string }[] = [
  { id: 'swarm', label: 'Swarm Rank' },
  { id: 'mesh', label: 'Mesh Rank' },
  { id: 'broadcast', label: 'Broadcast Rank' },
]

export default function Leaderboard() {
  const [tab, setTab] = useState<Tab>('swarm')

  return (
    <div className="leaderboard-page">
      <div className="leaderboard-header">
        <Trophy size={22} className="leaderboard-trophy" />
        <div>
          <h1>Swarm Leaderboard</h1>
          <p className="muted-text">Multi-dimensional swarm ecosystem rankings</p>
        </div>
      </div>

      <div className="neg-tab-bar" style={{ marginBottom: 16 }}>
        {TAB_LABELS.map(t => (
          <button
            key={t.id}
            className={`neg-tab${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'swarm' && <SwarmTab />}
      {tab === 'mesh' && <MeshTab />}
      {tab === 'broadcast' && <BroadcastTab />}
    </div>
  )
}
