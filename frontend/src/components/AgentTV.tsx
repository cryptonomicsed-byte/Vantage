import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Eye, Calendar, Play, X } from 'lucide-react'
import VideoModal from './VideoModal'
import { useFeedSocket } from '../hooks/useFeedSocket'

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

const HISTORY_KEY = 'vantage_history'
const MAX_HISTORY = 20

function readHistory(): Broadcast[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') }
  catch { return [] }
}

function saveToHistory(b: Broadcast) {
  const existing = readHistory().filter(h => h.id !== b.id)
  localStorage.setItem(
    HISTORY_KEY,
    JSON.stringify([b, ...existing].slice(0, MAX_HISTORY))
  )
}

export default function AgentTV({ searchQuery = '' }: { searchQuery?: string }) {
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortMode>('newest')
  const [selected, setSelected] = useState<Broadcast | null>(null)
  const [history, setHistory] = useState<Broadcast[]>([])
  const [toast, setToast] = useState('')

  useEffect(() => {
    fetch('/api/agents/feed?limit=100')
      .then(r => r.json())
      .then(data => { setBroadcasts(data); setLoading(false) })
      .catch(() => setLoading(false))
    setHistory(readHistory())
  }, [])

  useFeedSocket(b => {
    setBroadcasts(prev => [b, ...prev])
    setToast(`⚡ New transmission from ${b.agent_name}`)
    setTimeout(() => setToast(''), 4000)
  })

  function openBroadcast(b: Broadcast) {
    setSelected(b)
    saveToHistory(b)
    setHistory(readHistory())
  }

  function clearHistory() {
    localStorage.removeItem(HISTORY_KEY)
    setHistory([])
  }

  const sorted = useMemo(() =>
    [...broadcasts].sort((a, b) =>
      sort === 'newest'
        ? new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        : b.view_count - a.view_count
    ),
    [broadcasts, sort]
  )

  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return sorted
    const q = searchQuery.toLowerCase()
    return sorted.filter(b =>
      b.title.toLowerCase().includes(q) ||
      (b.description || '').toLowerCase().includes(q) ||
      b.agent_name.toLowerCase().includes(q)
    )
  }, [sorted, searchQuery])

  const byAgent = useMemo(() => {
    const map: Record<string, Broadcast[]> = {}
    for (const b of filtered) (map[b.agent_name] ??= []).push(b)
    return map
  }, [filtered])

  const hero = filtered[0] || null

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
      {toast && <div className="feed-toast">{toast}</div>}

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

      {/* Hero */}
      {hero && <HeroCard broadcast={hero} onClick={() => openBroadcast(hero)} />}

      {/* Continue watching */}
      {history.length > 0 && (
        <div style={{ marginBottom: 36 }}>
          <div className="agent-section-header">
            <span style={{ fontFamily: "'Orbitron', sans-serif", fontSize: 12, fontWeight: 700, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--cyan)' }}>
              Continue Watching
            </span>
            <button className="btn btn-ghost btn-sm" onClick={clearHistory} style={{ marginLeft: 'auto' }}>
              <X size={11} /> Clear
            </button>
          </div>
          <div className="continue-row">
            {history.map(b => (
              <div key={b.id} className="continue-card" onClick={() => openBroadcast(b)}>
                {b.thumbnail_url
                  ? <img src={b.thumbnail_url} alt={b.title} />
                  : <div className="continue-placeholder"><Play size={20} /></div>
                }
                <div className="continue-title">{b.title}</div>
                <div className="continue-agent">{b.agent_name}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No search results */}
      {searchQuery.trim() && !filtered.length && (
        <div className="empty-state" style={{ minHeight: '20vh' }}>
          <div className="empty-icon">🔍</div>
          <div className="empty-title">No Results</div>
          <div className="empty-sub">No broadcasts match "{searchQuery}"</div>
        </div>
      )}

      {/* Feed by agent */}
      {Object.entries(byAgent).map(([agent, items]) => (
        <section key={agent} style={{ marginBottom: 44 }}>
          <div className="agent-section-header">
            <Link to={`/agent/${agent}`} className="agent-section-link">{agent}</Link>
            <span className="agent-section-count">{items.length} broadcast{items.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="grid-3">
            {items.map(b => (
              <BroadcastCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
            ))}
          </div>
        </section>
      ))}

      {selected && <VideoModal broadcast={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

function HeroCard({ broadcast: b, onClick }: { broadcast: Broadcast; onClick: () => void }) {
  return (
    <div className="hero-card" onClick={onClick}>
      {b.thumbnail_url && <img src={b.thumbnail_url} alt={b.title} className="hero-thumb" />}
      <div className="hero-gradient" />
      <div className="hero-content">
        <div className="hero-agent">{b.agent_name}</div>
        <div className="hero-title">{b.title}</div>
        <div className="hero-meta">
          <Eye size={12} /> {b.view_count.toLocaleString()} views
        </div>
        <div className="hero-play-btn">
          <Play size={20} fill="white" color="white" /> Play
        </div>
      </div>
    </div>
  )
}

function BroadcastCard({ broadcast: b, onClick }: { broadcast: Broadcast; onClick: () => void }) {
  const date = new Date(b.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  return (
    <div className="broadcast-card" onClick={onClick}>
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
          <span>{date}</span>
        </div>
      </div>
    </div>
  )
}
