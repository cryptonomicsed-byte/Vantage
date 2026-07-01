import React from 'react'
import { Play, Eye, Sparkles } from 'lucide-react'

interface Broadcast {
  id: number; title: string; content_type: string; stream_url?: string | null
  thumbnail_url?: string | null; duration_sec?: number; view_count?: number
  agent_name?: string; created_at: string; agent_id?: number
}

export default function HeroSection({ featured, onPlay }: { featured: Broadcast | null; onPlay: (v: Broadcast) => void }) {
  if (!featured) return null

  const timeAgo = (d: string) => {
    const diff = Date.now() - new Date(d).getTime()
    const hrs = Math.floor(diff / 3600000)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  }

  return (
    <div
      onClick={() => onPlay(featured)}
      style={{
        borderRadius: 16, overflow: 'hidden', cursor: 'pointer',
        background: 'linear-gradient(135deg, #0a0a20 0%, #111133 50%, #0a0a20 100%)',
        border: '1px solid #1a1a3e', marginBottom: 28,
        position: 'relative', minHeight: 320,
        display: 'flex', flexDirection: 'column',
      }}
    >
      {/* Background ambient glow */}
      <div style={{
        position: 'absolute', top: '-30%', right: '-10%',
        width: 500, height: 500, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(0,255,200,0.06), transparent 70%)',
        pointerEvents: 'none',
      }} />

      <div style={{ display: 'flex', flex: 1, padding: 32, gap: 32, alignItems: 'center', position: 'relative', zIndex: 1 }}>
        {/* Thumbnail area */}
        <div style={{
          width: 320, height: 180, borderRadius: 12,
          background: '#08081a', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: '1px solid #1a1a3e',
        }}>
          <div style={{ width: 64, height: 64, borderRadius: '50%', background: 'rgba(0,255,200,0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Play size={28} color="#00ffcc" />
          </div>
        </div>

        {/* Info */}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
            <span style={{ padding: '2px 10px', borderRadius: 10, background: 'rgba(0,255,200,0.12)', color: '#00ffcc', fontSize: 11, fontWeight: 600 }}>FEATURED</span>
            <span style={{ padding: '2px 10px', borderRadius: 10, background: 'rgba(255,200,0,0.1)', color: '#ffcc00', fontSize: 11, fontWeight: 600 }}>
              <Sparkles size={11} style={{ verticalAlign: 'middle', marginRight: 4 }} />NEW
            </span>
          </div>
          <h2 style={{ color: '#fff', fontSize: 28, fontWeight: 700, margin: 0, lineHeight: 1.2 }}>{featured.title}</h2>
          <p style={{ color: '#778', fontSize: 15, marginTop: 8, lineHeight: 1.4 }}>{featured.description || 'No description'}</p>

          <div style={{ display: 'flex', gap: 24, marginTop: 16, fontSize: 13, color: '#556' }}>
            <span style={{ color: '#00ffcc', fontWeight: 600 }}>{featured.agent_name}</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Eye size={14} /> {featured.view_count || 0} views</span>
            <span>{timeAgo(featured.created_at)}</span>
            {featured.duration_sec && <span>{Math.floor(featured.duration_sec / 60)}:{String(Math.floor(featured.duration_sec % 60)).padStart(2, '0')}</span>}
          </div>
        </div>
      </div>
    </div>
  )
}
