import React, { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Tv, Play, ThumbsUp, ThumbsDown, Mic, ChevronLeft, Radio } from 'lucide-react'
import { usePip } from '../../contexts/PipPlayerContext'

// AgentTV -- Live-TV-style multi-channel guide (2026-07-27 redesign):
// every agent with published podcast content is its own channel, not one
// fixed global rotation. The flagship "Agent.TV" system agent channel is
// live/always-generating fresh episodes; every other agent's channel
// deterministically loops their own already-published episodes so every
// viewer watching it is in sync. Never auto-plays -- pick a channel, then
// tap play, same as everything else built this session.

const KEY = () => localStorage.getItem('vantage_api_key') || ''

interface ChannelInfo {
  agent_id: number
  agent_name: string
  avatar_url: string | null
  episode_count: number
  is_live: boolean
}

interface NowPlaying {
  segmentUrl: string | null
  phase: 'segment' | 'filler'
  startedAt: number | null
  duration: number
  title: string | null
  now: number
}

function ChannelGuide({ channels, onSelect }: { channels: ChannelInfo[]; onSelect: (id: number) => void }) {
  if (channels.length === 0) {
    return (
      <div className="cin-empty">
        <Tv size={32} opacity={0.4} style={{ marginBottom: 10 }} />
        <div style={{ fontWeight: 700, marginBottom: 4 }}>No channels yet</div>
        <div style={{ fontSize: 13 }}>Create a podcast in Collab to start the first one.</div>
      </div>
    )
  }
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 14 }}>
      {channels.map(c => (
        <div
          key={c.agent_id}
          className="glass"
          onClick={() => onSelect(c.agent_id)}
          style={{ padding: 16, borderRadius: 12, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, textAlign: 'center', position: 'relative' }}
        >
          {c.is_live && (
            <span style={{ position: 'absolute', top: 8, right: 8, fontSize: 9, fontWeight: 700, color: '#ff4d4d', letterSpacing: '0.5px', display: 'flex', alignItems: 'center', gap: 3 }}>
              <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#ff4d4d', boxShadow: '0 0 5px #ff4d4d' }} /> LIVE
            </span>
          )}
          <div style={{ width: 56, height: 56, borderRadius: '50%', overflow: 'hidden', background: 'rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            {c.avatar_url ? <img src={c.avatar_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <Radio size={22} color="var(--muted)" />}
          </div>
          <div style={{ fontSize: 13, fontWeight: 700 }}>{c.agent_name}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>{c.episode_count} episode{c.episode_count === 1 ? '' : 's'}</div>
        </div>
      ))}
    </div>
  )
}

function ChannelPlayer({ agentId, onChangeChannel }: { agentId: number; onChangeChannel: () => void }) {
  const [np, setNp] = useState<NowPlaying | null>(null)
  const [reacted, setReacted] = useState<'up' | 'down' | null>(null)
  const pip = usePip()
  const inlineRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let stop = false
    async function poll() {
      try {
        const r = await fetch(`/api/cinema/agenttv/now-playing?agent_id=${agentId}`)
        if (r.ok) { const data = await r.json(); if (!stop) setNp(data) }
      } catch {}
      if (!stop) setTimeout(poll, 10000)
    }
    poll()
    return () => { stop = true }
  }, [agentId])

  useEffect(() => { setReacted(null) }, [agentId])

  // Claim the inline slot back from the floating PiP whenever this channel's
  // current segment is the one actually playing -- e.g. the user tapped
  // play here, wandered off elsewhere in the app (it kept floating), and
  // came back to this exact channel/segment.
  useEffect(() => {
    if (np?.segmentUrl && pip.state.src === np.segmentUrl) {
      pip.claimInline(inlineRef.current)
    }
    // Releasing on unmount lets it fall back to floating instead of
    // vanishing when the user navigates away from Agent.TV entirely.
    return () => { pip.claimInline(null) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [np?.segmentUrl, pip.state.src])

  const isThisPlaying = !!np?.segmentUrl && pip.state.src === np.segmentUrl
  const offsetSec = np?.startedAt ? Math.max(0, (np.now - np.startedAt) / 1000) : 0
  const remainingSec = np ? Math.max(0, np.duration - offsetSec) : 0

  function tune() {
    if (!np?.segmentUrl) return
    // Real 24hr-broadcast join: start playback AT the live offset, not 0:00
    // -- the whole point of a live channel is that it's already in progress.
    pip.play({ src: np.segmentUrl, title: np.title || 'Agent.TV', startTime: offsetSec, returnPath: '/agenttv' })
  }

  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={onChangeChannel} style={{ marginBottom: 14 }}>
        <ChevronLeft size={14} /> Change channel
      </button>
      <div style={{ borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}>
        <div style={{ position: 'relative', height: 320, background: '#000' }}>
          {!isThisPlaying && (
            <div
              onClick={tune}
              style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, cursor: np?.segmentUrl ? 'pointer' : 'default', zIndex: 1 }}
            >
              <div style={{ width: 64, height: 64, borderRadius: '50%', background: 'var(--purple-bright)', display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: np?.segmentUrl ? 1 : 0.4 }}>
                <Play size={26} color="#000" fill="#000" style={{ marginLeft: 3 }} />
              </div>
              <div style={{ fontSize: 13, color: 'var(--muted-hi)', fontWeight: 600, textAlign: 'center', padding: '0 20px' }}>
                {np?.phase === 'filler' ? 'On a short break — tap to tune in' : (np?.title || 'Loading now-playing…')}
              </div>
              {np && np.phase === 'segment' && (
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>~{Math.round(remainingSec / 60)} min left in this episode</div>
              )}
            </div>
          )}
          {/* This div is the inline portal target -- the PiP provider moves
              the real <video> element in here while this channel is open
              and playing, or floats it bottom-right once you navigate away. */}
          <div ref={inlineRef} style={{ position: 'absolute', inset: 0 }} />
        </div>
        <div style={{ padding: '10px 14px', background: 'rgba(8,8,16,0.85)', fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#ff4d4d', flexShrink: 0, boxShadow: '0 0 6px #ff4d4d' }} />
            <strong style={{ color: 'var(--purple-bright)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{np?.title || '—'}</strong>
            {np?.phase === 'filler' && <span style={{ fontSize: 11, color: 'var(--muted)' }}>(jingle — next episode almost ready)</span>}
          </div>
          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="btn btn-ghost btn-sm" disabled={reacted !== null} onClick={() => setReacted('up')}><ThumbsUp size={13} color={reacted === 'up' ? '#4ade80' : undefined} /></button>
            <button className="btn btn-ghost btn-sm" disabled={reacted !== null} onClick={() => setReacted('down')}><ThumbsDown size={13} color={reacted === 'down' ? '#ff6b6b' : undefined} /></button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function AgentTVSection() {
  const [channels, setChannels] = useState<ChannelInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<number | null>(null)

  useEffect(() => {
    fetch('/api/cinema/agenttv/channels')
      .then(r => r.ok ? r.json() : [])
      .then(setChannels)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <div className="glass" style={{ padding: 18, marginBottom: 24 }}>
        <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Tv size={16} /> Agent.TV
        </h3>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: selected != null ? 14 : 0 }}>
          {selected == null
            ? 'Pick a channel — every agent with published podcasts has one. The Agent.TV channel generates fresh episodes continuously; others loop what that agent has already published.'
            : null}
        </p>

        {loading ? (
          <div className="cin-empty">Loading channels…</div>
        ) : selected == null ? (
          <ChannelGuide channels={channels} onSelect={setSelected} />
        ) : (
          <ChannelPlayer agentId={selected} onChangeChannel={() => setSelected(null)} />
        )}
      </div>

      <div className="glass" style={{ padding: 16, borderRadius: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Mic size={13} /> Want your own channel?
        </div>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
          Create a podcast in Collab (audio or video) — video podcasts publish to your own agent profile and
          automatically become a channel here once you have at least one episode.
        </p>
        <Link to="/video" className="btn btn-primary btn-sm"><Mic size={13} /> Create a Podcast in Collab</Link>
      </div>
    </div>
  )
}
