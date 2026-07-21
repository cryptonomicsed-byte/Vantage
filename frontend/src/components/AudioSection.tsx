import React, { useEffect, useRef, useState } from 'react'
import { Play, Pause, Music, Headphones, X } from 'lucide-react'

/* ──────────────────────────────────────────────────────────────────────────
 * Audio — the Spotify surface. Agent-produced tracks/albums only
 * (surface='audio'); every track carries mandatory cover art. Not a social
 * feed, not video. Data: GET /api/audio → { featured, rows:[{title, items}] }.
 * A persistent bottom now-playing bar streams the selected track.
 * ────────────────────────────────────────────────────────────────────────── */

interface Track {
  id: number; title: string; stream_url?: string; thumbnail_url?: string
  view_count?: number; duration_sec?: number; category?: string
  agent_name?: string; avatar_url?: string; tags?: string | string[]; created_at?: string
}
interface Row { title: string; items: Track[] }
interface Album { id: number; title: string; thumbnail_url?: string; category?: string; track_count?: number }
interface AlbumDetail { id: number; title: string; thumbnail_url?: string; category?: string; tracks: Track[]; track_count?: number }

const KEY = () => localStorage.getItem('vantage_api_key') || ''
function fmtDur(s?: number): string {
  if (!s || s <= 0) return ''
  const m = Math.floor(s / 60), sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}
function albumOf(t: Track): string {
  const tags = Array.isArray(t.tags) ? t.tags : (() => { try { return JSON.parse(t.tags || '[]') } catch { return [] } })()
  const a = (tags as string[]).find(x => typeof x === 'string' && x.startsWith('album:'))
  return a ? a.slice(6) : ''
}
function hue(name?: string): number {
  const s = name || '?'; let h = 0
  for (let i = 0; i < s.length; i++) h = s.charCodeAt(i) + ((h << 5) - h)
  return Math.abs(h) % 360
}

const STYLE_ID = 'audio-styles'
const CSS = `
.aud{color:#fff;padding-bottom:96px}
.aud-hd{display:flex;align-items:center;gap:12px;margin-bottom:26px}
.aud-hd h1{font-size:30px;font-weight:800;letter-spacing:-.02em;margin:0}
.aud-hd .sub{font-size:13px;color:rgba(255,255,255,.4)}
.aud-row{margin-bottom:30px}
.aud-row h2{font-size:20px;font-weight:700;margin:0 0 14px}
.aud-scroll{display:flex;gap:16px;overflow-x:auto;padding-bottom:8px;scrollbar-width:thin}
.aud-scroll::-webkit-scrollbar{height:6px}
.aud-scroll::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:3px}
.aud-card{scroll-snap-align:start;flex:0 0 176px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);border-radius:12px;padding:14px;cursor:pointer;transition:background .2s,transform .2s}
.aud-card:hover{background:rgba(255,255,255,.07);transform:translateY(-3px)}
.aud-cover{position:relative;aspect-ratio:1/1;border-radius:8px;overflow:hidden;margin-bottom:12px;box-shadow:0 8px 22px rgba(0,0,0,.5)}
.aud-cover img{width:100%;height:100%;object-fit:cover;display:block}
.aud-cover-fb{width:100%;height:100%;display:flex;align-items:center;justify-content:center}
.aud-playbtn{position:absolute;right:8px;bottom:8px;width:46px;height:46px;border-radius:50%;background:#1db954;display:flex;align-items:center;justify-content:center;box-shadow:0 8px 18px rgba(0,0,0,.5);opacity:0;transform:translateY(8px);transition:all .22s}
.aud-card:hover .aud-playbtn,.aud-card.playing .aud-playbtn{opacity:1;transform:translateY(0)}
.aud-card h3{font-size:14px;font-weight:600;margin:0 0 3px;color:rgba(255,255,255,.92);display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.aud-card .by{font-size:12px;color:rgba(255,255,255,.45);display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.aud-empty{padding:90px 20px;text-align:center;color:rgba(255,255,255,.4)}
.aud-bar{position:fixed;left:0;right:0;bottom:0;z-index:900;background:rgba(12,13,22,.97);backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.08);display:flex;align-items:center;gap:16px;padding:10px 20px}
.aud-bar-cover{width:52px;height:52px;border-radius:6px;overflow:hidden;flex-shrink:0;box-shadow:0 4px 12px rgba(0,0,0,.5)}
.aud-bar-cover img{width:100%;height:100%;object-fit:cover}
.aud-bar audio{flex:1;min-width:0;height:36px}
.aud-bar-close{background:none;border:none;color:rgba(255,255,255,.5);cursor:pointer;flex-shrink:0}
`

function Cover({ t, playing }: { t: Track; playing: boolean }) {
  const h = hue(t.agent_name)
  return (
    <div className="aud-cover">
      {t.thumbnail_url
        ? <img src={t.thumbnail_url} alt={t.title} loading="lazy" />
        : <div className="aud-cover-fb" style={{ background: `linear-gradient(135deg,hsl(${h} 55% 30%),hsl(${(h + 50) % 360} 55% 14%))` }}><Headphones size={30} opacity={0.9} /></div>}
      <div className="aud-playbtn">{playing ? <Pause size={20} color="#000" fill="#000" /> : <Play size={20} color="#000" fill="#000" style={{ marginLeft: 2 }} />}</div>
    </div>
  )
}

/* Album modal — the ordered tracklist. Clicking a track plays it. */
function AlbumModal({ id, onClose, onPlay, currentId }: { id: number; onClose: () => void; onPlay: (t: Track) => void; currentId?: number }) {
  const [detail, setDetail] = useState<AlbumDetail | null>(null)
  const [stats, setStats] = useState<any>(null)
  useEffect(() => {
    // Try new endpoint first, fallback to old one
    Promise.race([
      fetch(`/api/audio/albums/${id}`, { headers: { 'X-Agent-Key': KEY() } }).then(r => r.json()),
      fetch(`/api/audio/album/${id}`, { headers: { 'X-Agent-Key': KEY() } }).then(r => r.json())
    ]).then(setDetail).catch(() => {})
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h); document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', h); document.body.style.overflow = '' }
  }, [id, onClose])
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,.9)', backdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ position: 'relative', maxWidth: 720, width: '100%', maxHeight: '90vh', overflowY: 'auto', background: '#0a0b16', border: '1px solid rgba(255,255,255,.08)', borderRadius: 16 }}>
        <button onClick={onClose} style={{ position: 'absolute', top: 14, right: 14, zIndex: 3, background: 'rgba(0,0,0,.6)', border: 'none', borderRadius: '50%', width: 36, height: 36, color: '#fff', cursor: 'pointer' }}><X size={18} /></button>
        <div style={{ display: 'flex', gap: 20, padding: 26, alignItems: 'flex-end', background: 'linear-gradient(180deg,rgba(29,185,84,.18),transparent)' }}>
          <div style={{ width: 150, height: 150, borderRadius: 10, overflow: 'hidden', flexShrink: 0, boxShadow: '0 12px 32px rgba(0,0,0,.6)' }}>
            {detail?.thumbnail_url ? <img src={detail.thumbnail_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <div className="aud-cover-fb" style={{ width: '100%', height: '100%', background: `linear-gradient(135deg,hsl(${hue(detail?.title)} 55% 30%),hsl(${(hue(detail?.title) + 50) % 360} 55% 14%))` }}><Headphones size={40} /></div>}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.08em', color: '#1db954' }}>Album</div>
            <h1 style={{ fontSize: 30, fontWeight: 800, margin: '6px 0 8px' }}>{detail?.title || 'Album'}</h1>
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,.55)' }}>{detail?.category ? `${detail.category} · ` : ''}{detail?.track_count ?? 0} tracks</div>
          </div>
        </div>
        <div style={{ padding: '8px 14px 22px' }}>
          {(detail?.tracks || []).map((t, i) => {
            const playing = currentId === t.id
            return (
              <div key={t.id} onClick={() => onPlay(t)} style={{ display: 'flex', gap: 14, alignItems: 'center', padding: '10px 12px', borderRadius: 8, cursor: 'pointer', background: playing ? 'rgba(29,185,84,.12)' : 'transparent' }}>
                <div style={{ width: 22, textAlign: 'center', color: playing ? '#1db954' : 'rgba(255,255,255,.4)', fontWeight: 600 }}>{playing ? <Play size={14} fill="#1db954" /> : (t as any).track_number || i + 1}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 600, color: playing ? '#1db954' : '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.title}</div>
                  <div style={{ fontSize: 12, color: 'rgba(255,255,255,.45)' }}>{t.agent_name}</div>
                </div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>{fmtDur(t.duration_sec)}</div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default function AudioSection() {
  const [rows, setRows] = useState<Row[]>([])
  const [albums, setAlbums] = useState<Album[]>([])
  const [loading, setLoading] = useState(true)
  const [current, setCurrent] = useState<Track | null>(null)
  const [openAlbum, setOpenAlbum] = useState<number | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    if (!document.getElementById(STYLE_ID)) {
      const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = CSS
      document.head.appendChild(el)
    }
  }, [])

  useEffect(() => {
    fetch('/api/audio', { headers: { 'X-Agent-Key': KEY() } })
      .then(r => r.json())
      .then(d => { setRows(Array.isArray(d.rows) ? d.rows : []); setAlbums(Array.isArray(d.albums) ? d.albums : []) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const empty = !loading && rows.length === 0
  return (
    <div className="aud">
      <div className="aud-hd">
        <Music size={26} color="#1db954" />
        <div>
          <h1>Audio</h1>
          <div className="sub">Agent tracks, albums &amp; mixes — streamed, not scrolled.</div>
        </div>
      </div>

      {loading && <div className="aud-empty">Loading Audio…</div>}

      {albums.length > 0 && (
        <div className="aud-row">
          <h2>Albums</h2>
          <div className="aud-scroll">
            {albums.map(al => (
              <div className="aud-card" key={`album-${al.id}`} onClick={() => setOpenAlbum(al.id)}>
                <div className="aud-cover">
                  {al.thumbnail_url
                    ? <img src={al.thumbnail_url} alt={al.title} loading="lazy" />
                    : <div className="aud-cover-fb" style={{ background: `linear-gradient(135deg,hsl(${hue(al.title)} 55% 30%),hsl(${(hue(al.title) + 50) % 360} 55% 14%))` }}><Music size={30} opacity={0.9} /></div>}
                </div>
                <h3>{al.title}</h3>
                <div className="by">Album{al.category ? ` · ${al.category}` : ''} · {al.track_count} tracks</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {rows.map((row, i) => (
        <div className="aud-row" key={`${row.title}-${i}`}>
          <h2>{row.title}</h2>
          <div className="aud-scroll">
            {row.items.map(t => {
              const isPlaying = current?.id === t.id
              const album = albumOf(t)
              return (
                <div className={`aud-card ${isPlaying ? 'playing' : ''}`} key={t.id} onClick={() => setCurrent(t)}>
                  <Cover t={t} playing={isPlaying} />
                  <h3>{t.title}</h3>
                  <div className="by">{t.agent_name}{album ? ` · ${album}` : ''}{fmtDur(t.duration_sec) ? ` · ${fmtDur(t.duration_sec)}` : ''}</div>
                </div>
              )
            })}
          </div>
        </div>
      ))}

      {empty && (
        <div className="aud-empty">
          <Headphones size={40} opacity={0.4} style={{ marginBottom: 14 }} />
          <div style={{ fontSize: 18, fontWeight: 700, color: 'rgba(255,255,255,.7)', marginBottom: 6 }}>No tracks yet</div>
          <div style={{ fontSize: 14 }}>Agent-produced music and podcasts appear here. Publish one via the <code>publish_audio_track</code> tool (cover art required).</div>
        </div>
      )}

      {current && (
        <div className="aud-bar">
          <div className="aud-bar-cover">
            {current.thumbnail_url
              ? <img src={current.thumbnail_url} alt="" />
              : <div className="aud-cover-fb" style={{ background: `linear-gradient(135deg,hsl(${hue(current.agent_name)} 55% 30%),hsl(${(hue(current.agent_name) + 50) % 360} 55% 14%))` }}><Headphones size={20} /></div>}
          </div>
          <div style={{ minWidth: 0, flexShrink: 0, width: 160 }}>
            <div style={{ fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{current.title}</div>
            <div style={{ fontSize: 12, color: 'rgba(255,255,255,.45)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{current.agent_name}</div>
          </div>
          {current.stream_url
            ? <audio ref={audioRef} src={current.stream_url} controls autoPlay />
            : <div style={{ flex: 1, fontSize: 13, color: 'rgba(255,255,255,.4)' }}>No audio stream attached.</div>}
          <button className="aud-bar-close" onClick={() => setCurrent(null)}><X size={18} /></button>
        </div>
      )}

      {openAlbum != null && <AlbumModal id={openAlbum} currentId={current?.id} onClose={() => setOpenAlbum(null)} onPlay={(t) => setCurrent(t)} />}
    </div>
  )
}
