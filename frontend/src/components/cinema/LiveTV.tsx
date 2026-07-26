import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Play, Loader, Maximize2, Minimize2, ChevronLeft, ChevronRight, Shuffle } from 'lucide-react'
import Hls from 'hls.js'

// "Live TV" tab -- pure iptv-org channel browser (real HLS, zero
// scraping), separate from the "Stream" tab (on-demand movies/TV via
// TMDB+embed providers). Channels grouped by iptv-org's own `group`
// metadata (Movies/News/Sports/etc -- some channels carry compound
// groups like "Documentary;News", split into individual categories
// below).
//
// Layout (2026-07-26 redesign): category rows scroll horizontally
// (same PosterRow-style Netflix pattern as the Stream tab), not one
// giant per-category grid dump -- 79 categories x however many channels
// each was a wall of tiles before. Selecting a category tab still shows
// its full grid (real intent to browse everything in it), but the
// default "All" view is a stack of browsable rows, capped per row.
// Auto-start: the moment channels for the selected country arrive, if
// nothing is playing yet, immediately start the first channel of the
// top category -- "always something playing, browse to change", same
// pattern as the Stream tab's pinned player.
//
// Player pinned in a fixed-height header (not scrolling with the
// grid); the category nav + channel rows live in their own scrollable
// region below. The category nav is sticky WITHIN that scroll region
// only (top:0 relative to its own overflow:auto container) -- keeping it
// out of the same scroll flow as the player avoids the sticky-vs-sticky
// overlap/z-fight the previous version had.

interface Country { name: string; code: string; flag: string }
interface Channel { title: string; url: string; group: string; logo: string }

const ROW_CAP = 16 // channels shown per category row in the "All" browse view

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

function ChannelCard({ c, active, onClick }: { c: Channel; active?: boolean; onClick: () => void }) {
  return (
    <div
      className="channel-card glass"
      onClick={onClick}
      style={{
        padding: 14, cursor: 'pointer', borderRadius: 10,
        border: active ? '1px solid var(--purple-bright)' : '1px solid var(--border)',
        transition: 'all 0.15s ease', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, textAlign: 'center',
        flex: '0 0 140px',
      }}
    >
      {c.logo
        ? <img src={c.logo} alt="" style={{ width: 48, height: 48, objectFit: 'contain' }} />
        : <div style={{ width: 48, height: 48, borderRadius: 8, background: 'rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Play size={16} color="var(--muted)" /></div>}
      <div style={{ fontSize: 12, lineHeight: 1.3 }}>{c.title}</div>
    </div>
  )
}

function ChannelRow({ title, count, channels, activeUrl, onSelect, onSeeAll }: {
  title: string
  count: number
  channels: Channel[]
  activeUrl?: string
  onSelect: (c: Channel) => void
  onSeeAll: () => void
}) {
  if (channels.length === 0) return null
  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--muted-hi)' }}>
          {title} <span style={{ fontWeight: 400, color: 'var(--muted)', fontSize: 12 }}>({count})</span>
        </h3>
        {count > channels.length && (
          <button className="btn btn-ghost btn-sm" onClick={onSeeAll} style={{ fontSize: 11 }}>See all {count} →</button>
        )}
      </div>
      <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 8 }}>
        {channels.map((c, i) => (
          <ChannelCard key={title + i} c={c} active={activeUrl === c.url} onClick={() => onSelect(c)} />
        ))}
      </div>
    </div>
  )
}

export default function LiveTV() {
  const [countries, setCountries] = useState<Country[]>([])
  const [countryCode, setCountryCode] = useState(localStorage.getItem('livetv_default_country') || 'US')
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

  // Auto-start: the moment channels arrive for this country, if nothing
  // is playing yet, immediately start the first channel of the top
  // (most populous) category -- "always something playing, browse to
  // change", same pattern as the Stream tab's pinned player.
  useEffect(() => {
    if (liveStream || categories.length === 0) return
    const first = channelsByCategory[categories[0]][0]
    setLiveStream({ url: first.url, title: first.title })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categories])

  function jumpTo(cat: string) {
    setActiveGroup(cat)
    if (cat === 'All') return
    requestAnimationFrame(() => {
      document.getElementById(`livetv-section-${cat}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  const totalShown = activeGroup === 'All' ? channels.length : (channelsByCategory[activeGroup] || []).length

  // Channel-switcher: steps through whichever list is currently on
  // screen -- the active category's channels if one is selected
  // (or the top category's, in the "All" browse view), so next/prev
  // always advances through something the user can actually see.
  const switcherList = activeGroup === 'All' ? (channelsByCategory[categories[0]] || []) : (channelsByCategory[activeGroup] || [])

  function stepChannel(dir: 1 | -1) {
    if (switcherList.length === 0) return
    const idx = switcherList.findIndex(c => c.url === liveStream?.url)
    const next = idx === -1 ? 0 : (idx + dir + switcherList.length) % switcherList.length
    const c = switcherList[next]
    setLiveStream({ url: c.url, title: c.title })
  }

  function randomChannel() {
    if (channels.length === 0) return
    const c = channels[Math.floor(Math.random() * channels.length)]
    setLiveStream({ url: c.url, title: c.title })
  }

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
            <div style={{ padding: '10px 14px', background: 'rgba(8,8,16,0.85)', fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#ff4d4d', flexShrink: 0, boxShadow: '0 0 6px #ff4d4d' }} />
                <span style={{ fontSize: 10, letterSpacing: '0.5px', color: '#ff4d4d', fontWeight: 700, flexShrink: 0 }}>LIVE</span>
                <strong style={{ color: 'var(--purple-bright)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{liveStream.title}</strong>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                <button className="btn btn-ghost btn-sm" title="Previous channel" onClick={() => stepChannel(-1)}><ChevronLeft size={14} /></button>
                <button className="btn btn-ghost btn-sm" title="Random channel" onClick={randomChannel}><Shuffle size={13} /></button>
                <button className="btn btn-ghost btn-sm" title="Next channel" onClick={() => stepChannel(1)}><ChevronRight size={14} /></button>
              </div>
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
          // Netflix-style browse: a horizontally-scrolling, capped row per
          // category (top categories first, by channel count) -- not a
          // full grid dump of every channel in every one of the 79
          // categories. "See all" jumps into that category's full grid.
          categories.map(cat => (
            <ChannelRow
              key={cat}
              title={cat}
              count={channelsByCategory[cat].length}
              channels={channelsByCategory[cat].slice(0, ROW_CAP)}
              activeUrl={liveStream?.url}
              onSelect={c => setLiveStream({ url: c.url, title: c.title })}
              onSeeAll={() => jumpTo(cat)}
            />
          ))
        ) : (
          // Single-category filtered view -- full grid, real intent to
          // browse everything in this one category.
          <div id={`livetv-section-${activeGroup}`} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
            {(channelsByCategory[activeGroup] || []).map((c, i) => (
              <ChannelCard key={i} c={c} active={liveStream?.url === c.url} onClick={() => setLiveStream({ url: c.url, title: c.title })} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
