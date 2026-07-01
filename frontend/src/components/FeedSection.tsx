import React from 'react'
import VideoCard from './VideoCard'

interface Broadcast {
  id: number; title: string; content_type: string; stream_url?: string | null
  thumbnail_url?: string | null; duration_sec?: number; view_count?: number
  agent_name?: string; created_at: string; agent_id?: number; status?: string
}

interface SectionProps {
  title: string
  icon?: string
  broadcasts: Broadcast[]
  onPlay: (v: Broadcast) => void
  seeAll?: string
  emptyMsg?: string
}

export default function FeedSection({ title, icon, broadcasts, onPlay, seeAll, emptyMsg }: SectionProps) {
  if (broadcasts.length === 0 && emptyMsg) return null

  return (
    <div style={{ marginBottom: 36 }}>
      {/* Section header */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
        {icon && <span style={{ marginRight: 8, fontSize: 18 }}>{icon}</span>}
        <h2 style={{ color: '#d0d0e0', fontSize: 20, fontWeight: 700, margin: 0, letterSpacing: 0.5 }}>{title}</h2>
        {seeAll && (
          <a href={seeAll} style={{ marginLeft: 'auto', color: '#556677', fontSize: 13, textDecoration: 'none' }}>
            see all →
          </a>
        )}
      </div>

      {/* Horizontal scrollable row */}
      {broadcasts.length > 0 ? (
        <div style={{
          display: 'flex', gap: 14, overflowX: 'auto', paddingBottom: 8,
          scrollbarWidth: 'thin', scrollbarColor: '#222 transparent',
        }}>
          {broadcasts.map(b => (
            <VideoCard key={b.id} video={b} onPlay={onPlay} />
          ))}
        </div>
      ) : (
        <p style={{ color: '#445', fontSize: 14, fontStyle: 'italic' }}>{emptyMsg}</p>
      )}
    </div>
  )
}
