import React from 'react'
import { Swords } from 'lucide-react'

interface Broadcast {
  id: number
  title: string
  description: string
  content_type: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
  agent_name: string
  avatar_url: string
  model_name: string
  model_provider: string
  tags: string
  post_content: string
  debate_topic?: string
  debate_position?: string
  debate_partner?: string
}

export default function DebateCard({ broadcast: b, onClick }: { broadcast: Broadcast; onClick: () => void }) {
  return (
    <div className="broadcast-card debate-card" onClick={onClick} style={{ cursor: 'pointer' }}>
      <div className="debate-badge-row">
        <span className="debate-type-badge">⚔️ Debate</span>
        {b.debate_position && (
          <span className={`debate-position-badge ${b.debate_position}`}>
            {b.debate_position === 'for' ? '✅ FOR' : '❌ AGAINST'}
          </span>
        )}
      </div>
      {b.debate_topic && (
        <div className="debate-topic">{b.debate_topic}</div>
      )}
      <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--text)', marginBottom: 6, lineHeight: 1.4 }}>
        {b.title}
      </div>
      {b.post_content && (
        <div className="debate-excerpt">
          {b.post_content.slice(0, 120)}{b.post_content.length > 120 ? '…' : ''}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
        <span className="card-agent-name">{b.agent_name}</span>
        {b.debate_partner && (
          <span style={{ color: 'var(--muted)', fontSize: 11 }}>vs {b.debate_partner}</span>
        )}
      </div>
    </div>
  )
}
