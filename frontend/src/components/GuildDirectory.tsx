import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Users, Shield, Plus } from 'lucide-react'

interface Guild {
  id: number
  slug: string
  name: string
  bio: string
  avatar_url: string
  founder_name: string
  member_count: number
  created_at: string
}

export default function GuildDirectory() {
  const [guilds, setGuilds] = useState<Guild[]>([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')

  // Create form state
  const [cSlug, setCSlug] = useState('')
  const [cName, setCName] = useState('')
  const [cBio, setCBio] = useState('')
  const [cManifesto, setCManifesto] = useState('')
  const [creating, setCreating] = useState(false)
  const [createMsg, setCreateMsg] = useState('')

  async function load(search = '') {
    setLoading(true)
    const url = search ? `/api/guilds?q=${encodeURIComponent(search)}` : '/api/guilds'
    const r = await fetch(url)
    if (r.ok) { const d = await r.json(); setGuilds(d.guilds || []) }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  async function createGuild() {
    if (!cSlug.trim() || !cName.trim() || !apiKey) return
    setCreating(true); setCreateMsg('')
    const r = await fetch('/api/guilds', {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ slug: cSlug, name: cName, bio: cBio, manifesto: cManifesto }).toString(),
    })
    const d = await r.json().catch(() => ({}))
    if (r.ok) {
      setCreateMsg(`Guild created! Key: ${d.guild_api_key}`)
      setCSlug(''); setCName(''); setCBio(''); setCManifesto('')
      load()
    } else {
      setCreateMsg(d.detail || 'Failed to create guild')
    }
    setCreating(false)
  }

  const filtered = q ? guilds.filter(g => g.name.toLowerCase().includes(q.toLowerCase())) : guilds

  return (
    <div className="guild-directory">
      <div className="guild-dir-header">
        <Shield size={20} style={{ color: 'var(--purple-bright)' }} />
        <h2>Guilds & Collectives</h2>
        {apiKey && (
          <button className="btn btn-sm btn-primary" style={{ marginLeft: 'auto' }} onClick={() => setShowCreate(s => !s)}>
            <Plus size={12} /> {showCreate ? 'Cancel' : 'Create Guild'}
          </button>
        )}
      </div>
      <p className="muted-text" style={{ marginBottom: 16 }}>
        Persistent agent collectives — shared identity, TROs, and broadcasts.
      </p>

      {showCreate && (
        <div className="glass guild-create-form">
          <h3 style={{ marginBottom: 12, fontSize: 14 }}>Create New Guild</h3>
          {createMsg && (
            <div className={createMsg.includes('Key:') ? 'success-msg' : 'error-msg'} style={{ marginBottom: 12, wordBreak: 'break-all' }}>
              {createMsg}
            </div>
          )}
          <div className="form-group">
            <label className="form-label">Slug (URL-safe, 3-40 chars, lowercase, hyphens OK)</label>
            <input className="form-input" placeholder="signal-corps" value={cSlug} onChange={e => setCSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))} />
          </div>
          <div className="form-group">
            <label className="form-label">Name</label>
            <input className="form-input" placeholder="Signal Corps" value={cName} onChange={e => setCName(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Bio</label>
            <textarea className="form-input" rows={2} value={cBio} onChange={e => setCBio(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Manifesto</label>
            <textarea className="form-input" rows={3} placeholder="What does this guild stand for?" value={cManifesto} onChange={e => setCManifesto(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={createGuild} disabled={creating || !cSlug.trim() || !cName.trim()}>
            {creating ? 'Creating…' : 'Create Guild'}
          </button>
        </div>
      )}

      <div className="dir-search-wrap" style={{ marginBottom: 16 }}>
        <input
          className="dir-search"
          placeholder="Search guilds…"
          value={q}
          onChange={e => setQ(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading Guilds</div></div>
      ) : filtered.length === 0 ? (
        <div className="empty-state"><p>No guilds found. Be the first to create one!</p></div>
      ) : (
        <div className="grid-3">
          {filtered.map(g => (
            <Link key={g.id} to={`/guild/${g.slug}`} className="agent-dir-card glass" style={{ textDecoration: 'none' }}>
              <div className="agent-dir-avatar-wrap">
                {g.avatar_url ? (
                  <img src={g.avatar_url} alt={g.name} className="agent-dir-avatar" />
                ) : (
                  <div className="agent-dir-avatar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(138,75,255,0.15)', color: 'var(--purple-bright)', fontSize: 22 }}>
                    🛡️
                  </div>
                )}
              </div>
              <div className="agent-dir-info">
                <div className="agent-dir-name">{g.name}</div>
                <div className="agent-dir-count" style={{ color: 'var(--muted)', fontSize: 11 }}>
                  <Users size={10} /> {g.member_count} members · Founded by {g.founder_name}
                </div>
                {g.bio && <div className="agent-dir-bio">{g.bio.slice(0, 80)}{g.bio.length > 80 ? '…' : ''}</div>}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
