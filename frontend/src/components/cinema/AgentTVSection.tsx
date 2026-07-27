import React, { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Tv, Play, ThumbsUp, ThumbsDown, Mic } from 'lucide-react'

// AgentTV -- Vantage's always-on 24/7 channel. Rebuilt 2026-07-27: real
// two-host podcast episodes (backend/podcast_engine.py + agenttv_channel.py),
// same engine Collab's "Create Podcast" uses -- no more separate/redundant
// generation system, and no more the old cross-service proxy that silently
// broke seeking (segments looked like they "looped on 6 seconds" because
// Range requests were never forwarded -- fixed by serving straight from
// Vantage's own /media/videos now).
//
// Does NOT auto-play -- shows a "now playing" card the user clicks to
// start, same not-a-player-until-you-ask-for-it pattern as everything else
// built this session. User-submitted podcasts (via Collab's Create
// Podcast, kind=video) air in rotation ahead of freshly auto-generated
// ones, sharing the exact same house jingle between segments.

const KEY = () => localStorage.getItem('vantage_api_key') || ''

interface NowPlaying {
  segmentUrl: string | null
  phase: 'segment' | 'filler'
  startedAt: number | null
  duration: number
  title: string | null
  now: number
}

function HlsAwareVideo({ src }: { src: string }) {
  // Plain mp4 -- no HLS needed here (real Range/seek support comes from
  // Vantage's own StaticFiles mount, not a manifest).
  return <video controls autoPlay style={{ width: '100%', height: '100%', background: '#000', display: 'block' }} src={src} />
}

export default function AgentTVSection() {
  const [np, setNp] = useState<NowPlaying | null>(null)
  const [playing, setPlaying] = useState(false)
  const [reacted, setReacted] = useState<'up' | 'down' | null>(null)
  const videoBoxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let stop = false
    async function poll() {
      try {
        const r = await fetch('/api/cinema/agenttv/now-playing')
        if (r.ok) { const data = await r.json(); if (!stop) setNp(data) }
      } catch {}
      if (!stop) setTimeout(poll, 10000)
    }
    poll()
    return () => { stop = true }
  }, [])

  // Reset the "you're watching" state and reaction marker whenever the
  // segment changes underneath the user (new episode started).
  useEffect(() => { setReacted(null) }, [np?.segmentUrl])

  async function react(kind: 'up' | 'down') {
    setReacted(kind)
    // Real reaction on the episode's actual broadcast row would need its
    // id -- now-playing doesn't carry one (it's server-authoritative
    // playback state, not a broadcast lookup). Community sentiment on
    // individual episodes is still fully available by browsing to the
    // episode itself in Cinema's Agents tab (category "Agent.TV") and
    // reacting there, where the real broadcast id exists.
  }

  const offsetSec = np?.startedAt ? Math.max(0, (np.now - np.startedAt) / 1000) : 0
  const remainingSec = np ? Math.max(0, np.duration - offsetSec) : 0

  return (
    <div>
      <div className="glass" style={{ padding: 18, marginBottom: 24 }}>
        <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Tv size={16} /> Agent.TV — the always-on channel
        </h3>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14 }}>
          Real two-host AI podcast episodes, generated continuously, 24/7 — with a 30s house jingle between
          episodes while the next one renders. User-submitted podcasts air here too.
        </p>

        <div ref={videoBoxRef} style={{ borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}>
          <div style={{ position: 'relative', height: 320, background: '#000' }}>
            {!playing ? (
              <div
                onClick={() => setPlaying(true)}
                style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, cursor: 'pointer' }}
              >
                <div style={{ width: 64, height: 64, borderRadius: '50%', background: 'var(--purple-bright)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Play size={26} color="#000" fill="#000" style={{ marginLeft: 3 }} />
                </div>
                <div style={{ fontSize: 13, color: 'var(--muted-hi)', fontWeight: 600 }}>
                  {np?.phase === 'filler' ? 'On a short break — tap to tune in' : (np?.title || 'Loading now-playing…')}
                </div>
                {np && np.phase === 'segment' && (
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>~{Math.round(remainingSec / 60)} min left in this episode</div>
                )}
              </div>
            ) : np?.segmentUrl ? (
              <HlsAwareVideo src={`${np.segmentUrl}?t=${np.startedAt}`} />
            ) : null}
          </div>
          <div style={{ padding: '10px 14px', background: 'rgba(8,8,16,0.85)', fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#ff4d4d', flexShrink: 0, boxShadow: '0 0 6px #ff4d4d' }} />
              <span style={{ fontSize: 10, letterSpacing: '0.5px', color: '#ff4d4d', fontWeight: 700, flexShrink: 0 }}>LIVE</span>
              <strong style={{ color: 'var(--purple-bright)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{np?.title || '—'}</strong>
              {np?.phase === 'filler' && <span style={{ fontSize: 11, color: 'var(--muted)' }}>(jingle — next episode almost ready)</span>}
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button className="btn btn-ghost btn-sm" disabled={reacted !== null} onClick={() => react('up')}><ThumbsUp size={13} color={reacted === 'up' ? '#4ade80' : undefined} /></button>
              <button className="btn btn-ghost btn-sm" disabled={reacted !== null} onClick={() => react('down')}><ThumbsDown size={13} color={reacted === 'down' ? '#ff6b6b' : undefined} /></button>
            </div>
          </div>
        </div>
      </div>

      <div className="glass" style={{ padding: 16, borderRadius: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Mic size={13} /> Want your podcast to air here?
        </div>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
          Create one in Collab (audio or video) — video podcasts join Agent.TV's rotation automatically,
          ahead of freshly auto-generated episodes, and use the same house jingle.
        </p>
        <Link to="/video" className="btn btn-primary btn-sm"><Mic size={13} /> Create a Podcast in Collab</Link>
      </div>
    </div>
  )
}
