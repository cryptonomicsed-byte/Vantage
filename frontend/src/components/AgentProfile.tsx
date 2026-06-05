import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Eye, Video, User } from 'lucide-react'
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

  if (loading) return <p style={{ color: 'var(--muted)' }}>Loading profile…</p>
  if (error || !profile) return (
    <div className="not-found">
      <h1>404</h1>
      <h2>Agent Not Found</h2>
      <Link to="/agents" className="btn btn-primary" style={{ marginTop: 8 }}>Browse Agents</Link>
    </div>
  )

  const totalViews = profile.broadcasts.reduce((s, b) => s + b.view_count, 0)

  return (
    <div>
      <div className="agent-hero">
        {profile.avatar_url
          ? <img src={profile.avatar_url} alt={profile.name} className="agent-avatar" />
          : <div className="agent-avatar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}><User size={36} /></div>
        }
        <div style={{ flex: 1 }}>
          <h1 style={{ fontSize: 28, fontWeight: 800, marginBottom: 6 }}>{profile.name}</h1>
          {profile.bio && <p style={{ color: 'var(--muted)', marginBottom: 16 }}>{profile.bio}</p>}
          <div style={{ display: 'flex', gap: 32 }}>
            <div className="agent-stat">
              <div className="agent-stat-num">{profile.broadcasts.length}</div>
              <div className="agent-stat-label">Videos</div>
            </div>
            <div className="agent-stat">
              <div className="agent-stat-num">{totalViews}</div>
              <div className="agent-stat-label">Total Views</div>
            </div>
          </div>
        </div>
      </div>

      <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 16 }}>Videos</h2>

      {!profile.broadcasts.length && <p style={{ color: 'var(--muted)' }}>No videos yet.</p>}

      <div className="grid-3">
        {profile.broadcasts.map(b => (
          <div className="card" key={b.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(b)}>
            {b.thumbnail_url
              ? <img src={b.thumbnail_url} alt={b.title} style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover' }} />
              : <div className="card-thumb">▶</div>
            }
            <div className="card-body">
              <div className="card-title">{b.title}</div>
              <div className="card-meta">
                <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}><Eye size={11} /> {b.view_count}</span>
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
