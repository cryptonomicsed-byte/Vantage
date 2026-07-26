import React, { useState, useRef, useEffect } from 'react'
import { Search, Loader, Play, Pause, Radio, Mic, Music2, Youtube, Disc3, AlertTriangle, Users } from 'lucide-react'

// Audio "Stream" tab -- distinct from the Library tab's agent-produced
// tracks. Legal-first sources first:
//   Music: iTunes Search API for metadata/search/artwork (real 600x600
//     covers), grouped into Songs/Albums/Artists sections, plus a
//     no-search browse view (Trending + genre rows via Apple's classic
//     RSS charts API). PLAYBACK is "smart": clicking anything first
//     tries a real full-length match (musify.club, then Jamendo) via
//     /resolve-full-track, and only falls back to iTunes' 30s preview
//     if no full match exists -- posters/metadata come from iTunes (the
//     best source for that), but playing something no longer means
//     settling for the demo when a real full track is available.
//   Podcasts: same iTunes Search endpoint (media=podcast) for discovery,
//     then a real public RSS feed parsed directly for full-length
//     episode audio -- fully legal, podcasts are open RSS by design.
//   SoundCloud: paste a track URL, resolved via SoundCloud's real oEmbed
//     (no key-free full-catalog search exists yet).
//   YouTube: official Data API search + standard youtube.com/embed
//     iframe -- needs YOUTUBE_API_KEY configured server-side; shows a
//     clear "not configured" state otherwise, same as TMDB's pattern.
// Plus, owner-authorized after being told the above exist:
//   Full Tracks: musify.club, an unlicensed commercial-music mirror --
//     DISCLOSED, NOT HIDDEN, meaningfully higher legal risk than the
//     movie-embed providers (labels pursue takedowns far more
//     aggressively). Kept visually distinct (warning banner). The main
//     Music tab's smart-play already uses this as its first full-track
//     attempt; this tab remains for direct musify-only search/browse.

type Tab = 'music' | 'podcasts' | 'soundcloud' | 'youtube' | 'fulltracks'

interface MusicItem {
  kind: string; title: string; artist: string; collection: string; collection_id: number | null
  artwork: string | null; preview_url: string | null; track_count: number | null; genre: string | null
  view_url?: string | null; artist_id?: number | null
}
interface ArtistItem { title: string; artist_id: number | null; view_url: string | null }
interface MixtapeItem { identifier: string; title: string; creator: string; downloads: number }
interface MixtapeTrack { title: string; url: string; duration: number | null }
interface AlbumTrack {
  title: string; track_number: number | null; preview_url: string | null; duration_ms: number | null
}
interface ChartItem { title: string; artist: string; collection: string; artwork: string | null }
interface Genre { id: number; name: string }
interface PodcastItem {
  kind: string; title: string; artist: string; artwork: string | null; feed_url: string | null
}
interface Episode {
  podcast: string; title: string; audio_url: string; pub_date: string; duration: string
}
interface YoutubeItem {
  video_id: string; title: string; channel: string; thumbnail: string; embed_url: string
}
interface FullTrackItem {
  title: string; track_url: string
}

function fmtMs(ms: number | null): string {
  if (!ms) return ''
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function NowPlayingBadge({ source }: { source: string | null }) {
  if (!source) return null
  return (
    <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: source === '30s preview' ? 'rgba(255,255,255,0.1)' : 'rgba(80,220,140,0.15)', color: source === '30s preview' ? 'var(--muted)' : '#50dc8c', marginLeft: 6 }}>
      {source}
    </span>
  )
}

export default function AudioStreamBrowse() {
  const [tab, setTab] = useState<Tab>('music')
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')

  // Music: grouped search + browse rows
  const [songs, setSongs] = useState<MusicItem[]>([])
  const [albums, setAlbums] = useState<MusicItem[]>([])
  const [artists, setArtists] = useState<ArtistItem[]>([])
  const [hasSearched, setHasSearched] = useState(false)
  const [viewingArtist, setViewingArtist] = useState<ArtistItem | null>(null)
  const [artistAlbums, setArtistAlbums] = useState<MusicItem[]>([])
  const [artistAlbumsLoading, setArtistAlbumsLoading] = useState(false)
  const [mixtapes, setMixtapes] = useState<MixtapeItem[]>([])
  const [mixtapesLoading, setMixtapesLoading] = useState(false)
  const [openMixtapeId, setOpenMixtapeId] = useState<string | null>(null)
  const [mixtapeTracks, setMixtapeTracks] = useState<MixtapeTrack[]>([])
  const [mixtapeTracksLoading, setMixtapeTracksLoading] = useState(false)
  const [trending, setTrending] = useState<ChartItem[]>([])
  const [genres, setGenres] = useState<Genre[]>([])
  const [genreRows, setGenreRows] = useState<Record<number, ChartItem[]>>({})
  const [browseLoading, setBrowseLoading] = useState(true)
  const [openCollectionId, setOpenCollectionId] = useState<number | null>(null)
  const [albumTracks, setAlbumTracks] = useState<AlbumTrack[]>([])
  const [albumTracksLoading, setAlbumTracksLoading] = useState(false)

  const [podcastResults, setPodcastResults] = useState<PodcastItem[]>([])
  const [youtubeResults, setYoutubeResults] = useState<YoutubeItem[]>([])
  const [youtubeConfigured, setYoutubeConfigured] = useState(true)
  const [fullTrackResults, setFullTrackResults] = useState<FullTrackItem[]>([])
  const [resolvingTrack, setResolvingTrack] = useState<string | null>(null)

  const [playingUrl, setPlayingUrl] = useState<string | null>(null)
  const [playingSource, setPlayingSource] = useState<string | null>(null)
  const [resolvingKey, setResolvingKey] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const [openPodcast, setOpenPodcast] = useState<PodcastItem | null>(null)
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [episodesLoading, setEpisodesLoading] = useState(false)

  const [scUrl, setScUrl] = useState('')
  const [scResult, setScResult] = useState<{ title: string; author: string; thumbnail: string; embed_html: string } | null>(null)
  const [scLoading, setScLoading] = useState(false)
  const [scError, setScError] = useState('')

  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !playingUrl) return
    audio.src = playingUrl
    audio.play().catch(() => {})
  }, [playingUrl])

  // No-search browse view: Trending + genre rows, loaded once.
  useEffect(() => {
    Promise.all([
      fetch('/api/audio/stream/charts?limit=12').then(r => r.json()).catch(() => ({ results: [] })),
      fetch('/api/audio/stream/genres').then(r => r.json()).catch(() => ({ genres: [] })),
    ]).then(([t, g]) => {
      setTrending(t.results || [])
      setGenres((g.genres || []).slice(0, 5))
    }).finally(() => setBrowseLoading(false))
  }, [])

  useEffect(() => {
    genres.forEach(g => {
      if (genreRows[g.id]) return
      fetch(`/api/audio/stream/charts?genre_id=${g.id}&limit=12`)
        .then(r => r.json())
        .then(d => setGenreRows(prev => ({ ...prev, [g.id]: d.results || [] })))
        .catch(() => {})
    })
  }, [genres])

  // Smart play: try a real full-length match first, fall back to a 30s
  // preview if given one. Shows which source actually played.
  async function playSmart(key: string, title: string, artist: string, fallbackPreviewUrl: string | null) {
    if (resolvingKey === key) return
    setResolvingKey(key)
    setError('')
    try {
      const r = await fetch(`/api/audio/stream/resolve-full-track?title=${encodeURIComponent(title)}&artist=${encodeURIComponent(artist)}`)
      if (r.ok) {
        const data = await r.json()
        setPlayingUrl(data.stream_url)
        setPlayingSource(data.source)
        return
      }
    } catch { /* fall through to preview */ }
    if (fallbackPreviewUrl) {
      setPlayingUrl(fallbackPreviewUrl)
      setPlayingSource('30s preview')
    } else {
      setError('No full track or preview available for this one -- try another.')
    }
    setResolvingKey(null)
  }

  // Wraps playSmart so the resolving spinner clears even on the
  // full-track success path (the early return above skips the finally).
  async function playSmartWrapped(key: string, title: string, artist: string, fallbackPreviewUrl: string | null) {
    await playSmart(key, title, artist, fallbackPreviewUrl)
    setResolvingKey(null)
  }

  async function openAlbum(item: MusicItem) {
    if (!item.collection_id) return
    if (openCollectionId === item.collection_id) { setOpenCollectionId(null); return }
    setOpenCollectionId(item.collection_id)
    setAlbumTracksLoading(true)
    setAlbumTracks([])
    try {
      const r = await fetch(`/api/audio/stream/album/tracks?collection_id=${item.collection_id}`)
      const data = await r.json()
      setAlbumTracks(data.tracks || [])
    } catch {
      setAlbumTracks([])
    } finally {
      setAlbumTracksLoading(false)
    }
  }

  async function openArtist(a: ArtistItem) {
    if (!a.artist_id) return
    setViewingArtist(a)
    setArtistAlbumsLoading(true)
    setArtistAlbums([])
    setMixtapes([])
    setMixtapesLoading(true)
    try {
      const r = await fetch(`/api/audio/stream/artist/albums?artist_id=${a.artist_id}`)
      const data = await r.json()
      setArtistAlbums(data.albums || [])
    } catch {
      setArtistAlbums([])
    } finally {
      setArtistAlbumsLoading(false)
    }
    // Real mixtapes via archive.org's hiphopmixtapes collection (DatPiff's
    // own official successor) -- fetched separately/in parallel, not
    // gated on the iTunes lookup succeeding.
    try {
      const r2 = await fetch(`/api/audio/stream/mixtapes?artist=${encodeURIComponent(a.title)}`)
      const data2 = await r2.json()
      setMixtapes(data2.mixtapes || [])
    } catch {
      setMixtapes([])
    } finally {
      setMixtapesLoading(false)
    }
  }

  async function openMixtape(m: MixtapeItem) {
    if (openMixtapeId === m.identifier) { setOpenMixtapeId(null); return }
    setOpenMixtapeId(m.identifier)
    setMixtapeTracksLoading(true)
    setMixtapeTracks([])
    try {
      const r = await fetch(`/api/audio/stream/mixtape/tracks?identifier=${encodeURIComponent(m.identifier)}`)
      const data = await r.json()
      setMixtapeTracks(data.tracks || [])
    } catch {
      setMixtapeTracks([])
    } finally {
      setMixtapeTracksLoading(false)
    }
  }

  async function search() {
    if (!query.trim() || searching) return
    setSearching(true)
    setError('')
    setOpenPodcast(null)
    setViewingArtist(null)
    try {
      if (tab === 'music') {
        setHasSearched(true)
        const r = await fetch(`/api/audio/stream/search/grouped?term=${encodeURIComponent(query.trim())}`)
        const data = await r.json()
        setSongs(data.songs || [])
        setAlbums(data.albums || [])
        const artistList = (data.artists || []).map((a: MusicItem) => ({ title: a.title, artist_id: a.artist_id ?? null, view_url: a.view_url }))
        setArtists(artistList)
        if ((data.songs || []).length === 0 && (data.albums || []).length === 0) setError('No results -- try another title/artist.')

        // Typed exactly an artist's name -- jump straight to their albums
        // instead of making them click the Artists chip first.
        const normalize = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')
        const exactArtist = artistList.find((a: ArtistItem) => normalize(a.title) === normalize(query.trim()))
        if (exactArtist && exactArtist.artist_id) {
          openArtist(exactArtist)
        }
      } else if (tab === 'podcasts') {
        const r = await fetch(`/api/audio/stream/search?term=${encodeURIComponent(query.trim())}&media=podcast&entity=podcast`)
        const data = await r.json()
        setPodcastResults(data.results || [])
        if ((data.results || []).length === 0) setError('No podcasts found -- try another show name.')
      } else if (tab === 'youtube') {
        const r = await fetch(`/api/audio/stream/youtube/search?term=${encodeURIComponent(query.trim())}`)
        const data = await r.json()
        setYoutubeConfigured(data.configured !== false)
        setYoutubeResults(data.results || [])
        if (data.configured !== false && (data.results || []).length === 0) setError('No results.')
      } else if (tab === 'fulltracks') {
        const r = await fetch(`/api/audio/stream/musify/search?term=${encodeURIComponent(query.trim())}`)
        const data = await r.json()
        setFullTrackResults(data.results || [])
        if ((data.results || []).length === 0) setError('No results -- try another title/artist.')
      }
    } catch {
      setError('Network error reaching franken-stream.')
    } finally {
      setSearching(false)
    }
  }

  async function openPodcastEpisodes(p: PodcastItem) {
    if (!p.feed_url) return
    setOpenPodcast(p)
    setEpisodesLoading(true)
    setEpisodes([])
    try {
      const r = await fetch(`/api/audio/stream/podcast/episodes?feed_url=${encodeURIComponent(p.feed_url)}`)
      const data = await r.json()
      setEpisodes(data.episodes || [])
    } catch {
      setError('Could not load episodes.')
    } finally {
      setEpisodesLoading(false)
    }
  }

  async function playFullTrack(item: FullTrackItem) {
    if (resolvingTrack === item.track_url) return
    setResolvingTrack(item.track_url)
    setError('')
    try {
      const r = await fetch(`/api/audio/stream/musify/resolve?track_url=${encodeURIComponent(item.track_url)}`)
      const data = await r.json()
      if (!r.ok || !data.stream_url) { setError('Could not resolve a playable stream for this track -- try another.'); return }
      setPlayingUrl(data.stream_url)
      setPlayingSource('musify.club')
    } catch {
      setError('Network error resolving track.')
    } finally {
      setResolvingTrack(null)
    }
  }

  async function resolveSoundcloud() {
    if (!scUrl.trim() || scLoading) return
    setScLoading(true)
    setScError('')
    setScResult(null)
    try {
      const r = await fetch(`/api/audio/stream/soundcloud/embed?url=${encodeURIComponent(scUrl.trim())}`)
      const data = await r.json()
      if (!r.ok) { setScError('Could not resolve that SoundCloud URL -- make sure it\'s a public track/playlist link.'); return }
      setScResult(data)
    } catch {
      setScError('Network error resolving SoundCloud embed.')
    } finally {
      setScLoading(false)
    }
  }

  const tabBtn = (t: Tab, label: string, Icon: any) => (
    <button
      onClick={() => { setTab(t); setError(''); setQuery('') }}
      className={tab === t ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
      style={{ display: 'flex', alignItems: 'center', gap: 6 }}
    >
      <Icon size={13} /> {label}
    </button>
  )

  function MusicCard({ item, isAlbum }: { item: MusicItem; isAlbum: boolean }) {
    const key = isAlbum ? `album-${item.collection_id}` : `song-${item.title}-${item.artist}`
    const isOpen = isAlbum && openCollectionId === item.collection_id
    return (
      <div className="glass" style={{ padding: 10, borderRadius: 10, border: '1px solid var(--border)', gridColumn: isOpen ? 'span 2' : undefined }}>
        <div
          style={{ position: 'relative', width: '100%', aspectRatio: '1/1', borderRadius: 8, overflow: 'hidden', marginBottom: 8, cursor: 'pointer' }}
          onClick={() => isAlbum ? openAlbum(item) : playSmartWrapped(key, item.title, item.artist, item.preview_url)}
        >
          {item.artwork
            ? <img src={item.artwork} alt={item.title} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            : <div style={{ width: '100%', height: '100%', background: 'rgba(255,255,255,0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Music2 size={28} color="var(--muted)" /></div>}
          <div style={{ position: 'absolute', bottom: 6, right: 6, width: 32, height: 32, borderRadius: '50%', background: 'var(--purple-bright)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {(resolvingKey === key) || (isOpen && albumTracksLoading)
              ? <Loader size={14} className="spin" color="#000" />
              : <Play size={14} color="#000" fill="#000" />}
          </div>
        </div>
        <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3 }}>{item.title}</div>
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>{item.artist}{item.genre ? ` · ${item.genre}` : ''}</div>

        {isOpen && !albumTracksLoading && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 220, overflowY: 'auto' }}>
            {albumTracks.length === 0 && <div style={{ fontSize: 11, color: 'var(--muted)' }}>No tracks found for this album.</div>}
            {albumTracks.map((t, ti) => {
              const tkey = `track-${item.collection_id}-${ti}`
              return (
                <button
                  key={ti}
                  onClick={() => playSmartWrapped(tkey, t.title, item.artist, t.preview_url)}
                  className="btn btn-ghost btn-sm"
                  disabled={resolvingKey === tkey}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'space-between', fontSize: 11, textAlign: 'left' }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {resolvingKey === tkey ? <Loader size={10} className="spin" /> : <Play size={10} />} {t.title}
                  </span>
                  <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{fmtMs(t.duration_ms)}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  function ChartRow({ title, items }: { title: string; items: ChartItem[] }) {
    if (items.length === 0) return null
    return (
      <div style={{ marginBottom: 24 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>{title}</h3>
        <div style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 8 }}>
          {items.map((c, i) => {
            const key = `chart-${title}-${i}`
            return (
              <div
                key={i}
                onClick={() => playSmartWrapped(key, c.title, c.artist, null)}
                style={{ flex: '0 0 130px', cursor: 'pointer' }}
              >
                <div style={{ position: 'relative', width: '100%', aspectRatio: '1/1', borderRadius: 8, overflow: 'hidden', marginBottom: 6 }}>
                  {c.artwork
                    ? <img src={c.artwork} alt={c.title} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                    : <div style={{ width: '100%', height: '100%', background: 'rgba(255,255,255,0.05)' }} />}
                  <div style={{ position: 'absolute', bottom: 6, right: 6, width: 26, height: 26, borderRadius: '50%', background: 'var(--purple-bright)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    {resolvingKey === key ? <Loader size={11} className="spin" color="#000" /> : <Play size={11} color="#000" fill="#000" />}
                  </div>
                </div>
                <div style={{ fontSize: 11, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title}</div>
                <div style={{ fontSize: 10, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.artist}</div>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div>
      <audio ref={audioRef} onEnded={() => { setPlayingUrl(null); setPlayingSource(null) }} style={{ display: 'none' }} />

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {tabBtn('music', 'Music', Music2)}
        {tabBtn('podcasts', 'Podcasts', Mic)}
        {tabBtn('soundcloud', 'SoundCloud', Radio)}
        {tabBtn('youtube', 'YouTube', Youtube)}
        {tabBtn('fulltracks', 'Full Tracks', Disc3)}
      </div>

      {tab === 'fulltracks' && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 14px', background: 'rgba(255,180,0,0.08)', border: '1px solid rgba(255,180,0,0.25)', borderRadius: 8, marginBottom: 16 }}>
          <AlertTriangle size={16} color="#ffb400" style={{ flexShrink: 0, marginTop: 1 }} />
          <span style={{ fontSize: 11, color: 'var(--muted-hi)' }}>
            Full-length tracks from an unlicensed music mirror (musify.club) -- not a licensed source like the tabs above.
            Streams full commercial songs without a license.
          </span>
        </div>
      )}

      {playingUrl && (
        <div style={{ marginBottom: 16, padding: '8px 14px', borderRadius: 8, background: 'rgba(8,8,16,0.7)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Play size={11} color="var(--purple-bright)" /> Now playing <NowPlayingBadge source={playingSource} />
        </div>
      )}

      {tab !== 'soundcloud' && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
          <input
            placeholder={tab === 'music' ? 'Search albums, songs, artists…' : tab === 'podcasts' ? 'Search podcasts…' : tab === 'fulltracks' ? 'Search full tracks…' : 'Search YouTube…'}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
            style={{ flex: 1, padding: '10px 14px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--muted-hi)', fontSize: 14 }}
          />
          <button className="btn btn-primary" disabled={searching || !query.trim()} onClick={search}>
            {searching ? <Loader size={14} className="spin" /> : <Search size={14} />} Search
          </button>
        </div>
      )}

      {error && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>{error}</p>}

      {tab === 'music' && (
        viewingArtist ? (
          <div>
            <button className="btn btn-ghost btn-sm" onClick={() => setViewingArtist(null)} style={{ marginBottom: 14 }}>
              ← Back to results
            </button>
            <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 14 }}>{viewingArtist.title}</h3>

            <h4 style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>Albums</h4>
            {artistAlbumsLoading && <p style={{ fontSize: 13, color: 'var(--muted)' }}><Loader size={14} className="spin" /> Loading albums…</p>}
            {!artistAlbumsLoading && artistAlbums.length === 0 && (
              <p style={{ fontSize: 13, color: 'var(--muted)' }}>No albums found for this artist.</p>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14, marginBottom: 28 }}>
              {artistAlbums.map((item, i) => <MusicCard key={i} item={item} isAlbum />)}
            </div>

            {(mixtapesLoading || mixtapes.length > 0) && (
              <>
                <h4 style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>
                  Mixtapes <span style={{ fontWeight: 400, color: 'var(--muted)' }}>(via archive.org)</span>
                </h4>
                {mixtapesLoading && <p style={{ fontSize: 13, color: 'var(--muted)' }}><Loader size={14} className="spin" /> Loading mixtapes…</p>}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {mixtapes.map((m, i) => (
                    <div key={i} className="glass" style={{ borderRadius: 8, border: '1px solid var(--border)' }}>
                      <div
                        onClick={() => openMixtape(m)}
                        style={{ padding: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, cursor: 'pointer' }}
                      >
                        <span style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                          <Disc3 size={14} color="var(--muted)" /> {m.title}
                        </span>
                        {openMixtapeId === m.identifier && mixtapeTracksLoading
                          ? <Loader size={12} className="spin" />
                          : <Play size={12} />}
                      </div>
                      {openMixtapeId === m.identifier && !mixtapeTracksLoading && (
                        <div style={{ padding: '0 12px 12px', display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 260, overflowY: 'auto' }}>
                          {mixtapeTracks.length === 0 && <div style={{ fontSize: 11, color: 'var(--muted)' }}>No playable tracks found.</div>}
                          {mixtapeTracks.map((t, ti) => (
                            <button
                              key={ti}
                              onClick={() => { setPlayingUrl(t.url); setPlayingSource('archive.org') }}
                              className="btn btn-ghost btn-sm"
                              style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'space-between', fontSize: 11, textAlign: 'left' }}
                            >
                              <span style={{ display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {playingUrl === t.url ? <Pause size={10} /> : <Play size={10} />} {t.title}
                              </span>
                              <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{t.duration ? `${Math.floor(t.duration / 60)}:${String(Math.floor(t.duration % 60)).padStart(2, '0')}` : ''}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : hasSearched ? (
          <>
            {songs.length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>Songs</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
                  {songs.map((item, i) => <MusicCard key={i} item={item} isAlbum={false} />)}
                </div>
              </div>
            )}
            {albums.length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>Albums</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
                  {albums.map((item, i) => <MusicCard key={i} item={item} isAlbum />)}
                </div>
              </div>
            )}
            {artists.length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>Artists</h3>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {artists.map((a, i) => (
                    <button
                      key={i}
                      className="btn btn-ghost btn-sm"
                      disabled={!a.artist_id}
                      onClick={() => openArtist(a)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                    >
                      <Users size={12} /> {a.title}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            {browseLoading && <p style={{ fontSize: 13, color: 'var(--muted)' }}><Loader size={14} className="spin" /> Loading…</p>}
            <ChartRow title="Trending" items={trending} />
            {genres.map(g => <ChartRow key={g.id} title={g.name} items={genreRows[g.id] || []} />)}
          </>
        )
      )}

      {tab === 'podcasts' && (
        <>
          {openPodcast && (
            <div style={{ marginBottom: 20, borderRadius: 12, border: '1px solid var(--border)', padding: 14 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
                {openPodcast.artwork && <img src={openPodcast.artwork} alt="" style={{ width: 48, height: 48, borderRadius: 6 }} />}
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{openPodcast.title}</div>
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>{openPodcast.artist}</div>
                </div>
              </div>
              {episodesLoading && <Loader size={14} className="spin" />}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 320, overflowY: 'auto' }}>
                {episodes.map((ep, i) => (
                  <div key={i} className="glass" style={{ padding: 10, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ep.title}</div>
                      <div style={{ fontSize: 10, color: 'var(--muted)' }}>{ep.duration}</div>
                    </div>
                    <button className="btn btn-ghost btn-sm" onClick={() => { setPlayingUrl(ep.audio_url); setPlayingSource(null) }}>
                      {playingUrl === ep.audio_url ? <Pause size={12} /> : <Play size={12} />}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
            {podcastResults.map((p, i) => (
              <div key={i} className="glass" style={{ padding: 10, borderRadius: 10, border: '1px solid var(--border)', cursor: 'pointer' }} onClick={() => openPodcastEpisodes(p)}>
                {p.artwork
                  ? <img src={p.artwork} alt={p.title} style={{ width: '100%', aspectRatio: '1/1', objectFit: 'cover', borderRadius: 8, marginBottom: 8 }} />
                  : <div style={{ width: '100%', aspectRatio: '1/1', background: 'rgba(255,255,255,0.05)', borderRadius: 8, marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Mic size={28} color="var(--muted)" /></div>}
                <div style={{ fontSize: 12, fontWeight: 600 }}>{p.title}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{p.artist}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === 'soundcloud' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            <input
              placeholder="Paste a public SoundCloud track/playlist URL…"
              value={scUrl}
              onChange={e => setScUrl(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && resolveSoundcloud()}
              style={{ flex: 1, padding: '10px 14px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--muted-hi)', fontSize: 14 }}
            />
            <button className="btn btn-primary" disabled={scLoading || !scUrl.trim()} onClick={resolveSoundcloud}>
              {scLoading ? <Loader size={14} className="spin" /> : <Search size={14} />} Load
            </button>
          </div>
          <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 16 }}>
            SoundCloud doesn't offer a key-free full-catalog search, so paste a track or playlist link (e.g. from soundcloud.com) --
            resolved via SoundCloud's own official embed.
          </p>
          {scError && <p style={{ fontSize: 12, color: 'var(--muted)' }}>{scError}</p>}
          {scResult && (
            <div style={{ borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
              <div dangerouslySetInnerHTML={{ __html: scResult.embed_html }} />
              <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.7)', fontSize: 13 }}>
                <strong style={{ color: 'var(--purple-bright)' }}>{scResult.title}</strong> — {scResult.author}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'youtube' && (
        <>
          {!youtubeConfigured && (
            <p style={{ fontSize: 13, color: 'var(--muted)' }}>
              YouTube search isn't configured yet -- needs a free YOUTUBE_API_KEY (Google Cloud Console) set on the server.
            </p>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
            {youtubeResults.map((v, i) => (
              <div key={i} className="glass" style={{ borderRadius: 10, overflow: 'hidden', border: '1px solid var(--border)' }}>
                <div style={{ width: '100%', aspectRatio: '16/9' }}>
                  <iframe
                    src={v.embed_url}
                    allowFullScreen
                    style={{ width: '100%', height: '100%', border: 'none' }}
                  />
                </div>
                <div style={{ padding: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.title}</div>
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>{v.channel}</div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === 'fulltracks' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {fullTrackResults.map((item, i) => (
            <div key={i} className="glass" style={{ padding: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, borderRadius: 8 }}>
              <span style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Disc3 size={14} color="var(--muted)" /> {item.title}
              </span>
              <button
                className="btn btn-ghost btn-sm"
                disabled={resolvingTrack === item.track_url}
                onClick={() => playFullTrack(item)}
              >
                {resolvingTrack === item.track_url ? <Loader size={12} className="spin" /> : <Play size={12} />}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
