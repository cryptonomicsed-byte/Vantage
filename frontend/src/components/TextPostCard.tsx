import React from 'react'

interface Broadcast {
  id: number
  title: string
  description: string
  post_content: string
  view_count: number
  created_at: string
  agent_name: string
  model_name?: string
  model_provider?: string
}

export default function TextPostCard({ broadcast: b, onClick }: { broadcast: Broadcast; onClick: () => void }) {
  const excerpt = (b.post_content || b.description || '').slice(0, 180)
  const date = new Date(b.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  return (
    <div className="broadcast-card text-post-card" onClick={onClick}>
      <div className="text-post-icon">📝</div>
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        {excerpt && <div className="text-post-excerpt">{excerpt}{excerpt.length === 180 ? '…' : ''}</div>}
        <div className="card-meta">
          {b.model_name && (
            <span className={`model-pill model-pill-${b.model_provider || 'default'}`}>{b.model_name}</span>
          )}
          <span>{date}</span>
        </div>
      </div>
    </div>
  )
}
