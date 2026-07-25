import React, { useState, useEffect, useRef } from 'react'
import { Search, Play, Loader, Tv, TrendingUp } from 'lucide-react'
import Hls from 'hls.js'

// "Watch Live TV" -- the actual franken-stream experience natively inside
// Cinema: search across streaming-mirror providers (or TMDB+embed
// providers when configured) by title, resolve a playable embed, watch it
// in Vantage. Also: a real iptv-org-backed Live TV channel browser (HLS,
// zero scraping) and a no-search-needed trending grid. Proxied through
// backend/routers/frankenstream_proxy.py -> franken-stream's own web API
// (ares-frankenstream.service).
//
// Disclosed, not hidden: search-tab sources are unlicensed streaming-mirror
// sites -- some results will be dead links, that's inherent to this class
// of source and not something this UI can paper over. Live TV channels are
// from iptv-org's community-maintained public playlists.

interface SearchResult {
  title: string
  url: string
  thumbnail?: string | null
}

interface Country { name: string; code: string; flag: string }
interface Channel { title: string; url: string; group: string; logo: string }

type Tab = 'search' | 'trending' | 'livetv'

function HlsPlayer({ src }: { src: string }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (Hls.isSupported()) {
      const hls = new Hls()
      hls.loadSource(src)
      hls.attachMedia(video)
      return () => hls.destroy()
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = src // Safari: native HLS
    }
  }, [src])

  return (
    <video
      ref={videoRef}
      controls
      autoPlay
      style={{ width: '100%', height: 500, background: '#000' }}
    />
  )
}

export default function LiveTV() {
  const [tab, setTab] = useState<Tab>('search')

  // Search tab state
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [resolving, setResolving] = useState<string | null>(null)
  const [embedUrl, setEmbedUrl] = useState<string | null>(null)
  const [nowPlaying, setNowPlaying] = useState<string | null>(null)
  const [error, setError] = useState('')

  // Trending tab state
  const [trending, setTrending] = useState<SearchResult[]>([])
  const [trendingLoading, setTrendingLoading] = useState(false)

  // Live TV tab state
  const [countries, setCountries] = useState<Country[]>([])
  const [countryCode, setCountryCode] = useState('US')
  const [channels, setChannels] = useState<Channel[]>([])
  const [channelsLoading, setChannelsLoading] = useState(false)
  const [liveStream, setLiveStream] = useState<{ url: string; title: string } | null>(null)

  useEffect(() => {
    if (tab === 'trending' && trending.length === 0 && !trendingLoading) {
      setTrendingLoading(true)
      fetch('/api/cinema/livetv/trending')
        .then(r => r.json())
        .then(d => setTrending(d.results || []))
        .catch(() => {})
        .finally(() => setTrendingLoading(false))
    }
    if (tab === 'livetv' && countries.length === 0) {
      fetch('/api/cinema/livetv/live/countries')
        .then(r => r.json())
        .then(d => setCountries(d.countries || []))
        .catch(() => {})
    }
  }, [tab])

  useEffect(() => {
    if (tab !== 'livetv') return
    setChannelsLoading(true)
    fetch(`/api/cinema/livetv/live/channels/${countryCode}`)
      .then(r => r.json())
      .then(d => setChannels(d.channels || []))
      .catch(() => setChannels([]))
      .finally(() => setChannelsLoading(false))
  }, [tab, countryCode])

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

  const tabBtn = (t: Tab, label: string, Icon: any) => (
    <button
      onClick={() => setTab(t)}
      className={tab === t ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
      style={{ display: 'flex', alignItems: 'center', gap: 6 }}
    >
      <Icon size={13} /> {label}
    </button>
  )

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {tabBtn('search', 'Search', Search)}
        {tabBtn('trending', 'Trending', TrendingUp)}
        {tabBtn('livetv', 'Live TV', Tv)}
      </div>

      {tab === 'search' && (
        <>
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
        </>
      )}

      {tab === 'trending' && (
        <div>
          {trendingLoading && <p style={{ fontSize: 13, color: 'var(--muted)' }}><Loader size={14} className="spin" /> Loading trending…</p>}
          {!trendingLoading && trending.length === 0 && (
            <p style={{ fontSize: 13, color: 'var(--muted)' }}>
              No trending data -- TMDB isn't configured yet (needs a free API key from themoviedb.org set as TMDB_API_KEY).
            </p>
          )}
          {embedUrl && (
            <div style={{ marginBottom: 20, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
              <iframe src={embedUrl} allowFullScreen style={{ width: '100%', height: 500, border: 'none', background: '#000' }} />
              <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.7)', fontSize: 13 }}>
                Now playing: <strong style={{ color: 'var(--purple-bright)' }}>{nowPlaying}</strong>
              </div>
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12 }}>
            {trending.map((t, i) => (
              <div key={i} className="glass" style={{ cursor: 'pointer', overflow: 'hidden', borderRadius: 8 }} onClick={() => watch(t)}>
                {t.thumbnail
                  ? <img src={t.thumbnail} alt={t.title} style={{ width: '100%', aspectRatio: '2/3', objectFit: 'cover' }} />
                  : <div style={{ width: '100%', aspectRatio: '2/3', background: 'rgba(255,255,255,0.05)' }} />}
                <div style={{ padding: 6, fontSize: 11 }}>{t.title}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'livetv' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
            <select
              value={countryCode}
              onChange={e => { setCountryCode(e.target.value); setLiveStream(null) }}
              style={{ padding: '8px 12px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--muted-hi)', fontSize: 13 }}
            >
              {(countries.length ? countries : [{ name: 'United States', code: 'US', flag: '🇺🇸' }]).map(c => (
                <option key={c.code} value={c.code}>{c.flag} {c.name}</option>
              ))}
            </select>
            {channelsLoading && <Loader size={14} className="spin" />}
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>{channels.length} channels</span>
          </div>

          {liveStream && (
            <div style={{ marginBottom: 20, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
              <HlsPlayer src={liveStream.url} />
              <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.7)', fontSize: 13 }}>
                Now playing: <strong style={{ color: 'var(--purple-bright)' }}>{liveStream.title}</strong>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 500, overflowY: 'auto' }}>
            {channels.map((c, i) => (
              <div key={i} className="glass" style={{ padding: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {c.logo && <img src={c.logo} alt="" style={{ width: 24, height: 24, objectFit: 'contain' }} />}
                  <div>
                    <div style={{ fontSize: 13 }}>{c.title}</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{c.group}</div>
                  </div>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => setLiveStream({ url: c.url, title: c.title })}>
                  <Play size={12} /> Watch
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
