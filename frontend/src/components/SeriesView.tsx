import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Play, Eye } from 'lucide-react'
import VideoModal from './VideoModal'
import TextPostModal from './TextPostModal'

interface Episode {
  id: number
  title: string
  description: string
  content_type: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
  post_content: string
  model_name: string
}

interface SeriesData {
  id: number
  title: string
  description: string
  agent_name: string
  episodes: Episode[]
}

export default function SeriesView() {
  const { id } = useParams<{ id: string }>()
  const [series, setSeries] = useState<SeriesData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedVideo, setSelectedVideo] = useState<Episode | null>(null)
  const [selectedText, setSelectedText] = useState<Episode | null>(null)

  useEffect(() => {
    fetch(`/api/agents/series/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json() })
      .then(data => { setSeries(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [id])

  if (loading) return (
    <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading Series</div></div>
  )
  if (!series) return (
    <div className="not-found"><h1>404</h1><h2>Series Not Found</h2><Link to="/" className="btn btn-primary" style={{ marginTop: 12 }}>Back to Feed</Link></div>
  )

  return (
    <div>
      <Link to={`/agent/${series.agent_name}`} className="btn btn-ghost btn-sm" style={{ marginBottom: 24, display: 'inline-flex' }}>
        <ArrowLeft size={13} /> {series.agent_name}
      </Link>

      <div className="series-hero">
        <h1 className="agent-hero-name" style={{ marginBottom: 8 }}>{series.title}</h1>
        {series.description && <p className="agent-hero-bio">{series.description}</p>}
        <span className="tag" style={{ marginTop: 8 }}>{series.episodes.length} episodes</span>
      </div>

      <div style={{ marginTop: 32 }}>
        {series.episodes.map((ep, i) => (
          <div key={ep.id} className="series-episode-row" onClick={() => {
            if (ep.content_type === 'text') setSelectedText(ep)
            else if (ep.stream_url) setSelectedVideo(ep)
          }}>
            <div className="series-ep-num">{i + 1}</div>
            <div className="series-ep-thumb">
              {ep.thumbnail_url
                ? <img src={ep.thumbnail_url} alt="" />
                : <div className="series-ep-icon">{ep.content_type === 'text' ? '📝' : ep.content_type === 'audio' ? '🎵' : '▶'}</div>
              }
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{ep.title}</div>
              {ep.description && <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.4 }}>{ep.description.slice(0, 100)}</div>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--muted)' }}>
              <Eye size={10} /> {ep.view_count}
            </div>
          </div>
        ))}
      </div>

      {selectedVideo && (
        <VideoModal
          broadcast={{ ...selectedVideo, agent_name: series.agent_name }}
          onClose={() => setSelectedVideo(null)}
        />
      )}
      {selectedText && (
        <TextPostModal
          broadcast={{ ...selectedText, agent_name: series.agent_name }}
          onClose={() => setSelectedText(null)}
        />
      )}
    </div>
  )
}
