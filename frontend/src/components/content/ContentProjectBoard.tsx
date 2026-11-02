import React, { useState, useEffect, useCallback } from 'react'

interface ContentProject {
  id: string
  title: string
  brief: string
  status: string
  provider: string
  created_at: string
}

interface Props {
  slug: string
  spaceId: string
  authHeaders: Record<string, string>
}

const STAGES = [
  { id: 'brief_submitted', label: 'Brief Submitted', color: '#f59e0b' },
  { id: 'in_production', label: 'In Production', color: 'var(--cyan)' },
  { id: 'in_review', label: 'In Review', color: '#f59e0b' },
  { id: 'published', label: 'Published', color: '#3cc878' },
]

const STAGE_ORDER = ['brief_submitted', 'in_production', 'in_review', 'published']

const PROVIDERS = ['vantage_anvil', 'higgsfield', 'heygen']

function nextStage(current: string): string | null {
  const idx = STAGE_ORDER.indexOf(current)
  if (idx < 0 || idx >= STAGE_ORDER.length - 1) return null
  return STAGE_ORDER[idx + 1]
}

function stageColor(s: string): string {
  const found = STAGES.find(st => st.id === s)
  return found ? found.color : 'var(--muted)'
}

function stageLabel(s: string): string {
  const found = STAGES.find(st => st.id === s)
  return found ? found.label : s
}

export default function ContentProjectBoard({ slug, spaceId, authHeaders }: Props) {
  const formHeaders = { ...authHeaders, 'Content-Type': 'application/x-www-form-urlencoded' }
  const [projects, setProjects] = useState<ContentProject[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newBrief, setNewBrief] = useState('')
  const [newProvider, setNewProvider] = useState('vantage_anvil')
  const [creating, setCreating] = useState(false)
  const [advancing, setAdvancing] = useState<string | null>(null)

  const base = '/api/guilds/' + slug + '/content-spaces/' + spaceId + '/projects'

  const loadProjects = useCallback(async () => {
    try {
      const r = await fetch(base, { headers: authHeaders })
      if (r.ok) setProjects((await r.json()).projects || [])
    } finally { setLoading(false) }
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadProjects()
    const t = setInterval(loadProjects, 20000)
    return () => clearInterval(t)
  }, [loadProjects])

  async function createProject() {
    if (!newTitle.trim()) return
    setCreating(true)
    try {
      const body = new URLSearchParams({ title: newTitle, brief: newBrief, provider: newProvider })
      const r = await fetch(base, { method: 'POST', headers: formHeaders, body: body.toString() })
      if (r.ok) { setNewTitle(''); setNewBrief(''); setNewProvider('vantage_anvil'); setShowCreate(false); loadProjects() }
    } finally { setCreating(false) }
  }

  async function advanceProject(id: string, currentStatus: string) {
    const next = nextStage(currentStatus)
    if (!next) return
    setAdvancing(id)
    try {
      const body = new URLSearchParams({ status: next })
      await fetch(base + '/' + id, { method: 'PATCH', headers: formHeaders, body: body.toString() })
      loadProjects()
    } finally { setAdvancing(null) }
  }

  const projectsByStage: Record<string, ContentProject[]> = {}
  for (const s of STAGES) projectsByStage[s.id] = []
  for (const p of projects) {
    if (projectsByStage[p.status]) projectsByStage[p.status].push(p)
    else {
      if (!projectsByStage['brief_submitted']) projectsByStage['brief_submitted'] = []
      projectsByStage['brief_submitted'].push(p)
    }
  }

  const colStyle: React.CSSProperties = { flex: '1 1 0', minWidth: 220, display: 'flex', flexDirection: 'column', gap: 8 }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading projects…</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Content Projects</div>
        <button className=btn btn-sm onClick={() => setShowCreate(!showCreate)} style={{ fontSize: 11 }}>{showCreate ? 'Cancel' : '+ New Project'}</button>
      </div>

      {showCreate && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>New Project</div>
          <input value={newTitle} onChange={e => setNewTitle(e.target.value)} placeholder=Project title style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          <textarea value={newBrief} onChange={e => setNewBrief(e.target.value)} placeholder=Brief / description rows={3} style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none', resize: 'vertical' as const }} />
          <select value={newProvider} onChange={e => setNewProvider(e.target.value)} style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }}>
            {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className=btn btn-sm onClick={createProject} disabled={creating || !newTitle.trim()} style={{ alignSelf: 'flex-start', fontSize: 11 }}>{creating ? 'Creating…' : 'Create Project'}</button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 14, flex: 1, minHeight: 0, overflowX: 'auto' }}>
        {STAGES.map(stage => (
          <div key={stage.id} style={colStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.08em', color: stage.color }}>{stage.label}</span>
              <span style={{ fontSize: 10, background: 'rgba(255,255,255,0.06)', padding: '1px 6px', borderRadius: 10, color: 'var(--muted)' }}>{projectsByStage[stage.id].length}</span>
            </div>
            {projectsByStage[stage.id].map(proj => {
              const sc = stageColor(proj.status)
              const hasNext = !!nextStage(proj.status)
              return (
                <div key={proj.id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>{proj.title}</div>
                  {proj.brief && <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, lineHeight: 1.4 }}>{proj.brief.slice(0, 100)}{proj.brief.length > 100 ? '…' : ''}</div>}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: hasNext ? 8 : 0 }}>
                    <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: sc + '22', color: sc, textTransform: 'uppercase' as const }}>{stageLabel(proj.status)}</span>
                    {proj.provider && <span style={{ fontSize: 10, color: 'var(--muted)' }}>{proj.provider}</span>}
                  </div>
                  {hasNext && (
                    <button className=btn btn-sm onClick={() => advanceProject(proj.id, proj.status)} disabled={advancing === proj.id} style={{ fontSize: 10, width: '100%' }}>
                      {advancing === proj.id ? '…' : 'Move to ' + stageLabel(nextStage(proj.status) as string)}
                    </button>
                  )}
                </div>
              )
            })}
            {projectsByStage[stage.id].length === 0 && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', textAlign: 'center', marginTop: 16 }}>Empty</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
