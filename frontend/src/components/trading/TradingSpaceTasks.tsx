import React, { useState, useEffect, useCallback } from 'react'

interface SpaceTask {
  id: string
  title: string
  description: string
  status: string
  priority: number
  kind_tag: string
  created_by_name: string
  claimed_by_name: string | null
  created_at: string
}

interface Props {
  slug: string
  spaceId: string
  authHeaders: Record<string, string>
}

function ageLabel(ts: string): string {
  const ms = Date.now() - new Date(ts).getTime()
  const h = Math.floor(ms / 3600000)
  if (h < 1) return '<1h'
  if (h < 24) return h + 'h'
  return Math.floor(h / 24) + 'd'
}

function PriorityBadge({ priority }: { priority: number }) {
  const label = priority > 70 ? 'HIGH' : priority >= 40 ? 'MED' : 'LOW'
  const color = priority > 70 ? '#ef4444' : priority >= 40 ? '#f59e0b' : 'var(--muted)'
  return (
    <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: color + '22', color, textTransform: 'uppercase' as const, letterSpacing: '0.05em' }}>
      {label}
    </span>
  )
}

export default function TradingSpaceTasks({ slug, spaceId, authHeaders }: Props) {
  const formHeaders = { ...authHeaders, 'Content-Type': 'application/x-www-form-urlencoded' }
  const [proposed, setProposed] = useState<SpaceTask[]>([])
  const [inProgress, setInProgress] = useState<SpaceTask[]>([])
  const [inReview, setInReview] = useState<SpaceTask[]>([])
  const [settled, setSettled] = useState<SpaceTask[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newPriority, setNewPriority] = useState(50)
  const [newKind, setNewKind] = useState('')
  const [creating, setCreating] = useState(false)
  const [submitTaskId, setSubmitTaskId] = useState<string | null>(null)
  const [artifactContent, setArtifactContent] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [rejectTaskId, setRejectTaskId] = useState<string | null>(null)
  const [rejectNote, setRejectNote] = useState('')
  const [actioning, setActioning] = useState<string | null>(null)

  const base = '/api/guilds/' + slug + '/trading-spaces/' + spaceId + '/tasks'

  const loadAll = useCallback(async () => {
    try {
      const [p, ip, ir, d] = await Promise.all([
        fetch(base + '?status=proposed&limit=50', { headers: authHeaders }),
        fetch(base + '?status=claimed&limit=50', { headers: authHeaders }),
        fetch(base + '?status=review&limit=50', { headers: authHeaders }),
        fetch(base + '?status=accepted&limit=20', { headers: authHeaders }),
      ])
      if (p.ok) setProposed((await p.json()).tasks || [])
      if (ip.ok) setInProgress((await ip.json()).tasks || [])
      if (ir.ok) setInReview((await ir.json()).tasks || [])
      if (d.ok) setSettled((await d.json()).tasks || [])
    } finally { setLoading(false) }
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadAll()
    const t = setInterval(loadAll, 15000)
    return () => clearInterval(t)
  }, [loadAll])

  async function createTask() {
    if (!newTitle.trim()) return
    setCreating(true)
    try {
      const body = new URLSearchParams({ title: newTitle, description: newDesc, priority: String(newPriority), kind_tag: newKind })
      const r = await fetch(base, { method: 'POST', headers: formHeaders, body: body.toString() })
      if (r.ok) { setNewTitle(''); setNewDesc(''); setNewPriority(50); setNewKind(''); setShowCreate(false); loadAll() }
    } finally { setCreating(false) }
  }

  async function claimTask(id: string) {
    setActioning(id)
    try { await fetch(base + '/' + id + '/claim', { method: 'POST', headers: authHeaders }); loadAll() }
    finally { setActioning(null) }
  }

  async function submitTask(id: string) {
    setSubmitting(true)
    try {
      const body = new URLSearchParams({ artifact_content: artifactContent })
      await fetch(base + '/' + id + '/submit', { method: 'POST', headers: formHeaders, body: body.toString() })
      setSubmitTaskId(null); setArtifactContent(''); loadAll()
    } finally { setSubmitting(false) }
  }

  async function acceptTask(id: string) {
    setActioning(id)
    try { await fetch(base + '/' + id + '/accept', { method: 'POST', headers: authHeaders }); loadAll() }
    finally { setActioning(null) }
  }

  async function rejectTask(id: string) {
    setActioning(id)
    try {
      const body = new URLSearchParams({ note: rejectNote })
      await fetch(base + '/' + id + '/reject', { method: 'POST', headers: formHeaders, body: body.toString() })
      setRejectTaskId(null); setRejectNote(''); loadAll()
    } finally { setActioning(null) }
  }

  const colStyle: React.CSSProperties = { flex: '1 1 0', minWidth: 200, display: 'flex', flexDirection: 'column', gap: 8 }

  function colHeader(label: string, count: number, color: string) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.08em', color }}>{label}</span>
        <span style={{ fontSize: 10, background: 'rgba(255,255,255,0.06)', padding: '1px 6px', borderRadius: 10, color: 'var(--muted)' }}>{count}</span>
      </div>
    )
  }

  function TaskCard({ task, children }: { task: SpaceTask; children?: React.ReactNode }) {
    return (
      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginBottom: 6 }}>
          <PriorityBadge priority={task.priority} />
          {task.kind_tag && <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: 'rgba(0,200,180,0.1)', color: 'var(--cyan)', textTransform: 'uppercase' as const }}>{task.kind_tag}</span>}
        </div>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 4, lineHeight: 1.4 }}>{task.title}</div>
        {task.description && <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>{task.description.slice(0, 80)}{task.description.length > 80 ? '…' : ''}</div>}
        <div style={{ display: 'flex', gap: 8, fontSize: 10, color: 'rgba(255,255,255,0.3)' }}>
          <span>{ageLabel(task.created_at)}</span>
          {task.claimed_by_name && <span style={{ color: '#a78bfa' }}>→ {task.claimed_by_name}</span>}
        </div>
        {children && <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' as const }}>{children}</div>}
      </div>
    )
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading tasks…</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Trading Space Tasks</div>
        <button className="btn btn-sm" onClick={() => setShowCreate(!showCreate)} style={{ fontSize: 11 }}>{showCreate ? 'Cancel' : '+ New Task'}</button>
      </div>

      {showCreate && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>New Task</div>
          <input value={newTitle} onChange={e => setNewTitle(e.target.value)} placeholder="Task title" style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          <textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Description" rows={2} style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none', resize: 'vertical' as const }} />
          <div style={{ display: 'flex', gap: 10 }}>
            <input value={newKind} onChange={e => setNewKind(e.target.value)} placeholder="Kind tag" style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>Priority:</span>
              <input type="number" min={1} max={100} value={newPriority} onChange={e => setNewPriority(Number(e.target.value))} style={{ width: 60, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 8px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
            </div>
          </div>
          <button className="btn btn-sm" onClick={createTask} disabled={creating || !newTitle.trim()} style={{ alignSelf: 'flex-start', fontSize: 11 }}>{creating ? 'Creating…' : 'Create Task'}</button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 14, flex: 1, minHeight: 0, overflowX: 'auto' }}>
        <div style={colStyle}>
          {colHeader('Proposed', proposed.length, '#f59e0b')}
          {proposed.map(t => (
            <TaskCard key={t.id} task={t}>
              <button className="btn btn-sm" onClick={() => claimTask(t.id)} disabled={actioning === t.id} style={{ fontSize: 10 }}>{actioning === t.id ? '…' : 'Claim'}</button>
            </TaskCard>
          ))}
          {proposed.length === 0 && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', textAlign: 'center', marginTop: 16 }}>Empty</div>}
        </div>

        <div style={colStyle}>
          {colHeader('In Progress', inProgress.length, 'var(--cyan)')}
          {inProgress.map(t => (
            <TaskCard key={t.id} task={t}>
              {submitTaskId === t.id ? (
                <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <textarea value={artifactContent} onChange={e => setArtifactContent(e.target.value)} placeholder="Artifact content…" rows={3} style={{ width: '100%', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 8px', color: 'var(--text)', fontSize: 11, outline: 'none', resize: 'vertical' as const, boxSizing: 'border-box' }} />
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn btn-sm" onClick={() => submitTask(t.id)} disabled={submitting} style={{ fontSize: 10 }}>{submitting ? '…' : 'Submit'}</button>
                    <button className="btn btn-sm" onClick={() => setSubmitTaskId(null)} style={{ fontSize: 10 }}>Cancel</button>
                  </div>
                </div>
              ) : (
                <button className="btn btn-sm" onClick={() => setSubmitTaskId(t.id)} style={{ fontSize: 10 }}>Submit</button>
              )}
            </TaskCard>
          ))}
          {inProgress.length === 0 && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', textAlign: 'center', marginTop: 16 }}>Empty</div>}
        </div>

        <div style={colStyle}>
          {colHeader('In Review', inReview.length, '#a78bfa')}
          {inReview.map(t => (
            <TaskCard key={t.id} task={t}>
              <button className="btn btn-sm" onClick={() => acceptTask(t.id)} disabled={actioning === t.id} style={{ fontSize: 10, background: 'rgba(60,200,120,0.12)', color: '#3cc878' }}>{actioning === t.id ? '…' : 'Accept'}</button>
              {rejectTaskId === t.id ? (
                <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                  <input value={rejectNote} onChange={e => setRejectNote(e.target.value)} placeholder="Rejection note…" style={{ width: '100%', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 8px', color: 'var(--text)', fontSize: 11, outline: 'none', boxSizing: 'border-box' }} />
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn btn-sm" onClick={() => rejectTask(t.id)} disabled={actioning === t.id} style={{ fontSize: 10, color: '#ef4444' }}>Confirm</button>
                    <button className="btn btn-sm" onClick={() => setRejectTaskId(null)} style={{ fontSize: 10 }}>Cancel</button>
                  </div>
                </div>
              ) : (
                <button className="btn btn-sm" onClick={() => setRejectTaskId(t.id)} style={{ fontSize: 10, color: '#ef4444' }}>Reject</button>
              )}
            </TaskCard>
          ))}
          {inReview.length === 0 && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', textAlign: 'center', marginTop: 16 }}>Empty</div>}
        </div>

        <div style={colStyle}>
          {colHeader('Settled', settled.length, '#3cc878')}
          {settled.map(t => <TaskCard key={t.id} task={t} />)}
          {settled.length === 0 && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', textAlign: 'center', marginTop: 16 }}>Empty</div>}
        </div>
      </div>
    </div>
  )
}
