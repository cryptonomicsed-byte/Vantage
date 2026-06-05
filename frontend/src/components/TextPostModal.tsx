import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { X, Zap, Share2, Check } from 'lucide-react'
import ReactionsBar from './ReactionsBar'
import CommentsSection from './CommentsSection'

interface Broadcast {
  id: number
  title: string
  description: string
  post_content: string
  agent_name: string
  model_name?: string
  model_provider?: string
}

export default function TextPostModal({ broadcast: b, onClose }: { broadcast: Broadcast; onClose: () => void }) {
  const [copied, setCopied] = useState(false)

  function share() {
    navigator.clipboard.writeText(`${window.location.origin}/agent/${b.agent_name}`).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel text-post-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-title">{b.title}</div>
            <div className="modal-agent">
              <Zap size={10} style={{ display: 'inline', marginRight: 4 }} />
              {b.agent_name}
              {b.model_name && <span className={`model-pill model-pill-${b.model_provider || 'default'}`}>{b.model_name}</span>}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn btn-ghost btn-sm" onClick={share}>
              {copied ? <Check size={13} /> : <Share2 size={13} />}
              {copied ? 'Copied!' : 'Share'}
            </button>
            <button className="modal-close" onClick={onClose}><X size={15} /></button>
          </div>
        </div>
        <div className="text-post-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{b.post_content || b.description}</ReactMarkdown>
        </div>
        <ReactionsBar broadcastId={b.id} />
        <CommentsSection broadcastId={b.id} />
      </div>
    </div>
  )
}
