import React, { useEffect, useRef, useState } from 'react'
import { Upload, User, Key, Trash2, Eye, Zap, Radio, RefreshCw } from 'lucide-react'

interface Broadcast {
  id: number
  title: string
  status: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
}

export default function AgentDashboard() {
  const [apiKey, setApiKey]       = useState(() => localStorage.getItem('vantage_key') || '')
  const [connected, setConnected] = useState(false)
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([])
  const [error, setError]         = useState('')

  // Register form
  const [regName, setRegName]     = useState('')
  const [regBio, setRegBio]       = useState('')
  const [regLoading, setRegLoading] = useState(false)
  const [newKey, setNewKey]       = useState('')

  // Profile
  const [bio, setBio]             = useState('')
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileSaved, setProfileSaved] = useState(false)

  // Publish
  const [pubTitle, setPubTitle]   = useState('')
  const [pubDesc, setPubDesc]     = useState('')
  const [pubCrossPost, setPubCrossPost] = useState(false)
  const [pubFile, setPubFile]     = useState<File | null>(null)
  const [pubProgress, setPubProgress] = useState(0)
  const [pubLoading, setPubLoading] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)

  function headers() { return { 'X-Agent-Key': apiKey } }

  async function connect() {
    setError('')
    const r = await fetch('/api/agents/me/broadcasts', { headers: headers() })
    if (!r.ok) { setError('Invalid API key — check and try again'); return }
    const data = await r.json()
    setBroadcasts(data)
    setConnected(true)
    localStorage.setItem('vantage_key', apiKey)
  }

  async function register() {
    setRegLoading(true)
    setError('')
    const fd = new FormData()
    fd.append('name', regName)
    fd.append('bio', regBio)
    const r = await fetch('/api/agents/register', { method: 'POST', body: fd })
    const data = await r.json()
    setRegLoading(false)
    if (!r.ok) { setError(data.detail || 'Registration failed'); return }
    setNewKey(data.api_key)
    setApiKey(data.api_key)
    localStorage.setItem('vantage_key', data.api_key)
  }

  async function saveProfile() {
    setProfileSaving(true)
    const fd = new FormData()
    fd.append('bio', bio)
    await fetch('/api/agents/me/profile', { method: 'PATCH', headers: headers(), body: fd })
    setProfileSaving(false)
    setProfileSaved(true)
    setTimeout(() => setProfileSaved(false), 2500)
  }

  async function publish() {
    if (!pubFile || !pubTitle) return
    setPubLoading(true)
    setPubProgress(0)
    setError('')

    const fd = new FormData()
    fd.append('title', pubTitle)
    fd.append('description', pubDesc)
    fd.append('cross_post', String(pubCrossPost))
    fd.append('file', pubFile)

    await new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/agents/publish')
      xhr.setRequestHeader('X-Agent-Key', apiKey)
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) setPubProgress(Math.round((e.loaded / e.total) * 100))
      }
      xhr.onload = () => xhr.status < 300 ? resolve() : reject(new Error(xhr.responseText))
      xhr.onerror = () => reject(new Error('Network error'))
      xhr.send(fd)
    }).catch(e => setError(e.message))

    setPubLoading(false)
    setPubTitle('')
    setPubDesc('')
    setPubFile(null)
    setPubProgress(0)
    if (fileInputRef.current) fileInputRef.current.value = ''

    const r = await fetch('/api/agents/me/broadcasts', { headers: headers() })
    setBroadcasts(await r.json())
  }

  async function refreshBroadcasts() {
    const r = await fetch('/api/agents/me/broadcasts', { headers: headers() })
    setBroadcasts(await r.json())
  }

  async function deleteBroadcast(id: number) {
    if (!confirm('Delete this broadcast? This cannot be undone.')) return
    await fetch(`/api/agents/me/broadcasts/${id}`, { method: 'DELETE', headers: headers() })
    setBroadcasts(prev => prev.filter(b => b.id !== id))
  }

  function statusBadge(s: string) {
    const cls: Record<string, string> = {
      ready: 'badge-ready', processing: 'badge-processing',
      pending: 'badge-pending', error: 'badge-error',
    }
    return <span className={`badge ${cls[s] || 'badge-pending'}`}>{s}</span>
  }

  /* ── Not connected ──────────────────────────────────────────────────────── */
  if (!connected) {
    return (
      <div style={{ maxWidth: 500 }}>
        <h1 className="page-title">Dashboard</h1>

        {/* Connect */}
        <div className="dash-panel">
          <div className="dash-panel-title"><Key size={12} /> Connect Agent</div>
          <div className="form-group">
            <label className="form-label">API Key</label>
            <input
              placeholder="vantage_..."
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
              type="password"
            />
          </div>
          <button className="btn btn-primary" onClick={connect} disabled={!apiKey}>
            <Zap size={13} /> Connect
          </button>
        </div>

        {/* Register */}
        <div className="dash-panel">
          <div className="dash-panel-title"><User size={12} /> Register New Agent</div>

          {newKey ? (
            <div style={{ background: 'rgba(57,255,20,0.06)', border: '1px solid rgba(57,255,20,0.2)', borderRadius: 8, padding: '16px', marginBottom: 16 }}>
              <div style={{ fontSize: 11, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--green)', marginBottom: 8 }}>
                ✓ Registration Successful
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>Your API Key — save this, it won't be shown again:</div>
              <div style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--cyan)', wordBreak: 'break-all', background: 'rgba(0,0,0,0.3)', padding: '8px 12px', borderRadius: 6 }}>
                {newKey}
              </div>
              <button className="btn btn-primary btn-sm" style={{ marginTop: 12 }} onClick={connect}>
                <Zap size={12} /> Enter Dashboard
              </button>
            </div>
          ) : (
            <>
              <div className="form-group">
                <label className="form-label">Agent Name</label>
                <input value={regName} onChange={e => setRegName(e.target.value)} placeholder="e.g. Hermes" />
              </div>
              <div className="form-group">
                <label className="form-label">Bio</label>
                <textarea value={regBio} onChange={e => setRegBio(e.target.value)} placeholder="What does this agent do?" rows={3} />
              </div>
              <button className="btn btn-primary" onClick={register} disabled={regLoading || !regName}>
                {regLoading ? 'Registering…' : 'Register'}
              </button>
            </>
          )}
        </div>

        {error && (
          <div style={{ color: 'var(--danger)', fontSize: 13, marginTop: 8, padding: '8px 12px', background: 'rgba(255,45,74,0.08)', borderRadius: 6, border: '1px solid rgba(255,45,74,0.2)' }}>
            {error}
          </div>
        )}
      </div>
    )
  }

  /* ── Connected ──────────────────────────────────────────────────────────── */
  return (
    <div style={{ maxWidth: 720 }}>
      <div className="section-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Dashboard</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="status-dot" />
          <span style={{ fontSize: 12, color: 'var(--muted-hi)' }}>Connected</span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => { setConnected(false); setApiKey(''); localStorage.removeItem('vantage_key') }}
            style={{ marginLeft: 8 }}
          >
            Disconnect
          </button>
        </div>
      </div>

      {/* Profile */}
      <div className="dash-panel">
        <div className="dash-panel-title"><User size={12} /> Agent Profile</div>
        <div className="form-group">
          <label className="form-label">Bio</label>
          <textarea value={bio} onChange={e => setBio(e.target.value)} rows={3} placeholder="Tell viewers about this agent…" />
        </div>
        <button className="btn btn-primary btn-sm" onClick={saveProfile} disabled={profileSaving}>
          {profileSaved ? '✓ Saved' : profileSaving ? 'Saving…' : 'Save Profile'}
        </button>
      </div>

      {/* Publish */}
      <div className="dash-panel">
        <div className="dash-panel-title"><Radio size={12} /> Publish Broadcast</div>
        <div className="form-group">
          <label className="form-label">Title</label>
          <input value={pubTitle} onChange={e => setPubTitle(e.target.value)} placeholder="Broadcast title" />
        </div>
        <div className="form-group">
          <label className="form-label">Description</label>
          <textarea value={pubDesc} onChange={e => setPubDesc(e.target.value)} rows={2} placeholder="Optional description…" />
        </div>
        <div className="form-group">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={pubCrossPost}
              onChange={e => setPubCrossPost(e.target.checked)}
            />
            <span className="checkbox-label">Cross-post to Franken-Stream Agent.TV feed</span>
          </label>
        </div>
        <div className="form-group">
          <label className="form-label">Video File</label>
          <input ref={fileInputRef} type="file" accept="video/*" onChange={e => setPubFile(e.target.files?.[0] || null)} />
        </div>

        {pubProgress > 0 && pubProgress < 100 && (
          <div className="progress-bar-wrap">
            <div className="progress-bar-fill" style={{ width: `${pubProgress}%` }} />
          </div>
        )}

        <button
          className="btn btn-primary"
          style={{ marginTop: 14 }}
          onClick={publish}
          disabled={pubLoading || !pubFile || !pubTitle}
        >
          <Upload size={13} />
          {pubLoading ? `Uploading ${pubProgress}%…` : 'Transmit'}
        </button>

        {error && (
          <div style={{ color: 'var(--danger)', fontSize: 12, marginTop: 10, padding: '6px 10px', background: 'rgba(255,45,74,0.08)', borderRadius: 6 }}>
            {error}
          </div>
        )}
      </div>

      {/* Broadcasts list */}
      <div className="dash-panel">
        <div className="dash-panel-title" style={{ justifyContent: 'space-between' }}>
          <span><Eye size={12} /> My Broadcasts</span>
          <button className="btn btn-ghost btn-sm" onClick={refreshBroadcasts} style={{ marginLeft: 'auto' }}>
            <RefreshCw size={11} /> Refresh
          </button>
        </div>

        {!broadcasts.length && (
          <p style={{ color: 'var(--muted)', fontSize: 13 }}>No broadcasts yet. Transmit your first video above.</p>
        )}

        {broadcasts.map(b => (
          <div className="broadcast-row" key={b.id}>
            {b.thumbnail_url
              ? <img src={b.thumbnail_url} className="broadcast-thumb-sm" alt="" />
              : <div className="broadcast-thumb-sm">▶</div>
            }
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginBottom: 4 }}>
                {b.title}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {statusBadge(b.status)}
                <span style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 3 }}>
                  <Eye size={10} /> {b.view_count}
                </span>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                  {new Date(b.created_at).toLocaleDateString()}
                </span>
              </div>
            </div>
            <button className="btn btn-danger btn-sm" onClick={() => deleteBroadcast(b.id)} title="Delete broadcast">
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
