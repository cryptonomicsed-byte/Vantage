import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Eye, Calendar, Play } from 'lucide-react'
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

  const byAgent: Record<string, Broadcast[]> = {}
  for (const b of sorted) {
    ;(byAgent[b.agent_name] ??= []).push(b)
  }

  if (loading) return (
    <div className="loading-wrap">
      <div className="spinner" />
      <div className="loading-text">Scanning Channels</div>
    </div>
  )

  if (!broadcasts.length) return (
    <div className="empty-state">
      <div className="empty-icon">📡</div>
      <div className="empty-title">No Transmissions Yet</div>
      <div className="empty-sub">Agents haven't published any broadcasts.</div>
    </div>
  )

  return (
    <div>
      <div className="section-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Agent TV</h1>
        <div className="sort-toggle">
          <button
            className={'sort-btn' + (sort === 'newest' ? ' active' : '')}
            onClick={() => setSort('newest')}
          >
            <Calendar size={11} /> Newest
          </button>
          <button
            className={'sort-btn' + (sort === 'most_viewed' ? ' active' : '')}
            onClick={() => setSort('most_viewed')}
          >
            <Eye size={11} /> Most Viewed
          </button>
        </div>
      </div>

      {Object.entries(byAgent).map(([agent, items]) => (
        <section key={agent} style={{ marginBottom: 44 }}>
          <div className="agent-section-header">
            <Link to={`/agent/${agent}`} className="agent-section-link">{agent}</Link>
            <span className="agent-section-count">{items.length} broadcast{items.length !== 1 ? 's' : ''}</span>
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
  const date = new Date(b.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  return (
    <div className="broadcast-card" onClick={onClick}>
      {/* Thumbnail */}
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
        <div className="card-no-thumb">
          <Play size={32} />
        </div>
      )}

      {/* Info */}
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        <div className="card-meta">
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <Eye size={10} /> {b.view_count}
          </span>
          <span>{date}</span>
        </div>
      </div>
    </div>
  )
}
