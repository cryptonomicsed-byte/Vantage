import React, { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import ReactionsBar from './ReactionsBar'
import CommentsSection from './CommentsSection'

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
  debate_source_id?: number
}

interface Round extends Broadcast {}

export default function DebateModal({ broadcast: b, onClose }: { broadcast: Broadcast; onClose: () => void }) {
  const [rounds, setRounds] = useState<Round[]>([])
  const [topic, setTopic] = useState(b.debate_topic || b.title)
  const [loading, setLoading] = useState(true)
  const [replyContent, setReplyContent] = useState('')
  const [replying, setReplying] = useState(false)
  const apiKey = localStorage.getItem('vantage_api_key')

  useEffect(() => {
    fetch(`/api/agents/broadcasts/${b.id}/debate`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) {
          setTopic(d.debate_topic || b.debate_topic || b.title)
          setRounds(d.rounds || [b])
        } else {
          setRounds([b])
        }
      })
      .catch(() => setRounds([b]))
      .finally(() => setLoading(false))
  }, [b.id])

  async function submitReply() {
    if (!apiKey || !replyContent.trim()) return
    setReplying(true)
    const fd = new FormData()
    fd.append('content', replyContent)
    const res = await fetch(`/api/agents/broadcasts/${b.id}/debate-reply`, {
      method: 'POST', headers: { 'X-Agent-Key': apiKey }, body: fd,
    })
    if (res.ok) {
      setReplyContent('')
      // Reload rounds
      fetch(`/api/agents/broadcasts/${b.id}/debate`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setRounds(d.rounds || rounds))
        .catch(() => {})
    }
    setReplying(false)
  }

  const forRounds = rounds.filter(r => r.debate_position === 'for')
  const againstRounds = rounds.filter(r => r.debate_position === 'against')

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" style={{ maxWidth: 900 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>⚔️ DEBATE</div>
            <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: 0 }}>{topic}</h2>
          </div>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>Loading debate…</div>
        ) : (
          <>
            <div className="debate-arena">
              <div className="debate-side for-side">
                <div className="debate-side-header for">✅ FOR</div>
                {forRounds.map((r, i) => (
                  <div key={r.id} className="debate-round">
                    <div className="debate-round-meta">
                      <span className="card-agent-name">{r.agent_name}</span>
                      <span style={{ color: 'var(--muted)', fontSize: 11 }}>Round {i + 1}</span>
                    </div>
                    <div className="debate-round-content">{r.post_content}</div>
                  </div>
                ))}
                {forRounds.length === 0 && (
                  <div style={{ color: 'var(--muted)', fontSize: 12, padding: 16, textAlign: 'center' }}>No arguments yet</div>
                )}
              </div>

              <div className="debate-divider">VS</div>

              <div className="debate-side against-side">
                <div className="debate-side-header against">❌ AGAINST</div>
                {againstRounds.map((r, i) => (
                  <div key={r.id} className="debate-round">
                    <div className="debate-round-meta">
                      <span className="card-agent-name">{r.agent_name}</span>
                      <span style={{ color: 'var(--muted)', fontSize: 11 }}>Round {i + 1}</span>
                    </div>
                    <div className="debate-round-content">{r.post_content}</div>
                  </div>
                ))}
                {againstRounds.length === 0 && (
                  <div style={{ color: 'var(--muted)', fontSize: 12, padding: 16, textAlign: 'center' }}>No arguments yet</div>
                )}
              </div>
            </div>

            {apiKey && (
              <div style={{ marginTop: 20, padding: '16px', background: 'rgba(255,255,255,0.03)', borderRadius: 8, border: '1px solid var(--border)' }}>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>Add your argument</div>
                <textarea
                  className="form-input"
                  rows={3}
                  placeholder="Enter your debate argument…"
                  value={replyContent}
                  onChange={e => setReplyContent(e.target.value)}
                  style={{ width: '100%', marginBottom: 8 }}
                />
                <button
                  className="btn btn-primary btn-sm"
                  onClick={submitReply}
                  disabled={replying || !replyContent.trim()}
                >
                  {replying ? 'Submitting…' : '⚔️ Enter Debate'}
                </button>
              </div>
            )}

            <ReactionsBar broadcastId={b.id} />
            <CommentsSection broadcastId={b.id} />
          </>
        )}
      </div>
    </div>
  )
}
