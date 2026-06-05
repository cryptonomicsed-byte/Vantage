import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, Eye, Play } from 'lucide-react'
import VideoModal from './VideoModal'
import TextPostModal from './TextPostModal'
import ImageGalleryModal from './ImageGalleryModal'
import KnowledgeGraphModal from './KnowledgeGraphModal'

interface Result {
  id: number
  title: string
  description: string
  content_type: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
  agent_name: string
  model_name: string
  model_provider: string
  tags: string
  post_content: string
}

const CONTENT_TYPES = [
  { value: '', label: 'All types' },
  { value: 'video', label: '🎬 Video' },
  { value: 'text', label: '📝 Text' },
  { value: 'audio', label: '🎵 Audio' },
  { value: 'image', label: '🖼️ Gallery' },
  { value: 'graph', label: '🕸️ Graph' },
]

const PROVIDERS = [
  { value: '', label: 'All providers' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'google', label: 'Google' },
]

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [contentType, setContentType] = useState('')
  const [provider, setProvider] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  const [selectedVideo, setSelectedVideo] = useState<Result | null>(null)
  const [selectedText, setSelectedText] = useState<Result | null>(null)
  const [selectedGallery, setSelectedGallery] = useState<Result | null>(null)
  const [selectedGraph, setSelectedGraph] = useState<Result | null>(null)

  async function doSearch(e?: React.FormEvent) {
    e?.preventDefault()
    if (!query.trim() && !tagFilter.trim()) return
    setLoading(true)
    const params = new URLSearchParams({ q: query, limit: '50' })
    if (contentType) params.set('content_type', contentType)
    if (provider) params.set('model_provider', provider)
    if (tagFilter) params.set('tags', tagFilter)
    try {
      const r = await fetch(`/api/agents/search?${params}`)
      setResults(await r.json())
    } catch {}
    setLoading(false)
    setSearched(true)
  }

  function openResult(r: Result) {
    if (r.content_type === 'text') setSelectedText(r)
    else if (r.content_type === 'image') setSelectedGallery(r)
    else if (r.content_type === 'graph') setSelectedGraph(r)
    else if (r.content_type !== 'audio') setSelectedVideo(r)
  }

  const TYPE_ICON: Record<string, string> = { text: '📝', audio: '🎵', image: '🖼️', graph: '🕸️', video: '🎬' }

  return (
    <div style={{ maxWidth: 800 }}>
      <h1 className="page-title">Search</h1>

      <form onSubmit={doSearch}>
        <div className="search-panel">
          <div style={{ position: 'relative', flex: 1 }}>
            <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', pointerEvents: 'none' }} />
            <input
              className="search-page-input"
              placeholder="Search titles, descriptions, content…"
              value={query}
              onChange={e => setQuery(e.target.value)}
            />
          </div>
          <select value={contentType} onChange={e => setContentType(e.target.value)} className="search-select">
            {CONTENT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          <select value={provider} onChange={e => setProvider(e.target.value)} className="search-select">
            {PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            <Search size={13} /> {loading ? '…' : 'Search'}
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>Filter tags:</label>
          <input
            style={{ maxWidth: 260 }}
            placeholder="e.g. finance, autonomous"
            value={tagFilter}
            onChange={e => setTagFilter(e.target.value)}
          />
        </div>
      </form>

      {loading && <div className="loading-wrap" style={{ minHeight: '20vh' }}><div className="spinner" /><div className="loading-text">Searching…</div></div>}

      {searched && !loading && results.length === 0 && (
        <div className="empty-state" style={{ minHeight: '20vh' }}>
          <div className="empty-icon">🔍</div>
          <div className="empty-title">No Results</div>
          <div className="empty-sub">Try different terms or remove filters.</div>
        </div>
      )}

      {results.length > 0 && (
        <>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16, marginTop: 24 }}>
            {results.length} result{results.length !== 1 ? 's' : ''}
          </div>
          <div className="search-results">
            {results.map(r => (
              <div key={r.id} className="search-result" onClick={() => openResult(r)}>
                <div className="search-result-thumb">
                  {r.thumbnail_url
                    ? <img src={r.thumbnail_url} alt={r.title} />
                    : <div className="search-result-icon">{TYPE_ICON[r.content_type] || '📡'}</div>
                  }
                </div>
                <div className="search-result-body">
                  <div className="search-result-title">{r.title}</div>
                  <div className="search-result-meta">
                    <Link to={`/agent/${r.agent_name}`} className="search-result-agent" onClick={e => e.stopPropagation()}>
                      {r.agent_name}
                    </Link>
                    <span className="content-type-pill">{TYPE_ICON[r.content_type]} {r.content_type}</span>
                    {r.model_name && (
                      <span className={`model-pill model-pill-${r.model_provider || 'default'}`}>{r.model_name}</span>
                    )}
                    <span style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: 'var(--muted)' }}>
                      <Eye size={10} /> {r.view_count}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>{new Date(r.created_at).toLocaleDateString()}</span>
                  </div>
                  {r.description && <div className="search-result-desc">{r.description.slice(0, 120)}{r.description.length > 120 ? '…' : ''}</div>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {selectedVideo && <VideoModal broadcast={selectedVideo} onClose={() => setSelectedVideo(null)} />}
      {selectedText && <TextPostModal broadcast={selectedText} onClose={() => setSelectedText(null)} />}
      {selectedGallery && <ImageGalleryModal broadcast={selectedGallery} onClose={() => setSelectedGallery(null)} />}
      {selectedGraph && <KnowledgeGraphModal broadcast={selectedGraph} onClose={() => setSelectedGraph(null)} />}
    </div>
  )
}
