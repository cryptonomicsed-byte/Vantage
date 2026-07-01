import React from 'react'
import { Play, Clock, Eye } from 'lucide-react'

interface Broadcast {
  id: number
  title: string
  description?: string
  content_type: string
  stream_url?: string | null
  thumbnail_url?: string | null
  duration_sec?: number
  view_count?: number
  agent_id?: number
  agent_name?: string
  created_at: string
  status?: string
}

export default function VideoCard({ video, onPlay }: { video: Broadcast; onPlay: (v: Broadcast) => void }) {
  const fmtDuration = (sec?: number) => {
    if (!sec) return ''
    const m = Math.floor(sec / 60)
    const s = Math.floor(sec % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  const timeAgo = (date: string) => {
    const diff = Date.now() - new Date(date).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 60) return `${mins}m`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h`
    return `${Math.floor(hrs / 24)}d`
  }

  const thumbSrc = video.thumbnail_url || '/media/videos/default-thumb.jpg'
  const initials = (video.agent_name || '?').slice(0, 2).toUpperCase()

  return (
    <div
      onClick={() => onPlay(video)}
      style={{
        cursor: 'pointer', borderRadius: 12, overflow: 'hidden',
        background: '#111122', border: '1px solid #1a1a2e',
        transition: 'transform 0.15s, border-color 0.15s',
        flexShrink: 0, width: 330,
      }}
      onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.borderColor = '#334' }}
      onMouseLeave={e => { e.currentTarget.style.transform = ''; e.currentTarget.style.borderColor = '' }}
    >
      {/* Thumbnail */}
      <div style={{ position: 'relative', width: '100%', height: 186, background: '#08081a', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {video.content_type === 'video' ? (
          <div style={{ textAlign: 'center' }}>
            <Play size={36} color="#00ffcc" style={{ opacity: 0.8 }} />
            <div style={{ color: '#446', fontSize: 12, marginTop: 6 }}>{video.title?.slice(0, 30)}</div>
          </div>
        ) : (
          <div style={{ fontSize: 48, opacity: 0.3 }}>🎬</div>
        )}
        {video.duration_sec && (
          <div style={{
            position: 'absolute', bottom: 8, right: 8, background: 'rgba(0,0,0,0.85)',
            padding: '2px 8px', borderRadius: 4, fontSize: 12, color: '#fff',
            display: 'flex', alignItems: 'center', gap: 4,
          }}>
            <Clock size={11} /> {fmtDuration(video.duration_sec)}
          </div>
        )}
      </div>

      {/* Info */}
      <div style={{ padding: '12px 14px' }}>
        <h3 style={{ margin: 0, color: '#eee', fontSize: 15, fontWeight: 600, lineHeight: 1.3, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{video.title}</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
          <div style={{
            width: 24, height: 24, borderRadius: '50%', background: '#1a3355',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 10, color: '#00ffcc', fontWeight: 700, flexShrink: 0,
          }}>{initials}</div>
          <span style={{ color: '#889', fontSize: 13 }}>{video.agent_name || 'Unknown'}</span>
        </div>
        <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 12, color: '#556' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Eye size={12} /> {video.view_count || 0}</span>
          <span>{timeAgo(video.created_at)}</span>
          <span style={{ textTransform: 'capitalize' }}>{video.status || video.content_type}</span>
        </div>
      </div>
    </div>
  )
}
