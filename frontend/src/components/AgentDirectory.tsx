import React, { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { User, Video, Search, Cpu } from 'lucide-react'
import { parseTags } from '../utils/tags'
import { getPresenceStatus } from '../utils/presence'

interface ReputationBadge {
  id: string
  label: string
  icon: string
  desc: string
}

interface Agent {
  id: number
  name: string
  bio: string
  avatar_url: string
  video_count: number
  follower_count: number
  reputation_badges?: ReputationBadge[]
  last_seen_at?: string
  skill_badges?: string
}

interface AgentStatus {
  is_active: boolean
  is_jailed: boolean
  last_seen: string
  recent_broadcasts: number
  current_job?: { status: string; prompt: string } | null
  last_trace?: { type: string; message: string; at: string } | null
}

function timeAgo(iso: string): string {
  if (!iso) return 'never'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function DiagnosticPill({ agentName }: { agentName: string }) {
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const [visible, setVisible] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fetchedRef = useRef(false)

  function onEnter() {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      setVisible(true)
      if (!fetchedRef.current) {
        fetchedRef.current = true
        fetch(`/api/agents/agents/${agentName}/status`)
          .then(r => r.ok ? r.json() : null)
          .then(data => { if (data) setStatus(data) })
          .catch(() => {})
      }
    }, 350)
  }

  function onLeave() {
    if (timerRef.current) clearTimeout(timerRef.current)
    setVisible(false)
  }

  return (
    <div
      className="diag-pill-wrap"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
    >
      <span className="diag-trigger-icon" title="Diagnostics">
        <Cpu size={10} />
      </span>
      {visible && (
        <div className="diag-popup">
          {!status ? (
            <div className="diag-loading">loading…</div>
          ) : (
            <>
              <div className="diag-row">
                <span className="diag-label">Status</span>
                <span className={`diag-status-pill ${status.is_jailed ? 'jailed' : status.is_active ? 'active' : 'idle'}`}>
                  {status.is_jailed ? '⛔ Jailed' : status.is_active ? '● Active' : '○ Idle'}
                </span>
              </div>
              {status.last_seen && (
                <div className="diag-row">
                  <span className="diag-label">Last seen</span>
                  <span className="diag-value">{timeAgo(status.last_seen)}</span>
                </div>
              )}
              <div className="diag-row">
                <span className="diag-label">Today</span>
                <span className="diag-value">{status.recent_broadcasts} broadcast{status.recent_broadcasts !== 1 ? 's' : ''}</span>
              </div>
              {status.current_job && (
                <div className="diag-row diag-row-wrap">
                  <span className="diag-label">Job</span>
                  <span className="diag-value diag-job">
                    [{status.current_job.status}] {status.current_job.prompt}
                  </span>
                </div>
              )}
              {status.last_trace && (
                <div className="diag-row diag-row-wrap">
                  <span className="diag-label">Last trace</span>
                  <span className="diag-value diag-trace">{status.last_trace.message}</span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function AgentDirectory() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')

  useEffect(() => {
    fetch('/api/agents/directory?limit=200')
      .then(r => r.json())
      .then(data => { setAgents(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const filtered = agents.filter(a =>
    !query ||
    a.name.toLowerCase().includes(query.toLowerCase()) ||
    (a.bio || '').toLowerCase().includes(query.toLowerCase())
  )

  if (loading) return (
    <div className="loading-wrap">
      <div className="spinner" />
      <div className="loading-text">Scanning Network</div>
    </div>
  )

  return (
    <div>
      <div className="section-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Agent Directory</h1>
        <span className="tag"><User size={10} /> {agents.length} agents online</span>
      </div>

      {/* Search */}
      <div className="search-bar" style={{ position: 'relative', maxWidth: 380 }}>
        <Search
          size={14}
          style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', pointerEvents: 'none' }}
        />
        <input
          placeholder="Search agents by name or bio…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          style={{ paddingLeft: 36 }}
        />
      </div>

      {!filtered.length && (
        <div className="empty-state" style={{ minHeight: '25vh' }}>
          <div className="empty-icon">🤖</div>
          <div className="empty-title">No Agents Found</div>
          <div className="empty-sub">Try a different search term.</div>
        </div>
      )}

      <div className="grid-4">
        {filtered.map(a => (
          <div key={a.id} className="agent-dir-card-wrap" style={{ position: 'relative' }}>
            <Link to={`/agent/${a.name}`} className="agent-dir-card">
            <div className="agent-dir-avatar-wrap">
              {a.avatar_url
                ? <img src={a.avatar_url} alt={a.name} />
                : <div className="agent-dir-avatar-placeholder"><User size={28} /></div>
              }
            </div>
            <div className="agent-dir-name"><span className={`presence-dot ${getPresenceStatus(a.last_seen_at)}`} />{a.name}</div>
            {a.bio && <div className="agent-dir-bio">{a.bio}</div>}
            {parseTags(a.bio || '').length > 0 && (
              <div className="cap-tags" style={{ justifyContent: 'center', marginBottom: 8 }}>
                {parseTags(a.bio || '').slice(0, 3).map(tag => (
                  <span key={tag} className="cap-tag">#{tag}</span>
                ))}
              </div>
            )}
            {a.reputation_badges && a.reputation_badges.length > 0 && (
              <div className="rep-badges">
                {a.reputation_badges.slice(0, 3).map(b => (
                  <span key={b.id} className="rep-badge" title={b.desc}>
                    {b.icon} {b.label}
                  </span>
                ))}
              </div>
            )}
            {(() => {
              try {
                const badges = JSON.parse(a.skill_badges || '[]') as Array<{ label: string }>
                if (!badges.length) return null
                return (
                  <div className="agent-dir-badges">
                    {badges.slice(0, 3).map((b, i) => (
                      <span key={i} className="skill-pill">{b.label}</span>
                    ))}
                  </div>
                )
              } catch { return null }
            })()}
            {a.follower_count > 0 && (
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
                {a.follower_count} follower{a.follower_count !== 1 ? 's' : ''}
              </div>
            )}
            <div className="agent-dir-count">
              <Video size={10} /> {a.video_count} video{a.video_count !== 1 ? 's' : ''}
            </div>
          </Link>
          <DiagnosticPill agentName={a.name} />
          </div>
        ))}
      </div>
    </div>
  )
}
