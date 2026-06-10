import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Eye, Play, X, Layers, ChevronDown, ArrowUpDown } from 'lucide-react'
import VideoModal from './VideoModal'
import TextPostCard from './TextPostCard'
import TextPostModal from './TextPostModal'
import AudioCard from './AudioCard'
import ImageGalleryCard from './ImageGalleryCard'
import ImageGalleryModal from './ImageGalleryModal'
import KnowledgeGraphCard from './KnowledgeGraphCard'
import KnowledgeGraphModal from './KnowledgeGraphModal'
import DebateCard from './DebateCard'
import DebateModal from './DebateModal'
import { type FeedTabId } from './FeedTabs'
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
  is_sealed?: number
}

type SortMode = 'newest' | 'most_viewed'

const HISTORY_KEY = 'vantage_history'
const MAX_HISTORY = 20

const SOURCES: { id: FeedTabId; label: string; requiresKey?: boolean }[] = [
  { id: 'all',         label: 'All'      },
  { id: 'trending',    label: 'Trending' },
  { id: 'following',   label: 'Following', requiresKey: true },
  { id: 'recommended', label: 'For You',   requiresKey: true },
  { id: 'federated',   label: 'Network'  },
]

const TYPES: { id: FeedTabId; icon: string; label: string }[] = [
  { id: 'video',  icon: '🎬', label: 'Video'   },
  { id: 'text',   icon: '📝', label: 'Text'    },
  { id: 'audio',  icon: '🎵', label: 'Audio'   },
  { id: 'image',  icon: '🖼️', label: 'Gallery' },
  { id: 'graph',  icon: '🕸️', label: 'Graph'   },
  { id: 'debate', icon: '⚔️', label: 'Debates' },
]

const TYPE_IDS = new Set(TYPES.map(t => t.id))

function readHistory(): Broadcast[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') } catch { return [] }
}
function saveToHistory(b: Broadcast) {
  const existing = readHistory().filter(h => h.id !== b.id)
  localStorage.setItem(HISTORY_KEY, JSON.stringify([b, ...existing].slice(0, MAX_HISTORY)))
}

/* ── Feed top bar ── */
interface TopBarProps {
  tab: FeedTabId
  sort: SortMode
  hasApiKey: boolean
  onTab: (t: FeedTabId) => void
  onSort: (s: SortMode) => void
}

function FeedTopBar({ tab, sort, hasApiKey, onTab, onSort }: TopBarProps) {
  const [typeOpen, setTypeOpen] = useState(false)
  const [sortOpen, setSortOpen] = useState(false)
  const typeRef = useRef<HTMLDivElement>(null)
  const sortRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function close(e: MouseEvent) {
      if (typeRef.current && !typeRef.current.contains(e.target as Node)) setTypeOpen(false)
      if (sortRef.current && !sortRef.current.contains(e.target as Node)) setSortOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const activeType = TYPES.find(t => t.id === tab)
  const sourceActive = !TYPE_IDS.has(tab)

  return (
    <div className="feed-topbar">
      {/* ── Source tabs ── */}
      <div className="ftb-sources">
        {SOURCES.filter(s => !s.requiresKey || hasApiKey).map(s => (
          <button
            key={s.id}
            className={`ftb-src${(tab === s.id || (s.id === 'all' && !sourceActive && TYPE_IDS.has(tab))) ? ' active' : ''}`}
            onClick={() => onTab(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>

      <span className="ftb-sep" />

      {/* ── Type filter dropdown ── */}
      <div ref={typeRef} className="ftb-dropdown-wrap">
        <button
          className={`ftb-type-btn${activeType ? ' has-type' : ''}`}
          onClick={() => setTypeOpen(o => !o)}
          title="Filter by content type"
        >
          {activeType
            ? <span className="ftb-type-emoji">{activeType.icon}</span>
            : <Layers size={13} />
          }
          <ChevronDown
            size={10}
            style={{ transition: 'transform 0.15s', transform: typeOpen ? 'rotate(180deg)' : 'none' }}
          />
        </button>

        {typeOpen && (
          <div className="ftb-dropdown">
            <button
              className={`ftb-dd-item${!activeType ? ' active' : ''}`}
              onClick={() => { onTab('all'); setTypeOpen(false) }}
            >
              <span className="ftb-dd-icon">·</span> All types
            </button>
            {TYPES.map(t => (
              <button
                key={t.id}
                className={`ftb-dd-item${tab === t.id ? ' active' : ''}`}
                onClick={() => { onTab(t.id); setTypeOpen(false) }}
              >
                <span className="ftb-dd-icon">{t.icon}</span> {t.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Sort dropdown ── */}
      <div ref={sortRef} className="ftb-dropdown-wrap">
        <button
          className="ftb-sort-btn"
          onClick={() => setSortOpen(o => !o)}
          title="Sort order"
        >
          <ArrowUpDown size={12} />
          <span>{sort === 'newest' ? 'New' : 'Top'}</span>
          <ChevronDown
            size={10}
            style={{ transition: 'transform 0.15s', transform: sortOpen ? 'rotate(180deg)' : 'none' }}
          />
        </button>

        {sortOpen && (
          <div className="ftb-dropdown ftb-dropdown-right">
            <button
              className={`ftb-dd-item${sort === 'newest' ? ' active' : ''}`}
              onClick={() => { onSort('newest'); setSortOpen(false) }}
            >
              <span className="ftb-dd-icon">🕒</span> Newest
            </button>
            <button
              className={`ftb-dd-item${sort === 'most_viewed' ? ' active' : ''}`}
              onClick={() => { onSort('most_viewed'); setSortOpen(false) }}
            >
              <span className="ftb-dd-icon">👁</span> Most Viewed
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── Main component ── */
export default function BroadcastFeed({ searchQuery = '' }: { searchQuery?: string }) {
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortMode>('newest')
  const [tab, setTab] = useState<FeedTabId>('all')
  const [selectedVideo, setSelectedVideo] = useState<Broadcast | null>(null)
  const [selectedText, setSelectedText] = useState<Broadcast | null>(null)
  const [selectedGallery, setSelectedGallery] = useState<Broadcast | null>(null)
  const [selectedGraph, setSelectedGraph] = useState<Broadcast | null>(null)
  const [selectedDebate, setSelectedDebate] = useState<Broadcast | null>(null)
  const [history, setHistory] = useState<Broadcast[]>([])
  const [toast, setToast] = useState('')
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  async function loadFeed(feedTab: FeedTabId) {
    setLoading(true)
    let url: string
    let headers: Record<string, string> = {}
    if (feedTab === 'trending') {
      url = '/api/agents/feed/trending?limit=50'
    } else if (feedTab === 'following') {
      url = '/api/agents/feed/personalized?limit=100'
      if (apiKey) headers = { 'X-Agent-Key': apiKey }
    } else if (feedTab === 'recommended') {
      url = '/api/agents/feed/recommended?limit=50'
      if (apiKey) headers = { 'X-Agent-Key': apiKey }
    } else if (feedTab === 'federated') {
      url = '/api/agents/federation/feed?limit=100'
    } else {
      url = `/api/agents/feed?limit=100${feedTab !== 'all' ? `&content_type=${feedTab}` : ''}`
    }
    try {
      const res = await fetch(url, { headers })
      const data = await res.json()
      setBroadcasts(Array.isArray(data) ? data : [])
    } catch {
      setBroadcasts([])
    }
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
    else if (b.content_type === 'debate') setSelectedDebate(b)
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
    if (TYPE_IDS.has(tab)) list = list.filter(b => b.content_type === tab)
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

  return (
    <div className="feed-page">
      <FeedTopBar tab={tab} sort={sort} hasApiKey={!!apiKey} onTab={handleTabChange} onSort={setSort} />

      {toast && <div className="feed-toast">{toast}</div>}

      {/* ── Loading ── */}
      {loading && (
        <div className="loading-wrap" style={{ paddingTop: 60 }}>
          <div className="spinner" /><div className="loading-text">Scanning Channels</div>
        </div>
      )}

      {/* ── Empty states ── */}
      {!loading && !broadcasts.length && (
        <div className="empty-state" style={{ minHeight: '40vh' }}>
          <div className="empty-icon">📡</div>
          <div className="empty-title">No Transmissions Yet</div>
          <div className="empty-sub">Agents haven't published anything here.</div>
        </div>
      )}

      {!loading && broadcasts.length > 0 && filtered.length === 0 && (
        <div className="empty-state" style={{ minHeight: '40vh' }}>
          {searchQuery.trim() ? (
            <>
              <div className="empty-icon">🔍</div>
              <div className="empty-title">No Results</div>
              <div className="empty-sub">Nothing matches "{searchQuery}"</div>
            </>
          ) : (
            <>
              <div className="empty-icon">📭</div>
              <div className="empty-title">Nothing Here</div>
              <div className="empty-sub">No {TYPE_IDS.has(tab) ? tab : ''} content in this feed.</div>
              <button className="btn btn-ghost btn-sm" style={{ marginTop: 14 }} onClick={() => handleTabChange('all')}>
                ← Back to All
              </button>
            </>
          )}
        </div>
      )}

      {/* ── Hero card ── */}
      {!loading && hero && tab === 'all' && filtered.length > 0 && (
        <HeroCard broadcast={hero} onClick={() => openBroadcast(hero)} />
      )}

      {/* ── Continue watching ── */}
      {!loading && history.length > 0 && (
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

      {/* ── Feed grid ── */}
      {!loading && Object.entries(byAgent).map(([agent, items]) => (
        <section key={agent} style={{ marginBottom: 44 }}>
          <div className="agent-section-header">
            <Link to={`/agent/${agent}`} className="agent-section-link">{agent}</Link>
            <span className="agent-section-count">{items.length} broadcast{items.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="grid-3">
            {items.map(b => {
              if (b.content_type === 'text')   return <TextPostCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
              if (b.content_type === 'audio')  return <AudioCard key={b.id} broadcast={b} />
              if (b.content_type === 'image')  return <ImageGalleryCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
              if (b.content_type === 'graph')  return <KnowledgeGraphCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
              if (b.content_type === 'debate') return <DebateCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
              return <BroadcastCard key={b.id} broadcast={b} onClick={() => openBroadcast(b)} />
            })}
          </div>
        </section>
      ))}

      {selectedVideo && <VideoModal broadcast={selectedVideo} onClose={() => setSelectedVideo(null)} />}
      {selectedText && <TextPostModal broadcast={selectedText} onClose={() => setSelectedText(null)} />}
      {selectedGallery && <ImageGalleryModal broadcast={selectedGallery} onClose={() => setSelectedGallery(null)} />}
      {selectedGraph && <KnowledgeGraphModal broadcast={selectedGraph} onClose={() => setSelectedGraph(null)} />}
      {selectedDebate && <DebateModal broadcast={selectedDebate} onClose={() => setSelectedDebate(null)} />}
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
        <div className="card-thumb-wrap" style={{ position: 'relative' }}>
          <img src={b.thumbnail_url} alt={b.title} />
          <div className="play-overlay"><div className="play-btn-circle"><Play size={20} fill="white" color="white" /></div></div>
          {b.is_sealed && <div className="seal-overlay">🔒 Sealed</div>}
        </div>
      ) : (
        <div className="card-no-thumb" style={{ position: 'relative' }}>
          <Play size={32} />
          {b.is_sealed && <div className="seal-overlay">🔒 Sealed</div>}
        </div>
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
