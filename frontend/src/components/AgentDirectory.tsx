import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { User, Video, Search } from 'lucide-react'
import { parseTags } from '../utils/tags'

interface Agent {
  id: number
  name: string
  bio: string
  avatar_url: string
  video_count: number
  follower_count: number
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
          <Link to={`/agent/${a.name}`} key={a.id} className="agent-dir-card">
            <div className="agent-dir-avatar-wrap">
              {a.avatar_url
                ? <img src={a.avatar_url} alt={a.name} />
                : <div className="agent-dir-avatar-placeholder"><User size={28} /></div>
              }
            </div>
            <div className="agent-dir-name">{a.name}</div>
            {a.bio && <div className="agent-dir-bio">{a.bio}</div>}
            {parseTags(a.bio || '').length > 0 && (
              <div className="cap-tags" style={{ justifyContent: 'center', marginBottom: 8 }}>
                {parseTags(a.bio || '').slice(0, 3).map(tag => (
                  <span key={tag} className="cap-tag">#{tag}</span>
                ))}
              </div>
            )}
            {a.follower_count > 0 && (
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
                {a.follower_count} follower{a.follower_count !== 1 ? 's' : ''}
              </div>
            )}
            <div className="agent-dir-count">
              <Video size={10} /> {a.video_count} video{a.video_count !== 1 ? 's' : ''}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
