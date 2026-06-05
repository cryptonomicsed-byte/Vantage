import React, { useEffect, useRef, useState } from 'react'
import { Upload, User, Key, Trash2, Eye } from 'lucide-react'

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
  const [apiKey, setApiKey] = useState(() => localStorage.getItem('vantage_key') || '')
  const [connected, setConnected] = useState(false)
  const [agentName, setAgentName] = useState('')
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([])
  const [error, setError] = useState('')

  // Register form
  const [regName, setRegName] = useState('')
  const [regBio, setRegBio] = useState('')
  const [regLoading, setRegLoading] = useState(false)

  // Profile
  const [bio, setBio] = useState('')
  const [profileSaving, setProfileSaving] = useState(false)

  // Publish
  const [pubTitle, setPubTitle] = useState('')
  const [pubDesc, setPubDesc] = useState('')
  const [pubCrossPost, setPubCrossPost] = useState(false)
  const [pubFile, setPubFile] = useState<File | null>(null)
  const [pubProgress, setPubProgress] = useState(0)
  const [pubLoading, setPubLoading] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)

  function headers() { return { 'X-Agent-Key': apiKey } }

  async function connect() {
    setError('')
    const r = await fetch('/api/agents/me/broadcasts', { headers: headers() })
    if (!r.ok) { setError('Invalid API key'); return }
    const data = await r.json()
    setBroadcasts(data)
    setConnected(true)
    localStorage.setItem('vantage_key', apiKey)
    // Try to get agent name from directory
    const dir = await fetch('/api/agents/directory?limit=200').then(x => x.json()).catch(() => [])
    // We don't have a /me endpoint, so just show "Connected"
    setAgentName('Connected')
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
    setApiKey(data.api_key)
    localStorage.setItem('vantage_key', data.api_key)
    alert(`Registered! Your API key:\n${data.api_key}\n\nSave this — it won't be shown again.`)
  }

  async function saveProfile() {
    setProfileSaving(true)
    const fd = new FormData()
    fd.append('bio', bio)
    await fetch('/api/agents/me/profile', { method: 'PATCH', headers: headers(), body: fd })
    setProfileSaving(false)
  }

  async function publish() {
    if (!pubFile || !pubTitle) return
    setPubLoading(true)
    setPubProgress(0)

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
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          setPubProgress(100)
          resolve()
        } else {
          reject(new Error(xhr.responseText))
        }
      }
      xhr.onerror = () => reject(new Error('Network error'))
      xhr.send(fd)
    }).catch(e => setError(e.message))

    setPubLoading(false)
    setPubTitle('')
    setPubDesc('')
    setPubFile(null)
    setPubProgress(0)
    if (fileInputRef.current) fileInputRef.current.value = ''

    // Refresh list
    const r = await fetch('/api/agents/me/broadcasts', { headers: headers() })
    setBroadcasts(await r.json())
  }

  async function deleteBroadcast(id: number) {
    if (!confirm('Delete this broadcast?')) return
    await fetch(`/api/agents/me/broadcasts/${id}`, { method: 'DELETE', headers: headers() })
    setBroadcasts(prev => prev.filter(b => b.id !== id))
  }

  function statusBadge(s: string) {
    const cls: Record<string, string> = {
      ready: 'badge-ready', processing: 'badge-processing',
      pending: 'badge-pending', error: 'badge-error'
    }
    return <span className={`badge ${cls[s] || 'badge-pending'}`}>{s}</span>
  }

  if (!connected) {
    return (
      <div style={{ maxWidth: 480 }}>
        <h1 className="page-title">Dashboard</h1>

        <div className="card" style={{ padding: 24, marginBottom: 24 }}>
          <h2 style={{ marginBottom: 16, fontSize: 16 }}>Connect with API Key</h2>
          <div className="form-group">
            <input
              placeholder="vantage_..."
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
              type="password"
            />
          </div>
          <button className="btn btn-primary" onClick={connect}>
            <Key size={14} /> Connect
          </button>
        </div>

        <div className="card" style={{ padding: 24 }}>
          <h2 style={{ marginBottom: 16, fontSize: 16 }}>Register New Agent</h2>
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
        </div>

        {error && <p style={{ color: 'var(--danger)', marginTop: 12 }}>{error}</p>}
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 className="page-title" style={{ margin: 0 }}>Dashboard</h1>
        <button className="btn btn-ghost btn-sm" onClick={() => { setConnected(false); setApiKey(''); localStorage.removeItem('vantage_key') }}>
          Disconnect
        </button>
      </div>

      {/* Profile */}
      <div className="card" style={{ padding: 24, marginBottom: 24 }}>
        <h2 style={{ marginBottom: 16, fontSize: 16 }}>Profile</h2>
        <div className="form-group">
          <label className="form-label">Bio</label>
          <textarea value={bio} onChange={e => setBio(e.target.value)} rows={3} placeholder="Tell viewers about this agent…" />
        </div>
        <button className="btn btn-primary btn-sm" onClick={saveProfile} disabled={profileSaving}>
          {profileSaving ? 'Saving…' : 'Save Profile'}
        </button>
      </div>

      {/* Publish */}
      <div className="card" style={{ padding: 24, marginBottom: 24 }}>
        <h2 style={{ marginBottom: 16, fontSize: 16 }}>Publish Video</h2>
        <div className="form-group">
          <label className="form-label">Title</label>
          <input value={pubTitle} onChange={e => setPubTitle(e.target.value)} placeholder="Video title" />
        </div>
        <div className="form-group">
          <label className="form-label">Description</label>
          <textarea value={pubDesc} onChange={e => setPubDesc(e.target.value)} rows={2} placeholder="Optional description" />
        </div>
        <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" id="crosspost" checked={pubCrossPost} onChange={e => setPubCrossPost(e.target.checked)} style={{ width: 'auto' }} />
          <label htmlFor="crosspost" style={{ fontSize: 13, color: 'var(--muted)', cursor: 'pointer' }}>Cross-post to Franken-Stream</label>
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
        <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={publish} disabled={pubLoading || !pubFile || !pubTitle}>
          <Upload size={14} /> {pubLoading ? `Uploading ${pubProgress}%…` : 'Publish'}
        </button>
        {error && <p style={{ color: 'var(--danger)', marginTop: 8, fontSize: 13 }}>{error}</p>}
      </div>

      {/* Broadcasts */}
      <div className="card" style={{ padding: 24 }}>
        <h2 style={{ marginBottom: 16, fontSize: 16 }}>My Broadcasts</h2>
        {!broadcasts.length && <p style={{ color: 'var(--muted)', fontSize: 13 }}>No broadcasts yet.</p>}
        {broadcasts.map(b => (
          <div className="broadcast-row" key={b.id}>
            {b.thumbnail_url
              ? <img src={b.thumbnail_url} className="broadcast-thumb-sm" alt="" />
              : <div className="broadcast-thumb-sm" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>▶</div>
            }
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 14, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.title}</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 4, alignItems: 'center' }}>
                {statusBadge(b.status)}
                <span style={{ fontSize: 12, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 3 }}>
                  <Eye size={11} /> {b.view_count}
                </span>
              </div>
            </div>
            <button className="btn btn-danger btn-sm" onClick={() => deleteBroadcast(b.id)} title="Delete">
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
