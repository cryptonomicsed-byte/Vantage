import React, { useState, useEffect, useCallback } from 'react'

interface ContentTask {
  id: string
  title: string
  description: string
  status: string
  priority: number
  kind_tag: string
  project_id: string | null
  created_by_name: string
  claimed_by_name: string | null
  created_at: string
}

interface Project {
  id: string
  title: string
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

const STATUS_COLUMNS = [
  { id: 'brief_submitted', label: 'Brief Submitted', color: '#f59e0b' },
  { id: 'claimed', label: 'In Production', color: 'var(--cyan)' },
  { id: 'review', label: 'In Review', color: '#a78bfa' },
  { id: 'accepted', label: 'Published', color: '#3cc878' },
]

export default function ContentSpaceTasks({ slug, spaceId, authHeaders }: Props) {
  const formHeaders = { ...authHeaders, 'Content-Type': 'application/x-www-form-urlencoded' }
  const [tasksByStatus, setTasksByStatus] = useState<Record<string, ContentTask[]>>({})
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newPriority, setNewPriority] = useState(50)
  const [newKind, setNewKind] = useState('')
  const [newProjectId, setNewProjectId] = useState('')
  const [creating, setCreating] = useState(false)
  const [submitTaskId, setSubmitTaskId] = useState<string | null>(null)
  const [submitContent, setSubmitContent] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [rejectTaskId, setRejectTaskId] = useState<string | null>(null)
  const [rejectNote, setRejectNote] = useState('')
  const [actioning, setActioning] = useState<string | null>(null)

  const base = '/api/guilds/' + slug + '/content-spaces/' + spaceId + '/tasks'
  const projectsBase = '/api/guilds/' + slug + '/content-spaces/' + spaceId + '/projects'

  const loadAll = useCallback(async () => {
    try {
      const results = await Promise.all(
        STATUS_COLUMNS.map(col => fetch(base + '?status=' + col.id + '&limit=50', { headers: authHeaders }))
      )
      const newMap: Record<string, ContentTask[]> = {}
      for (let i = 0; i < STATUS_COLUMNS.length; i++) {
        const r = results[i]
        newMap[STATUS_COLUMNS[i].id] = r.ok ? (await r.json()).tasks || [] : []
      }
      setTasksByStatus(newMap)
    } finally { setLoading(false) }
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadAll()
    const t = setInterval(loadAll, 15000)
    fetch(projectsBase, { headers: authHeaders })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setProjects(d.projects || []))
      .catch(() => {})
    return () => clearInterval(t)
  }, [loadAll]) // eslint-disable-line react-hooks/exhaustive-deps

  async function createTask() {
    if (!newTitle.trim()) return
    setCreating(true)
    try {
      const body = new URLSearchParams({ title: newTitle, description: newDesc, priority: String(newPriority), kind_tag: newKind })
      if (newProjectId) body.set('project_id', newProjectId)
      const r = await fetch(base, { method: 'POST', headers: formHeaders, body: body.toString() })
      if (r.ok) { setNewTitle(''); setNewDesc(''); setNewPriority(50); setNewKind(''); setNewProjectId(''); setShowCreate(false); loadAll() }
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
      const body = new URLSearchParams({ artifact_content: submitContent })
      await fetch(base + '/' + id + '/submit', { method: 'POST', headers: formHeaders, body: body.toString() })
      setSubmitTaskId(null); setSubmitContent(''); loadAll()
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

  function getProjectName(id: string | null): string {
    if (!id) return ''
    const p = projects.find(p => p.id === id)
    return p ? p.title : id.slice(0, 8)
  }

  const colStyle: React.CSSProperties = { flex: '1 1 0', minWidth: 200, display: 'flex', flexDirection: 'column', gap: 8 }

  function TaskCard({ task, children }: { task: ContentTask; children?: React.ReactNode }) {
    return (
      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 4, lineHeight: 1.4 }}>{task.title}</div>
        {task.description && <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>{task.description.slice(0, 80)}{task.description.length > 80 ? '…' : ''}</div>}
        <div style={{ display: 'flex', gap: 8, fontSize: 10, color: 'rgba(255,255,255,0.3)', flexWrap: 'wrap' as const, marginBottom: children ? 6 : 0 }}>
          <span>{ageLabel(task.created_at)}</span>
          {task.project_id && <span style={{ color: 'var(--cyan)' }}>{getProjectName(task.project_id)}</span>}
          {task.claimed_by_name && <span style={{ color: '#a78bfa' }}>to {task.claimed_by_name}</span>}
        </div>
        {children && <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' as const }}>{children}</div>}
      </div>
    )
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading tasks…</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Content Tasks</div>
        <button className="btn btn-sm" onClick={() => setShowCreate(!showCreate)} style={{ fontSize: 11 }}>{showCreate ? 'Cancel' : '+ New Task'}</button>
      </div>

      {showCreate && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>New Task</div>
          <input value={newTitle} onChange={e => setNewTitle(e.target.value)} placeholder="Task title" style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          <textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Description" rows={2} style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none', resize: 'vertical' as const }} />
          <div style={{ display: 'flex', gap: 10 }}>
            <input value={newKind} onChange={e => setNewKind(e.target.value)} placeholder="Kind tag" style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
            <select value={newProjectId} onChange={e => setNewProjectId(e.target.value)} style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }}>
              <option value="">No project</option>
              {projects.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
            </select>
            <input type="number" min={1} max={100} value={newPriority} onChange={e => setNewPriority(Number(e.target.value))} style={{ width: 70, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 8px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          </div>
          <button className="btn btn-sm" onClick={createTask} disabled={creating || !newTitle.trim()} style={{ alignSelf: 'flex-start', fontSize: 11 }}>{creating ? 'Creating…' : 'Create Task'}</button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 14, flex: 1, minHeight: 0, overflowX: 'auto' }}>
        {STATUS_COLUMNS.map((col, idx) => (
          <div key={col.id} style={colStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.08em', color: col.color }}>{col.label}</span>
              <span style={{ fontSize: 10, background: 'rgba(255,255,255,0.06)', padding: '1px 6px', borderRadius: 10, color: 'var(--muted)' }}>{(tasksByStatus[col.id] || []).length}</span>
            </div>
            {(tasksByStatus[col.id] || []).map(t => (
              <TaskCard key={t.id} task={t}>
                {idx === 0 && <button className="btn btn-sm" onClick={() => claimTask(t.id)} disabled={actioning === t.id} style={{ fontSize: 10 }}>{actioning === t.id ? '…' : 'Claim'}</button>}
                {idx === 1 && (submitTaskId === t.id ? (
                  <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <textarea value={submitContent} onChange={e => setSubmitContent(e.target.value)} placeholder="Submission notes…" rows={2} style={{ width: '100%', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 8px', color: 'var(--text)', fontSize: 11, outline: 'none', resize: 'vertical' as const, boxSizing: 'border-box' }} />
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-sm" onClick={() => submitTask(t.id)} disabled={submitting} style={{ fontSize: 10 }}>{submitting ? '…' : 'Submit'}</button>
                      <button className="btn btn-sm" onClick={() => setSubmitTaskId(null)} style={{ fontSize: 10 }}>Cancel</button>
                    </div>
                  </div>
                ) : <button className="btn btn-sm" onClick={() => setSubmitTaskId(t.id)} style={{ fontSize: 10 }}>Submit</button>)}
                {idx === 2 && (
                  <>
                    <button className="btn btn-sm" onClick={() => acceptTask(t.id)} disabled={actioning === t.id} style={{ fontSize: 10, color: '#3cc878' }}>{actioning === t.id ? '…' : 'Accept'}</button>
                    {rejectTaskId === t.id ? (
                      <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                        <input value={rejectNote} onChange={e => setRejectNote(e.target.value)} placeholder="Rejection note…" style={{ width: '100%', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 8px', color: 'var(--text)', fontSize: 11, outline: 'none', boxSizing: 'border-box' }} />
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button className="btn btn-sm" onClick={() => rejectTask(t.id)} disabled={actioning === t.id} style={{ fontSize: 10, color: '#ef4444' }}>Confirm</button>
                          <button className="btn btn-sm" onClick={() => setRejectTaskId(null)} style={{ fontSize: 10 }}>Cancel</button>
                        </div>
                      </div>
                    ) : <button className="btn btn-sm" onClick={() => setRejectTaskId(t.id)} style={{ fontSize: 10, color: '#ef4444' }}>Reject</button>}
                  </>
                )}
              </TaskCard>
            ))}
            {(tasksByStatus[col.id] || []).length === 0 && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', textAlign: 'center', marginTop: 16 }}>Empty</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
