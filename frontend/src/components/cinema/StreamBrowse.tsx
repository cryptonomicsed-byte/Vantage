import React, { useState, useEffect } from 'react'
import { Search, Loader } from 'lucide-react'
import { PosterRow, PosterItem, POSTER_ROW_CSS } from './PosterRow'

// "Stream" tab -- Netflix-style browse for movies/TV, backed by TMDB
// (search/trending/now_playing/upcoming/discover-by-genre) resolved to
// embed-provider players (vidsrc.to etc) on click. Separate from the
// "Live TV" tab (iptv-org channels) entirely -- this is on-demand
// movies/shows, that's linear broadcast TV.

interface Genre { id: number; name: string }

const STYLE_ID = 'stream-browse-css'

export default function StreamBrowse() {
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<PosterItem[] | null>(null)
  const [searching, setSearching] = useState(false)

  const [trending, setTrending] = useState<PosterItem[]>([])
  const [newReleases, setNewReleases] = useState<PosterItem[]>([])
  const [upcoming, setUpcoming] = useState<PosterItem[]>([])
  const [genres, setGenres] = useState<Genre[]>([])
  const [genreRows, setGenreRows] = useState<Record<number, PosterItem[]>>({})
  const [loading, setLoading] = useState(true)

  const [resolving, setResolving] = useState<string | null>(null)
  const [embedUrl, setEmbedUrl] = useState<string | null>(null)
  const [nowPlaying, setNowPlaying] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!document.getElementById(STYLE_ID)) {
      const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = POSTER_ROW_CSS
      document.head.appendChild(el)
    }
  }, [])

  useEffect(() => {
    Promise.all([
      fetch('/api/cinema/livetv/trending').then(r => r.json()).catch(() => ({ results: [] })),
      fetch('/api/cinema/livetv/now-playing').then(r => r.json()).catch(() => ({ results: [] })),
      fetch('/api/cinema/livetv/upcoming').then(r => r.json()).catch(() => ({ results: [] })),
      fetch('/api/cinema/livetv/genres').then(r => r.json()).catch(() => ({ genres: [] })),
    ]).then(([t, n, u, g]) => {
      setTrending(t.results || [])
      setNewReleases(n.results || [])
      setUpcoming(u.results || [])
      setGenres((g.genres || []).slice(0, 6)) // cap rows so the page isn't endless
    }).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    genres.forEach(g => {
      if (genreRows[g.id]) return
      fetch(`/api/cinema/livetv/discover?genre_id=${g.id}`)
        .then(r => r.json())
        .then(d => setGenreRows(prev => ({ ...prev, [g.id]: d.results || [] })))
        .catch(() => {})
    })
  }, [genres])

  async function search() {
    if (!query.trim() || searching) return
    setSearching(true)
    setError('')
    try {
      const r = await fetch('/api/cinema/livetv/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
      })
      const data = await r.json()
      if (!r.ok) { setError(data.detail || 'Search failed'); setSearchResults([]); return }
      setSearchResults(data.results || [])
      if ((data.results || []).length === 0) setError('No results -- try another title.')
    } catch {
      setError('Network error reaching franken-stream.')
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  async function watch(item: PosterItem) {
    setResolving(item.url)
    setError('')
    setEmbedUrl(null)
    try {
      const r = await fetch('/api/cinema/livetv/embed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: item.url }),
      })
      const data = await r.json()
      if (!r.ok || !data.embed_url) { setError('Could not resolve a playable stream for this title -- try another.'); return }
      setEmbedUrl(data.embed_url)
      setNowPlaying(item.title)
    } catch {
      setError('Network error resolving stream.')
    } finally {
      setResolving(null)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        <input
          placeholder="Search movies and shows…"
          value={query}
          onChange={e => { setQuery(e.target.value); if (!e.target.value.trim()) setSearchResults(null) }}
          onKeyDown={e => e.key === 'Enter' && search()}
          style={{ flex: 1, padding: '10px 14px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--muted-hi)', fontSize: 14 }}
        />
        <button className="btn btn-primary" disabled={searching || !query.trim()} onClick={search}>
          {searching ? <Loader size={14} className="spin" /> : <Search size={14} />} Search
        </button>
      </div>

      {error && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>{error}</p>}

      {embedUrl && (
        <div style={{ marginBottom: 24, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
          <iframe src={embedUrl} allowFullScreen style={{ width: '100%', height: 500, border: 'none', background: '#000' }} />
          <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.7)', fontSize: 13 }}>
            Now playing: <strong style={{ color: 'var(--purple-bright)' }}>{nowPlaying}</strong>
          </div>
        </div>
      )}

      {searchResults !== null ? (
        <PosterRow title={`Search results for "${query}"`} items={searchResults} resolvingUrl={resolving} onSelect={watch} />
      ) : (
        <>
          {loading && <p style={{ fontSize: 13, color: 'var(--muted)' }}><Loader size={14} className="spin" /> Loading…</p>}
          {!loading && trending.length === 0 && (
            <p style={{ fontSize: 13, color: 'var(--muted)' }}>
              No data -- TMDB may be unreachable right now.
            </p>
          )}
          <PosterRow title="Trending This Week" items={trending} resolvingUrl={resolving} onSelect={watch} />
          <PosterRow title="New Releases" items={newReleases} resolvingUrl={resolving} onSelect={watch} />
          <PosterRow title="Coming Soon" items={upcoming} resolvingUrl={resolving} onSelect={watch} />
          {genres.map(g => (
            <PosterRow key={g.id} title={g.name} items={genreRows[g.id] || []} resolvingUrl={resolving} onSelect={watch} />
          ))}
        </>
      )}
    </div>
  )
}
