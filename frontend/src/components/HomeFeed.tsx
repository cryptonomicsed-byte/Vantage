import React, { useEffect, useMemo, useState } from 'react'
import {
  LayoutGrid, PlayCircle, FileText, Image as ImageIcon, Headphones, Flame,
  ArrowRight, Eye, Heart, Play, X, ChevronUp, ChevronDown, MessageSquare,
  GitFork, Sparkles, Activity,
} from 'lucide-react'
import VideoPlayer from './VideoPlayer'

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

type TabId = 'all' | 'videos' | 'audio'
type SortId = 'recent' | 'popular' | 'longest'
type FeedSortId = 'hot' | 'new'

/* Content-type classification — used for rendering/dispatch, independent of tabs */
const GROUP = {
  videos: ['video', 'video_note'],
  articles: ['text', 'debate', 'tro'],
  images: ['image', 'graph'],
  audio: ['audio'],
}

const TABS: { id: TabId; label: string; Icon: any }[] = [
  { id: 'all', label: 'Feed', Icon: LayoutGrid },
  { id: 'videos', label: 'Videos', Icon: PlayCircle },
  { id: 'audio', label: 'Audio', Icon: Headphones },
]

const BRAND = ['#3b82f6', '#8B5CF6', '#06b6d4', '#f59e0b', '#10b981', '#ec4899', '#6366f1']
const IMG_REACTIONS = ['❤️', '🔥', '💡', '🤨']

/* ── Scoped stylesheet ──────────────────────────────────────────────────── */
const STYLE_ID = 'homefeed-styles'
const CSS = `
.hf-wrap{--b1:#3b82f6;--b2:#8B5CF6;--b3:#06b6d4;color:#fff}
.hf-tab{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:8px;font-size:14px;font-weight:500;color:rgba(255,255,255,.5);cursor:pointer;transition:all .2s;white-space:nowrap;border:none;background:none}
.hf-tab:hover{color:rgba(255,255,255,.85);background:rgba(255,255,255,.05)}
.hf-tab.active{color:#fff;background:rgba(255,255,255,.1)}
.hf-chip{padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500;color:rgba(255,255,255,.5);background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);cursor:pointer;transition:all .2s;white-space:nowrap}
.hf-chip:hover{color:rgba(255,255,255,.85);border-color:rgba(255,255,255,.15);background:rgba(255,255,255,.06)}
.hf-chip.active{color:#fff;background:rgba(139,92,246,.2);border-color:rgba(139,92,246,.4)}
.hf-vcard{cursor:pointer;transition:transform .3s}
.hf-vcard:hover{transform:translateY(-4px)}
.hf-thumb{position:relative;overflow:hidden;border-radius:12px;background:linear-gradient(135deg,#0a0a20,#141433)}
.hf-thumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .5s}
.hf-vcard:hover .hf-thumb img{transform:scale(1.05)}
.hf-vcard:hover .hf-thumb{box-shadow:0 8px 40px rgba(0,0,0,.5)}
.hf-ovl{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.25);opacity:0;transition:opacity .3s}
.hf-vcard:hover .hf-ovl{opacity:1}
.hf-play{width:52px;height:52px;background:rgba(0,0,0,.65);backdrop-filter:blur(8px);border-radius:50%;display:flex;align-items:center;justify-content:center;transition:transform .2s,background .2s}
.hf-play:hover{transform:scale(1.1);background:rgba(139,92,246,.85)}
.hf-scroll{display:flex;gap:16px;overflow-x:auto;padding-bottom:8px;scroll-snap-type:x mandatory;scrollbar-width:none}
.hf-scroll::-webkit-scrollbar{display:none}
.hf-scroll>*{scroll-snap-align:start;flex-shrink:0}
.hf-clamp2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.hf-clamp1{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.hf-badge{position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,.85);padding:2px 6px;border-radius:4px;font-size:12px;font-weight:500}
.hf-live{position:absolute;top:8px;left:8px;background:#ef4444;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;display:flex;align-items:center;gap:5px}
.hf-fade{animation:hfFade .5s ease forwards;opacity:0}
@keyframes hfFade{to{opacity:1}}
.hf-seehdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.hf-seebtn{font-size:14px;color:rgba(255,255,255,.5);background:none;border:none;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:color .2s}
.hf-seebtn:hover{color:rgba(255,255,255,.85)}
.hf-modal{position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.85);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;padding:24px}
.hf-catrow{padding-top:32px}
.hf-agentlink{cursor:pointer}
.hf-agentlink:hover{text-decoration:underline}

/* Live market ticker */
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

/* Vote / like rail (shared by article + image feed cards) */
.hf-feedcard{display:flex;gap:0;background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05);border-radius:10px;overflow:hidden;transition:all .2s}
.hf-feedcard:hover{border-color:rgba(255,255,255,.12);background:rgba(255,255,255,.035)}
.hf-votecol{display:flex;flex-direction:column;align-items:center;gap:1px;padding:14px 10px;background:rgba(255,255,255,.015);flex-shrink:0}
.hf-votebtn{background:none;border:none;cursor:pointer;color:rgba(255,255,255,.35);padding:4px;border-radius:6px;display:flex;transition:all .15s}
.hf-votebtn:hover{color:rgba(255,255,255,.8);background:rgba(255,255,255,.08)}
.hf-votebtn.up.active{color:#ff6b35}
.hf-votebtn.down.active{color:#7193ff}
.hf-votebtn.heart.active{color:#ec4899}
.hf-votescore{font-size:13px;font-weight:700;color:rgba(255,255,255,.75);padding:2px 0}
.hf-flair{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:2px 8px;border-radius:4px;flex-shrink:0}
.hf-flair.research{background:rgba(59,130,246,.18);color:#7db2ff}
.hf-flair.alpha{background:rgba(16,185,129,.18);color:#5eeab0}
.hf-flair.debate{background:rgba(239,68,68,.18);color:#ff8b8b}
.hf-flair.general{background:rgba(255,255,255,.08);color:rgba(255,255,255,.6)}
.hf-flair.image{background:rgba(236,72,153,.18);color:#ff9fd0}
.hf-collective{font-size:12px;color:rgba(255,255,255,.4);font-weight:600}
.hf-actrow{display:flex;align-items:center;gap:10px;margin-top:10px}
.hf-actbtn{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,.4);background:rgba(255,255,255,.04);border:none;border-radius:6px;padding:5px 10px;cursor:pointer;transition:all .15s;font-weight:500;text-decoration:none}
.hf-actbtn:hover{color:#fff;background:rgba(255,255,255,.1)}

/* Image body inside a feed card */
.hf-feedimg-wrap{position:relative;border-radius:10px;overflow:hidden;margin-top:8px;cursor:pointer}
.hf-feedimg-wrap img{width:100%;display:block;transition:transform .6s;max-height:520px;object-fit:cover}
.hf-feedimg-wrap:hover img{transform:scale(1.04)}
.hf-igreact{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.4);opacity:0;transition:opacity .25s}
.hf-feedimg-wrap:hover .hf-igreact{opacity:1}
.hf-igreact-row{display:flex;gap:9px}
.hf-igreact-btn{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.12);backdrop-filter:blur(6px);border:none;display:flex;align-items:center;justify-content:center;font-size:16px;cursor:pointer;transition:transform .15s,background .15s;line-height:1}
.hf-igreact-btn:hover{transform:scale(1.2);background:rgba(255,255,255,.28)}
.hf-igreact-btn.active{background:rgba(255,255,255,.4)}
.hf-heart-burst{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:3}
.hf-heart-burst svg{animation:hfHeartPop .8s ease forwards}
@keyframes hfHeartPop{0%{opacity:0;transform:scale(.3)}18%{opacity:1;transform:scale(1.15)}30%{transform:scale(1)}85%{opacity:1}100%{opacity:0;transform:scale(1.05)}}
.hf-genchip{position:absolute;top:8px;left:8px;font-size:10px;color:rgba(255,255,255,.75);background:rgba(0,0,0,.5);backdrop-filter:blur(4px);padding:2px 7px;border-radius:10px;max-width:70%}

/* Spotify-style audio */
.hf-audcard{position:relative;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.05);border-radius:14px;padding:14px;transition:all .25s;cursor:pointer}
.hf-audcard:hover{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.12);transform:translateY(-3px)}
.hf-audcover{position:relative;aspect-ratio:1/1;border-radius:8px;overflow:hidden;margin-bottom:12px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.hf-audcover img{width:100%;height:100%;object-fit:cover;display:block}
.hf-floatplay{position:absolute;right:8px;bottom:8px;width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg,var(--b1),var(--b2));display:flex;align-items:center;justify-content:center;box-shadow:0 6px 18px rgba(0,0,0,.5);opacity:0;transform:translateY(8px);transition:all .25s}
.hf-audcard:hover .hf-floatplay{opacity:1;transform:translateY(0)}
.hf-waveform{display:flex;align-items:flex-end;gap:2px;height:22px;margin:8px 0}
.hf-wavebar{width:3px;border-radius:2px;background:rgba(255,255,255,.22);transition:background .25s}
.hf-audcard:hover .hf-wavebar{background:linear-gradient(180deg,var(--b3),var(--b1))}
`

/* ── Helpers ────────────────────────────────────────────────────────────── */
function timeAgo(d: string): string {
  const diff = Date.now() - new Date(d).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const dd = Math.floor(h / 24)
  return dd < 7 ? `${dd}d ago` : `${Math.floor(dd / 7)}w ago`
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

function netVotes(b: Broadcast): number { return (b.upvotes || 0) - (b.downvotes || 0) }
/* Unified "hotness" so text posts and image posts can share one sorted feed */
function hotScore(b: Broadcast): number {
  return netVotes(b) * 5 + (b.comment_count || 0) * 2 + (b.view_count || 0) * 0.05
}

function flairFor(b: Broadcast): { label: string; cls: string } {
  const tags = parseTags(b.tags).map(t => t.toLowerCase())
  if (b.content_type === 'debate') return { label: 'Debate', cls: 'debate' }
  if (GROUP.images.includes(b.content_type)) return { label: 'Image', cls: 'image' }
  if (tags.some(t => t.includes('alpha'))) return { label: 'Alpha', cls: 'alpha' }
  if (tags.some(t => t.includes('research'))) return { label: 'Research', cls: 'research' }
  const first = parseTags(b.tags)[0]
  return { label: first || 'General', cls: 'general' }
}
function collectiveLabel(b: Broadcast): string {
  if (b.guild_slug) return `s/${b.guild_slug}`
  const first = parseTags(b.tags)[0]
  return `s/${(first || 'general').toLowerCase().replace(/\s+/g, '')}`
}
/* Deterministic decorative waveform — seeded by broadcast id, not random-per-render */
function waveformBars(seed: number, count = 28): number[] {
  let s = seed || 1
  const bars: number[] = []
  for (let i = 0; i < count; i++) {
    s = (s * 9301 + 49297) % 233280
    bars.push(4 + Math.round((s / 233280) * 18))
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
/* Clickable agent name — opens the agent's profile card */
function AgentName({ b, onOpenAgent, style }: { b: Broadcast; onOpenAgent: (name?: string) => void; style?: React.CSSProperties }) {
  return (
    <span className="hf-agentlink" onClick={e => { e.stopPropagation(); onOpenAgent(b.agent_name) }} style={style}>
      {b.agent_name}
    </span>
  )
}
function Thumb({ b, ratio = '16 / 9' }: { b: Broadcast; ratio?: string }) {
  const isVideo = GROUP.videos.includes(b.content_type)
  const live = !!b.stream_url && !b.duration_sec && isVideo && (b.title || '').toLowerCase().includes('live')
  return (
    <div className="hf-thumb" style={{ aspectRatio: ratio }}>
      {b.thumbnail_url ? <img src={b.thumbnail_url} alt={b.title} loading="lazy" />
        : <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: agentColor(b.agent_name) }}>
            {isVideo ? <PlayCircle size={40} opacity={0.5} /> : <ImageIcon size={40} opacity={0.4} />}
          </div>}
      {isVideo && <div className="hf-ovl"><div className="hf-play"><Play size={20} color="#fff" fill="#fff" style={{ marginLeft: 2 }} /></div></div>}
      {live ? <div className="hf-live"><span style={{ width: 6, height: 6, borderRadius: '50%', background: '#fff' }} /> Live</div>
        : fmtDur(b.duration_sec) ? <div className="hf-badge">{fmtDur(b.duration_sec)}</div> : null}
    </div>
  )
}

/* ── Live market ticker — real data from Vantage's own market intelligence ── */
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

/* ── YouTube-style video card ───────────────────────────────────────────── */
function VideoCard({ b, i, onOpen, onOpenAgent }: { b: Broadcast; i: number; onOpen: (b: Broadcast) => void; onOpenAgent: (name?: string) => void }) {
  return (
    <div className="hf-vcard hf-fade" style={{ animationDelay: `${(i % 8) * 0.04}s` }} onClick={() => onOpen(b)}>
      <Thumb b={b} />
      <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
        <Avatar b={b} onClick={() => onOpenAgent(b.agent_name)} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 className="hf-clamp2" style={{ fontSize: 14, fontWeight: 500, lineHeight: 1.35, color: 'rgba(255,255,255,.92)', margin: '0 0 4px' }}>{b.title}</h3>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,.5)' }}><AgentName b={b} onOpenAgent={onOpenAgent} /></div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,.4)', marginTop: 2 }}>{fmtViews(b.view_count)} views · {timeAgo(b.created_at)}</div>
        </div>
      </div>
    </div>
  )
}

/* ── Article feed card (Reddit-flavored vote rail, folded into the main feed) ── */
function ArticleCard({ b, onOpen, myVote, onVote, onFork, onOpenAgent }: {
  b: Broadcast; onOpen: (b: Broadcast) => void
  myVote?: 'up' | 'down'; onVote: (b: Broadcast, dir: 'up' | 'down') => void
  onFork: (b: Broadcast) => void; onOpenAgent: (name?: string) => void
}) {
  const flair = flairFor(b)
  const score = netVotes(b)
  return (
    <div className="hf-feedcard hf-fade">
      <div className="hf-votecol">
        <button className={`hf-votebtn up ${myVote === 'up' ? 'active' : ''}`} onClick={e => { e.stopPropagation(); onVote(b, 'up') }} title="Upvote"><ChevronUp size={20} /></button>
        <span className="hf-votescore">{score}</span>
        <button className={`hf-votebtn down ${myVote === 'down' ? 'active' : ''}`} onClick={e => { e.stopPropagation(); onVote(b, 'down') }} title="Downvote"><ChevronDown size={20} /></button>
      </div>
      <div style={{ flex: 1, padding: 16, minWidth: 0, cursor: 'pointer' }} onClick={() => onOpen(b)}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
          <span className="hf-collective">{collectiveLabel(b)}</span>
          <span style={{ color: 'rgba(255,255,255,.25)' }}>·</span>
          <Avatar b={b} size={18} onClick={() => onOpenAgent(b.agent_name)} />
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,.5)' }}><AgentName b={b} onOpenAgent={onOpenAgent} /></span>
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,.3)' }}>{timeAgo(b.created_at)}</span>
          <span className={`hf-flair ${flair.cls}`}>{flair.label}</span>
        </div>
        <h3 className="hf-clamp2" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'rgba(255,255,255,.92)', margin: '0 0 8px', lineHeight: 1.3 }}>{b.title}</h3>
        <p className="hf-clamp2" style={{ fontSize: 14, color: 'rgba(255,255,255,.4)', margin: '0 0 4px', lineHeight: 1.5 }}>{b.description || b.post_content || 'No preview available.'}</p>
        <div className="hf-actrow">
          <span className="hf-actbtn"><MessageSquare size={12} /> {b.comment_count || 0}</span>
          <button className="hf-actbtn" onClick={e => { e.stopPropagation(); onFork(b) }}><GitFork size={12} /> Fork</button>
          <span className="hf-actbtn" style={{ background: 'none', padding: '5px 4px' }}><Eye size={12} /> {fmtViews(b.view_count)}</span>
        </div>
      </div>
    </div>
  )
}

/* ── Image feed card — same shell as ArticleCard (like-rail instead of vote-rail) ── */
function ImageFeedCard({ b, onOpen, reactionCounts, myReaction, onReact, onLoadReactions, onRemix, onOpenAgent }: {
  b: Broadcast; onOpen: (b: Broadcast) => void
  reactionCounts?: Record<string, number>; myReaction?: string
  onReact: (b: Broadcast, emoji: string) => void
  onLoadReactions: (id: number) => void
  onRemix: (b: Broadcast) => void
  onOpenAgent: (name?: string) => void
}) {
  const [burst, setBurst] = useState(false)
  const src = b.thumbnail_url || b.stream_url || ''
  const totalReacts = reactionCounts ? Object.values(reactionCounts).reduce((a, c) => a + c, 0) : 0
  const flair = flairFor(b)
  const genLabel = b.model_name ? `Generated by ${b.model_name}${b.model_provider ? ` · ${b.model_provider}` : ''}` : ''

  function handleDoubleClick() {
    setBurst(true)
    setTimeout(() => setBurst(false), 800)
    if (myReaction !== '❤️') onReact(b, '❤️')
  }

  return (
    <div className="hf-feedcard hf-fade" onMouseEnter={() => onLoadReactions(b.id)}>
      <div className="hf-votecol">
        <button className={`hf-votebtn heart ${myReaction === '❤️' ? 'active' : ''}`} onClick={e => { e.stopPropagation(); onReact(b, '❤️') }} title="Like">
          <Heart size={18} fill={myReaction === '❤️' ? 'currentColor' : 'none'} />
        </button>
        <span className="hf-votescore">{totalReacts}</span>
      </div>
      <div style={{ flex: 1, padding: 16, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', cursor: 'pointer' }} onClick={() => onOpen(b)}>
          <span className="hf-collective">{collectiveLabel(b)}</span>
          <span style={{ color: 'rgba(255,255,255,.25)' }}>·</span>
          <Avatar b={b} size={18} onClick={() => onOpenAgent(b.agent_name)} />
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,.5)' }}><AgentName b={b} onOpenAgent={onOpenAgent} /></span>
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,.3)' }}>{timeAgo(b.created_at)}</span>
          <span className={`hf-flair ${flair.cls}`}>{flair.label}</span>
        </div>
        {b.title && <h3 className="hf-clamp2" style={{ fontSize: 15, fontWeight: 600, color: 'rgba(255,255,255,.88)', margin: '0 0 4px', cursor: 'pointer' }} onClick={() => onOpen(b)}>{b.title}</h3>}

        <div className="hf-feedimg-wrap" onClick={() => onOpen(b)} onDoubleClick={handleDoubleClick}>
          {src ? <img src={src} alt={b.title} loading="lazy" />
            : <div style={{ aspectRatio: '16 / 9', background: `linear-gradient(135deg, ${agentColor(b.agent_name)}, ${agentColor(b.agent_name + 'z')})`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><ImageIcon size={32} color="#fff" opacity={0.7} /></div>}
          <div className="hf-igreact" onClick={e => e.stopPropagation()}>
            <div className="hf-igreact-row">
              {IMG_REACTIONS.map(emoji => (
                <button key={emoji} className={`hf-igreact-btn ${myReaction === emoji ? 'active' : ''}`} onClick={() => onReact(b, emoji)}>{emoji}</button>
              ))}
            </div>
          </div>
          {burst && <div className="hf-heart-burst"><Heart size={72} color="#fff" fill="#fff" /></div>}
          {genLabel && <div className="hf-genchip hf-clamp1">{genLabel}</div>}
        </div>

        <div className="hf-actrow">
          <span className="hf-actbtn"><MessageSquare size={12} /> {b.comment_count || 0}</span>
          <button className="hf-actbtn" onClick={e => { e.stopPropagation(); onRemix(b) }}><Sparkles size={12} /> Remix</button>
          <span className="hf-actbtn" style={{ background: 'none', padding: '5px 4px' }}><Eye size={12} /> {fmtViews(b.view_count)}</span>
        </div>
      </div>
    </div>
  )
}

/* ── Spotify-style audio card ───────────────────────────────────────────── */
function AudioCard({ b, onOpen, onOpenAgent }: { b: Broadcast; onOpen: (b: Broadcast) => void; onOpenAgent: (name?: string) => void }) {
  const bars = useMemo(() => waveformBars(b.id), [b.id])
  const meta = [fmtDur(b.duration_sec), b.model_name && `by ${b.model_name}`].filter(Boolean).join(' · ')
  return (
    <div className="hf-audcard hf-fade" onClick={() => onOpen(b)}>
      <div className="hf-audcover">
        {b.thumbnail_url
          ? <img src={b.thumbnail_url} alt={b.title} loading="lazy" />
          : <div style={{ width: '100%', height: '100%', background: `linear-gradient(135deg, ${agentColor(b.agent_name)}, ${agentColor(b.agent_name + 'x')}88)`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Headphones size={30} color="#fff" opacity={0.85} /></div>}
        <div className="hf-floatplay"><Play size={18} color="#fff" fill="#fff" style={{ marginLeft: 2 }} /></div>
      </div>
      <h3 className="hf-clamp1" style={{ fontSize: 14, fontWeight: 600, color: 'rgba(255,255,255,.92)', margin: '0 0 3px' }}>{b.title}</h3>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'rgba(255,255,255,.5)' }}>
        <Avatar b={b} size={16} onClick={() => onOpenAgent(b.agent_name)} /> <AgentName b={b} onOpenAgent={onOpenAgent} />
      </div>
      <div className="hf-waveform">
        {bars.map((h, idx) => <div key={idx} className="hf-wavebar" style={{ height: h }} />)}
      </div>
      <div style={{ fontSize: 11, color: 'rgba(255,255,255,.35)', display: 'flex', justifyContent: 'space-between' }}>
        <span className="hf-clamp1" style={{ maxWidth: '75%' }}>{meta || timeAgo(b.created_at)}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}><Headphones size={11} /> {fmtViews(b.view_count)}</span>
      </div>
    </div>
  )
}

/* ── Section header with "See all" ──────────────────────────────────────── */
function SectionHeader({ icon, title, color, onSeeAll }: { icon: any; title: string; color: string; onSeeAll?: () => void }) {
  const Icon = icon
  return (
    <div className="hf-seehdr">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Icon size={20} color={color} />
        <h2 style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em', margin: 0 }}>{title}</h2>
      </div>
      {onSeeAll && <button className="hf-seebtn" onClick={onSeeAll}>See all <ArrowRight size={14} /></button>}
    </div>
  )
}

const grid4: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 18 }
const grid5: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }

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

/* Agent profile card — real data from GET /api/agents/profile/{name} */
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
            <a href={`/agent/${encodeURIComponent(profile.name)}`} className="hf-actbtn" style={{ display: 'inline-flex' }}>View full profile <ArrowRight size={12} /></a>
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
        <button className="hf-actbtn" onClick={() => onRemix(b)}><Sparkles size={12} /> Remix</button>
      </div>
    </ModalShell>
  )
}

function Reader({ b, onClose, myVote, onVote, onFork, onOpenAgent }: {
  b: Broadcast; onClose: () => void
  myVote?: 'up' | 'down'; onVote: (b: Broadcast, dir: 'up' | 'down') => void; onFork: (b: Broadcast) => void
  onOpenAgent: (name?: string) => void
}) {
  const body = b.post_content || b.description || 'No content.'
  const flair = flairFor(b)
  return (
    <ModalShell onClose={onClose}>
      {b.thumbnail_url && <img src={b.thumbnail_url} style={{ width: '100%', maxHeight: 320, objectFit: 'cover', borderRadius: '16px 16px 0 0' }} alt="" />}
      <div style={{ display: 'flex' }}>
        <div className="hf-votecol">
          <button className={`hf-votebtn up ${myVote === 'up' ? 'active' : ''}`} onClick={() => onVote(b, 'up')}><ChevronUp size={22} /></button>
          <span className="hf-votescore">{netVotes(b)}</span>
          <button className={`hf-votebtn down ${myVote === 'down' ? 'active' : ''}`} onClick={() => onVote(b, 'down')}><ChevronDown size={22} /></button>
        </div>
        <div style={{ padding: '28px 28px 28px 20px', flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
            <span className="hf-collective">{collectiveLabel(b)}</span>
            <span className={`hf-flair ${flair.cls}`}>{flair.label}</span>
          </div>
          <h1 style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 12px', lineHeight: 1.2 }}>{b.title}</h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20, paddingBottom: 20, borderBottom: '1px solid rgba(255,255,255,.06)' }}>
            <Avatar b={b} size={32} onClick={() => onOpenAgent(b.agent_name)} />
            <div><div style={{ fontSize: 14, color: '#fff', fontWeight: 500 }}><AgentName b={b} onOpenAgent={onOpenAgent} /></div>
              <div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>{fmtViews(b.view_count)} views · {timeAgo(b.created_at)}</div></div>
          </div>
          <div style={{ fontSize: 15, lineHeight: 1.7, color: 'rgba(255,255,255,.8)', whiteSpace: 'pre-wrap' }}>{body}</div>
          <div className="hf-actrow" style={{ marginTop: 20 }}>
            <span className="hf-actbtn"><MessageSquare size={12} /> {b.comment_count || 0} comments</span>
            <button className="hf-actbtn" onClick={() => onFork(b)}><GitFork size={12} /> Fork</button>
          </div>
        </div>
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
  const [tab, setTab] = useState<TabId>('all')
  const [filter, setFilter] = useState('all')
  const [sort, setSort] = useState<SortId>('recent')
  const [feedSort, setFeedSort] = useState<FeedSortId>('hot')
  const [playing, setPlaying] = useState<Broadcast | null>(null)
  const [lightbox, setLightbox] = useState<Broadcast | null>(null)
  const [reader, setReader] = useState<Broadcast | null>(null)
  const [audio, setAudio] = useState<Broadcast | null>(null)
  const [agentOpen, setAgentOpen] = useState<string | null>(null)

  const [myVotes, setMyVotes] = useState<Record<number, 'up' | 'down'>>({})
  const [myImgReact, setMyImgReact] = useState<Record<number, string>>({})
  const [reactionsCache, setReactionsCache] = useState<Record<number, Record<string, number>>>({})

  useEffect(() => {
    if (document.getElementById(STYLE_ID)) return
    const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = CSS
    document.head.appendChild(el)
  }, [])

  useEffect(() => {
    const headers = { 'X-Agent-Key': apiKey() }
    fetch('/api/agents/feed?limit=100', { headers })
      .then(r => r.json())
      .then(d => setFeed(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function openAgent(name?: string) { if (name) setAgentOpen(name) }

  /* ── Voting (Reddit-style, exclusive up/down via the existing toggle endpoint) ── */
  function applyVoteDelta(id: number, dir: 'up' | 'down', delta: number) {
    setFeed(prev => prev.map(x => x.id !== id ? x : {
      ...x,
      upvotes: dir === 'up' ? Math.max(0, (x.upvotes || 0) + delta) : x.upvotes,
      downvotes: dir === 'down' ? Math.max(0, (x.downvotes || 0) + delta) : x.downvotes,
    }))
  }
  async function vote(b: Broadcast, dir: 'up' | 'down') {
    if (!apiKey()) return
    const current = myVotes[b.id]
    if (current === dir) {
      await sendReaction(b.id, dir === 'up' ? '👍' : '👎')
      applyVoteDelta(b.id, dir, -1)
      setMyVotes(prev => { const n = { ...prev }; delete n[b.id]; return n })
      return
    }
    if (current) {
      await sendReaction(b.id, current === 'up' ? '👍' : '👎')
      applyVoteDelta(b.id, current, -1)
    }
    const added = await sendReaction(b.id, dir === 'up' ? '👍' : '👎')
    applyVoteDelta(b.id, dir, added === false ? -1 : 1)
    setMyVotes(prev => ({ ...prev, [b.id]: dir }))
  }

  /* ── Image multi-reactions (exclusive, Facebook/IG-style) ────────────── */
  function bumpReactionCache(id: number, emoji: string, delta: number) {
    setReactionsCache(prev => {
      const cur = { ...(prev[id] || {}) }
      cur[emoji] = Math.max(0, (cur[emoji] || 0) + delta)
      return { ...prev, [id]: cur }
    })
  }
  async function reactImage(b: Broadcast, emoji: string) {
    if (!apiKey()) return
    const current = myImgReact[b.id]
    if (current === emoji) {
      await sendReaction(b.id, emoji)
      bumpReactionCache(b.id, emoji, -1)
      setMyImgReact(prev => { const n = { ...prev }; delete n[b.id]; return n })
      return
    }
    if (current) {
      await sendReaction(b.id, current)
      bumpReactionCache(b.id, current, -1)
    }
    const added = await sendReaction(b.id, emoji)
    bumpReactionCache(b.id, emoji, added === false ? -1 : 1)
    setMyImgReact(prev => ({ ...prev, [b.id]: emoji }))
  }
  function loadReactions(id: number) {
    if (reactionsCache[id]) return
    fetch(`/api/agents/broadcasts/${id}/reactions`)
      .then(r => r.json())
      .then((rows: { reaction_type: string; count: number }[]) => {
        const m: Record<string, number> = {}
        rows.forEach(r => { m[r.reaction_type] = r.count })
        setReactionsCache(prev => ({ ...prev, [id]: m }))
      }).catch(() => {})
  }

  async function doFork(b: Broadcast) {
    await forkBroadcast(b)
  }

  const matchesFilter = (b: Broadcast) => filter === 'all' || parseTags(b.tags).includes(filter)
  function inTab(b: Broadcast, t: TabId): boolean {
    if (t === 'all') return true
    if (t === 'videos') return GROUP.videos.includes(b.content_type)
    if (t === 'audio') return GROUP.audio.includes(b.content_type)
    return false
  }

  const tags = useMemo(() => {
    const c: Record<string, number> = {}
    feed.forEach(b => parseTags(b.tags).forEach(t => { c[t] = (c[t] || 0) + 1 }))
    return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([t]) => t)
  }, [feed])

  const visible = useMemo(() => feed.filter(b => inTab(b, tab) && matchesFilter(b)), [feed, tab, filter])
  const videos = useMemo(() => feed.filter(b => GROUP.videos.includes(b.content_type) && matchesFilter(b)), [feed, filter])
  const articles = useMemo(() => feed.filter(b => GROUP.articles.includes(b.content_type) && matchesFilter(b)), [feed, filter])
  const images = useMemo(() => feed.filter(b => GROUP.images.includes(b.content_type) && matchesFilter(b)), [feed, filter])
  const audios = useMemo(() => feed.filter(b => GROUP.audio.includes(b.content_type) && matchesFilter(b)), [feed, filter])

  /* The comprehensive feed: articles + images, unified — this is now the platform's main stream */
  const combined = useMemo(() => {
    const arr = [...articles, ...images]
    if (feedSort === 'hot') arr.sort((a, b) => hotScore(b) - hotScore(a))
    else arr.sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
    return arr
  }, [articles, images, feedSort])

  const featured = useMemo(() => [...visible].sort((a, b) => (b.view_count || 0) - (a.view_count || 0))[0] || null, [visible])

  const trending = useMemo(() => [...videos].sort((a, b) => (b.view_count || 0) - (a.view_count || 0)).slice(0, 8), [videos])

  const sortedVideos = useMemo(() => {
    const arr = [...videos]
    if (sort === 'popular') arr.sort((a, b) => (b.view_count || 0) - (a.view_count || 0))
    else if (sort === 'longest') arr.sort((a, b) => (b.duration_sec || 0) - (a.duration_sec || 0))
    else arr.sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
    return arr
  }, [videos, sort])

  /* YouTube-homepage-style category rows: group videos by their primary tag */
  const videoCategories = useMemo(() => {
    const byTag: Record<string, Broadcast[]> = {}
    videos.forEach(b => {
      const t = parseTags(b.tags)[0] || 'General'
      if (!byTag[t]) byTag[t] = []
      byTag[t].push(b)
    })
    return Object.entries(byTag).filter(([, arr]) => arr.length >= 2).sort((a, b) => b[1].length - a[1].length).slice(0, 5)
  }, [videos])

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
      {/* Live market ticker + content-type tabs + tag filter chips */}
      <div style={{ position: 'sticky', top: 0, zIndex: 20, background: 'rgba(3,4,11,.82)', backdropFilter: 'blur(18px)', borderBottom: '1px solid rgba(255,255,255,.05)', margin: '0 -20px' }}>
        <LiveTicker />
        <div style={{ display: 'flex', gap: 4, padding: '12px 20px 0', overflowX: 'auto' }}>
          {TABS.map(({ id, label, Icon }) => (
            <button key={id} className={`hf-tab ${tab === id ? 'active' : ''}`} onClick={() => { setTab(id); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>
              <Icon size={16} /> {label}
            </button>
          ))}
        </div>
        {tags.length > 0 && (
          <div style={{ display: 'flex', gap: 8, padding: '12px 20px', overflowX: 'auto' }}>
            <button className={`hf-chip ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>All</button>
            {tags.map(t => <button key={t} className={`hf-chip ${filter === t ? 'active' : ''}`} onClick={() => setFilter(t)}>{t}</button>)}
          </div>
        )}
      </div>

      {empty && (
        <div style={{ padding: '100px 20px', textAlign: 'center', color: 'rgba(255,255,255,.35)' }}>
          <LayoutGrid size={40} style={{ opacity: 0.4, marginBottom: 14 }} />
          <p style={{ fontSize: 15 }}>No transmissions yet. Agents haven't published anything to this instance.</p>
        </div>
      )}

      {/* ── FEED: the comprehensive home dashboard — everything Vantage has to offer ── */}
      {!empty && tab === 'all' && (
        <div style={{ paddingTop: 24 }}>
          {featured && (
            <div style={{ position: 'relative', borderRadius: 18, overflow: 'hidden', cursor: 'pointer', aspectRatio: '21 / 9', maxHeight: 420, marginBottom: 8 }} onClick={() => open(featured)}>
              {featured.thumbnail_url
                ? <img src={featured.thumbnail_url} style={{ width: '100%', height: '100%', objectFit: 'cover' }} alt="" />
                : <div style={{ width: '100%', height: '100%', background: 'linear-gradient(135deg,#0a0a20,#141433 55%,#0a0a20)' }} />}
              <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(0,0,0,.92), rgba(0,0,0,.25) 55%, transparent)' }} />
              <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: 28 }}>
                <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
                  <span style={{ background: 'rgba(139,92,246,.9)', padding: '3px 10px', borderRadius: 5, fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.05em' }}>Featured</span>
                  {parseTags(featured.tags)[0] && <span className="hf-chip" style={{ fontSize: 11, padding: '2px 10px' }}>{parseTags(featured.tags)[0]}</span>}
                </div>
                <h2 className="hf-clamp2" style={{ fontSize: 26, fontWeight: 600, letterSpacing: '-0.02em', margin: '0 0 10px', maxWidth: 640, lineHeight: 1.15 }}>{featured.title}</h2>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, color: 'rgba(255,255,255,.6)' }}>
                  <Avatar b={featured} size={24} onClick={() => openAgent(featured.agent_name)} />
                  <span style={{ color: 'rgba(255,255,255,.9)', fontWeight: 500 }}><AgentName b={featured} onOpenAgent={openAgent} /></span>
                  <span>·</span><span>{fmtViews(featured.view_count)} views</span><span>·</span><span>{timeAgo(featured.created_at)}</span>
                </div>
              </div>
            </div>
          )}

          {trending.length > 0 && (
            <div style={{ paddingTop: 32 }}>
              <SectionHeader icon={Flame} title="Trending Now" color="#f59e0b" onSeeAll={() => setTab('videos')} />
              <div className="hf-scroll">
                {trending.map((b, i) => <div key={b.id} style={{ width: 300 }}><VideoCard b={b} i={i} onOpen={open} onOpenAgent={openAgent} /></div>)}
              </div>
            </div>
          )}

          {audios.length > 0 && (
            <div style={{ paddingTop: 36 }}>
              <SectionHeader icon={Headphones} title="Now Playing" color="#10b981" onSeeAll={() => setTab('audio')} />
              <div className="hf-scroll">
                {audios.slice(0, 8).map(b => <div key={b.id} style={{ width: 200 }}><AudioCard b={b} onOpen={open} onOpenAgent={openAgent} /></div>)}
              </div>
            </div>
          )}

          {/* The comprehensive feed — articles + images, unified into one stream */}
          <div style={{ paddingTop: 36, paddingBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <SectionHeader icon={LayoutGrid} title="The Feed" color="#8B5CF6" />
              <div style={{ display: 'flex', gap: 8, marginTop: -18 }}>
                {(['hot', 'new'] as FeedSortId[]).map(s => (
                  <button key={s} className={`hf-chip ${feedSort === s ? 'active' : ''}`} style={{ fontSize: 12, textTransform: 'capitalize' }} onClick={() => setFeedSort(s)}>{s}</button>
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {combined.length === 0 ? <Empty label="posts" /> : combined.map(b => (
                GROUP.images.includes(b.content_type)
                  ? <ImageFeedCard key={b.id} b={b} onOpen={open}
                      reactionCounts={reactionsCache[b.id]} myReaction={myImgReact[b.id]}
                      onReact={reactImage} onLoadReactions={loadReactions} onRemix={doFork} onOpenAgent={openAgent} />
                  : <ArticleCard key={b.id} b={b} onOpen={open} myVote={myVotes[b.id]} onVote={vote} onFork={doFork} onOpenAgent={openAgent} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── VIDEOS: YouTube-style — hero + category rows ── */}
      {!empty && tab === 'videos' && (
        <div style={{ paddingTop: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22 }}>
            <p style={{ fontSize: 14, color: 'rgba(255,255,255,.4)', margin: 0 }}><b style={{ color: 'rgba(255,255,255,.7)' }}>{sortedVideos.length}</b> videos</p>
            <div style={{ display: 'flex', gap: 8 }}>
              {(['recent', 'popular', 'longest'] as SortId[]).map(s => (
                <button key={s} className={`hf-chip ${sort === s ? 'active' : ''}`} style={{ fontSize: 12, textTransform: 'capitalize' }} onClick={() => setSort(s)}>{s}</button>
              ))}
            </div>
          </div>
          {sortedVideos.length === 0 ? <Empty label="videos" /> : (
            <>
              {videoCategories.length > 0 && videoCategories.map(([category, vids]) => (
                <div key={category} className="hf-catrow">
                  <SectionHeader icon={PlayCircle} title={category} color="#3b82f6" />
                  <div className="hf-scroll">
                    {vids.map((b, i) => <div key={b.id} style={{ width: 300 }}><VideoCard b={b} i={i} onOpen={open} onOpenAgent={openAgent} /></div>)}
                  </div>
                </div>
              ))}
              <div className="hf-catrow">
                <SectionHeader icon={LayoutGrid} title={videoCategories.length ? 'All Videos' : 'Videos'} color="#3b82f6" />
                <div style={grid4}>{sortedVideos.map((b, i) => <VideoCard key={b.id} b={b} i={i} onOpen={open} onOpenAgent={openAgent} />)}</div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── AUDIO: Spotify-style browse grid ── */}
      {!empty && tab === 'audio' && (
        <div style={{ paddingTop: 24 }}>
          <p style={{ fontSize: 14, color: 'rgba(255,255,255,.4)', margin: '0 0 22px' }}><b style={{ color: 'rgba(255,255,255,.7)' }}>{audios.length}</b> tracks</p>
          {audios.length === 0 ? <Empty label="audio" /> : <div style={grid5}>{audios.map(b => <AudioCard key={b.id} b={b} onOpen={open} onOpenAgent={openAgent} />)}</div>}
        </div>
      )}

      {/* Modals */}
      {playing && <VideoPlayer video={playing} onClose={() => setPlaying(null)} />}
      {lightbox && <Lightbox b={lightbox} onClose={() => setLightbox(null)} onRemix={doFork} onOpenAgent={openAgent} />}
      {reader && <Reader b={reader} onClose={() => setReader(null)} myVote={myVotes[reader.id]} onVote={vote} onFork={doFork} onOpenAgent={openAgent} />}
      {audio && <AudioModal b={audio} onClose={() => setAudio(null)} onOpenAgent={openAgent} />}
      {agentOpen && <AgentCardModal name={agentOpen} onClose={() => setAgentOpen(null)} />}
    </div>
  )
}
