import React, { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ListMusic, Play, Trash2, Loader, Plus, Film, Music, Tv, ChevronLeft } from 'lucide-react'

// Cross-surface playlist/queue -- "easy pickup" for anything stored:
// Cinema titles, Audio tracks (real broadcast rows), and Live TV channels
// or anything else with no broadcast row (external items). Same backend
// (/api/playlists) an agent can call directly -- this is the human view.

interface PlaylistSummary { id: number; name: string; item_count: number; created_at: string }
interface PlaylistItem {
  id: number
  kind: 'broadcast' | 'external'
  broadcast_id?: number
  title: string
  url?: string
  thumbnail?: string
  content_type?: string
  surface?: string
  external_kind?: string
  duration_sec?: number
  agent_name?: string
}

const KEY = () => localStorage.getItem('vantage_api_key') || ''

function kindIcon(item: PlaylistItem) {
  if (item.kind === 'external') return <Tv size={14} />
  if (item.surface === 'cinema') return <Film size={14} />
  return <Music size={14} />
}

function playHref(item: PlaylistItem): string {
  if (item.kind === 'broadcast') {
    return item.surface === 'cinema' ? '/cinema' : '/audio'
  }
  return '/video' // Live TV channels live under Studio too
}

function PlaylistList({ onOpen }: { onOpen: (id: number) => void }) {
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [newName, setNewName] = useState('')

  function load() {
    setLoading(true)
    fetch('/api/playlists', { headers: { 'X-Agent-Key': KEY() } })
      .then(r => r.ok ? r.json() : [])
      .then(setPlaylists)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function create() {
    if (!newName.trim()) return
    await fetch('/api/playlists', {
      method: 'POST', headers: { 'X-Agent-Key': KEY(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName.trim() }),
    })
    setNewName('')
    load()
  }

  async function remove(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    if (!confirm('Delete this playlist?')) return
    await fetch(`/api/playlists/${id}`, { method: 'DELETE', headers: { 'X-Agent-Key': KEY() } })
    load()
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 }}>
        <ListMusic size={24} />
        <div>
          <h1 style={{ fontSize: 26, fontWeight: 800, margin: 0 }}>Playlists</h1>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>Your saved queue — Cinema, Audio, Live TV, all in one place.</div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
        <input
          placeholder="New playlist name…"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && create()}
          style={{ flex: 1, maxWidth: 320, padding: '10px 14px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--muted-hi)', fontSize: 14 }}
        />
        <button className="btn btn-primary" disabled={!newName.trim()} onClick={create}><Plus size={14} /> Create</button>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: 'center' }}><Loader size={20} className="spin" /></div>
      ) : playlists.length === 0 ? (
        <div className="empty-state" style={{ minHeight: 160 }}>
          <ListMusic size={32} style={{ opacity: 0.4, marginBottom: 10 }} />
          <div className="empty-title">No playlists yet</div>
          <div className="empty-sub">Create one above, or use the “+” button on any track, title, or channel.</div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 14 }}>
          {playlists.map(p => (
            <div
              key={p.id}
              className="glass"
              onClick={() => onOpen(p.id)}
              style={{ padding: 16, borderRadius: 12, cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 8 }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ fontSize: 15, fontWeight: 700 }}>{p.name}</div>
                <button onClick={e => remove(p.id, e)} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}><Trash2 size={13} /></button>
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>{p.item_count} item{p.item_count === 1 ? '' : 's'}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PlaylistDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const [name, setName] = useState('')
  const [items, setItems] = useState<PlaylistItem[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  function load() {
    setLoading(true)
    fetch(`/api/playlists/${id}`, { headers: { 'X-Agent-Key': KEY() } })
      .then(r => r.json())
      .then(d => { setName(d.name || ''); setItems(d.items || []) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [id])

  async function removeItem(itemId: number) {
    await fetch(`/api/playlists/${id}/items/${itemId}`, { method: 'DELETE', headers: { 'X-Agent-Key': KEY() } })
    load()
  }

  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={onBack} style={{ marginBottom: 16 }}><ChevronLeft size={14} /> All playlists</button>
      <h1 style={{ fontSize: 24, fontWeight: 800, marginBottom: 4 }}>{name}</h1>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 20 }}>{items.length} item{items.length === 1 ? '' : 's'}</div>

      {loading ? (
        <div style={{ padding: 40, textAlign: 'center' }}><Loader size={20} className="spin" /></div>
      ) : items.length === 0 ? (
        <div className="empty-state" style={{ minHeight: 140 }}>
          <div className="empty-title">Empty</div>
          <div className="empty-sub">Add items from Cinema, Audio, or Live TV using the “+” button.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {items.map(item => (
            <div key={item.id} className="glass" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 10 }}>
              <div style={{ width: 40, height: 40, borderRadius: 6, overflow: 'hidden', flexShrink: 0, background: 'rgba(255,255,255,0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {item.thumbnail ? <img src={item.thumbnail} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : kindIcon(item)}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{item.agent_name || item.external_kind || item.kind}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => navigate(playHref(item))}><Play size={13} /></button>
              <button onClick={() => removeItem(item.id)} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Playlists() {
  const params = useParams()
  const navigate = useNavigate()
  const openId = params.id ? Number(params.id) : null

  return openId != null
    ? <PlaylistDetail id={openId} onBack={() => navigate('/playlists')} />
    : <PlaylistList onOpen={id => navigate(`/playlists/${id}`)} />
}
