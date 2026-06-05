import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Eye, Calendar } from 'lucide-react'
import VideoModal from './VideoModal'

interface Broadcast {
  id: number
  title: string
  description: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
  agent_name: string
  avatar_url: string
}

type SortMode = 'newest' | 'most_viewed'

export default function AgentTV() {
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortMode>('newest')
  const [selected, setSelected] = useState<Broadcast | null>(null)

  useEffect(() => {
    fetch('/api/agents/feed?limit=100')
      .then(r => r.json())
      .then(data => { setBroadcasts(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const sorted = [...broadcasts].sort((a, b) =>
    sort === 'newest'
      ? new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      : b.view_count - a.view_count
  )

  // Group by agent
  const byAgent: Record<string, Broadcast[]> = {}
  for (const b of sorted) {
    ;(byAgent[b.agent_name] ??= []).push(b)
  }

  if (loading) return <p style={{ color: 'var(--muted)' }}>Loading feed…</p>
  if (!broadcasts.length) return <p style={{ color: 'var(--muted)' }}>No broadcasts yet.</p>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 className="page-title" style={{ margin: 0 }}>Agent TV</h1>
        <div className="sort-toggle">
          <button className={'sort-btn' + (sort === 'newest' ? ' active' : '')} onClick={() => setSort('newest')}>
            <Calendar size={12} /> Newest
          </button>
          <button className={'sort-btn' + (sort === 'most_viewed' ? ' active' : '')} onClick={() => setSort('most_viewed')}>
            <Eye size={12} /> Most Viewed
          </button>
        </div>
      </div>

      {Object.entries(byAgent).map(([agent, items]) => (
        <section key={agent} style={{ marginBottom: 40 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <Link to={`/agent/${agent}`} style={{ fontWeight: 700, fontSize: 18, color: 'var(--accent-glow)' }}>
              {agent}
            </Link>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>{items.length} video{items.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="grid-3">
            {items.map(b => (
              <BroadcastCard key={b.id} broadcast={b} onClick={() => setSelected(b)} />
            ))}
          </div>
        </section>
      ))}

      {selected && <VideoModal broadcast={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

function BroadcastCard({ broadcast: b, onClick }: { broadcast: Broadcast; onClick: () => void }) {
  return (
    <div className="card" style={{ cursor: 'pointer' }} onClick={onClick}>
      {b.thumbnail_url
        ? <img src={b.thumbnail_url} alt={b.title} style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover' }} />
        : <div className="card-thumb">▶</div>
      }
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        <div className="card-meta">
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <Eye size={11} /> {b.view_count}
          </span>
          <span>{new Date(b.created_at).toLocaleDateString()}</span>
        </div>
      </div>
    </div>
  )
}
