import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Play, Loader } from 'lucide-react'
import Hls from 'hls.js'

// "Live TV" tab -- pure iptv-org channel browser (real HLS, zero
// scraping), separate from the "Stream" tab (on-demand movies/TV via
// TMDB+embed providers). Channels grouped by iptv-org's own `group`
// metadata (Movies/News/Sports/etc), grid layout with logos, TV-app style.

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

  return <video ref={videoRef} controls autoPlay style={{ width: '100%', height: 500, background: '#000' }} />
}

export default function LiveTV() {
  const [countries, setCountries] = useState<Country[]>([])
  const [countryCode, setCountryCode] = useState('US')
  const [channels, setChannels] = useState<Channel[]>([])
  const [channelsLoading, setChannelsLoading] = useState(false)
  const [activeGroup, setActiveGroup] = useState<string>('All')
  const [liveStream, setLiveStream] = useState<{ url: string; title: string } | null>(null)

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

  const groups = useMemo(() => {
    const set = new Set<string>()
    channels.forEach(c => set.add(c.group || 'Other'))
    return ['All', ...Array.from(set).sort()]
  }, [channels])

  const filtered = activeGroup === 'All' ? channels : channels.filter(c => (c.group || 'Other') === activeGroup)

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
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
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>{filtered.length} channels</span>
      </div>

      {liveStream && (
        <div style={{ marginBottom: 24, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
          <HlsPlayer src={liveStream.url} />
          <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.7)', fontSize: 13 }}>
            Now playing: <strong style={{ color: 'var(--purple-bright)' }}>{liveStream.title}</strong>
          </div>
        </div>
      )}

      {groups.length > 1 && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 16, overflowX: 'auto', paddingBottom: 4 }}>
          {groups.map(g => (
            <button
              key={g}
              onClick={() => setActiveGroup(g)}
              className={g === activeGroup ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
              style={{ whiteSpace: 'nowrap' }}
            >
              {g}
            </button>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
        {filtered.map((c, i) => (
          <div
            key={i}
            className="channel-card glass"
            onClick={() => setLiveStream({ url: c.url, title: c.title })}
            style={{ padding: 14, cursor: 'pointer', borderRadius: 10, border: '1px solid var(--border)', transition: 'all 0.15s ease', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, textAlign: 'center' }}
          >
            {c.logo
              ? <img src={c.logo} alt="" style={{ width: 48, height: 48, objectFit: 'contain' }} />
              : <div style={{ width: 48, height: 48, borderRadius: 8, background: 'rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Play size={16} color="var(--muted)" /></div>}
            <div style={{ fontSize: 12, lineHeight: 1.3 }}>{c.title}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>{c.group}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
