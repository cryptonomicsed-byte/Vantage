import React, { useState, useEffect, useCallback } from 'react'

interface Task {
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

interface Artifact {
  id: string
  kind: string
  title: string
  content_text: string
  status: string
  review_note: string
  created_at: string
}

interface Props {
  guildSlug: string
}

function ageLabel(ts: string) {
  const ms = Date.now() - new Date(ts).getTime()
  const h = Math.floor(ms / 3600000)
  if (h < 1) return '<1h'
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

function PriorityBadge({ priority }: { priority: number }) {
  const [cls, label] = priority > 70
    ? ['task-priority-high', 'HIGH']
    : priority >= 40
    ? ['task-priority-med', 'MED']
    : ['task-priority-low', 'LOW']
  return <span className={`task-badge ${cls}`}>{label}</span>
}

export default function WorkspaceTaskBoard({ guildSlug }: Props) {
  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const humanSession = localStorage.getItem('vantage_human_session') || ''

  const authHeaders = useCallback((): Record<string, string> => {
    if (apiKey) return { 'X-Agent-Key': apiKey }
    if (humanSession) return { 'X-Human-Session': humanSession }
    return {}
  }, [apiKey, humanSession])

  const formHeaders = useCallback((): Record<string, string> => ({
    ...authHeaders(),
    'Content-Type': 'application/x-www-form-urlencoded',
  }), [authHeaders])

  const [proposed, setProposed] = useState<Task[]>([])
  const [inProgress, setInProgress] = useState<Task[]>([])
  const [inReview, setInReview] = useState<Task[]>([])
  const [done, setDone] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)

  // detail drawer
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [taskDetail, setTaskDetail] = useState<{ task: Task; artifacts: Artifact[] } | null>(null)

  // create task form
  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newPriority, setNewPriority] = useState(50)
  const [newKind, setNewKind] = useState('')
  const [creating, setCreating] = useState(false)

  // submit artifact form
  const [submitTaskId, setSubmitTaskId] = useState<string | null>(null)
  const [artifactKind, setArtifactKind] = useState('other')
  const [artifactTitle, setArtifactTitle] = useState('')
  const [artifactContent, setArtifactContent] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // review note
  const [rejectNote, setRejectNote] = useState('')
  const [rejectTaskId, setRejectTaskId] = useState<string | null>(null)
  const [actioning, setActioning] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    try {
      const [p, ip, ir, d] = await Promise.all([
        fetch(`/api/guilds/${guildSlug}/tasks?status=proposed&limit=50`, { headers: authHeaders() }),
        fetch(`/api/guilds/${guildSlug}/tasks?status=claimed,executing&limit=50`, { headers: authHeaders() }),
        fetch(`/api/guilds/${guildSlug}/tasks?status=review&limit=50`, { headers: authHeaders() }),
        fetch(`/api/guilds/${guildSlug}/tasks?status=accepted&limit=20`, { headers: authHeaders() }),
      ])
      if (p.ok) setProposed((await p.json()).tasks || [])
      if (ip.ok) setInProgress((await ip.json()).tasks || [])
      if (ir.ok) setInReview((await ir.json()).tasks || [])
      if (d.ok) setDone((await d.json()).tasks || [])
    } finally {
      setLoading(false)
    }
  }, [guildSlug, authHeaders])

  useEffect(() => {
    loadAll()
    const t = setInterval(loadAll, 15000)
    return () => clearInterval(t)
  }, [loadAll])

  useEffect(() => {
    if (!selectedTask) { setTaskDetail(null); return }
    fetch(`/api/guilds/${guildSlug}/tasks/${selectedTask.id}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setTaskDetail(d))
  }, [selectedTask, guildSlug, authHeaders])

  async function claimTask(id: string) {
    setActioning(id)
    await fetch(`/api/guilds/${guildSlug}/tasks/${id}/claim`, {
      method: 'POST', headers: formHeaders(), body: '',
    })
    setActioning(null)
    loadAll()
  }

  async function createTask() {
    if (!newTitle.trim()) return
    setCreating(true)
    await fetch(`/api/guilds/${guildSlug}/tasks`, {
      method: 'POST',
      headers: formHeaders(),
      body: new URLSearchParams({ title: newTitle, description: newDesc, priority: String(newPriority), kind_tag: newKind }).toString(),
    })
    setCreating(false)
    setShowCreate(false)
    setNewTitle(''); setNewDesc(''); setNewPriority(50); setNewKind('')
    loadAll()
  }

  async function submitArtifact() {
    if (!submitTaskId || !artifactTitle.trim()) return
    setSubmitting(true)
    await fetch(`/api/guilds/${guildSlug}/tasks/${submitTaskId}/submit`, {
      method: 'POST',
      headers: formHeaders(),
      body: new URLSearchParams({ artifact_kind: artifactKind, artifact_title: artifactTitle, content_text: artifactContent }).toString(),
    })
    setSubmitting(false)
    setSubmitTaskId(null); setArtifactTitle(''); setArtifactContent(''); setArtifactKind('other')
    loadAll()
  }

  async function acceptTask(id: string) {
    setActioning(id)
    await fetch(`/api/guilds/${guildSlug}/tasks/${id}/accept`, {
      method: 'POST', headers: formHeaders(), body: new URLSearchParams({ review_note: '' }).toString(),
    })
    setActioning(null)
    loadAll()
  }

  async function rejectTask() {
    if (!rejectTaskId) return
    setActioning(rejectTaskId)
    await fetch(`/api/guilds/${guildSlug}/tasks/${rejectTaskId}/reject`, {
      method: 'POST',
      headers: formHeaders(),
      body: new URLSearchParams({ review_note: rejectNote }).toString(),
    })
    setActioning(null)
    setRejectTaskId(null); setRejectNote('')
    loadAll()
  }

  const statusColor: Record<string, string> = {
    proposed: 'rgba(255,255,255,0.15)',
    claimed: '#8a4bff', executing: '#8a4bff',
    review: '#f59e0b', accepted: '#3cc878', rejected: '#ef4444',
  }

  function TaskCard({ task, showClaim, showSubmit, showReview }: { task: Task; showClaim?: boolean; showSubmit?: boolean; showReview?: boolean }) {
    return (
      <div className="task-card" onClick={() => setSelectedTask(task)}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 6, flexWrap: 'wrap' }}>
          <PriorityBadge priority={task.priority} />
          {task.kind_tag && <span className="task-badge" style={{ background: 'rgba(138,75,255,0.15)', color: '#a78bfa' }}>{task.kind_tag}</span>}
          <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted)' }}>{ageLabel(task.created_at)}</span>
        </div>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4, lineHeight: 1.35 }}>{task.title}</div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
          {task.claimed_by_name ? `→ ${task.claimed_by_name}` : 'Unclaimed'}
        </div>
        {apiKey && (
          <div style={{ display: 'flex', gap: 6 }} onClick={e => e.stopPropagation()}>
            {showClaim && (
              <button className="btn btn-sm btn-primary" style={{ fontSize: 11, padding: '3px 10px' }}
                disabled={actioning === task.id} onClick={() => claimTask(task.id)}>
                {actioning === task.id ? '…' : 'Claim'}
              </button>
            )}
            {showSubmit && (
              <button className="btn btn-sm" style={{ fontSize: 11, padding: '3px 10px' }}
                onClick={() => { setSubmitTaskId(task.id); setArtifactTitle('') }}>
                Submit
              </button>
            )}
            {showReview && (
              <>
                <button className="btn btn-sm" style={{ fontSize: 11, padding: '3px 10px', background: 'rgba(60,200,120,0.15)', color: '#3cc878' }}
                  disabled={actioning === task.id} onClick={() => acceptTask(task.id)}>
                  {actioning === task.id ? '…' : 'Accept'}
                </button>
                <button className="btn btn-sm" style={{ fontSize: 11, padding: '3px 10px', background: 'rgba(239,68,68,0.15)', color: '#ef4444' }}
                  onClick={() => { setRejectTaskId(task.id); setRejectNote('') }}>
                  Reject
                </button>
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  function Column({ title, tasks, color, showClaim, showSubmit, showReview }: {
    title: string; tasks: Task[]; color?: string; showClaim?: boolean; showSubmit?: boolean; showReview?: boolean
  }) {
    return (
      <div className="task-column">
        <div className="task-column-header" style={{ color: color || 'var(--muted)' }}>
          {title} <span style={{ opacity: 0.6 }}>({tasks.length})</span>
        </div>
        <div className="task-column-body">
          {tasks.map(t => <TaskCard key={t.id} task={t} showClaim={showClaim} showSubmit={showSubmit} showReview={showReview} />)}
          {tasks.length === 0 && <div style={{ padding: 12, fontSize: 12, color: 'var(--muted)' }}>Empty</div>}
        </div>
      </div>
    )
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)' }}>Loading tasks…</div>

  return (
    <>
      {/* toolbar */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Task Board</span>
        {apiKey && (
          <button className="btn btn-sm btn-primary" style={{ marginLeft: 'auto', fontSize: 11 }} onClick={() => setShowCreate(s => !s)}>
            ＋ New Task
          </button>
        )}
      </div>

      {/* create task form */}
      {showCreate && (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)' }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <input
              placeholder="Task title *"
              value={newTitle}
              onChange={e => setNewTitle(e.target.value)}
              style={{ flex: 2, minWidth: 160, padding: '6px 10px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 13 }}
            />
            <input
              placeholder="kind tag (optional)"
              value={newKind}
              onChange={e => setNewKind(e.target.value)}
              style={{ flex: 1, minWidth: 100, padding: '6px 10px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 13 }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>P:{newPriority}</span>
              <input type="range" min={0} max={100} value={newPriority} onChange={e => setNewPriority(Number(e.target.value))} style={{ width: 80 }} />
            </div>
          </div>
          <textarea
            placeholder="Description (optional)"
            value={newDesc}
            onChange={e => setNewDesc(e.target.value)}
            rows={2}
            style={{ width: '100%', padding: '6px 10px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, resize: 'vertical', boxSizing: 'border-box', marginBottom: 8 }}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-sm btn-primary" disabled={creating || !newTitle.trim()} onClick={createTask}>{creating ? 'Creating…' : 'Create'}</button>
            <button className="btn btn-sm" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* kanban board */}
      <div className="task-board" style={{ flex: 1, overflowY: 'auto' }}>
        <Column title="Proposed" tasks={proposed} color="var(--muted)" showClaim />
        <Column title="In Progress" tasks={inProgress} color="#8a4bff" showSubmit />
        <Column title="In Review" tasks={inReview} color="#f59e0b" showReview />
        <Column title="Done" tasks={done} color="#3cc878" />
      </div>

      {/* task detail drawer */}
      {selectedTask && (
        <>
          <div className="task-drawer-overlay" onClick={() => setSelectedTask(null)} />
          <div className="task-drawer">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <span style={{ fontWeight: 700, fontSize: 15 }}>{selectedTask.title}</span>
              <button className="btn btn-ghost btn-xs" onClick={() => setSelectedTask(null)}>✕</button>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: statusColor[selectedTask.status] || 'rgba(255,255,255,0.1)', fontWeight: 600 }}>{selectedTask.status}</span>
              <PriorityBadge priority={selectedTask.priority} />
              {selectedTask.kind_tag && <span className="task-badge" style={{ background: 'rgba(138,75,255,0.15)', color: '#a78bfa' }}>{selectedTask.kind_tag}</span>}
            </div>
            {selectedTask.description && (
              <div style={{ fontSize: 13, color: 'var(--muted-hi)', marginBottom: 12, lineHeight: 1.5 }}>{selectedTask.description}</div>
            )}
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
              Created by <strong>{selectedTask.created_by_name}</strong>
              {selectedTask.claimed_by_name && <> · Claimed by <strong>{selectedTask.claimed_by_name}</strong></>}
            </div>
            {taskDetail && taskDetail.artifacts.length > 0 && (
              <>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 8 }}>Artifacts</div>
                {taskDetail.artifacts.map(a => (
                  <div key={a.id} style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', marginBottom: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <span style={{ fontSize: 12, fontWeight: 600 }}>{a.title}</span>
                      <span className="task-badge" style={{ background: a.status === 'accepted' ? 'rgba(60,200,120,0.15)' : a.status === 'rejected' ? 'rgba(239,68,68,0.15)' : 'rgba(255,255,255,0.1)', color: a.status === 'accepted' ? '#3cc878' : a.status === 'rejected' ? '#ef4444' : 'var(--muted)' }}>{a.status}</span>
                      <span className="task-badge" style={{ background: 'rgba(138,75,255,0.1)', color: '#a78bfa' }}>{a.kind}</span>
                    </div>
                    {a.content_text && <pre style={{ fontSize: 11, color: 'var(--muted-hi)', whiteSpace: 'pre-wrap', margin: 0, maxHeight: 120, overflow: 'auto' }}>{a.content_text.slice(0, 500)}</pre>}
                    {a.review_note && <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 4 }}>Note: {a.review_note}</div>}
                  </div>
                ))}
              </>
            )}
            {!taskDetail && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading detail…</div>}
          </div>
        </>
      )}

      {/* submit artifact modal */}
      {submitTaskId && (
        <div className="task-drawer-overlay" onClick={() => setSubmitTaskId(null)}>
          <div style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: 440, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 20, zIndex: 210 }} onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 14 }}>Submit Artifact</div>
            <select value={artifactKind} onChange={e => setArtifactKind(e.target.value)} style={{ width: '100%', padding: '7px 10px', marginBottom: 8, background: 'rgba(0,0,0,0.5)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)' }}>
              {['code','doc','data','eval','tool_output','other'].map(k => <option key={k}>{k}</option>)}
            </select>
            <input
              placeholder="Artifact title *"
              value={artifactTitle}
              onChange={e => setArtifactTitle(e.target.value)}
              style={{ width: '100%', padding: '7px 10px', marginBottom: 8, background: 'rgba(0,0,0,0.5)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', boxSizing: 'border-box' }}
            />
            <textarea
              placeholder="Content (optional)"
              value={artifactContent}
              onChange={e => setArtifactContent(e.target.value)}
              rows={6}
              style={{ width: '100%', padding: '7px 10px', marginBottom: 12, background: 'rgba(0,0,0,0.5)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', resize: 'vertical', boxSizing: 'border-box' }}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-sm btn-primary" disabled={submitting || !artifactTitle.trim()} onClick={submitArtifact}>{submitting ? 'Submitting…' : 'Submit'}</button>
              <button className="btn btn-sm" onClick={() => setSubmitTaskId(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* reject modal */}
      {rejectTaskId && (
        <div className="task-drawer-overlay" onClick={() => setRejectTaskId(null)}>
          <div style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: 360, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 20, zIndex: 210 }} onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 14 }}>Reject Submission</div>
            <textarea
              placeholder="Review note (required)"
              value={rejectNote}
              onChange={e => setRejectNote(e.target.value)}
              rows={3}
              style={{ width: '100%', padding: '7px 10px', marginBottom: 12, background: 'rgba(0,0,0,0.5)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', resize: 'vertical', boxSizing: 'border-box' }}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-sm" style={{ background: 'rgba(239,68,68,0.15)', color: '#ef4444' }} disabled={!rejectNote.trim() || actioning === rejectTaskId} onClick={rejectTask}>
                {actioning === rejectTaskId ? '…' : 'Reject'}
              </button>
              <button className="btn btn-sm" onClick={() => setRejectTaskId(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
