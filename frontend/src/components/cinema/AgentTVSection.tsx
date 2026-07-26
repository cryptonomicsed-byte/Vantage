import React, { useEffect, useRef, useState } from 'react'
import { Tv, Send, ThumbsUp, ThumbsDown } from 'lucide-react'

// AgentTV -- Vantage's window into Seemplify (Agent.TV2), a separate
// service. 2026-07-26: replaced the old Theta/Solana pilot-voting pipeline
// with a lean always-on ChannelLoop -- real DeepSeek-scripted, Piper-TTS
// narrated, ffmpeg-composited segments looping forever (3min segment, 30s
// filler while the next renders). The player below polls /now-playing and
// seeks to the server-authoritative offset so every viewer is in sync on
// the same "channel", the same way a real live broadcast would feel,
// without any actual broadcast/CDN infra behind it.
//
// Pilot submission + off-chain thumbs-up/down voting below are unchanged --
// kept as the existing simple community-feedback signal (no on-chain
// program, per the owner's lean-scope call).

const KEY = () => localStorage.getItem('vantage_api_key') || ''

interface NowPlaying {
  segmentUrl: string | null
  phase: 'segment' | 'filler'
  startedAt: number | null
  duration: number
  title: string | null
  now: number
}

function ChannelPlayer() {
  const [np, setNp] = useState<NowPlaying | null>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const lastUrl = useRef<string | null>(null)

  useEffect(() => {
    let stop = false
    async function poll() {
      try {
        const r = await fetch('/api/cinema/agenttv/now-playing')
        if (r.ok) {
          const data = await r.json()
          if (!stop) setNp(data)
        }
      } catch {}
      if (!stop) setTimeout(poll, 10000)
    }
    poll()
    return () => { stop = true }
  }, [])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !np?.segmentUrl) return
    const filename = np.segmentUrl.split('/').pop()
    const src = `/api/cinema/agenttv/media/${filename}`
    const offsetSec = np.startedAt ? Math.max(0, (np.now - np.startedAt) / 1000) : 0

    if (lastUrl.current !== src) {
      lastUrl.current = src
      video.src = src
      const seekWhenReady = () => {
        video.currentTime = offsetSec
        video.play().catch(() => {})
        video.removeEventListener('loadedmetadata', seekWhenReady)
      }
      video.addEventListener('loadedmetadata', seekWhenReady)
    } else if (Math.abs(video.currentTime - offsetSec) > 3) {
      // Drift correction -- keep every viewer roughly in sync with the
      // server's authoritative clock without a jarring reload.
      video.currentTime = offsetSec
    }
  }, [np])

  return (
    <div className="glass" style={{ padding: 18, marginBottom: 24 }}>
      <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Tv size={16} /> AgentTV — live now {np?.phase === 'filler' && <span style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 400 }}>(back in a moment)</span>}
      </h3>
      <video ref={videoRef} controls autoPlay muted style={{ width: '100%', maxHeight: 420, background: '#000', borderRadius: 8 }} />
      {np?.title && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8 }}>{np.title}</div>}
    </div>
  )
}

interface Proposal {
  id: string
  title: string
  status: string
  votes: { yes: number; no: number; abstain: number }
  yesPercent?: string
  passed?: boolean
}

interface Channel {
  id: string
  title: string
  active?: boolean
}

export default function AgentTVSection() {
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [channels, setChannels] = useState<Channel[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState('')

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [tone, setTone] = useState('casual')

  function load() {
    Promise.all([
      fetch('/api/cinema/agenttv/governance/proposals').then(r => r.ok ? r.json() : []),
      fetch('/api/cinema/agenttv/channels/featured?limit=10').then(r => r.ok ? r.json() : []),
    ])
      .then(([p, c]) => { setProposals(Array.isArray(p) ? p : []); setChannels(Array.isArray(c) ? c : []) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function submitPilot() {
    if (!title.trim() || !description.trim()) return
    setSubmitting(true)
    setNotice('')
    try {
      const r = await fetch('/api/cinema/agenttv/pilots/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': KEY() },
        body: JSON.stringify({ title, description, tone, duration: 300, tags: [] }),
      })
      const data = await r.json()
      if (!r.ok) { setNotice(data.detail || 'Submission failed'); return }
      setNotice(`Submitted "${title}" — now in the pipeline.`)
      setTitle(''); setDescription('')
      load()
    } catch {
      setNotice('Network error reaching AgentTV.')
    } finally {
      setSubmitting(false)
    }
  }

  async function vote(proposalId: string, choice: 'yes' | 'no') {
    try {
      await fetch('/api/cinema/agenttv/governance/vote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': KEY() },
        body: JSON.stringify({ proposalId, voterTokenBalance: 100, voteChoice: choice }),
      })
      load()
    } catch {}
  }

  if (loading) return <div className="cin-empty">Loading AgentTV…</div>

  return (
    <div>
      <ChannelPlayer />
      <div className="glass" style={{ padding: 18, marginBottom: 24 }}>
        <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Tv size={16} /> Submit a pilot
        </h3>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
          Agents research, script, generate, and stream it automatically — the community votes to greenlight.
        </p>
        <input placeholder="Show title" value={title} onChange={e => setTitle(e.target.value)}
               style={{ display: 'block', width: '100%', marginBottom: 8, padding: '8px 10px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 13 }} />
        <textarea placeholder="What's this show about?" value={description} onChange={e => setDescription(e.target.value)}
                  style={{ display: 'block', width: '100%', marginBottom: 8, padding: '8px 10px', minHeight: 60, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 13 }} />
        <select value={tone} onChange={e => setTone(e.target.value)}
                style={{ marginBottom: 10, padding: '6px 10px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 13 }}>
          <option value="casual">Casual</option>
          <option value="serious">Serious</option>
          <option value="comedic">Comedic</option>
        </select>
        {notice && <p style={{ fontSize: 12, color: 'var(--cyan)' }}>{notice}</p>}
        <div>
          <button className="btn btn-primary btn-sm" disabled={submitting || !title.trim()} onClick={submitPilot}>
            <Send size={12} /> Submit pilot
          </button>
        </div>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>Governance — vote on pilots</h3>
      {proposals.length === 0 ? (
        <div className="cin-empty" style={{ marginBottom: 24 }}>No proposals up for vote right now.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 24 }}>
          {proposals.map(p => (
            <div key={p.id} className="glass" style={{ padding: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{p.title}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                  {p.votes?.yes ?? 0} yes / {p.votes?.no ?? 0} no · {p.status}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => vote(p.id, 'yes')}><ThumbsUp size={12} /></button>
                <button className="btn btn-ghost btn-sm" onClick={() => vote(p.id, 'no')}><ThumbsDown size={12} /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>Live channels</h3>
      {channels.length === 0 ? (
        <div className="cin-empty">No channels deployed yet.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
          {channels.map(c => (
            <div key={c.id} className="glass" style={{ padding: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{c.title}</div>
              <div style={{ fontSize: 11, color: c.active ? 'var(--cyan)' : 'var(--muted)' }}>{c.active ? 'Active' : 'Paused'}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
