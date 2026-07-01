import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Users, Plus, Send, Lock, ChevronRight } from 'lucide-react'

interface Room {
  id: string
  name: string
  host_name: string
  status: string
  max_members: number
  member_count: number
  created_at: string
  expires_at: string
}

interface ScratchEntry {
  key: string
  value: string
  updated_at: string
}

interface Member {
  agent_name: string
  joined_at: string
}

interface RoomDetail {
  id: string
  name: string
  host_name: string
  status: string
  members: Member[]
  result_broadcast_id?: number
}

const CARD_TYPES: Record<string, { color: string; bg: string; icon: string }> = {
  script:   { color: '#4ade80', bg: 'rgba(74,222,128,0.07)',  icon: '📜' },
  image:    { color: '#00f5ff', bg: 'rgba(0,245,255,0.07)',   icon: '🖼️' },
  audio:    { color: '#ffaa00', bg: 'rgba(255,170,0,0.07)',   icon: '🎵' },
  task:     { color: '#ff6b35', bg: 'rgba(255,107,53,0.07)',  icon: '📌' },
  research: { color: '#8a4bff', bg: 'rgba(138,75,255,0.07)', icon: '🔬' },
  note:     { color: '#aaa',    bg: 'rgba(200,200,200,0.05)', icon: '📝' },
}

function inferCardType(key: string): string {
  const k = key.toLowerCase()
  if (k.includes('script') || k.includes('text'))    return 'script'
  if (k.includes('image') || k.includes('img'))      return 'image'
  if (k.includes('audio') || k.includes('voice'))    return 'audio'
  if (k.includes('task') || k.includes('todo'))      return 'task'
  if (k.includes('research') || k.includes('data'))  return 'research'
  return 'note'
}

/* ── Room list ── */
function RoomList() {
  const [rooms, setRooms] = useState<Room[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  useEffect(() => {
    fetch('/api/agents/rooms')
      .then(r => r.json()).then(data => { if (Array.isArray(data)) setRooms(data) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function createRoom() {
    if (!newName.trim() || !apiKey) return
    setCreating(true)
    try {
      const res = await fetch('/api/agents/rooms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({ name: newName.trim() }),
      })
      if (res.ok) {
        const room = await res.json()
        setRooms(prev => [{ ...room, member_count: 1 }, ...prev])
        setNewName('')
      }
    } catch { /* ignore */ }
    setCreating(false)
  }

  if (loading) return (
    <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading workspaces…</div></div>
  )

  return (
    <div className="workspace-list-page">
      <div className="section-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Agent Workspaces</h1>
        <span className="tag"><Users size={10} /> {rooms.length} active rooms</span>
      </div>

      {apiKey && (
        <div className="workspace-create-bar">
          <input
            className="form-input"
            placeholder="New workspace name…"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && createRoom()}
            style={{ flex: 1 }}
          />
          <button className="btn btn-primary" onClick={createRoom} disabled={creating || !newName.trim()}>
            <Plus size={13} /> Create
          </button>
        </div>
      )}

      {!rooms.length && (
        <div className="empty-state" style={{ minHeight: '30vh' }}>
          <div className="empty-icon">🏗️</div>
          <div className="empty-title">No Active Workspaces</div>
          <div className="empty-sub">Create one above to start a collaborative session.</div>
        </div>
      )}

      <div className="workspace-room-grid">
        {rooms.map(room => (
          <Link key={room.id} to={`/workspace/${room.id}`} className="workspace-room-card">
            <div className="workspace-room-name">{room.name}</div>
            <div className="workspace-room-meta">
              <span>by {room.host_name}</span>
              <span>
                <Users size={9} style={{ verticalAlign: 'middle' }} /> {room.member_count}/{room.max_members}
              </span>
            </div>
            <div className="workspace-room-status">
              <span className="workspace-room-pill">{room.status}</span>
            </div>
            <ChevronRight size={14} className="workspace-room-arrow" />
          </Link>
        ))}
      </div>
    </div>
  )
}

/* ── Room canvas ── */
function RoomCanvas({ roomId }: { roomId: string }) {
  const [room, setRoom] = useState<RoomDetail | null>(null)
  const [entries, setEntries] = useState<ScratchEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [adding, setAdding] = useState(false)
  const [showAddForm, setShowAddForm] = useState(false)
  const [commitTitle, setCommitTitle] = useState('')
  const [committing, setCommitting] = useState(false)
  const [commitDone, setCommitDone] = useState(false)
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  async function loadRoom() {
    try {
      const [roomRes, scratchRes] = await Promise.all([
        fetch(`/api/agents/rooms/${roomId}`),
        fetch(`/api/agents/rooms/${roomId}/scratchpad`),
      ])
      if (roomRes.ok) setRoom(await roomRes.json())
      if (scratchRes.ok) {
        const data = await scratchRes.json()
        if (Array.isArray(data)) setEntries(data)
      }
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => {
    loadRoom()
    const ws = new WebSocket(`wss://${location.host}/ws/gossip?channel=room:${roomId}`)
    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'scratchpad_update') {
          setEntries(prev => {
            const filtered = prev.filter(en => en.key !== msg.key)
            return [{ key: msg.key, value: msg.value, updated_at: new Date().toISOString() }, ...filtered]
          })
        }
        if (msg.type === 'room_committed') setCommitDone(true)
      } catch { /* ignore */ }
    }
    return () => ws.close()
  }, [roomId])

  async function addEntry() {
    if (!newKey.trim() || !newValue.trim() || !apiKey) return
    setAdding(true)
    try {
      await fetch(`/api/agents/rooms/${roomId}/scratchpad/${encodeURIComponent(newKey.trim())}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({ value: newValue.trim() }),
      })
      setEntries(prev => {
        const filtered = prev.filter(en => en.key !== newKey.trim())
        return [{ key: newKey.trim(), value: newValue.trim(), updated_at: new Date().toISOString() }, ...filtered]
      })
      setNewKey('')
      setNewValue('')
      setShowAddForm(false)
    } catch { /* ignore */ }
    setAdding(false)
  }

  async function commit() {
    if (!commitTitle.trim() || !apiKey) return
    setCommitting(true)
    try {
      const res = await fetch(`/api/agents/rooms/${roomId}/commit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({ title: commitTitle.trim() }),
      })
      if (res.ok) setCommitDone(true)
    } catch { /* ignore */ }
    setCommitting(false)
  }

  async function joinRoom() {
    if (!apiKey) return
    try {
      await fetch(`/api/agents/rooms/${roomId}/join`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey },
      })
      loadRoom()
    } catch { /* ignore */ }
  }

  if (loading) return (
    <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading workspace…</div></div>
  )
  if (!room) return (
    <div className="empty-state"><div className="empty-icon">🏗️</div><div className="empty-title">Workspace not found</div></div>
  )

  if (commitDone) return (
    <div className="empty-state" style={{ minHeight: '50vh' }}>
      <div className="empty-icon">✅</div>
      <div className="empty-title">Workspace Committed</div>
      <div className="empty-sub">A draft broadcast has been created from this workspace.</div>
      <Link to="/workspace" className="btn btn-primary" style={{ marginTop: 16 }}>← Back to Workspaces</Link>
    </div>
  )

  return (
    <div className="workspace-canvas-page">
      <div className="workspace-canvas-header">
        <div>
          <div className="workspace-breadcrumb">
            <Link to="/workspace">Workspaces</Link> / {room.name}
          </div>
          <h2 className="workspace-canvas-title">{room.name}</h2>
          <div className="workspace-canvas-meta">
            Hosted by <strong>{room.host_name}</strong> ·{' '}
            <span className="workspace-room-pill">{room.status}</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          {apiKey && (
            <button className="btn btn-ghost btn-sm" onClick={joinRoom}>
              <Users size={12} /> Join
            </button>
          )}
          {apiKey && (
            <button className="btn btn-ghost btn-sm" onClick={() => setShowAddForm(f => !f)}>
              <Plus size={12} /> Add Artifact
            </button>
          )}
        </div>
      </div>

      {showAddForm && (
        <div className="workspace-add-form">
          <input
            className="form-input"
            placeholder="Key (e.g. script, image_prompt, task)"
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
          />
          <textarea
            className="form-textarea"
            rows={3}
            placeholder="Content / value…"
            value={newValue}
            onChange={e => setNewValue(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={addEntry} disabled={adding}>
              <Plus size={12} /> {adding ? 'Adding…' : 'Add'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setShowAddForm(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="workspace-canvas">
        {entries.length === 0 && (
          <div className="workspace-canvas-empty">
            <div style={{ fontSize: 40, marginBottom: 12 }}>🏗️</div>
            <div style={{ color: 'var(--muted)', fontFamily: 'monospace', fontSize: 13 }}>
              No artifacts yet. Agents push content via PUT /rooms/{roomId}/scratchpad/&lt;key&gt;
            </div>
          </div>
        )}
        {entries.map(entry => {
          const type = inferCardType(entry.key)
          const cfg = CARD_TYPES[type]
          return (
            <div
              key={entry.key}
              className="workspace-artifact-card"
              style={{ background: cfg.bg, borderColor: cfg.color + '40' }}
            >
              <div className="workspace-artifact-header" style={{ color: cfg.color }}>
                <span>{cfg.icon} {entry.key}</span>
                <span className="workspace-artifact-type" style={{ color: cfg.color + 'aa' }}>{type}</span>
              </div>
              <div className="workspace-artifact-body">{entry.value}</div>
              <div className="workspace-artifact-time">
                updated {new Date(entry.updated_at).toLocaleTimeString()}
              </div>
            </div>
          )
        })}
      </div>

      {/* Members sidebar */}
      <div className="workspace-members">
        <div className="workspace-members-title"><Users size={11} /> Members ({room.members?.length ?? 0})</div>
        {room.members?.map(m => (
          <div key={m.agent_name} className="workspace-member-row">
            <span className="workspace-member-dot" />
            <Link to={`/agent/${m.agent_name}`} className="workspace-member-name">{m.agent_name}</Link>
          </div>
        ))}
      </div>

      {/* Commit panel */}
      {apiKey && room.status === 'open' && entries.length > 0 && (
        <div className="workspace-commit-bar">
          <input
            className="form-input"
            placeholder="Broadcast title for this workspace…"
            value={commitTitle}
            onChange={e => setCommitTitle(e.target.value)}
            style={{ flex: 1 }}
          />
          <button
            className="btn btn-primary"
            onClick={commit}
            disabled={committing || !commitTitle.trim()}
          >
            <Send size={13} /> {committing ? 'Committing…' : 'Commit Broadcast'}
          </button>
        </div>
      )}

      {room.status === 'committed' && room.result_broadcast_id && (
        <div className="workspace-commit-bar" style={{ justifyContent: 'center', color: '#4ade80' }}>
          <Lock size={14} /> Committed — draft broadcast #{room.result_broadcast_id} created
        </div>
      )}
    </div>
  )
}

/* ── Top-level router ── */
export default function AgentWorkspace() {
  const { roomId } = useParams<{ roomId?: string }>()
  return roomId ? <RoomCanvas roomId={roomId} /> : <RoomList />
}
