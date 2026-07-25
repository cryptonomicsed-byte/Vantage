import React, { useState, useRef, useEffect } from 'react'
import { Search, Loader, Play, Pause, Radio, Mic, Music2, Youtube } from 'lucide-react'

// Audio "Stream" tab -- legal-first external audio sources, distinct from
// the Library tab's agent-produced tracks:
//   Music: iTunes Search API (real metadata, 600x600 cover art, legal
//     30s preview clips -- NOT full songs, Apple's own design).
//   Podcasts: same iTunes Search endpoint (media=podcast) for discovery,
//     then a real public RSS feed parsed directly for full-length
//     episode audio -- fully legal, podcasts are open RSS by design.
//   SoundCloud: paste a track URL, resolved via SoundCloud's real oEmbed
//     (no key-free full-catalog search exists yet).
//   YouTube: official Data API search + standard youtube.com/embed
//     iframe -- needs YOUTUBE_API_KEY configured server-side; shows a
//     clear "not configured" state otherwise, same as TMDB's pattern.

type Tab = 'music' | 'podcasts' | 'soundcloud' | 'youtube'

interface MusicItem {
  kind: string; title: string; artist: string; collection: string
  artwork: string | null; preview_url: string | null; track_count: number | null; genre: string | null
}
interface PodcastItem {
  kind: string; title: string; artist: string; artwork: string | null; feed_url: string | null
}
interface Episode {
  podcast: string; title: string; audio_url: string; pub_date: string; duration: string
}
interface YoutubeItem {
  video_id: string; title: string; channel: string; thumbnail: string; embed_url: string
}

function MusicResults({ items, playingUrl, onPlay }: { items: MusicItem[]; playingUrl: string | null; onPlay: (i: MusicItem) => void }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
      {items.map((item, i) => (
        <div key={i} className="glass" style={{ padding: 10, borderRadius: 10, border: '1px solid var(--border)' }}>
          <div style={{ position: 'relative', width: '100%', aspectRatio: '1/1', borderRadius: 8, overflow: 'hidden', marginBottom: 8 }}>
            {item.artwork
              ? <img src={item.artwork} alt={item.title} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              : <div style={{ width: '100%', height: '100%', background: 'rgba(255,255,255,0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Music2 size={28} color="var(--muted)" /></div>}
            {item.preview_url && (
              <button
                onClick={() => onPlay(item)}
                style={{ position: 'absolute', bottom: 6, right: 6, width: 32, height: 32, borderRadius: '50%', background: 'var(--purple-bright)', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}
              >
                {playingUrl === item.preview_url ? <Pause size={14} color="#000" /> : <Play size={14} color="#000" fill="#000" />}
              </button>
            )}
          </div>
          <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3 }}>{item.title}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>{item.artist}{item.genre ? ` · ${item.genre}` : ''}</div>
          {item.preview_url && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>30s preview</div>}
        </div>
      ))}
    </div>
  )
}

export default function AudioStreamBrowse() {
  const [tab, setTab] = useState<Tab>('music')
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')

  const [musicResults, setMusicResults] = useState<MusicItem[]>([])
  const [podcastResults, setPodcastResults] = useState<PodcastItem[]>([])
  const [youtubeResults, setYoutubeResults] = useState<YoutubeItem[]>([])
  const [youtubeConfigured, setYoutubeConfigured] = useState(true)

  const [playingUrl, setPlayingUrl] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const [openPodcast, setOpenPodcast] = useState<PodcastItem | null>(null)
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [episodesLoading, setEpisodesLoading] = useState(false)

  const [scUrl, setScUrl] = useState('')
  const [scResult, setScResult] = useState<{ title: string; author: string; thumbnail: string; embed_html: string } | null>(null)
  const [scLoading, setScLoading] = useState(false)
  const [scError, setScError] = useState('')

  function playPreview(item: MusicItem) {
    if (!item.preview_url) return
    if (playingUrl === item.preview_url) {
      audioRef.current?.pause()
      setPlayingUrl(null)
      return
    }
    setPlayingUrl(item.preview_url)
  }

  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !playingUrl) return
    audio.src = playingUrl
    audio.play().catch(() => {})
  }, [playingUrl])

  async function search() {
    if (!query.trim() || searching) return
    setSearching(true)
    setError('')
    setOpenPodcast(null)
    try {
      if (tab === 'music') {
        const r = await fetch(`/api/audio/stream/search?term=${encodeURIComponent(query.trim())}&media=music&entity=album`)
        const data = await r.json()
        setMusicResults(data.results || [])
        if ((data.results || []).length === 0) setError('No results -- try another title/artist.')
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

  return (
    <div>
      <audio ref={audioRef} onEnded={() => setPlayingUrl(null)} style={{ display: 'none' }} />

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {tabBtn('music', 'Music', Music2)}
        {tabBtn('podcasts', 'Podcasts', Mic)}
        {tabBtn('soundcloud', 'SoundCloud', Radio)}
        {tabBtn('youtube', 'YouTube', Youtube)}
      </div>

      {tab !== 'soundcloud' && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
          <input
            placeholder={tab === 'music' ? 'Search albums or songs…' : tab === 'podcasts' ? 'Search podcasts…' : 'Search YouTube…'}
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

      {tab === 'music' && <MusicResults items={musicResults} playingUrl={playingUrl} onPlay={playPreview} />}

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
                    <button className="btn btn-ghost btn-sm" onClick={() => setPlayingUrl(ep.audio_url)}>
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
    </div>
  )
}
