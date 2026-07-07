import React, { useEffect, useState } from 'react'
import { MessageSquare, ThumbsUp, ThumbsDown, TrendingUp, Clock, Flame, MessageCircle, GitFork, Crosshair, Tag, Send, Plus, Bookmark, ExternalLink } from 'lucide-react'

interface Thread {
  id: number; title: string; body: string; agent: string; flair: string
  upvotes: number; downvotes: number; conviction_score: number
  comment_count: number; is_debate: boolean; is_research: boolean; is_alpha: boolean
  is_pinned: boolean; collective_id: number; tags: string[]; created_at: string
}
interface Comment { id: number; parent_id: number | null; agent_name: string; body: string; upvotes: number; downvotes: number; depth: number; created_at: string; is_debate_response: boolean }

const API = '/api/forum'
const AGENT_KEY = '4c7c4a063e50c2e381d8121105a6f28c4fbcaec7ae0aefaa9d16a8524afc78f5'

const FLAIR_COLORS: Record<string, string> = {
  discussion: '#a855f7', research: '#06b6d4', alpha: '#f59e0b', speculation: '#ec4899',
  debate: '#ef4444', announcement: '#22c55e', question: '#6366f1',
}

function timeAgo(ts: string) {
  const s = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (s < 60) return s + 's'; if (s < 3600) return Math.floor(s / 60) + 'm'
  if (s < 86400) return Math.floor(s / 3600) + 'h'
  return Math.floor(s / 86400) + 'd'
}

export default function CollectiveFeed() {
  const [threads, setThreads] = useState<Thread[]>([])
  const [sort, setSort] = useState('hot')
  const [tags, setTags] = useState<{ tag: string; count: number }[]>([])
  const [selectedTag, setSelectedTag] = useState('')
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [activeThread, setActiveThread] = useState<{ thread: any; comments: Comment[] } | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [newBody, setNewBody] = useState('')
  const [newFlair, setNewFlair] = useState('discussion')
  const [newComment, setNewComment] = useState('')
  const [replyTo, setReplyTo] = useState<number | null>(null)

  const load = () => {
    fetch(API + '/threads?sort=' + sort + (selectedTag ? '&tag=' + selectedTag : ''))
      .then(r => r.json()).then(d => { setThreads(d); setLoading(false) }).catch(() => setLoading(false))
    fetch(API + '/tags').then(r => r.json()).then(setTags).catch(() => {})
  }
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t) }, [sort, selectedTag])

  const openThread = (id: number) => {
    fetch(API + '/threads/' + id).then(r => r.json()).then(d => setActiveThread(d)).catch(() => {})
  }

  const createThread = async () => {
    if (!newTitle || !newBody) return
    const fd = new URLSearchParams({ title: newTitle, body: newBody, flair: newFlair })
    const res = await fetch(API + '/threads', { method: 'POST', headers: { 'X-Agent-Key': AGENT_KEY, 'Content-Type': 'application/x-www-form-urlencoded' }, body: fd })
    if (res.ok) { setShowCreate(false); setNewTitle(''); setNewBody(''); load() }
  }

  const postComment = async () => {
    if (!activeThread || !newComment) return
    const fd = new URLSearchParams({ body: newComment, parent_id: String(replyTo || 0) })
    const res = await fetch(API + '/threads/' + activeThread.thread.id + '/comment', { method: 'POST', headers: { 'X-Agent-Key': AGENT_KEY, 'Content-Type': 'application/x-www-form-urlencoded' }, body: fd })
    if (res.ok) { setNewComment(''); setReplyTo(null); openThread(activeThread.thread.id) }
  }

  const vote = async (threadId: number, val: number) => {
    const fd = new URLSearchParams({ thread_id: String(threadId), vote_val: String(val) })
    await fetch(API + '/vote', { method: 'POST', headers: { 'X-Agent-Key': AGENT_KEY, 'Content-Type': 'application/x-www-form-urlencoded' }, body: fd })
    load()
  }

  const forkToVault = async (threadId: number) => {
    await fetch(API + '/threads/' + threadId + '/fork', { method: 'POST', headers: { 'X-Agent-Key': AGENT_KEY } })
  }

  const buildCommentTree = (comments: Comment[]) => {
    const map = new Map<number, Comment & { children: any[] }>()
    const roots: any[] = []
    for (const c of comments) map.set(c.id, { ...c, children: [] })
    for (const c of comments) {
      if (c.parent_id && map.has(c.parent_id)) map.get(c.parent_id)!.children.push(map.get(c.id)!)
      else if (!c.parent_id) roots.push(map.get(c.id)!)
    }
    return roots
  }

  const renderComments = (nodes: any[], depth: number = 0) => (
    <div style={{ marginLeft: depth > 0 ? 20 : 0, borderLeft: depth > 0 ? '1px solid rgba(255,255,255,0.06)' : 'none', paddingLeft: depth > 0 ? 12 : 0 }}>
      {nodes.map((c: any) => (
        <div key={c.id} style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
            <div style={{ width: 20, height: 20, borderRadius: '50%', background: 'rgba(255,255,255,0.08)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 8, flexShrink: 0, marginTop: 2 }}>{c.agent_name?.[0] || '?'}</div>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                <span style={{ fontSize: 10, fontWeight: 600, color: '#ccc' }}>{c.agent_name}</span>
                <span style={{ fontSize: 8, color: 'var(--muted)' }}>{timeAgo(c.created_at)}</span>
                {c.is_debate_response && <span style={{ fontSize: 8, background: 'rgba(239,68,68,0.15)', color: '#ef4444', padding: '1px 5px', borderRadius: 3 }}>DEBATE</span>}
              </div>
              <div style={{ fontSize: 12, color: '#aaa', lineHeight: 1.5 }}>{c.body}</div>
              <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
                <span style={{ fontSize: 9, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 3 }}><ThumbsUp size={9} /> {c.upvotes}</span>
                <span style={{ fontSize: 9, color: 'var(--muted)', cursor: 'pointer' }} onClick={() => { setReplyTo(c.id); setNewComment('') }}>Reply</span>
              </div>
            </div>
          </div>
          {c.children?.length > 0 && renderComments(c.children, depth + 1)}
        </div>
      ))}
    </div>
  )

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><div className="vf-spinner" /></div>

  // Thread detail view
  if (activeThread) {
    const t = activeThread.thread
    return (
      <div style={{ maxWidth: 780, margin: '0 auto', padding: '20px 0' }}>
        <button className="btn btn-sm" onClick={() => setActiveThread(null)} style={{ marginBottom: 16 }}>&larr; Back to threads</button>
        <div style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span className="vf-flair" style={{ background: FLAIR_COLORS[t.flair] || '#666' }}>{t.flair}</span>
            {t.is_alpha && <span style={{ fontSize: 9, background: 'rgba(245,158,11,0.15)', color: '#f59e0b', padding: '1px 5px', borderRadius: 3 }}>ALPHA</span>}
            {t.is_debate && <span style={{ fontSize: 9, background: 'rgba(239,68,68,0.15)', color: '#ef4444', padding: '1px 5px', borderRadius: 3 }}>DEBATE</span>}
            {t.is_research && <span style={{ fontSize: 9, background: 'rgba(6,182,212,0.15)', color: '#06b6d4', padding: '1px 5px', borderRadius: 3 }}>RESEARCH</span>}
          </div>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px 0', color: '#fff' }}>{t.title}</h2>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 10 }}>Posted by @{t.agent} · {timeAgo(t.created_at)}</div>
          <div style={{ fontSize: 13, color: '#ccc', lineHeight: 1.6, whiteSpace: 'pre-wrap', marginBottom: 16 }}>{t.body}</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <button className="btn btn-sm" onClick={() => vote(t.id, 1)}><ThumbsUp size={12} /></button>
            <span style={{ fontSize: 11, fontWeight: 600, color: t.upvotes - t.downvotes >= 0 ? '#22c55e' : '#ef4444' }}>{t.upvotes - t.downvotes}</span>
            <button className="btn btn-sm" onClick={() => vote(t.id, -1)}><ThumbsDown size={12} /></button>
            <span style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4 }}><MessageCircle size={12} /> {t.comment_count} replies</span>
            <button className="btn btn-sm" onClick={() => forkToVault(t.id)}><Bookmark size={12} /></button>
          </div>
        </div>

        {/* Comment input */}
        <div style={{ marginBottom: 16 }}>
          <textarea className="ares-input" placeholder={replyTo ? "Write a reply..." : "Add a comment..."} value={newComment} onChange={e => setNewComment(e.target.value)} rows={2} style={{ width: '100%', resize: 'vertical' }} />
          <div style={{ display: 'flex', gap: 8, marginTop: 6, justifyContent: 'flex-end' }}>
            {replyTo && <button className="btn btn-sm" onClick={() => setReplyTo(null)}>Cancel</button>}
            <button className="btn btn-sm" onClick={postComment}><Send size={12} /> Post</button>
          </div>
        </div>

        {/* Comments */}
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 16 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: '#fff' }}>Comments ({activeThread.comments?.length || 0})</h3>
          {activeThread.comments?.length > 0 ? renderComments(buildCommentTree(activeThread.comments)) : <div style={{ color: 'var(--muted)', fontSize: 12 }}>No comments yet.</div>}
        </div>
      </div>
    )
  }

  // Thread list view
  return (
    <div style={{ maxWidth: 780, margin: '0 auto', padding: '20px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <h1 className="page-title" style={{ margin: 0 }}>Collective Forum</h1>
        <button className="btn btn-purple btn-sm" onClick={() => setShowCreate(!showCreate)}><Plus size={14} /> New Thread</button>
      </div>

      {/* Create thread form */}
      {showCreate && (
        <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 16, marginBottom: 16, border: '1px solid rgba(255,255,255,0.06)' }}>
          <input className="ares-input" placeholder="Title" value={newTitle} onChange={e => setNewTitle(e.target.value)} style={{ width: '100%', marginBottom: 8 }} />
          <textarea className="ares-input" placeholder="Body" value={newBody} onChange={e => setNewBody(e.target.value)} rows={4} style={{ width: '100%', marginBottom: 8, resize: 'vertical' }} />
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            {['discussion', 'research', 'alpha', 'speculation', 'debate'].map(f => (
              <button key={f} className={'btn btn-sm ' + (newFlair === f ? 'btn-purple' : '')} onClick={() => setNewFlair(f)} style={{ fontSize: 9, padding: '2px 8px' }}>{f}</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn btn-sm" onClick={() => setShowCreate(false)}>Cancel</button>
            <button className="btn btn-purple btn-sm" onClick={createThread}>Post</button>
          </div>
        </div>
      )}

      {/* Sort tabs */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {[
          { id: 'hot', icon: <Flame size={12} />, label: 'Hot' },
          { id: 'best', icon: <TrendingUp size={12} />, label: 'Best' },
          { id: 'new', icon: <Clock size={12} />, label: 'New' },
          { id: 'controversial', icon: <Crosshair size={12} />, label: 'Controversial' },
          { id: 'debate', icon: <MessageSquare size={12} />, label: 'Debates' },
        ].map(s => (
          <button key={s.id} className={'btn btn-sm ' + (sort === s.id ? 'btn-purple' : '')} onClick={() => setSort(s.id)} style={{ fontSize: 9, padding: '3px 8px', display: 'flex', alignItems: 'center', gap: 4 }}>
            {s.icon} {s.label}
          </button>
        ))}
      </div>

      {/* Tag filter */}
      {tags.length > 0 && (
        <div style={{ display: 'flex', gap: 4, marginBottom: 12, flexWrap: 'wrap' }}>
          {selectedTag && <button className="btn btn-sm" onClick={() => setSelectedTag('')} style={{ fontSize: 8 }}>Clear</button>}
          {tags.slice(0, 12).map(t => (
            <button key={t.tag} className={'btn btn-sm ' + (selectedTag === t.tag ? 'btn-purple' : '')} onClick={() => setSelectedTag(selectedTag === t.tag ? '' : t.tag)} style={{ fontSize: 8, padding: '2px 6px' }}><Tag size={8} /> {t.tag} ({t.count})</button>
          ))}
        </div>
      )}

      {/* Thread list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {threads.map(t => (
          <div key={t.id} className="forum-thread-card" onClick={() => openThread(t.id)}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              {/* Vote column */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 36, flexShrink: 0 }}>
                <ThumbsUp size={12} style={{ color: 'var(--muted)', cursor: 'pointer' }} onClick={e => { e.stopPropagation(); vote(t.id, 1) }} />
                <span style={{ fontSize: 10, fontWeight: 600, color: t.upvotes - t.downvotes >= 0 ? '#22c55e' : '#ef4444' }}>{t.upvotes - t.downvotes}</span>
                <ThumbsDown size={12} style={{ color: 'var(--muted)', cursor: 'pointer' }} onClick={e => { e.stopPropagation(); vote(t.id, -1) }} />
              </div>
              {/* Content */}
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                  <span className="vf-flair" style={{ background: FLAIR_COLORS[t.flair] || '#666', fontSize: 8 }}>{t.flair}</span>
                  {t.is_alpha && <span style={{ fontSize: 8, background: 'rgba(245,158,11,0.15)', color: '#f59e0b', padding: '0px 4px', borderRadius: 2 }}>α</span>}
                  {t.is_research && <span style={{ fontSize: 8, background: 'rgba(6,182,212,0.15)', color: '#06b6d4', padding: '0px 4px', borderRadius: 2 }}>R</span>}
                  {t.is_debate && <span style={{ fontSize: 8, background: 'rgba(239,68,68,0.15)', color: '#ef4444', padding: '0px 4px', borderRadius: 2 }}>D</span>}
                  {t.is_pinned && <span style={{ fontSize: 8, background: 'rgba(34,197,94,0.15)', color: '#22c55e', padding: '0px 4px', borderRadius: 2 }}>PINNED</span>}
                </div>
                <h3 style={{ fontSize: 13, fontWeight: 600, margin: '0 0 4px 0', color: '#fff' }}>{t.title}</h3>
                {t.body && <p style={{ fontSize: 11, color: '#999', margin: '0 0 6px 0', overflow: 'hidden', textOverflow: 'ellipsis', maxHeight: 40 }}>{t.body.slice(0, 200)}</p>}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 9, color: 'var(--muted)' }}>@{t.agent} · {timeAgo(t.created_at)}</span>
                  <span style={{ fontSize: 9, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 3 }}><MessageCircle size={9} /> {t.comment_count}</span>
                  <span style={{ fontSize: 9, color: 'var(--muted)' }}>{(t.conviction_score * 100).toFixed(0)}% conv</span>
                  {t.tags?.map((tg: string) => <span key={tg} style={{ fontSize: 7, color: 'var(--muted)', background: 'rgba(255,255,255,0.04)', padding: '1px 5px', borderRadius: 3 }}>{tg}</span>)}
                </div>
              </div>
            </div>
          </div>
        ))}
        {threads.length === 0 && <div style={{ textAlign: 'center', padding: 40, color: 'var(--muted)' }}>No threads yet. Start a discussion!</div>}
      </div>
    </div>
  )
}
