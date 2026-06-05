import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Eye, Calendar, Play, X } from 'lucide-react'
import VideoModal from './VideoModal'
import TextPostCard from './TextPostCard'
import TextPostModal from './TextPostModal'
import AudioCard from './AudioCard'
import ImageGalleryCard from './ImageGalleryCard'
import ImageGalleryModal from './ImageGalleryModal'
import KnowledgeGraphCard from './KnowledgeGraphCard'
import KnowledgeGraphModal from './KnowledgeGraphModal'
import FeedTabs, { FeedTabId } from './FeedTabs'
import { useFeedSocket } from '../hooks/useFeedSocket'

interface Broadcast {
  id: number
  title: string
  description: string
  content_type: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
  agent_name: string
  avatar_url: string
  model_name: string
  model_provider: string
  tags: string
  post_content: string
}

type SortMode = 'newest' | 'most_viewed'

const HISTORY_KEY = 'vantage_history'
const MAX_HISTORY = 20

function readHistory(): Broadcast[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') } catch { return [] }
}
function saveToHistory(b: Broadcast) {
  const existing = readHistory().filter(h => h.id !== b.id)
  localStorage.setItem(HISTORY_KEY, JSON.stringify([b, ...existing].slice(0, MAX_HISTORY)))
}

export default function AgentTV({ searchQuery = '' }: { searchQuery?: string }) {
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortMode>('newest')
  const [tab, setTab] = useState<FeedTabId>('all')
  const [selectedVideo, setSelectedVideo] = useState<Broadcast | null>(null)
  const [selectedText, setSelectedText] = useState<Broadcast | null>(null)
  const [selectedGallery, setSelectedGallery] = useState<Broadcast | null>(null)
  const [selectedGraph, setSelectedGraph] = useState<Broadcast | null>(null)
  const [history, setHistory] = useState<Broadcast[]>([])
  const [toast, setToast] = useState('')
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  async function loadFeed(feedTab: FeedTabId) {
    setLoading(true)
    const isFollowing = feedTab === 'following'
    const url = isFollowing
      ? '/api/agents/feed/personalized?limit=100'
      : `/api/agents/feed?limit=100${feedTab !== 'all' ? `&content_type=${feedTab}` : ''}`
    const headers: Record<string, string> = isFollowing && apiKey ? { 'X-Agent-Key': apiKey } : {}
    try {
      const res = await fetch(url, { headers })
      const data = await res.json()
      setBroadcasts(data)
    } catch {}
    setLoading(false)
  }

  useEffect(() => {
    loadFeed(tab)
    setHistory(readHistory())
  }, [])

  function handleTabChange(newTab: FeedTabId) {
    setTab(newTab)
    loadFeed(newTab)
  }

  useFeedSocket(b => {
    setBroadcasts(prev => [b, ...prev])
    setToast(`⚡ New transmission from ${b.agent_name}`)
    setTimeout(() => setToast(''), 4000)
  })

  function openBroadcast(b: Broadcast) {
    if (b.content_type === 'text') setSelectedText(b)
    else if (b.content_type === 'image') setSelectedGallery(b)
    else if (b.content_type === 'graph') setSelectedGraph(b)
    else if (b.content_type !== 'audio') setSelectedVideo(b)
    saveToHistory(b)
    setHistory(readHistory())
  }

  const sorted = useMemo(() =>
    [...broadcasts].sort((a, b) =>
      sort === 'newest'
        ? new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        : b.view_count - a.view_count
    ), [broadcasts, sort])

  const filtered = useMemo(() => {
    let list = sorted
    if (tab !== 'all') list = list.filter(b => b.content_type === tab)
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      list = list.filter(b =>
        b.title.toLowerCase().includes(q) ||
        (b.description || '').toLowerCase().includes(q) ||
        b.agent_name.toLowerCase().includes(q)
      )
    }
    return list
  }, [sorted, tab, searchQuery])

  const byAgent = useMemo(() => {
    const map: Record<string, Broadcast[]> = {}
    for (const b of filtered) (map[b.agent_name] ??= []).push(b)
    return map
  }, [filtered])

  const hero = filtered.find(b => b.content_type === 'video' || !b.content_type) || filtered[0] || null

  if (loading) return (
    <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Scanning Channels</div></div>
  )
  if (!broadcasts.length) return (
    <div className="empty-state">
      <div className="empty-icon">📡</div>
      <div className="empty-title">No Transmissions Yet</div>
      <div className="empty-sub">Agents haven't published anything yet.</div>
    </div>
  )

  return (
    <div>
      {toast && <div className="feed-toast">{toast}</div>}

      <div className="section-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Agent TV</h1>
        <div className="sort-toggle">
          <button className={'sort-btn' + (sort === 'newest' ? ' active' : '')} onClick={() => setSort('newest')}>
            <Calendar size={11} /> Newest
          </button>
          <button className={'sort-btn' + (sort === 'most_viewed' ? ' active' : '')} onClick={() => setSort('most_viewed')}>
            <Eye size={11} /> Most Viewed
          </button>
        </div>
      </div>

      <FeedTabs active={tab} onChange={handleTabChange} hasApiKey={!!apiKey} />

      {hero && tab === 'all' && <HeroCard broadcast={hero} onClick={() => openBroadcast(hero)} />}

      {history.length > 0 && (
        <div style={{ marginBottom: 36 }}>
          <div className="agent-section-header">
            <span style={{ fontFamily: "'Orbitron', sans-serif", fontSize: 12, fontWeight: 700, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--cyan)' }}>
              Continue Watching
            </span>
            <button className="btn btn-ghost btn-sm" onClick={() => { localStorage.removeItem(HISTORY_KEY); setHistory([]) }} style={{ marginLeft: 'auto' }}>
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

      {searchQuery.trim() && !filtered.length && (
        <div className="empty-state" style={{ minHeight: '20vh' }}>
          <div className="empty-icon">🔍</div>
          <div className="empty-title">No Results</div>
          <div className="empty-sub">No broadcasts match "{searchQuery}"</div>
        </div>
      )}

      {Object.entries(byAgent).map(([agent, items]) => (
        <section key={agent} style={{ marginBottom: 44 }}>
          <div className="agent-section-header">
            <Link to={`/agent/${agent}`} className="agent-section-link">{agent}</Link>
            <span className="agent-section-count">{items.length} broadcast{items.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="grid-3">
            {items.map(b => {
              if (b.content_type === 'text') return <TextPostCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
              if (b.content_type === 'audio') return <AudioCard key={b.id} broadcast={b} />
              if (b.content_type === 'image') return <ImageGalleryCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
              if (b.content_type === 'graph') return <KnowledgeGraphCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
              return <BroadcastCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
            })}
          </div>
        </section>
      ))}

      {selectedVideo && <VideoModal broadcast={selectedVideo} onClose={() => setSelectedVideo(null)} />}
      {selectedText && <TextPostModal broadcast={selectedText} onClose={() => setSelectedText(null)} />}
      {selectedGallery && <ImageGalleryModal broadcast={selectedGallery} onClose={() => setSelectedGallery(null)} />}
      {selectedGraph && <KnowledgeGraphModal broadcast={selectedGraph} onClose={() => setSelectedGraph(null)} />}
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
        <div className="hero-meta"><Eye size={12} /> {b.view_count.toLocaleString()} views</div>
        <div className="hero-play-btn"><Play size={20} fill="white" color="white" /> Play</div>
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
          <div className="play-overlay"><div className="play-btn-circle"><Play size={20} fill="white" color="white" /></div></div>
        </div>
      ) : (
        <div className="card-no-thumb"><Play size={32} /></div>
      )}
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        <div className="card-meta">
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}><Eye size={10} /> {b.view_count}</span>
          {b.model_name && <span className={`model-pill model-pill-${b.model_provider || 'default'}`}>{b.model_name}</span>}
          <span>{date}</span>
        </div>
      </div>
    </div>
  )
}
