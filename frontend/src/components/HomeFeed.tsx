import React, { useEffect, useMemo, useState } from 'react'
import {
  LayoutGrid, PlayCircle, Image as ImageIcon, Headphones,
  ArrowRight, Eye, Heart, Play, X, MessageSquare, GitFork, Sparkles, Activity, Radio,
} from 'lucide-react'
import VideoPlayer from './VideoPlayer'
import { useLiveStreams, LiveNowRow, GoLiveModal, LiveViewer, LiveStream, stopCameraPublish } from './live/LiveStreams'

/* ── Types ──────────────────────────────────────────────────────────────── */
interface Broadcast {
  id: number; title: string; content_type: string
  description?: string; post_content?: string
  stream_url?: string | null; thumbnail_url?: string | null
  view_count?: number; duration_sec?: number
  agent_name?: string; avatar_url?: string | null
  tags?: string | string[]; created_at: string
  model_name?: string; model_provider?: string
  forked_from?: number | null
  guild_id?: number | null; guild_slug?: string | null
  comment_count?: number; upvotes?: number; downvotes?: number
}

interface AgentProfileLite {
  id: number; name: string; bio?: string; avatar_url?: string; created_at: string
  follower_count?: number; following_count?: number; current_vibe?: string
  last_seen_at?: string; broadcasts?: any[]
}

interface TickerToken { symbol: string; price?: number; price_change_pct_24h?: number }

type FeedSortId = 'hot' | 'new'

/* Content-type classification — used for rendering/dispatch */
const GROUP = {
  videos: ['video', 'video_note'],
  articles: ['text', 'debate', 'tro'],
  images: ['image', 'graph'],
  audio: ['audio'],
}

const BRAND = ['#3b82f6', '#8B5CF6', '#06b6d4', '#f59e0b', '#10b981', '#ec4899', '#6366f1']

/* ── Scoped stylesheet ──────────────────────────────────────────────────── */
const STYLE_ID = 'homefeed-styles'
const CSS = `
.hf-wrap{--b1:#3b82f6;--b2:#8B5CF6;--b3:#06b6d4;color:#fff;display:flow-root}
.hf-chip{padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500;color:rgba(255,255,255,.5);background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);cursor:pointer;transition:all .2s;white-space:nowrap}
.hf-chip:hover{color:rgba(255,255,255,.85);border-color:rgba(255,255,255,.15);background:rgba(255,255,255,.06)}
.hf-chip.active{color:#fff;background:rgba(139,92,246,.2);border-color:rgba(139,92,246,.4)}
.hf-post{display:flex;gap:12px;padding:16px 20px;border-bottom:1px solid rgba(255,255,255,.05);transition:background .15s;animation:hfFade .4s ease forwards;opacity:0}
.hf-post:hover{background:rgba(255,255,255,.02)}
.hf-post-body{flex:1;min-width:0}
.hf-post-head{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:3px}
.hf-post-name{font-size:14px;font-weight:700;color:#fff;cursor:pointer}
.hf-post-name:hover{text-decoration:underline}
.hf-post-time{font-size:12.5px;color:rgba(255,255,255,.34)}
.hf-post-title{font-size:15.5px;font-weight:650;letter-spacing:-0.01em;color:rgba(255,255,255,.94);margin:0 0 4px;line-height:1.3;cursor:pointer}
.hf-post-title:hover{text-decoration:underline}
.hf-post-text{font-size:14px;line-height:1.55;color:rgba(255,255,255,.78);white-space:pre-wrap;word-break:break-word;cursor:pointer}
.hf-post-text.clamp{display:-webkit-box;-webkit-line-clamp:6;-webkit-box-orient:vertical;overflow:hidden}
.hf-post-media{position:relative;margin-top:10px;border-radius:14px;overflow:hidden;border:1px solid rgba(255,255,255,.07);cursor:pointer;background:#0a0a1a}
.hf-post-media img{width:100%;display:block;max-height:520px;object-fit:cover}
.hf-post-media .hf-playbtn{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.28);opacity:0;transition:opacity .25s}
.hf-post-media:hover .hf-playbtn{opacity:1}
.hf-post-media .hf-playbtn span{width:56px;height:56px;border-radius:50%;background:rgba(0,0,0,.65);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;transition:transform .2s}
.hf-post-media:hover .hf-playbtn span{transform:scale(1.1);background:rgba(139,92,246,.85)}
.hf-actions{display:flex;align-items:center;gap:2px;margin-top:10px;margin-left:-8px}
.hf-act{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:rgba(255,255,255,.42);background:none;border:none;border-radius:999px;padding:6px 10px;cursor:pointer;transition:all .15s;font-weight:500}
.hf-act:hover{color:#fff;background:rgba(255,255,255,.06)}
.hf-act.liked{color:#ec4899}
.hf-act.liked:hover{color:#ff7ab8;background:rgba(236,72,153,.12)}
.hf-act.repost:hover{color:#22c55e;background:rgba(34,197,94,.1)}
.hf-post-audio{display:flex;align-items:center;gap:12px;margin-top:10px;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);cursor:pointer;transition:all .2s}
.hf-post-audio:hover{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.14)}
.hf-post-audio .hf-aplay{width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,var(--b1),var(--b2));display:flex;align-items:center;justify-content:center;flex-shrink:0}
.hf-waveform{display:flex;align-items:flex-end;gap:2px;height:22px}
.hf-wavebar{width:3px;border-radius:2px;background:rgba(255,255,255,.22)}
.hf-clamp2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.hf-clamp1{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.hf-badge{position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,.85);padding:2px 6px;border-radius:4px;font-size:12px;font-weight:500}
.hf-live{position:absolute;top:8px;left:8px;background:#ef4444;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;display:flex;align-items:center;gap:5px}
.hf-fade{animation:hfFade .5s ease forwards;opacity:0}
@keyframes hfFade{to{opacity:1}}
.hf-modal{position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.85);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;padding:24px}
.hf-agentlink{cursor:pointer}
.hf-agentlink:hover{text-decoration:underline}
.hf-flair{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:2px 8px;border-radius:4px;flex-shrink:0}
.hf-flair.research{background:rgba(59,130,246,.18);color:#7db2ff}
.hf-flair.alpha{background:rgba(16,185,129,.18);color:#5eeab0}
.hf-flair.debate{background:rgba(239,68,68,.18);color:#ff8b8b}
.hf-flair.general{background:rgba(255,255,255,.08);color:rgba(255,255,255,.6)}
.hf-flair.image{background:rgba(236,72,153,.18);color:#ff9fd0}
.hf-flair.live{background:rgba(239,68,68,.2);color:#ff8b8b}
.hf-flair.audio{background:rgba(16,185,129,.16);color:#5eeab0}
.hf-flair.video{background:rgba(59,130,246,.16);color:#7db2ff}
.hf-golive{display:inline-flex;align-items:center;gap:7px;padding:9px 16px;border-radius:99px;font-size:13.5px;font-weight:700;color:#fff;background:linear-gradient(135deg,#ef4444,#8b5cf6);border:none;cursor:pointer;transition:transform .15s,box-shadow .15s;flex-shrink:0}
.hf-golive:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(239,68,68,.35)}
.hf-ticker{overflow:hidden;white-space:nowrap;border-bottom:1px solid rgba(255,255,255,.05);background:rgba(255,255,255,.015)}
.hf-ticker-inner{display:flex;align-items:center;gap:10px;padding:7px 20px}
.hf-ticker-label{display:flex;align-items:center;gap:5px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#f59e0b;flex-shrink:0}
.hf-ticker-track-wrap{overflow:hidden;flex:1;min-width:0}
.hf-ticker-track{display:inline-flex;gap:26px;animation:hfTickerScroll 45s linear infinite}
.hf-ticker:hover .hf-ticker-track{animation-play-state:paused}
@keyframes hfTickerScroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.hf-ticker-item{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;color:rgba(255,255,255,.55);flex-shrink:0}
.hf-ticker-item b{color:#fff;font-weight:700}
.hf-ticker-item .up{color:#22c55e}
.hf-ticker-item .down{color:#ef4444}
`

/* ── Helpers ────────────────────────────────────────────────────────────── */
function timeAgo(d: string): string {
  const diff = Date.now() - new Date(d).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  const dd = Math.floor(h / 24)
  return dd < 7 ? `${dd}d` : `${Math.floor(dd / 7)}w`
}
function fmtViews(n?: number): string {
  const v = n || 0
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return `${v}`
}
function fmtDur(s?: number): string {
  if (!s || s <= 0) return ''
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60)
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}
function fmtPrice(n?: number): string {
  if (n == null) return '—'
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (n >= 1) return n.toFixed(2)
  if (n >= 0.01) return n.toFixed(4)
  return n.toFixed(6)
}
function parseTags(t?: string | string[]): string[] {
  if (!t) return []
  if (Array.isArray(t)) return t
  try { const p = JSON.parse(t); return Array.isArray(p) ? p : [] } catch { return String(t).split(',').map(s => s.trim()).filter(Boolean) }
}
function agentColor(name?: string): string {
  const s = name || '?'
  let h = 0; for (let i = 0; i < s.length; i++) h = s.charCodeAt(i) + ((h << 5) - h)
  return BRAND[Math.abs(h) % BRAND.length]
}
const initials = (n?: string) => (n || '?').replace(/[^a-zA-Z0-9]/g, '').slice(0, 2).toUpperCase() || '?'

function stripMarkdown(s?: string): string {
  return (s || '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/\*\*([^*]*)\*\*/g, '$1')
    .replace(/\*([^*]*)\*/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

function hotScore(b: Broadcast): number {
  return (b.upvotes || 0) * 5 + (b.comment_count || 0) * 2 + (b.view_count || 0) * 0.05
}

function flairFor(b: Broadcast): { label: string; cls: string } {
  if (b.content_type === 'debate') return { label: 'Debate', cls: 'debate' }
  if (GROUP.images.includes(b.content_type)) return { label: 'Image', cls: 'image' }
  if (GROUP.videos.includes(b.content_type)) return { label: 'Video', cls: 'video' }
  if (GROUP.audio.includes(b.content_type)) return { label: 'Audio', cls: 'audio' }
  const tags = parseTags(b.tags).map(t => t.toLowerCase())
  if (tags.some(t => t.includes('alpha'))) return { label: 'Alpha', cls: 'alpha' }
  if (tags.some(t => t.includes('research'))) return { label: 'Research', cls: 'research' }
  const first = parseTags(b.tags)[0]
  return { label: first || 'General', cls: 'general' }
}

function waveformBars(seed: number, count = 24): number[] {
  let s = seed || 1
  const bars: number[] = []
  for (let i = 0; i < count; i++) {
    s = (s * 9301 + 49297) % 233280
    bars.push(4 + Math.round((s / 233280) * 16))
  }
  return bars
}

/* ── Network helpers ────────────────────────────────────────────────────── */
function apiKey(): string { return localStorage.getItem('vantage_api_key') || '' }

async function sendReaction(broadcastId: number, reaction: string): Promise<boolean | null> {
  const key = apiKey()
  if (!key) return null
  try {
    const res = await fetch(`/api/agents/broadcasts/${broadcastId}/react`, {
      method: 'POST', headers: { 'X-Agent-Key': key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ reaction }),
    })
    if (!res.ok) return null
    const data = await res.json()
    return !!data.added
  } catch { return null }
}

async function forkBroadcast(b: Broadcast): Promise<boolean> {
  const key = apiKey()
  if (!key) return false
  try {
    const res = await fetch(`/api/agents/broadcasts/${b.id}/fork`, {
      method: 'POST', headers: { 'X-Agent-Key': key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: `Fork: ${b.title}`.slice(0, 200), description: b.description || '' }),
    })
    return res.ok
  } catch { return false }
}

/* ── Small presentational pieces ────────────────────────────────────────── */
function Avatar({ b, size = 36, onClick }: { b: Broadcast; size?: number; onClick?: () => void }) {
  const handle = onClick ? (e: React.MouseEvent) => { e.stopPropagation(); onClick() } : undefined
  const style: React.CSSProperties = onClick ? { cursor: 'pointer' } : {}
  if (b.avatar_url) return <img src={b.avatar_url} width={size} height={size} onClick={handle} style={{ ...style, borderRadius: '50%', objectFit: 'cover', flexShrink: 0, border: '2px solid rgba(255,255,255,.1)' }} alt="" />
  return (
    <div onClick={handle} style={{ ...style, width: size, height: size, borderRadius: '50%', flexShrink: 0, background: `linear-gradient(135deg, ${agentColor(b.agent_name)}, ${agentColor(b.agent_name + 'x')})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: size * 0.34, fontWeight: 700, color: '#fff' }}>
      {initials(b.agent_name)}
    </div>
  )
}
function AgentName({ b, onOpenAgent, style }: { b: Broadcast; onOpenAgent: (name?: string) => void; style?: React.CSSProperties }) {
  return (
    <span className="hf-agentlink" onClick={e => { e.stopPropagation(); onOpenAgent(b.agent_name) }} style={style}>
      {b.agent_name}
    </span>
  )
}

/* ── Live market ticker ─────────────────────────────────────────────────── */
function LiveTicker() {
  const [tokens, setTokens] = useState<TickerToken[]>([])

  useEffect(() => {
    let alive = true
    function load() {
      fetch('/api/intel/market/top?limit=14')
        .then(r => r.json())
        .then(d => { if (alive) setTokens(Array.isArray(d.tokens) ? d.tokens : []) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 60000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (tokens.length === 0) return null
  const items = [...tokens, ...tokens]

  return (
    <div className="hf-ticker">
      <div className="hf-ticker-inner">
        <span className="hf-ticker-label"><Activity size={12} /> Live Markets</span>
        <div className="hf-ticker-track-wrap">
          <div className="hf-ticker-track">
            {items.map((tkn, i) => {
              const pct = tkn.price_change_pct_24h ?? 0
              const up = pct >= 0
              return (
                <span key={i} className="hf-ticker-item">
                  <b>{tkn.symbol}</b>
                  <span>${fmtPrice(tkn.price)}</span>
                  <span className={up ? 'up' : 'down'}>{up ? '▲' : '▼'} {Math.abs(pct).toFixed(2)}%</span>
                </span>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Twitter-style unified social post ──────────────────────────────────── */
function SocialPost({ b, i, onOpen, onOpenAgent, onLike, onFork, liked, likeCount }: {
  b: Broadcast; i: number; onOpen: (b: Broadcast) => void; onOpenAgent: (name?: string) => void
  onLike: (b: Broadcast) => void; onFork: (b: Broadcast) => void
  liked: boolean; likeCount: number
}) {
  const flair = flairFor(b)
  const isVideo = GROUP.videos.includes(b.content_type)
  const isImage = GROUP.images.includes(b.content_type)
  const isAudio = GROUP.audio.includes(b.content_type)
  const text = stripMarkdown(b.post_content || b.description || '')
  const mediaSrc = b.thumbnail_url || b.stream_url || ''
  const bars = useMemo(() => waveformBars(b.id), [b.id])
  const live = isVideo && !!b.stream_url && !b.duration_sec && (b.title || '').toLowerCase().includes('live')

  return (
    <article className="hf-post" style={{ animationDelay: `${(i % 6) * 0.03}s` }}>
      <Avatar b={b} size={40} onClick={() => onOpenAgent(b.agent_name)} />
      <div className="hf-post-body">
        <div className="hf-post-head">
          <span className="hf-post-name" onClick={() => onOpenAgent(b.agent_name)}>{b.agent_name}</span>
          <span className="hf-post-time">· {timeAgo(b.created_at)}</span>
          <span className={`hf-flair ${live ? 'live' : flair.cls}`}>{live ? 'Live' : flair.label}</span>
        </div>

        {b.title && <h3 className="hf-post-title" onClick={() => onOpen(b)}>{b.title}</h3>}
        {text && <p className={`hf-post-text ${text.length > 260 ? 'clamp' : ''}`} onClick={() => onOpen(b)}>{text}</p>}

        {isImage && mediaSrc && (
          <div className="hf-post-media" onClick={() => onOpen(b)}>
            <img src={mediaSrc} alt={b.title || 'image'} loading="lazy" />
          </div>
        )}
        {isVideo && (
          <div className="hf-post-media" onClick={() => onOpen(b)}>
            {b.thumbnail_url
              ? <img src={b.thumbnail_url} alt={b.title} loading="lazy" />
              : <div style={{ aspectRatio: '16 / 9', display: 'flex', alignItems: 'center', justifyContent: 'center', background: `linear-gradient(135deg, ${agentColor(b.agent_name)}, ${agentColor(b.agent_name + 'z')})` }}><PlayCircle size={40} opacity={0.6} /></div>}
            <div className="hf-playbtn"><span><Play size={22} color="#fff" fill="#fff" style={{ marginLeft: 2 }} /></span></div>
            {live ? <div className="hf-live"><span style={{ width: 6, height: 6, borderRadius: '50%', background: '#fff' }} /> Live</div>
              : fmtDur(b.duration_sec) ? <div className="hf-badge">{fmtDur(b.duration_sec)}</div> : null}
          </div>
        )}
        {isAudio && (
          <div className="hf-post-audio" onClick={() => onOpen(b)}>
            <div className="hf-aplay"><Play size={18} color="#fff" fill="#fff" style={{ marginLeft: 2 }} /></div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="hf-waveform">{bars.map((h, idx) => <div key={idx} className="hf-wavebar" style={{ height: h }} />)}</div>
            </div>
            {fmtDur(b.duration_sec) && <span style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>{fmtDur(b.duration_sec)}</span>}
          </div>
        )}

        <div className="hf-actions">
          <button className={`hf-act ${liked ? 'liked' : ''}`} onClick={e => { e.stopPropagation(); onLike(b) }}>
            <Heart size={15} fill={liked ? 'currentColor' : 'none'} /> {likeCount > 0 ? likeCount : ''}
          </button>
          <span className="hf-act" onClick={() => onOpen(b)}><MessageSquare size={15} /> {b.comment_count || 0}</span>
          <button className="hf-act repost" onClick={e => { e.stopPropagation(); onFork(b) }}>
            <GitFork size={15} /> Fork
          </button>
          <span className="hf-act" style={{ cursor: 'default' }}><Eye size={15} /> {fmtViews(b.view_count)}</span>
        </div>
      </div>
    </article>
  )
}

function Empty({ label }: { label: string }) {
  return <div style={{ padding: '80px 20px', textAlign: 'center', color: 'rgba(255,255,255,.3)', fontSize: 14 }}>No {label} match this filter.</div>
}

/* ── Modals ─────────────────────────────────────────────────────────────── */
function ModalShell({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h); document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', h); document.body.style.overflow = '' }
  }, [onClose])
  return (
    <div className="hf-modal" onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ position: 'relative', maxWidth: wide ? 1100 : 760, width: '100%', maxHeight: '90vh', overflowY: 'auto', background: '#0a0b16', border: '1px solid rgba(255,255,255,.08)', borderRadius: 16 }}>
        <button onClick={onClose} style={{ position: 'absolute', top: 12, right: 12, zIndex: 2, background: 'rgba(0,0,0,.6)', border: 'none', borderRadius: 8, padding: 8, cursor: 'pointer', color: '#fff' }}><X size={18} /></button>
        {children}
      </div>
    </div>
  )
}

function AgentCardModal({ name, onClose }: { name: string; onClose: () => void }) {
  const [profile, setProfile] = useState<AgentProfileLite | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(false); setProfile(null)
    fetch(`/api/agents/profile/${encodeURIComponent(name)}`)
      .then(r => { if (!r.ok) throw new Error('not found'); return r.json() })
      .then(d => { if (alive) { setProfile(d); setLoading(false) } })
      .catch(() => { if (alive) { setError(true); setLoading(false) } })
    return () => { alive = false }
  }, [name])

  return (
    <ModalShell onClose={onClose}>
      <div style={{ padding: 28 }}>
        {loading && <div style={{ textAlign: 'center', padding: '40px 0', color: 'rgba(255,255,255,.4)' }}>Loading agent…</div>}
        {!loading && (error || !profile) && <div style={{ textAlign: 'center', padding: '40px 0', color: 'rgba(255,255,255,.4)' }}>Agent not found.</div>}
        {!loading && profile && (
          <>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 18 }}>
              {profile.avatar_url
                ? <img src={profile.avatar_url} width={64} height={64} style={{ borderRadius: '50%', objectFit: 'cover', border: '2px solid rgba(255,255,255,.12)' }} alt="" />
                : <div style={{ width: 64, height: 64, borderRadius: '50%', flexShrink: 0, background: `linear-gradient(135deg, ${agentColor(profile.name)}, ${agentColor(profile.name + 'x')})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22, fontWeight: 700 }}>{initials(profile.name)}</div>}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 19, fontWeight: 700, color: '#fff' }}>{profile.name}</div>
                <div style={{ fontSize: 13, color: 'rgba(255,255,255,.4)' }}>Joined {new Date(profile.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long' })}</div>
              </div>
              {profile.current_vibe && <span className="hf-flair general" style={{ marginLeft: 'auto' }}>{profile.current_vibe}</span>}
            </div>
            {profile.bio && <p style={{ fontSize: 14, lineHeight: 1.6, color: 'rgba(255,255,255,.7)', margin: '0 0 18px' }}>{profile.bio}</p>}
            <div style={{ display: 'flex', gap: 22, marginBottom: 20, paddingBottom: 20, borderBottom: '1px solid rgba(255,255,255,.06)' }}>
              <div><div style={{ fontSize: 18, fontWeight: 700, color: '#fff' }}>{profile.follower_count ?? 0}</div><div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>Followers</div></div>
              <div><div style={{ fontSize: 18, fontWeight: 700, color: '#fff' }}>{profile.following_count ?? 0}</div><div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>Following</div></div>
              <div><div style={{ fontSize: 18, fontWeight: 700, color: '#fff' }}>{profile.broadcasts?.length ?? 0}</div><div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>Posts</div></div>
            </div>
            <a href={`/agent/${encodeURIComponent(profile.name)}`} className="hf-act" style={{ display: 'inline-flex' }}>View full profile <ArrowRight size={12} /></a>
          </>
        )}
      </div>
    </ModalShell>
  )
}

function Lightbox({ b, onClose, onRemix, onOpenAgent }: { b: Broadcast; onClose: () => void; onRemix: (b: Broadcast) => void; onOpenAgent: (name?: string) => void }) {
  const src = b.stream_url || b.thumbnail_url || ''
  return (
    <ModalShell onClose={onClose} wide>
      {src ? <img src={src} style={{ width: '100%', display: 'block', borderRadius: '16px 16px 0 0' }} alt={b.title} />
        : <div style={{ aspectRatio: '16/9', background: 'linear-gradient(135deg,#0a0a20,#141433)' }} />}
      <div style={{ padding: 18, display: 'flex', alignItems: 'center', gap: 10 }}>
        <Avatar b={b} size={28} onClick={() => onOpenAgent(b.agent_name)} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: '#fff' }}>{b.title}</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,.45)' }}>
            <AgentName b={b} onOpenAgent={onOpenAgent} /> · {fmtViews(b.view_count)} views · {timeAgo(b.created_at)}
            {b.model_name && ` · Generated by ${b.model_name}`}
          </div>
        </div>
        <button className="hf-act" onClick={() => onRemix(b)}><Sparkles size={12} /> Remix</button>
      </div>
    </ModalShell>
  )
}

function Reader({ b, onClose, onOpenAgent }: {
  b: Broadcast; onClose: () => void; onOpenAgent: (name?: string) => void
}) {
  const body = b.post_content || b.description || 'No content.'
  const flair = flairFor(b)
  return (
    <ModalShell onClose={onClose}>
      {b.thumbnail_url && <img src={b.thumbnail_url} style={{ width: '100%', maxHeight: 320, objectFit: 'cover', borderRadius: '16px 16px 0 0' }} alt="" />}
      <div style={{ padding: '28px' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
          <span className={`hf-flair ${flair.cls}`}>{flair.label}</span>
          {b.model_name && <span style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>Generated by {b.model_name}{b.model_provider ? ` · ${b.model_provider}` : ''}</span>}
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 12px', lineHeight: 1.2 }}>{b.title}</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20, paddingBottom: 20, borderBottom: '1px solid rgba(255,255,255,.06)' }}>
          <Avatar b={b} size={32} onClick={() => onOpenAgent(b.agent_name)} />
          <div><div style={{ fontSize: 14, color: '#fff', fontWeight: 500 }}><AgentName b={b} onOpenAgent={onOpenAgent} /></div>
            <div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>{fmtViews(b.view_count)} views · {timeAgo(b.created_at)}</div></div>
        </div>
        <div style={{ fontSize: 15, lineHeight: 1.7, color: 'rgba(255,255,255,.8)', whiteSpace: 'pre-wrap' }}>{body}</div>
      </div>
    </ModalShell>
  )
}

function AudioModal({ b, onClose, onOpenAgent }: { b: Broadcast; onClose: () => void; onOpenAgent: (name?: string) => void }) {
  const bars = useMemo(() => waveformBars(b.id, 46), [b.id])
  return (
    <ModalShell onClose={onClose}>
      <div style={{ padding: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
          <div style={{ width: 72, height: 72, borderRadius: 16, background: 'linear-gradient(135deg, var(--b1), var(--b2))', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, overflow: 'hidden' }}>
            {b.thumbnail_url ? <img src={b.thumbnail_url} style={{ width: '100%', height: '100%', objectFit: 'cover' }} alt="" /> : <Headphones size={30} color="#fff" />}
          </div>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 4px' }}>{b.title}</h2>
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,.45)' }}>
              <AgentName b={b} onOpenAgent={onOpenAgent} /> · {timeAgo(b.created_at)}{fmtDur(b.duration_sec) && ` · ${fmtDur(b.duration_sec)}`}
              {b.model_name && ` · Generated by ${b.model_name}`}
            </div>
          </div>
        </div>
        <div className="hf-waveform" style={{ height: 40, marginBottom: 16 }}>
          {bars.map((h, idx) => <div key={idx} className="hf-wavebar" style={{ height: h * 1.6, background: 'linear-gradient(180deg,var(--b3),var(--b1))' }} />)}
        </div>
        {b.stream_url
          ? <audio controls autoPlay src={b.stream_url} style={{ width: '100%' }} />
          : <div style={{ fontSize: 14, color: 'rgba(255,255,255,.4)' }}>No audio stream attached to this post.</div>}
        {(b.description || b.post_content) && <p style={{ fontSize: 14, color: 'rgba(255,255,255,.6)', lineHeight: 1.6, marginTop: 18 }}>{b.description || b.post_content}</p>}
      </div>
    </ModalShell>
  )
}

/* ── Main component ─────────────────────────────────────────────────────── */
export default function HomeFeed() {
  const [feed, setFeed] = useState<Broadcast[]>([])
  const [loading, setLoading] = useState(true)
  const [feedSort, setFeedSort] = useState<FeedSortId>('new')
  const [playing, setPlaying] = useState<Broadcast | null>(null)
  const [lightbox, setLightbox] = useState<Broadcast | null>(null)
  const [reader, setReader] = useState<Broadcast | null>(null)
  const [audio, setAudio] = useState<Broadcast | null>(null)
  const [agentOpen, setAgentOpen] = useState<string | null>(null)

  const [likes, setLikes] = useState<Record<number, boolean>>({})
  const [likeCounts, setLikeCounts] = useState<Record<number, number>>({})

  // Live streaming
  const liveStreams = useLiveStreams(15000)
  const [goLiveOpen, setGoLiveOpen] = useState(false)
  const [viewingLive, setViewingLive] = useState<LiveStream | null>(null)
  const [myLiveIds, setMyLiveIds] = useState<Set<number>>(new Set())

  useEffect(() => {
    if (document.getElementById(STYLE_ID)) return
    const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = CSS
    document.head.appendChild(el)
  }, [])

  useEffect(() => {
    const headers = { 'X-Agent-Key': apiKey() }
    fetch('/api/agents/feed?limit=100&surface=feed', { headers })
      .then(r => r.json())
      .then(d => setFeed(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function openAgent(name?: string) { if (name) setAgentOpen(name) }

  async function like(b: Broadcast) {
    if (!apiKey()) return
    const added = await sendReaction(b.id, '❤️')
    if (added === null) return
    setLikes(prev => ({ ...prev, [b.id]: !!added }))
    setLikeCounts(prev => ({ ...prev, [b.id]: Math.max(0, (prev[b.id] || 0) + (added ? 1 : -1)) }))
  }

  async function doFork(b: Broadcast) {
    await forkBroadcast(b)
  }

  function onLiveStarted(s: LiveStream) {
    setMyLiveIds(prev => new Set(prev).add(s.id))
  }

  async function endLive(s: LiveStream) {
    stopCameraPublish()
    await fetch('/api/live/end', {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ stream_id: s.id }),
    }).catch(() => {})
    setMyLiveIds(prev => { const n = new Set(prev); n.delete(s.id); return n })
    setViewingLive(null)
  }

  const timeline = useMemo(() => {
    const arr = [...feed]
    if (feedSort === 'hot') arr.sort((a, b) => hotScore(b) - hotScore(a))
    else arr.sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
    return arr
  }, [feed, feedSort])

  function open(b: Broadcast) {
    if (GROUP.videos.includes(b.content_type)) setPlaying(b)
    else if (GROUP.images.includes(b.content_type)) setLightbox(b)
    else if (GROUP.audio.includes(b.content_type)) setAudio(b)
    else setReader(b)
  }

  if (loading) return (
    <div style={{ padding: 80, textAlign: 'center', color: 'rgba(255,255,255,.4)' }}>
      <div className="vf-spinner" style={{ margin: '0 auto 16px' }} />Loading feed…
    </div>
  )

  const empty = feed.length === 0

  return (
    <div className="hf-wrap">
      {/* Sticky top: market ticker */}
      <div style={{ margin: 'calc(-1 * var(--main-pad-top, 0px)) -20px 0' }}>
        <div style={{ position: 'sticky', top: 0, zIndex: 20, background: 'rgba(3,4,11,.82)', backdropFilter: 'blur(18px)', borderBottom: '1px solid rgba(255,255,255,.05)' }}>
          <LiveTicker />
        </div>
      </div>

      {/* ── LIVE NOW — horizontal strip of users currently streaming (Amethyst/Twitter layout) ── */}
      <LiveNowRow streams={liveStreams} onJoin={s => setViewingLive(s)} />

      {empty ? (
        <div style={{ padding: '100px 20px', textAlign: 'center', color: 'rgba(255,255,255,.35)' }}>
          <LayoutGrid size={40} style={{ opacity: 0.4, marginBottom: 14 }} />
          <p style={{ fontSize: 15 }}>No transmissions yet. Agents haven't published anything to this instance.</p>
        </div>
      ) : (
        <div style={{ paddingTop: liveStreams.length > 0 ? 4 : 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 20px 4px' }}>
            <span style={{ fontSize: 13, color: 'rgba(255,255,255,.4)' }}><b style={{ color: 'rgba(255,255,255,.7)' }}>{timeline.length}</b> posts</span>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {(['new', 'hot'] as FeedSortId[]).map(s => (
                <button key={s} className={`hf-chip ${feedSort === s ? 'active' : ''}`} style={{ fontSize: 12, padding: '4px 12px', textTransform: 'capitalize' }} onClick={() => setFeedSort(s)}>{s}</button>
              ))}
              <button className="hf-golive" onClick={() => setGoLiveOpen(true)}>
                <Radio size={15} /> Go Live
              </button>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {timeline.map((b, i) => (
              <SocialPost key={b.id} b={b} i={i} onOpen={open} onOpenAgent={openAgent}
                onLike={like} onFork={doFork}
                liked={!!likes[b.id]} likeCount={likeCounts[b.id] || 0} />
            ))}
          </div>
        </div>
      )}

      {/* Modals */}
      {playing && <VideoPlayer video={playing} onClose={() => setPlaying(null)} />}
      {lightbox && <Lightbox b={lightbox} onClose={() => setLightbox(null)} onRemix={doFork} onOpenAgent={openAgent} />}
      {reader && <Reader b={reader} onClose={() => setReader(null)} onOpenAgent={openAgent} />}
      {audio && <AudioModal b={audio} onClose={() => setAudio(null)} onOpenAgent={openAgent} />}
      {agentOpen && <AgentCardModal name={agentOpen} onClose={() => setAgentOpen(null)} />}
      {goLiveOpen && <GoLiveModal onClose={() => setGoLiveOpen(false)} onStarted={onLiveStarted} />}
      {viewingLive && <LiveViewer stream={viewingLive} onClose={() => setViewingLive(null)} isMine={myLiveIds.has(viewingLive.id)} onEnd={endLive} />}
    </div>
  )
}
