import React, { useState, useEffect, useRef } from 'react'
import { Search, Loader, Check, Maximize2, Minimize2 } from 'lucide-react'
import { PosterRow, PosterItem, POSTER_ROW_CSS } from './PosterRow'

// "Stream" tab -- Netflix-style browse for movies/TV, backed by TMDB
// (search/trending/now_playing/upcoming/discover-by-genre) resolved to
// embed-provider players (vidlink.pro etc) on click. Separate from the
// "Live TV" tab (iptv-org channels) entirely -- this is on-demand
// movies/shows, that's linear broadcast TV.
//
// Layout: player is pinned in a fixed-height header region; the browse
// grid below scrolls independently in its own flex child (not
// position:sticky -- two stacked sticky elements with different top
// offsets was overlapping/z-fighting). Click the expand icon to enlarge
// the player without leaving the page.
//
// Auto-fallback note: these embed sites return HTTP 200 with real HTML
// even when the actual video fails to initialize (confirmed live: some
// run anti-automation JS that swaps in a fake page client-side, after
// load). Cross-origin iframes can't be inspected from parent JS (same-
// origin policy), so there is no reliable "did it actually play" signal
// available -- the auto-advance below is a timed rotation through the
// candidate list, not a real success/failure detector. It stops the
// moment the user confirms a provider is working, or manually picks one.
//
// Per-provider sandbox: live-tested (Playwright) which providers' click
// handlers fire a popup/popunder ad BEFORE any video appears -- confirmed
// vidlink.pro AND multiembed.mov both do this. Sandboxing (iframe
// `sandbox` attribute, no allow-popups/allow-top-navigation) fully blocks
// it. vidcore.org (the new default, see franken-stream's tmdb_embed.py)
// tolerates sandboxing fine -- real video, zero popups, sandboxed or not,
// confirmed with real force-clicks on the <video> element. vidlink.pro
// remains the sole exception: it detects the mere presence of a
// `sandbox` attribute (any token combination) and refuses to initialize
// its video at all, so it's deliberately left unsandboxed as a fallback,
// accepting its popup risk (mitigated by the beforeunload guard below
// for the top-navigation variant of that risk). The other 3 providers
// never render video either way, so sandboxing them costs nothing.
const SANDBOXED_EXCEPTIONS = new Set(['vidlink.pro'])
const SAFE_SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-presentation'

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
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState('')
  const autoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!document.getElementById(STYLE_ID)) {
      const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = POSTER_ROW_CSS
      document.head.appendChild(el)
    }
  }, [])

  // Best-effort top-navigation guard for the one unsandboxed provider
  // (vidlink.pro) -- see module docstring. Can't stop window.open popups
  // (those don't fire beforeunload), but does intercept a top-frame
  // navigation hijack via the browser's native "leave site?" prompt.
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
  const sandboxAttr = activeProvider && !SANDBOXED_EXCEPTIONS.has(activeProvider) ? SAFE_SANDBOX : undefined

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 220px)', minHeight: 400 }}>
      {/* Pinned header: search + player. Does not scroll with the browse grid below. */}
      <div style={{ flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: embedUrl ? 12 : 20 }}>
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
          <div style={{ marginBottom: 16, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}>
            <div style={{ position: 'relative' }}>
              <iframe
                key={embedUrl}
                src={embedUrl}
                allowFullScreen
                sandbox={sandboxAttr}
                style={{ width: '100%', height: expanded ? '70vh' : 260, border: 'none', background: '#000', display: 'block', transition: 'height 0.2s ease' }}
              />
              <button
                onClick={() => setExpanded(e => !e)}
                title={expanded ? 'Collapse player' : 'Expand player'}
                style={{ position: 'absolute', top: 8, right: 8, background: 'rgba(0,0,0,0.6)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 6, padding: 6, cursor: 'pointer', color: '#fff', display: 'flex' }}
              >
                {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
            </div>
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
      </div>

      {/* Independently scrolling browse region -- player above stays put. */}
      <div style={{ flex: 1, overflowY: 'auto', paddingRight: 4 }}>
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
    </div>
  )
}
