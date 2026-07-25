import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Play, Loader, Maximize2, Minimize2 } from 'lucide-react'
import Hls from 'hls.js'

// "Live TV" tab -- pure iptv-org channel browser (real HLS, zero
// scraping), separate from the "Stream" tab (on-demand movies/TV via
// TMDB+embed providers). Channels grouped by iptv-org's own `group`
// metadata (Movies/News/Sports/etc -- some channels carry compound
// groups like "Documentary;News", split into individual categories
// below), grid layout with logos, TV-app style.
//
// Layout: player pinned in a fixed-height header (not scrolling with the
// grid); the category nav + channel grid live in their own scrollable
// region below. The category nav is sticky WITHIN that scroll region
// only (top:0 relative to its own overflow:auto container) -- keeping it
// out of the same scroll flow as the player avoids the sticky-vs-sticky
// overlap/z-fight the previous version had.

interface Country { name: string; code: string; flag: string }
interface Channel { title: string; url: string; group: string; logo: string }

const STYLE_ID = 'livetv-css'
const CSS = `
.channel-card:hover { transform: translateY(-2px); border-color: var(--purple-bright); }
`

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

  return <video ref={videoRef} controls autoPlay style={{ width: '100%', height: '100%', background: '#000', display: 'block' }} />
}

function ChannelCard({ c, onClick }: { c: Channel; onClick: () => void }) {
  return (
    <div
      className="channel-card glass"
      onClick={onClick}
      style={{ padding: 14, cursor: 'pointer', borderRadius: 10, border: '1px solid var(--border)', transition: 'all 0.15s ease', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, textAlign: 'center' }}
    >
      {c.logo
        ? <img src={c.logo} alt="" style={{ width: 48, height: 48, objectFit: 'contain' }} />
        : <div style={{ width: 48, height: 48, borderRadius: 8, background: 'rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Play size={16} color="var(--muted)" /></div>}
      <div style={{ fontSize: 12, lineHeight: 1.3 }}>{c.title}</div>
    </div>
  )
}

export default function LiveTV() {
  const [countries, setCountries] = useState<Country[]>([])
  const [countryCode, setCountryCode] = useState('US')
  const [channels, setChannels] = useState<Channel[]>([])
  const [channelsLoading, setChannelsLoading] = useState(false)
  const [activeGroup, setActiveGroup] = useState<string>('All')
  const [liveStream, setLiveStream] = useState<{ url: string; title: string } | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!document.getElementById(STYLE_ID)) {
      const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = CSS
      document.head.appendChild(el)
    }
    fetch('/api/cinema/livetv/live/countries')
      .then(r => r.json())
      .then(d => setCountries(d.countries || []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    setChannelsLoading(true)
    setActiveGroup('All')
    fetch(`/api/cinema/livetv/live/channels/${countryCode}`)
      .then(r => r.json())
      .then(d => setChannels(d.channels || []))
      .catch(() => setChannels([]))
      .finally(() => setChannelsLoading(false))
  }, [countryCode])

  // iptv-org groups are sometimes compound ("Documentary;News") -- split
  // each channel into every category it belongs to, not just its first tag.
  const channelsByCategory = useMemo(() => {
    const map: Record<string, Channel[]> = {}
    for (const c of channels) {
      const cats = (c.group || 'Other').split(';').map(s => s.trim()).filter(Boolean)
      for (const cat of cats.length ? cats : ['Other']) {
        if (!map[cat]) map[cat] = []
        map[cat].push(c)
      }
    }
    return map
  }, [channels])

  const categories = useMemo(
    () => Object.keys(channelsByCategory).sort((a, b) => channelsByCategory[b].length - channelsByCategory[a].length),
    [channelsByCategory]
  )

  function jumpTo(cat: string) {
    setActiveGroup(cat)
    if (cat === 'All') return
    requestAnimationFrame(() => {
      document.getElementById(`livetv-section-${cat}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  const totalShown = activeGroup === 'All' ? channels.length : (channelsByCategory[activeGroup] || []).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 220px)', minHeight: 400 }}>
      {/* Pinned header: country picker + player. Does not scroll with the grid below. */}
      <div style={{ flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: liveStream ? 12 : 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <select
            value={countryCode}
            onChange={e => setCountryCode(e.target.value)}
            style={{ padding: '8px 12px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--muted-hi)', fontSize: 13 }}
          >
            {(countries.length ? countries : [{ name: 'United States', code: 'US', flag: '🇺🇸' }]).map(c => (
              <option key={c.code} value={c.code}>{c.flag} {c.name}</option>
            ))}
          </select>
          {channelsLoading && <Loader size={14} className="spin" />}
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>{totalShown} channels</span>
        </div>

        {liveStream && (
          <div style={{ marginBottom: 16, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}>
            <div style={{ position: 'relative', height: expanded ? '70vh' : 260, transition: 'height 0.2s ease' }}>
              <HlsPlayer src={liveStream.url} />
              <button
                onClick={() => setExpanded(e => !e)}
                title={expanded ? 'Collapse player' : 'Expand player'}
                style={{ position: 'absolute', top: 8, right: 8, background: 'rgba(0,0,0,0.6)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 6, padding: 6, cursor: 'pointer', color: '#fff', display: 'flex' }}
              >
                {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
            </div>
            <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.85)', fontSize: 13 }}>
              Now playing: <strong style={{ color: 'var(--purple-bright)' }}>{liveStream.title}</strong>
            </div>
          </div>
        )}
      </div>

      {/* Independently scrolling category nav + channel grid. */}
      <div style={{ flex: 1, overflowY: 'auto', paddingRight: 4 }}>
        {categories.length > 1 && (
          <div style={{
            position: 'sticky', top: 0, zIndex: 5,
            background: 'var(--bg, #0a0a12)', paddingTop: 4, paddingBottom: 8,
            display: 'flex', gap: 6, marginBottom: 16, overflowX: 'auto',
          }}>
            <button
              onClick={() => jumpTo('All')}
              className={activeGroup === 'All' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
              style={{ whiteSpace: 'nowrap' }}
            >
              All
            </button>
            {categories.map(cat => (
              <button
                key={cat}
                onClick={() => jumpTo(cat)}
                className={cat === activeGroup ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
                style={{ whiteSpace: 'nowrap' }}
              >
                {cat} <span style={{ opacity: 0.6, fontSize: 10 }}>({channelsByCategory[cat].length})</span>
              </button>
            ))}
          </div>
        )}

        {activeGroup === 'All' ? (
          // Sectioned view: a labeled row per category, so scrolling shows
          // real structure instead of one giant unsorted grid.
          categories.map(cat => (
            <div key={cat} id={`livetv-section-${cat}`} style={{ marginBottom: 28, scrollMarginTop: 56 }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>
                {cat} <span style={{ fontWeight: 400, color: 'var(--muted)', fontSize: 12 }}>({channelsByCategory[cat].length})</span>
              </h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
                {channelsByCategory[cat].map((c, i) => (
                  <ChannelCard key={cat + i} c={c} onClick={() => setLiveStream({ url: c.url, title: c.title })} />
                ))}
              </div>
            </div>
          ))
        ) : (
          // Single-category filtered view
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
            {(channelsByCategory[activeGroup] || []).map((c, i) => (
              <ChannelCard key={i} c={c} onClick={() => setLiveStream({ url: c.url, title: c.title })} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
