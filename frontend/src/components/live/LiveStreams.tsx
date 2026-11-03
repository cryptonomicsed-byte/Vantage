import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Radio, X, Play, Users, Eye, Video, Send, Link2 } from 'lucide-react'
import Hls from 'hls.js'

/* ── Live streaming (NIP-53 / Amethyst / Twitter style) ───────────────────
   Users "go live" two ways:
     • Camera — browser WebRTC (WHIP) → mediamtx → HLS → viewers (hls.js)
     • Stream URL — announce an existing HLS URL (Amethyst/zap.stream model)
   Active streams appear in a horizontal "Live now" row at the top of the feed. */

export interface LiveStream {
  id: number
  agent_id: number
  agent_name: string | null
  avatar_url: string
  d_tag: string
  title: string
  summary: string
  stream_url: string
  image_url: string
  status: string
  viewer_count: number
  started_at: string
  ended_at: string | null
}

interface ChatMessage {
  id: number
  stream_id: number
  agent_id: number
  agent_name: string
  content: string
  created_at: string
}

const STYLE_ID = 'live-streams-styles'
const CSS = `
.ls-liverow{display:flex;gap:12px;overflow-x:auto;padding:14px 20px;scroll-snap-type:x mandatory;scrollbar-width:none}
.ls-liverow::-webkit-scrollbar{display:none}
.ls-livehead{display:flex;align-items:center;gap:8px;padding:16px 20px 0}
.ls-bubble{flex-shrink:0;width:220px;cursor:pointer;border-radius:14px;overflow:hidden;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.02);transition:transform .2s,border-color .2s;scroll-snap-align:start;position:relative}
.ls-bubble:hover{transform:translateY(-3px);border-color:rgba(255,255,255,.18)}
.ls-bubble-cover{position:relative;aspect-ratio:16/10;background:linear-gradient(135deg,#241a4a,#05040d);overflow:hidden}
.ls-bubble-cover img{width:100%;height:100%;object-fit:cover;display:block}
.ls-bubble-cover::after{content:'';position:absolute;inset:0;background:linear-gradient(to top,rgba(0,0,0,.7),transparent 55%)}
.ls-livebadge{position:absolute;top:8px;left:8px;z-index:2;background:rgba(239,68,68,.94);padding:3px 8px;border-radius:5px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;display:flex;align-items:center;gap:5px}
.ls-livebadge .dot{width:6px;height:6px;border-radius:50%;background:#fff;animation:lsPulse 1.2s ease-in-out infinite}
@keyframes lsPulse{0%,100%{opacity:1}50%{opacity:.3}}
.ls-viewers{position:absolute;bottom:8px;right:8px;z-index:2;display:flex;align-items:center;gap:4px;font-size:11px;font-weight:700;color:#fff;background:rgba(0,0,0,.55);padding:3px 7px;border-radius:99px}
.ls-bubble-body{padding:10px 12px}
.ls-bubble-title{font-size:13px;font-weight:700;color:rgba(255,255,255,.92);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ls-bubble-agent{font-size:11.5px;color:rgba(255,255,255,.45);margin-top:2px}
.ls-modal{position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.85);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;padding:24px}
.ls-panel{position:relative;width:100%;max-width:720px;max-height:90vh;overflow-y:auto;background:#0a0b16;border:1px solid rgba(255,255,255,.08);border-radius:16px}
.ls-field{margin-bottom:14px}
.ls-label{display:block;font-size:12px;font-weight:600;color:rgba(255,255,255,.55);margin-bottom:6px}
.ls-input{width:100%;box-sizing:border-box;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:11px 13px;font-size:14px;color:#fff;outline:none;transition:border-color .15s}
.ls-input:focus{border-color:rgba(139,92,246,.6)}
.ls-input::placeholder{color:rgba(255,255,255,.28)}
.ls-hint{font-size:11.5px;color:rgba(255,255,255,.38);margin-top:6px;line-height:1.45}
.ls-golive{display:inline-flex;align-items:center;gap:8px;padding:11px 18px;border-radius:99px;font-size:14px;font-weight:700;color:#fff;background:linear-gradient(135deg,#ef4444,#8b5cf6);border:none;cursor:pointer;transition:transform .15s,box-shadow .15s}
.ls-golive:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(239,68,68,.35)}
.ls-golive:disabled{opacity:.5;cursor:not-allowed}
.ls-modes{display:flex;gap:6px;margin-bottom:16px}
.ls-mode{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:10px;border-radius:10px;font-size:13px;font-weight:600;color:rgba(255,255,255,.5);background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);cursor:pointer;transition:all .15s}
.ls-mode.active{color:#fff;background:rgba(139,92,246,.18);border-color:rgba(139,92,246,.4)}
.ls-camwrap{position:relative;aspect-ratio:16/9;border-radius:12px;overflow:hidden;background:#000;margin-bottom:14px}
.ls-camwrap video{width:100%;height:100%;object-fit:cover;display:block;transform:scaleX(-1)}
.ls-player-wrap{position:relative;width:100%;aspect-ratio:16/9;background:#000}
.ls-player-wrap video{width:100%;height:100%;display:block;background:#000}
/* chat */
.ls-chat{display:flex;flex-direction:column;height:260px;border-top:1px solid rgba(255,255,255,.06)}
.ls-chat-msgs{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:8px}
.ls-chat-msg{font-size:13px;line-height:1.4}
.ls-chat-name{font-weight:700;color:rgba(255,255,255,.75);margin-right:6px}
.ls-chat-text{color:rgba(255,255,255,.85)}
.ls-chat-input{display:flex;gap:8px;padding:10px 12px;border-top:1px solid rgba(255,255,255,.06)}
.ls-chat-input input{flex:1;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:99px;padding:9px 14px;font-size:13px;color:#fff;outline:none}
.ls-chat-input button{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,#8b5cf6,#06b6d4);border:none;color:#fff;cursor:pointer;flex-shrink:0}
.ls-chat-input button:disabled{opacity:.4;cursor:not-allowed}
`

function timeAgo(d: string): string {
  const diff = Date.now() - new Date(d + (d.includes('Z') ? '' : 'Z')).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`
}

function apiKey(): string { return localStorage.getItem('vantage_api_key') || '' }

/* ── HLS player ─────────────────────────────────────────────────────────── */
function HlsVideo({ src }: { src: string }) {
  const ref = useRef<HTMLVideoElement | null>(null)
  useEffect(() => {
    const video = ref.current
    if (!video) return
    if (Hls.isSupported()) {
      const hls = new Hls({ liveSyncDurationCount: 3 })
      hls.loadSource(src)
      hls.attachMedia(video)
      video.play().catch(() => {})
      return () => hls.destroy()
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = src
      video.play().catch(() => {})
    }
  }, [src])
  return <video ref={ref} controls autoPlay playsInline />
}

/* ── WHIP (WebRTC ingest) publisher — module-scoped so it survives modal close ── */
let activePeer: RTCPeerConnection | null = null
let activeStream: MediaStream | null = null

function waitForIceComplete(pc: RTCPeerConnection): Promise<void> {
  return new Promise(resolve => {
    if (pc.iceGatheringState === 'complete') return resolve()
    const done = () => { if (pc.iceGatheringState === 'complete') resolve() }
    pc.addEventListener('icegatheringstatechange', done)
    setTimeout(() => { pc.removeEventListener('icegatheringstatechange', done); resolve() }, 3000)
  })
}

export async function startCameraPublish(whipUrl: string, stream: MediaStream): Promise<void> {
  const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] })
  stream.getTracks().forEach(t => pc.addTrack(t, stream))
  const offer = await pc.createOffer()
  await pc.setLocalDescription(offer)
  await waitForIceComplete(pc)
  const sdp = pc.localDescription?.sdp
  if (!sdp) throw new Error('no local SDP')
  const res = await fetch(whipUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/sdp' },
    body: sdp,
  })
  if (!res.ok) throw new Error(`WHIP failed (${res.status})`)
  const answerSdp = await res.text()
  await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp })
  activePeer = pc
  activeStream = stream
}

export function stopCameraPublish(): void {
  try { activePeer?.close() } catch { /* noop */ }
  activeStream?.getTracks().forEach(t => t.stop())
  activePeer = null
  activeStream = null
}

/* ── Live "now" row ─────────────────────────────────────────────────────── */
export function useLiveStreams(pollMs = 15000) {
  const [streams, setStreams] = useState<LiveStream[]>([])
  useEffect(() => {
    let alive = true
    function load() {
      fetch('/api/live/active')
        .then(r => r.json())
        .then(d => { if (alive) setStreams(Array.isArray(d.streams) ? d.streams : []) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, pollMs)
    return () => { alive = false; clearInterval(t) }
  }, [pollMs])
  return streams
}

export function LiveNowRow({ streams, onJoin }: { streams: LiveStream[]; onJoin: (s: LiveStream) => void }) {
  useEffect(() => {
    if (document.getElementById(STYLE_ID)) return
    const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = CSS
    document.head.appendChild(el)
  }, [])

  if (streams.length === 0) return null

  return (
    <div>
      <div className="ls-livehead">
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.06em', color: '#ef4444' }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#ef4444', boxShadow: '0 0 8px #ef4444', animation: 'lsPulse 1.2s ease-in-out infinite' }} />
          Live now
        </span>
        <span style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>{streams.length} streaming</span>
      </div>
      <div className="ls-liverow">
        {streams.map(s => (
          <div key={s.id} className="ls-bubble" onClick={() => onJoin(s)}>
            <div className="ls-bubble-cover">
              {s.image_url ? <img src={s.image_url} alt={s.title} loading="lazy" />
                : <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Radio size={26} opacity={0.6} /></div>}
              <span className="ls-livebadge"><span className="dot" /> Live</span>
              <span className="ls-viewers"><Users size={11} /> {s.viewer_count || 0}</span>
            </div>
            <div className="ls-bubble-body">
              <div className="ls-bubble-title">{s.title}</div>
              <div className="ls-bubble-agent">{s.agent_name || 'Agent'} · {timeAgo(s.started_at)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── Go Live modal ──────────────────────────────────────────────────────── */
export function GoLiveModal({ onClose, onStarted }: { onClose: () => void; onStarted: (s: LiveStream) => void }) {
  const [mode, setMode] = useState<'camera' | 'url'>('camera')
  const [title, setTitle] = useState('')
  const [streamUrl, setStreamUrl] = useState('')
  const [summary, setSummary] = useState('')
  const [imageUrl, setImageUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [camError, setCamError] = useState('')

  const camVideoRef = useRef<HTMLVideoElement | null>(null)
  const camStreamRef = useRef<MediaStream | null>(null)

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h); document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', h); document.body.style.overflow = '' }
  }, [onClose])

  // Acquire camera for preview (camera mode only)
  useEffect(() => {
    if (mode !== 'camera') return
    let cancelled = false
    navigator.mediaDevices?.getUserMedia({ video: true, audio: true })
      .then(stream => {
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
        camStreamRef.current = stream
        if (camVideoRef.current) camVideoRef.current.srcObject = stream
      })
      .catch(() => setCamError('Camera/mic unavailable — switch to Stream URL mode.'))
    return () => {
      cancelled = true
      // Only stop the preview stream if it is not actively publishing.
      if (camStreamRef.current && camStreamRef.current !== activeStream) {
        camStreamRef.current.getTracks().forEach(t => t.stop())
      }
      camStreamRef.current = null
    }
  }, [mode])

  async function goLive() {
    if (!title.trim()) { setError('Title is required.'); return }
    setBusy(true); setError('')
    try {
      if (mode === 'camera') {
        if (!camStreamRef.current) { setError('Camera not ready.'); return }
        // 1. announce (camera mode → get whip_url + stream)
        const res = await fetch('/api/live/start', {
          method: 'POST',
          headers: { 'X-Agent-Key': apiKey(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: title.trim(), camera: true, summary: summary.trim(), image_url: imageUrl.trim() }),
        })
        const data = await res.json()
        if (!res.ok) { setError(data.detail || 'Failed to go live'); return }
        // 2. publish camera → mediamtx via WHIP
        try {
          await startCameraPublish(data.whip_url, camStreamRef.current)
        } catch (e) {
          // stop the announce on publish failure
          await fetch('/api/live/end', {
            method: 'POST',
            headers: { 'X-Agent-Key': apiKey(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ stream_id: data.stream.id }),
          }).catch(() => {})
          setError('Could not start camera broadcast. Check permissions/network and try again.')
          return
        }
        if (data.stream) onStarted(data.stream)
      } else {
        if (!streamUrl.trim()) { setError('Stream URL is required.'); return }
        const res = await fetch('/api/live/start', {
          method: 'POST',
          headers: { 'X-Agent-Key': apiKey(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: title.trim(), stream_url: streamUrl.trim(), summary: summary.trim(), image_url: imageUrl.trim() }),
        })
        const data = await res.json()
        if (!res.ok) { setError(data.detail || 'Failed to go live'); return }
        if (data.stream) onStarted(data.stream)
      }
      onClose()
    } catch { setError('Network error — try again.') } finally { setBusy(false) }
  }

  return (
    <div className="ls-modal" onClick={onClose}>
      <div className="ls-panel" onClick={e => e.stopPropagation()}>
        <div style={{ padding: 22 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: '#ef4444', boxShadow: '0 0 8px #ef4444' }} />
              <h2 style={{ fontSize: 17, fontWeight: 700, margin: 0 }}>Go Live</h2>
            </div>
            <button onClick={onClose} style={{ background: 'rgba(0,0,0,.6)', border: 'none', borderRadius: 8, padding: 8, cursor: 'pointer', color: '#fff' }}><X size={18} /></button>
          </div>

          <div className="ls-modes">
            <button className={`ls-mode ${mode === 'camera' ? 'active' : ''}`} onClick={() => setMode('camera')}><Video size={15} /> Camera</button>
            <button className={`ls-mode ${mode === 'url' ? 'active' : ''}`} onClick={() => setMode('url')}><Link2 size={15} /> Stream URL</button>
          </div>

          <div className="ls-field">
            <label className="ls-label">Stream title</label>
            <input className="ls-input" value={title} onChange={e => setTitle(e.target.value)} placeholder="What are you streaming?" maxLength={200} />
          </div>

          {mode === 'camera' && (
            <div className="ls-camwrap">
              <video ref={camVideoRef} autoPlay playsInline muted />
              {camError && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ff7b7b', fontSize: 13, padding: 20, textAlign: 'center' }}>{camError}</div>}
            </div>
          )}

          {mode === 'url' && (
            <div className="ls-field">
              <label className="ls-label">HLS stream URL (.m3u8)</label>
              <input className="ls-input" value={streamUrl} onChange={e => setStreamUrl(e.target.value)} placeholder="https://…/stream.m3u8" />
              <div className="ls-hint">Announce an existing stream (OBS → zap.stream, nginx-rtmp, etc.). Viewers join from the feed.</div>
            </div>
          )}

          <div className="ls-field">
            <label className="ls-label">Cover image URL (optional)</label>
            <input className="ls-input" value={imageUrl} onChange={e => setImageUrl(e.target.value)} placeholder="https://…/cover.jpg" />
          </div>
          <div className="ls-field">
            <label className="ls-label">Summary (optional)</label>
            <input className="ls-input" value={summary} onChange={e => setSummary(e.target.value)} placeholder="One-line description" maxLength={2000} />
          </div>

          {error && <div style={{ color: '#ff7b7b', fontSize: 13, marginBottom: 12 }}>{error}</div>}

          <button className="ls-golive" onClick={goLive} disabled={busy}>
            <Radio size={16} /> {busy ? 'Going live…' : 'Go Live'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── Live chat hook ─────────────────────────────────────────────────────── */
function useLiveChat(streamId: number | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  useEffect(() => {
    if (!streamId) { setMessages([]); return }
    let alive = true
    function load() {
      fetch(`/api/live/${streamId}/chat`)
        .then(r => r.json())
        .then(d => { if (alive) setMessages(Array.isArray(d.messages) ? d.messages : []) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 2500)
    return () => { alive = false; clearInterval(t) }
  }, [streamId])
  return messages
}

/* ── Live viewer (join) ─────────────────────────────────────────────────── */
export function LiveViewer({ stream, onClose, isMine, onEnd }: {
  stream: LiveStream; onClose: () => void; isMine: boolean; onEnd: (s: LiveStream) => void
}) {
  const messages = useLiveChat(stream.id)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const chatRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h); document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', h); document.body.style.overflow = '' }
  }, [onClose])

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight
  }, [messages.length])

  async function send() {
    const content = draft.trim()
    if (!content || sending) return
    setSending(true)
    try {
      await fetch(`/api/live/${stream.id}/chat`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      setDraft('')
    } catch { /* noop */ } finally { setSending(false) }
  }

  return (
    <div className="ls-modal" onClick={onClose}>
      <div className="ls-panel" style={{ maxWidth: 920 }} onClick={e => e.stopPropagation()}>
        <div className="ls-player-wrap">
          {stream.stream_url
            ? <HlsVideo src={stream.stream_url} />
            : <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,.4)' }}>No stream URL attached.</div>}
          <span className="ls-livebadge" style={{ top: 12, left: 12 }}><span className="dot" /> Live</span>
          <button onClick={onClose} style={{ position: 'absolute', top: 12, right: 12, zIndex: 3, background: 'rgba(0,0,0,.6)', border: 'none', borderRadius: 8, padding: 8, cursor: 'pointer', color: '#fff' }}><X size={18} /></button>
        </div>
        <div style={{ padding: '12px 18px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{stream.title}</div>
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,.45)', marginTop: 3, display: 'flex', alignItems: 'center', gap: 12 }}>
              <span>{stream.agent_name || 'Agent'}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Eye size={12} /> {stream.viewer_count || 0} watching</span>
              <span>started {timeAgo(stream.started_at)}</span>
            </div>
          </div>
          {isMine && (
            <button className="ls-golive" style={{ background: 'rgba(255,255,255,.08)', flexShrink: 0 }} onClick={() => onEnd(stream)}>
              End stream
            </button>
          )}
        </div>

        {/* Live chat */}
        <div className="ls-chat">
          <div className="ls-chat-msgs" ref={chatRef}>
            {messages.length === 0 && <div style={{ fontSize: 12, color: 'rgba(255,255,255,.3)' }}>No messages yet — say hi.</div>}
            {messages.map(m => (
              <div key={m.id} className="ls-chat-msg">
                <span className="ls-chat-name">{m.agent_name}</span>
                <span className="ls-chat-text">{m.content}</span>
              </div>
            ))}
          </div>
          <div className="ls-chat-input">
            <input
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') send() }}
              placeholder="Send a message…"
              maxLength={1000}
            />
            <button onClick={send} disabled={sending || !draft.trim()}><Send size={16} /></button>
          </div>
        </div>
      </div>
    </div>
  )
}
