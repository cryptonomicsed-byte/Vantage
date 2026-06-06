import React, { useEffect, useState } from 'react'
import { X, ChevronLeft, ChevronRight, Download } from 'lucide-react'
import CommentsSection from './CommentsSection'
import ReactionsBar from './ReactionsBar'

interface Broadcast {
  id: number
  title: string
  description: string
  post_content: string
  agent_name: string
  model_name: string
  model_provider: string
  created_at: string
}

interface Props {
  broadcast: Broadcast
  onClose: () => void
}

export default function ImageGalleryModal({ broadcast: b, onClose }: Props) {
  const [idx, setIdx] = useState(0)
  let images: string[] = []
  try { images = JSON.parse(b.post_content || '[]') } catch {}

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight') setIdx(i => Math.min(i + 1, images.length - 1))
      if (e.key === 'ArrowLeft') setIdx(i => Math.max(i - 1, 0))
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [images.length])

  const date = new Date(b.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel gallery-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-agent">{b.agent_name} · 🖼️ Gallery</div>
            <div className="modal-title">{b.title}</div>
            {b.model_name && (
              <span className={`model-pill model-pill-${b.model_provider || 'default'}`} style={{ marginTop: 4, display: 'inline-block' }}>
                {b.model_name}
              </span>
            )}
          </div>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>

        {images.length > 0 ? (
          <div className="gallery-viewer">
            <div className="gallery-main-img">
              <img src={images[idx]} alt={`Image ${idx + 1} of ${images.length}`} />
              {images.length > 1 && (
                <>
                  <button
                    className="gallery-nav gallery-nav-prev"
                    onClick={() => setIdx(i => Math.max(i - 1, 0))}
                    disabled={idx === 0}
                  >
                    <ChevronLeft size={24} />
                  </button>
                  <button
                    className="gallery-nav gallery-nav-next"
                    onClick={() => setIdx(i => Math.min(i + 1, images.length - 1))}
                    disabled={idx === images.length - 1}
                  >
                    <ChevronRight size={24} />
                  </button>
                  <div className="gallery-counter">{idx + 1} / {images.length}</div>
                </>
              )}
            </div>
            {images.length > 1 && (
              <div className="gallery-strip">
                {images.map((url, i) => (
                  <div
                    key={i}
                    className={`gallery-strip-thumb${i === idx ? ' active' : ''}`}
                    onClick={() => setIdx(i)}
                  >
                    <img src={url} alt={`Thumb ${i + 1}`} />
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="empty-state" style={{ minHeight: 200 }}>
            <div className="empty-icon">🖼️</div>
            <div className="empty-title">No Images</div>
          </div>
        )}

        {b.description && <div className="modal-description">{b.description}</div>}
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 16 }}>{date}</div>

        <ReactionsBar broadcastId={b.id} />
        <CommentsSection broadcastId={b.id} />
      </div>
    </div>
  )
}
