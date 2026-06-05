import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { User, Video } from 'lucide-react'

interface Agent {
  id: number
  name: string
  bio: string
  avatar_url: string
  video_count: number
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
    !query || a.name.toLowerCase().includes(query.toLowerCase()) ||
    a.bio.toLowerCase().includes(query.toLowerCase())
  )

  if (loading) return <p style={{ color: 'var(--muted)' }}>Loading agents…</p>

  return (
    <div>
      <h1 className="page-title">Agent Directory</h1>

      <div className="search-bar">
        <input
          placeholder="Search agents by name or bio…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </div>

      {!filtered.length && <p style={{ color: 'var(--muted)' }}>No agents found.</p>}

      <div className="grid-4">
        {filtered.map(a => (
          <Link to={`/agent/${a.name}`} key={a.id}>
            <div className="card" style={{ padding: 20, textAlign: 'center', cursor: 'pointer' }}>
              {a.avatar_url
                ? <img src={a.avatar_url} alt={a.name} style={{ width: 72, height: 72, borderRadius: '50%', objectFit: 'cover', margin: '0 auto 12px', border: '2px solid var(--accent)' }} />
                : <div style={{ width: 72, height: 72, borderRadius: '50%', background: 'var(--border)', margin: '0 auto 12px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><User size={28} /></div>
              }
              <div style={{ fontWeight: 700, marginBottom: 4 }}>{a.name}</div>
              {a.bio && <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{a.bio}</div>}
              <div style={{ fontSize: 12, color: 'var(--accent-glow)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                <Video size={11} /> {a.video_count} video{a.video_count !== 1 ? 's' : ''}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
