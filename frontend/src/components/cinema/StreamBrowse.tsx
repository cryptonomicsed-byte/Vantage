import React, { useState, useEffect, useRef } from 'react'
import { Search, Loader, Check } from 'lucide-react'
import { PosterRow, PosterItem, POSTER_ROW_CSS } from './PosterRow'

// "Stream" tab -- Netflix-style browse for movies/TV, backed by TMDB
// (search/trending/now_playing/upcoming/discover-by-genre) resolved to
// embed-provider players (vidlink.pro etc) on click. Separate from the
// "Live TV" tab (iptv-org channels) entirely -- this is on-demand
// movies/shows, that's linear broadcast TV.
//
// Auto-fallback note: these embed sites return HTTP 200 with real HTML
// even when the actual video fails to initialize (confirmed live: some
// run anti-automation JS that swaps in a fake page client-side, after
// load). Cross-origin iframes can't be inspected from parent JS (same-
// origin policy), so there is no reliable "did it actually play" signal
// available -- the auto-advance below is a timed rotation through the
// candidate list, not a real success/failure detector. It stops the
// moment the user confirms a provider is working, or manually picks one.

const AUTO_ADVANCE_MS = 9000

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
  const [alternates, setAlternates] = useState<{ provider: string; url: string }[]>([])
  const [providerIndex, setProviderIndex] = useState(0)
  const [autoAdvancing, setAutoAdvancing] = useState(false)
  const [nowPlaying, setNowPlaying] = useState<string | null>(null)
  const [error, setError] = useState('')
  const autoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!document.getElementById(STYLE_ID)) {
      const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = POSTER_ROW_CSS
      document.head.appendChild(el)
    }
  }, [])

  // Best-effort top-navigation guard: these embed providers can inject a
  // top-level redirect (window.top.location = ...) as an ad-monetization
  // hijack. The standard browser defense is the iframe `sandbox` attribute
  // (without allow-top-navigation/allow-popups) -- tested live and found
  // it breaks the one provider (vidlink.pro) that actually plays video at
  // all: EVERY sandbox token combination, including a maximally permissive
  // one, drops the <video> element entirely (the player detects sandboxing
  // and refuses to initialize). So sandboxing isn't usable here without
  // regressing playback. This beforeunload listener is a partial fallback:
  // it can't stop a window.open() popup (that never fires beforeunload),
  // but it does intercept an actual top-frame navigation attempt and lets
  // the user cancel it via the browser's native "leave site?" prompt,
  // active only while a player is on screen.
  useEffect(() => {
    if (!embedUrl) return
    const guard = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [embedUrl])

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

  // Auto-advance: while enabled, silently rotate to the next candidate
  // provider on a timer. Clears itself once the user locks in a provider
  // or manually switches (both set autoAdvancing=false).
  useEffect(() => {
    if (autoTimer.current) clearTimeout(autoTimer.current)
    if (!autoAdvancing || alternates.length === 0) return
    autoTimer.current = setTimeout(() => {
      setProviderIndex(i => {
        const next = i + 1
        if (next >= alternates.length) {
          setAutoAdvancing(false) // exhausted the list, stop silently
          return i
        }
        setEmbedUrl(alternates[next].url)
        return next
      })
    }, AUTO_ADVANCE_MS)
    return () => { if (autoTimer.current) clearTimeout(autoTimer.current) }
  }, [autoAdvancing, providerIndex, alternates])

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
    setAlternates([])
    setProviderIndex(0)
    setAutoAdvancing(false)
    try {
      const r = await fetch('/api/cinema/livetv/embed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: item.url }),
      })
      const data = await r.json()
      if (!r.ok || !data.embed_url) { setError('Could not resolve a playable stream for this title -- try another.'); return }
      setEmbedUrl(data.embed_url)
      setAlternates(data.alternates || [])
      setProviderIndex(0)
      setNowPlaying(item.title)
      // Start auto-rotating in the background if there's more than one
      // candidate -- see the module docstring for why this is a timed
      // rotation, not a real failure detector.
      setAutoAdvancing((data.alternates || []).length > 1)
    } catch {
      setError('Network error resolving stream.')
    } finally {
      setResolving(null)
    }
  }

  function switchProvider(index: number) {
    setAutoAdvancing(false)
    setProviderIndex(index)
    setEmbedUrl(alternates[index].url)
  }

  function keepThisOne() {
    setAutoAdvancing(false)
  }

  const activeProvider = alternates[providerIndex]?.provider

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
        <div style={{ marginBottom: 24, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)', position: 'sticky', top: 16, zIndex: 20, background: 'var(--bg, #0a0a12)', boxShadow: '0 8px 24px rgba(0,0,0,0.5)' }}>
          <iframe key={embedUrl} src={embedUrl} allowFullScreen style={{ width: '100%', height: 500, border: 'none', background: '#000' }} />
          <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.85)', fontSize: 13 }}>
            Now playing: <strong style={{ color: 'var(--purple-bright)' }}>{nowPlaying}</strong>
            {alternates.length > 1 && (
              <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                {autoAdvancing ? (
                  <>
                    <Loader size={11} className="spin" />
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                      Trying {activeProvider}… auto-switching if this doesn't play
                    </span>
                    <button className="btn btn-primary btn-sm" onClick={keepThisOne} style={{ fontSize: 11 }}>
                      <Check size={11} /> This works, keep it
                    </button>
                  </>
                ) : (
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>Not working? Try:</span>
                )}
                {alternates.map((alt, i) => (
                  <button
                    key={alt.provider}
                    className="btn btn-ghost btn-sm"
                    disabled={i === providerIndex && !autoAdvancing}
                    onClick={() => switchProvider(i)}
                    style={{ fontSize: 11, opacity: i === providerIndex ? (autoAdvancing ? 1 : 0.5) : 1 }}
                  >
                    {alt.provider}
                  </button>
                ))}
              </div>
            )}
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
