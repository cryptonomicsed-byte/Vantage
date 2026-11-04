import React, { useState, useEffect, useCallback } from 'react'

interface ContentAsset {
  id: string
  kind: string
  title: string
  status: string
  provider: string
  project_id: string | null
  content_uri: string | null
  created_at: string
}

interface Project { id: string; title: string }

interface Props { slug: string; spaceId: string; authHeaders: Record<string, string> }

const KIND_ICONS: Record<string, string> = { video: 'Vid', audio: 'Aud', image: 'Img', document: 'Doc', other: 'Pkg' }
const KIND_COLORS: Record<string, string> = { video: '#ef4444', audio: '#8B5CF6', image: '#3cc878', document: 'var(--cyan)', other: 'var(--muted)' }
const KIND_GROUPS = ['video', 'audio', 'image', 'document', 'other']

export default function ContentAssetLibrary({ slug, spaceId, authHeaders }: Props) {
  const formHeaders = { ...authHeaders, 'Content-Type': 'application/x-www-form-urlencoded' }
  const [assets, setAssets] = useState<ContentAsset[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newKind, setNewKind] = useState('video')
  const [newProjectId, setNewProjectId] = useState('')
  const [newUri, setNewUri] = useState('')
  const [creating, setCreating] = useState(false)

  const base = '/api/guilds/' + slug + '/content-spaces/' + spaceId + '/assets'
  const projectsBase = '/api/guilds/' + slug + '/content-spaces/' + spaceId + '/projects'

  const loadAssets = useCallback(async () => {
    try {
      const r = await fetch(base, { headers: authHeaders })
      if (r.ok) setAssets((await r.json()).assets || [])
    } finally { setLoading(false) }
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadAssets()
    const t = setInterval(loadAssets, 20000)
    fetch(projectsBase, { headers: authHeaders })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setProjects(d.projects || []))
      .catch(() => {})
    return () => clearInterval(t)
  }, [loadAssets]) // eslint-disable-line react-hooks/exhaustive-deps

  async function createAsset() {
    if (!newTitle.trim()) return
    setCreating(true)
    try {
      const body = new URLSearchParams({ title: newTitle, kind: newKind, content_uri: newUri })
      if (newProjectId) body.set('project_id', newProjectId)
      const r = await fetch(base, { method: 'POST', headers: formHeaders, body: body.toString() })
      if (r.ok) { setNewTitle(''); setNewKind('video'); setNewProjectId(''); setNewUri(''); setShowCreate(false); loadAssets() }
    } finally { setCreating(false) }
  }

  function getProjectName(id: string | null): string {
    if (!id) return ''
    const p = projects.find(p => p.id === id)
    return p ? p.title : id.slice(0, 8) + '...'
  }

  function statusColor(s: string): string {
    if (s === 'published') return '#3cc878'
    if (s === 'draft') return '#f59e0b'
    if (s === 'archived') return 'var(--muted)'
    return 'var(--cyan)'
  }

  const inputStyle: React.CSSProperties = {
    background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)',
    borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none',
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading assets...</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Asset Library - {assets.length}</div>
        <button className="btn btn-sm" onClick={() => setShowCreate(!showCreate)} style={{ fontSize: 11 }}>{showCreate ? 'Cancel' : '+ Add Asset'}</button>
      </div>

      {showCreate && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>New Asset</div>
          <input value={newTitle} onChange={e => setNewTitle(e.target.value)} placeholder="Asset title" style={inputStyle} />
          <div style={{ display: 'flex', gap: 10 }}>
            <select value={newKind} onChange={e => setNewKind(e.target.value)} style={{ ...inputStyle, flex: 1 }}>
              {KIND_GROUPS.map(k => <option key={k} value={k}>{KIND_ICONS[k]} {k.charAt(0).toUpperCase() + k.slice(1)}</option>)}
            </select>
            <select value={newProjectId} onChange={e => setNewProjectId(e.target.value)} style={{ ...inputStyle, flex: 1 }}>
              <option value="">No project</option>
              {projects.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
            </select>
          </div>
          <input value={newUri} onChange={e => setNewUri(e.target.value)} placeholder="Content URI (optional)" style={inputStyle} />
          <button className="btn btn-sm" onClick={createAsset} disabled={creating || !newTitle.trim()} style={{ alignSelf: 'flex-start', fontSize: 11 }}>
            {creating ? 'Adding...' : 'Add Asset'}
          </button>
        </div>
      )}

      {assets.length === 0 && !showCreate && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No assets yet.</div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
        {assets.map(asset => {
          const icon = KIND_ICONS[asset.kind] || KIND_ICONS.other
          const kc = KIND_COLORS[asset.kind] || KIND_COLORS.other
          const sc = statusColor(asset.status)
          return (
            <div key={asset.id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 20, fontWeight: 700, color: kc }}>{icon}</span>
                <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 3, background: sc + '22', color: sc, textTransform: 'uppercase' as const }}>{asset.status}</span>
              </div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', lineHeight: 1.3 }}>{asset.title}</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' as const }}>
                <span style={{ fontSize: 9, padding: '1px 6px', borderRadius: 3, background: kc + '22', color: kc, textTransform: 'uppercase' as const, fontWeight: 700 }}>{asset.kind}</span>
                {asset.provider && <span style={{ fontSize: 10, color: 'var(--muted)' }}>{asset.provider}</span>}
              </div>
              {asset.project_id && <div style={{ fontSize: 10, color: 'var(--cyan)' }}>{getProjectName(asset.project_id)}</div>}
              {asset.content_uri && (
                <a href={asset.content_uri} target="_blank" rel="noopener noreferrer"
                  style={{ fontSize: 11, color: 'var(--cyan)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const, display: 'block' }}>
                  {asset.content_uri.length > 35 ? asset.content_uri.slice(0, 35) + '...' : asset.content_uri}
                </a>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
