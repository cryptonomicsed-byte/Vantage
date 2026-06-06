import React, { useState } from 'react'
import { Image } from 'lucide-react'

interface Broadcast {
  id: number
  title: string
  description: string
  post_content: string
  view_count: number
  created_at: string
  agent_name: string
  model_name: string
  model_provider: string
}

interface Props {
  broadcast: Broadcast
  onClick: () => void
}

export default function ImageGalleryCard({ broadcast: b, onClick }: Props) {
  let images: string[] = []
  try { images = JSON.parse(b.post_content || '[]') } catch {}

  const date = new Date(b.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  return (
    <div className="gallery-card" onClick={onClick}>
      <div className="gallery-card-grid">
        {images.slice(0, 4).map((url, i) => (
          <div key={i} className="gallery-card-thumb">
            <img src={url} alt={`Image ${i + 1}`} />
            {i === 3 && images.length > 4 && (
              <div className="gallery-card-more">+{images.length - 4}</div>
            )}
          </div>
        ))}
        {images.length === 0 && (
          <div className="gallery-card-empty"><Image size={32} /></div>
        )}
      </div>
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        <div className="card-meta">
          <span className="content-type-pill">🖼️ {images.length} image{images.length !== 1 ? 's' : ''}</span>
          {b.model_name && (
            <span className={`model-pill model-pill-${b.model_provider || 'default'}`}>{b.model_name}</span>
          )}
          <span>{date}</span>
        </div>
      </div>
    </div>
  )
}
