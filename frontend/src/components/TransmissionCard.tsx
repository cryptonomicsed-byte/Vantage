import React from 'react'

interface Broadcast {
  id: number; title: string; content_type: string
  agent_name?: string; created_at: string
  view_count?: number; stream_url?: string | null
}

const TYPE_COLORS: Record<string, string> = {
  video: '#00ffcc', text: '#ffd93d', debate: '#ff6b6b',
  graph: '#6c5ce7', audio: '#ff8c42', image: '#48dbfb',
  tro: '#f368e0', video_note: '#00ffcc',
}
const TYPE_ICONS: Record<string, string> = {
  video: '▶', text: '¶', debate: '⚡', graph: '◈',
  audio: '♪', image: '▣', tro: '◆', video_note: '▶',
}

function timeAgo(d: string) {
  const diff = Date.now() - new Date(d).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

export default function TransmissionCard({ tx, onPlay }: { tx: Broadcast; onPlay: (t: Broadcast) => void }) {
  const color = TYPE_COLORS[tx.content_type] || '#556677'
  const icon = TYPE_ICONS[tx.content_type] || '○'
  const isVideo = tx.content_type === 'video'

  return (
    <div onClick={() => isVideo && onPlay(tx)} style={{
      display: 'flex', alignItems: 'center', gap: 14,
      padding: '10px 16px', cursor: isVideo ? 'pointer' : 'default',
      borderBottom: '1px solid rgba(255,255,255,0.03)',
      transition: 'background 0.12s',
    }}
    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.02)'}
    onMouseLeave={e => e.currentTarget.style.background = ''}
    >
      {/* Type badge */}
      <div style={{
        width: 28, height: 28, borderRadius: 6, flexShrink: 0,
        background: `${color}14`, border: `1px solid ${color}33`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 13, color,
      }}>{icon}</div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, color: '#d0d0e0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: 500 }}>
          {isVideo && <span style={{ color, fontSize: 10, marginRight: 6 }}>▶</span>}
          {tx.title}
        </div>
        <div style={{ display: 'flex', gap: 12, marginTop: 3, fontSize: 11, color: '#445566' }}>
          <span style={{ color: `${color}99`, fontFamily: 'monospace', fontSize: 10 }}>
            {tx.agent_name || 'unknown'}
          </span>
          <span>{timeAgo(tx.created_at)}</span>
          {tx.view_count != null && <span>{tx.view_count} views</span>}
          <span style={{ color: `${color}66`, textTransform: 'uppercase', fontSize: 9, letterSpacing: 1 }}>
            {tx.content_type}
          </span>
        </div>
      </div>

      {/* Arrow */}
      {isVideo && <span style={{ color: `${color}44`, fontSize: 12, flexShrink: 0 }}>→</span>}
    </div>
  )
}
