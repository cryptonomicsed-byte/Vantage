import React, { useState } from 'react'
import { Search, Play, Loader } from 'lucide-react'

// "Watch Live TV" -- the actual franken-stream experience natively inside
// Cinema: search across streaming-mirror providers by title, resolve a
// playable embed, watch it in Vantage. Proxied through
// backend/routers/frankenstream_proxy.py -> franken-stream's own web API
// (ares-frankenstream.service).
//
// Disclosed, not hidden: sources are unlicensed streaming-mirror sites --
// some results will be dead links, that's inherent to this class of
// source and not something this UI can paper over.

interface SearchResult {
  title: string
  url: string
}

export default function LiveTV() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [resolving, setResolving] = useState<string | null>(null)
  const [embedUrl, setEmbedUrl] = useState<string | null>(null)
  const [nowPlaying, setNowPlaying] = useState<string | null>(null)
  const [error, setError] = useState('')

  async function search() {
    if (!query.trim() || searching) return
    setSearching(true)
    setError('')
    setResults([])
    try {
      const r = await fetch('/api/cinema/livetv/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
      })
      const data = await r.json()
      if (!r.ok) { setError(data.detail || 'Search failed'); return }
      setResults(data.results || [])
      if ((data.results || []).length === 0) setError('No results -- providers may be down right now, try another title.')
    } catch {
      setError('Network error reaching franken-stream.')
    } finally {
      setSearching(false)
    }
  }

  async function watch(result: SearchResult) {
    setResolving(result.url)
    setError('')
    setEmbedUrl(null)
    try {
      const r = await fetch('/api/cinema/livetv/embed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: result.url }),
      })
      const data = await r.json()
      if (!r.ok || !data.embed_url) { setError('Could not resolve a playable stream from this result -- try another.'); return }
      setEmbedUrl(data.embed_url)
      setNowPlaying(result.title)
    } catch {
      setError('Network error resolving stream.')
    } finally {
      setResolving(null)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          placeholder="Search for a movie or show…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && search()}
          style={{ flex: 1, padding: '10px 14px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--muted-hi)', fontSize: 14 }}
        />
        <button className="btn btn-primary" disabled={searching || !query.trim()} onClick={search}>
          {searching ? <Loader size={14} className="spin" /> : <Search size={14} />} Search
        </button>
      </div>

      {error && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>{error}</p>}

      {embedUrl && (
        <div style={{ marginBottom: 20, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
          <iframe
            src={embedUrl}
            allowFullScreen
            style={{ width: '100%', height: 500, border: 'none', background: '#000' }}
          />
          <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.7)', fontSize: 13 }}>
            Now playing: <strong style={{ color: 'var(--purple-bright)' }}>{nowPlaying}</strong>
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {results.map((r, i) => (
            <div key={i} className="glass" style={{ padding: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ fontSize: 13 }}>{r.title}</span>
              <button className="btn btn-ghost btn-sm" disabled={resolving === r.url} onClick={() => watch(r)}>
                {resolving === r.url ? <Loader size={12} className="spin" /> : <Play size={12} />} Watch
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
