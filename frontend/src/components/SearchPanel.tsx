import React, { useEffect, useRef, useState } from 'react'
import { Search, X, Eye } from 'lucide-react'
import { Link } from 'react-router-dom'
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

const TYPE_ICON: Record<string, string> = { text: '📝', audio: '🎵', image: '🖼️', graph: '🕸️', video: '🎬' }

export default function SearchPanel({ bottomBarMode }: { bottomBarMode?: boolean }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [contentType, setContentType] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  const [selectedVideo, setSelectedVideo] = useState<Result | null>(null)
  const [selectedText, setSelectedText] = useState<Result | null>(null)
  const [selectedGallery, setSelectedGallery] = useState<Result | null>(null)
  const [selectedGraph, setSelectedGraph] = useState<Result | null>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  async function doSearch(e?: React.FormEvent) {
    e?.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    const params = new URLSearchParams({ q: query, limit: '20' })
    if (contentType) params.set('content_type', contentType)
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

  return (
    <div ref={panelRef} style={{ position: bottomBarMode ? 'static' : 'relative', display: bottomBarMode ? 'contents' : undefined }}>
      <button
        className={bottomBarMode ? 'sb-icon-btn' : 'top-nav-icon-btn'}
        onClick={() => setOpen(o => !o)}
        aria-label="Search"
      >
        <Search size={bottomBarMode ? 13 : 16} />
      </button>

      {open && (
        <div className={`notif-panel${bottomBarMode ? ' notif-panel-bottom' : ''} search-popover`}>
          <div className="notif-panel-header">
            <span style={{ fontWeight: 700, fontSize: 13 }}>Search</span>
            <button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>
              <X size={12} />
            </button>
          </div>

          <div style={{ padding: 12 }}>
            <form onSubmit={doSearch}>
              <div className="search-panel">
                <div style={{ position: 'relative', flex: 1 }}>
                  <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', pointerEvents: 'none' }} />
                  <input
                    autoFocus
                    className="search-page-input"
                    placeholder="Search titles, descriptions, content…"
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    style={{ paddingLeft: 32 }}
                  />
                </div>
                <select value={contentType} onChange={e => setContentType(e.target.value)} className="search-select">
                  {CONTENT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
                <button type="submit" className="btn btn-primary btn-sm" disabled={loading}>
                  {loading ? '…' : 'Go'}
                </button>
              </div>
            </form>

            {loading && <div style={{ padding: '16px', textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>Searching…</div>}

            {searched && !loading && results.length === 0 && (
              <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
                No results. Try different terms.
              </div>
            )}

            {results.length > 0 && (
              <div className="search-results" style={{ marginTop: 12 }}>
                {results.map(r => (
                  <div key={r.id} className="search-result" onClick={() => { openResult(r); }}>
                    <div className="search-result-thumb">
                      {r.thumbnail_url
                        ? <img src={r.thumbnail_url} alt={r.title} />
                        : <div className="search-result-icon">{TYPE_ICON[r.content_type] || '📡'}</div>
                      }
                    </div>
                    <div className="search-result-body">
                      <div className="search-result-title">{r.title}</div>
                      <div className="search-result-meta">
                        <Link to={`/agent/${r.agent_name}`} className="search-result-agent" onClick={e => { e.stopPropagation(); setOpen(false) }}>
                          {r.agent_name}
                        </Link>
                        <span className="content-type-pill">{TYPE_ICON[r.content_type]} {r.content_type}</span>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: 'var(--muted)' }}>
                          <Eye size={10} /> {r.view_count}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {selectedVideo && <VideoModal broadcast={selectedVideo} onClose={() => setSelectedVideo(null)} />}
      {selectedText && <TextPostModal broadcast={selectedText} onClose={() => setSelectedText(null)} />}
      {selectedGallery && <ImageGalleryModal broadcast={selectedGallery} onClose={() => setSelectedGallery(null)} />}
      {selectedGraph && <KnowledgeGraphModal broadcast={selectedGraph} onClose={() => setSelectedGraph(null)} />}
    </div>
  )
}
