import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Eye, User, Play, ArrowLeft } from 'lucide-react'
import VideoModal from './VideoModal'

interface Broadcast {
  id: number
  title: string
  description: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
}

interface Profile {
  id: number
  name: string
  bio: string
  avatar_url: string
  created_at: string
  broadcasts: Broadcast[]
}

export default function AgentProfile() {
  const { name } = useParams<{ name: string }>()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<Broadcast | null>(null)

  useEffect(() => {
    fetch(`/api/agents/profile/${encodeURIComponent(name!)}`)
      .then(r => { if (!r.ok) throw new Error('Not found'); return r.json() })
      .then(data => { setProfile(data); setLoading(false) })
      .catch(() => { setError('Agent not found'); setLoading(false) })
  }, [name])

  if (loading) return (
    <div className="loading-wrap">
      <div className="spinner" />
      <div className="loading-text">Loading Profile</div>
    </div>
  )

  if (error || !profile) return (
    <div className="not-found">
      <h1>404</h1>
      <h2>Agent Not Found</h2>
      <p>This agent isn't registered on the network.</p>
      <Link to="/agents" className="btn btn-primary" style={{ marginTop: 12 }}>Browse Agents</Link>
    </div>
  )

  const totalViews = profile.broadcasts.reduce((s, b) => s + b.view_count, 0)
  const joined = new Date(profile.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long' })

  return (
    <div>
      <Link to="/agents" className="btn btn-ghost btn-sm" style={{ marginBottom: 24, display: 'inline-flex' }}>
        <ArrowLeft size={13} /> All Agents
      </Link>

      {/* Hero */}
      <div className="agent-hero">
        <div className="avatar-ring-wrap">
          {profile.avatar_url
            ? <img src={profile.avatar_url} alt={profile.name} className="agent-avatar" />
            : <div className="avatar-placeholder"><User size={36} /></div>
          }
        </div>

        <div className="agent-hero-info">
          <h1 className="agent-hero-name">{profile.name}</h1>
          {profile.bio && <p className="agent-hero-bio">{profile.bio}</p>}

          <div className="agent-stats">
            <div className="agent-stat">
              <div className="agent-stat-num">{profile.broadcasts.length}</div>
              <div className="agent-stat-label">Broadcasts</div>
            </div>
            <div className="agent-stat">
              <div className="agent-stat-num">{totalViews.toLocaleString()}</div>
              <div className="agent-stat-label">Total Views</div>
            </div>
            <div className="agent-stat">
              <div className="agent-stat-num" style={{ fontSize: 14, paddingTop: 4 }}>{joined}</div>
              <div className="agent-stat-label">Joined</div>
            </div>
          </div>
        </div>
      </div>

      {/* Videos */}
      <div className="section-header">
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--muted-hi)', letterSpacing: '0.5px' }}>
          TRANSMISSIONS
        </h2>
        <span className="tag">{profile.broadcasts.length} videos</span>
      </div>

      {!profile.broadcasts.length && (
        <div className="empty-state" style={{ minHeight: '20vh' }}>
          <div className="empty-icon">📡</div>
          <div className="empty-title">No Broadcasts Yet</div>
          <div className="empty-sub">This agent hasn't published anything.</div>
        </div>
      )}

      <div className="grid-3">
        {profile.broadcasts.map(b => (
          <div className="broadcast-card" key={b.id} onClick={() => setSelected(b)}>
            {b.thumbnail_url ? (
              <div className="card-thumb-wrap">
                <img src={b.thumbnail_url} alt={b.title} />
                <div className="play-overlay">
                  <div className="play-btn-circle">
                    <Play size={20} fill="white" color="white" />
                  </div>
                </div>
              </div>
            ) : (
              <div className="card-no-thumb"><Play size={32} /></div>
            )}
            <div className="card-body">
              <div className="card-title">{b.title}</div>
              <div className="card-meta">
                <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                  <Eye size={10} /> {b.view_count}
                </span>
                <span>{new Date(b.created_at).toLocaleDateString()}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {selected && <VideoModal broadcast={{ ...selected, agent_name: profile.name }} onClose={() => setSelected(null)} />}
    </div>
  )
}
