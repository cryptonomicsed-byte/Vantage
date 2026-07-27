import React, { useEffect, useRef, useState } from 'react'
import { ListPlus, Plus, Check, Loader } from 'lucide-react'

// Reusable "+" control that adds any item (a real broadcast, or an
// external item like a Live TV channel) into one of the agent's
// playlists -- backs the "easy pickup" queue across Cinema, Audio, and
// Live TV. Same backend contract an agent can call directly via
// POST /api/playlists/{id}/items -- this is just the human-facing button.

interface PlaylistSummary { id: number; name: string; item_count: number }

interface AddToPlaylistItem {
  broadcast_id?: number
  external_title?: string
  external_url?: string
  external_thumbnail?: string
  external_kind?: string
}

const KEY = () => localStorage.getItem('vantage_api_key') || ''

export default function AddToPlaylistButton({ item, size = 14 }: { item: AddToPlaylistItem; size?: number }) {
  const [open, setOpen] = useState(false)
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [added, setAdded] = useState(false)
  const [newName, setNewName] = useState('')
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  function load() {
    setLoading(true)
    fetch('/api/playlists', { headers: { 'X-Agent-Key': KEY() } })
      .then(r => r.ok ? r.json() : [])
      .then(setPlaylists)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  function toggle(e: React.MouseEvent) {
    e.stopPropagation()
    if (!open) load()
    setOpen(o => !o)
    setAdded(false)
  }

  async function addTo(playlistId: number, e: React.MouseEvent) {
    e.stopPropagation()
    await fetch(`/api/playlists/${playlistId}/items`, {
      method: 'POST',
      headers: { 'X-Agent-Key': KEY(), 'Content-Type': 'application/json' },
      body: JSON.stringify(item),
    })
    setAdded(true)
    setTimeout(() => setOpen(false), 700)
  }

  async function createAndAdd(e: React.MouseEvent) {
    e.stopPropagation()
    if (!newName.trim()) return
    const r = await fetch('/api/playlists', {
      method: 'POST',
      headers: { 'X-Agent-Key': KEY(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName.trim() }),
    })
    const d = await r.json()
    setNewName('')
    if (d.id) await addTo(d.id, e)
  }

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }} onClick={e => e.stopPropagation()}>
      <button
        onClick={toggle}
        title="Add to playlist"
        style={{ background: 'rgba(0,0,0,0.5)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 6, padding: 5, cursor: 'pointer', color: '#fff', display: 'flex' }}
      >
        {added ? <Check size={size} color="#4ade80" /> : <ListPlus size={size} />}
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 6, zIndex: 50, minWidth: 200,
          background: '#0f0f1a', border: '1px solid var(--border)', borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.5)', padding: 8,
        }}>
          {loading ? (
            <div style={{ padding: 10, textAlign: 'center' }}><Loader size={14} className="spin" /></div>
          ) : (
            <>
              {playlists.length === 0 && <div style={{ fontSize: 11, color: 'var(--muted)', padding: '4px 8px' }}>No playlists yet</div>}
              {playlists.map(p => (
                <div
                  key={p.id}
                  onClick={e => addTo(p.id, e)}
                  style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 13 }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.06)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <span>{p.name}</span>
                  <span style={{ color: 'var(--muted)', fontSize: 11 }}>{p.item_count}</span>
                </div>
              ))}
              <div style={{ display: 'flex', gap: 6, marginTop: 6, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                <input
                  placeholder="New playlist…"
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  onClick={e => e.stopPropagation()}
                  style={{ flex: 1, padding: '5px 8px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: '#fff', fontSize: 12 }}
                />
                <button onClick={createAndAdd} style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 8px', color: '#fff', cursor: 'pointer' }}>
                  <Plus size={12} />
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
