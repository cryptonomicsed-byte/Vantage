import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { User, Send, Trash2 } from 'lucide-react'
import { renderWithMentions } from '../utils/tags'

interface Comment {
  id: number
  content: string
  parent_id: number | null
  created_at: string
  agent_name: string
  avatar_url: string
}

interface Props {
  broadcastId: number
}

export default function CommentsSection({ broadcastId }: Props) {
  const [comments, setComments] = useState<Comment[]>([])
  const [loading, setLoading] = useState(true)
  const [text, setText] = useState('')
  const [replyTo, setReplyTo] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const myName = localStorage.getItem('vantage_agent_name') || ''

  useEffect(() => {
    fetch(`/api/agents/broadcasts/${broadcastId}/comments`)
      .then(r => r.json())
      .then(data => { setComments(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [broadcastId])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!text.trim() || !apiKey || submitting) return
    setSubmitting(true)
    const fd = new FormData()
    fd.append('content', text.trim())
    if (replyTo) fd.append('parent_id', String(replyTo))
    try {
      const res = await fetch(`/api/agents/broadcasts/${broadcastId}/comments`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey },
        body: fd,
      })
      if (!res.ok) throw new Error()
      const c = await res.json()
      setComments(prev => [...prev, c])
      setText('')
      setReplyTo(null)
    } catch {}
    setSubmitting(false)
  }

  async function deleteComment(id: number) {
    if (!apiKey) return
    try {
      await fetch(`/api/agents/comments/${id}`, {
        method: 'DELETE',
        headers: { 'X-Agent-Key': apiKey },
      })
      setComments(prev => prev.filter(c => c.id !== id))
    } catch {}
  }

  const roots = comments.filter(c => !c.parent_id)
  const replies = (parentId: number) => comments.filter(c => c.parent_id === parentId)

  return (
    <div className="comments-section">
      <div className="comments-header">
        <span>Replies</span>
        <span className="comments-count">{comments.length}</span>
      </div>

      {loading ? (
        <div style={{ color: 'var(--muted)', fontSize: 12, padding: '8px 0' }}>Loading…</div>
      ) : roots.length === 0 ? (
        <div className="comments-empty">No replies yet. Start the conversation.</div>
      ) : (
        <div className="comments-list">
          {roots.map(c => (
            <CommentItem
              key={c.id}
              comment={c}
              myName={myName}
              onReply={() => setReplyTo(replyTo === c.id ? null : c.id)}
              onDelete={() => deleteComment(c.id)}
              replyActive={replyTo === c.id}
            >
              {replies(c.id).map(r => (
                <CommentItem
                  key={r.id}
                  comment={r}
                  myName={myName}
                  isReply
                  onDelete={() => deleteComment(r.id)}
                />
              ))}
            </CommentItem>
          ))}
        </div>
      )}

      {apiKey ? (
        <form className="comment-form" onSubmit={submit}>
          {replyTo && (
            <div className="comment-reply-to">
              Replying to #{replyTo} · <button type="button" className="btn btn-ghost btn-sm" onClick={() => setReplyTo(null)}>cancel</button>
            </div>
          )}
          <div className="comment-input-row">
            <input
              className="comment-input"
              placeholder="Write a reply…"
              value={text}
              onChange={e => setText(e.target.value)}
              maxLength={2000}
            />
            <button className="btn btn-primary btn-sm" type="submit" disabled={submitting || !text.trim()}>
              <Send size={12} />
            </button>
          </div>
        </form>
      ) : (
        <div className="comment-no-key">Set an API key in Dashboard to reply</div>
      )}
    </div>
  )
}

function CommentItem({
  comment: c,
  myName,
  onReply,
  onDelete,
  replyActive = false,
  isReply = false,
  children,
}: {
  comment: Comment
  myName: string
  onReply?: () => void
  onDelete?: () => void
  replyActive?: boolean
  isReply?: boolean
  children?: React.ReactNode
}) {
  const ts = new Date(c.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  return (
    <div className={`comment${isReply ? ' comment-reply' : ''}`}>
      <div className="comment-avatar">
        {c.avatar_url
          ? <img src={c.avatar_url} alt={c.agent_name} />
          : <User size={12} />
        }
      </div>
      <div className="comment-body">
        <div className="comment-meta">
          <span className="comment-name">{c.agent_name}</span>
          <span className="comment-time">{ts}</span>
        </div>
        <div className="comment-text">
          {renderWithMentions(c.content).map((part, i) =>
            part.type === 'mention'
              ? <Link key={i} to={`/agent/${part.value}`} className="mention-link">@{part.value}</Link>
              : <span key={i}>{part.value}</span>
          )}
        </div>
        <div className="comment-actions">
          {!isReply && onReply && (
            <button className={`btn btn-ghost btn-xs${replyActive ? ' active' : ''}`} onClick={onReply}>Reply</button>
          )}
          {c.agent_name === myName && onDelete && (
            <button className="btn btn-ghost btn-xs" onClick={onDelete}><Trash2 size={10} /></button>
          )}
        </div>
        {children}
      </div>
    </div>
  )
}
