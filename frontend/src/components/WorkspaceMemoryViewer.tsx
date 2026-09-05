import React, { useState, useEffect, useCallback } from 'react'

interface MemoryEntry {
  id: string
  guild_id: number
  agent_id: number
  agent_name: string
  key: string
  value: string
  visibility: string
  created_at: string
  updated_at: string
}

interface Props {
  guildSlug: string
}

export default function WorkspaceMemoryViewer({ guildSlug }: Props) {
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

  const [tab, setTab] = useState<'mine' | 'shared'>('mine')
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [shared, setShared] = useState<MemoryEntry[]>([])
  const [loading, setLoading] = useState(true)

  // add form
  const [showAdd, setShowAdd] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [newVis, setNewVis] = useState('agent')
  const [saving, setSaving] = useState(false)

  // edit state
  const [editKey, setEditKey] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [editVis, setEditVis] = useState('agent')
  const [editSaving, setEditSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [mine, sh] = await Promise.all([
        fetch(`/api/guilds/${guildSlug}/memory`, { headers: authHeaders() }),
        fetch(`/api/guilds/${guildSlug}/memory/shared`, { headers: authHeaders() }),
      ])
      if (mine.ok) setEntries((await mine.json()).entries || [])
      if (sh.ok) setShared((await sh.json()).entries || [])
    } finally {
      setLoading(false)
    }
  }, [guildSlug, authHeaders])

  useEffect(() => { load() }, [load])

  if (!apiKey) {
    return (
      <div className="memory-viewer">
        <div style={{ color: 'var(--muted)', fontSize: 13, padding: '20px 0' }}>
          Memory is only accessible to agents using an API key.
        </div>
      </div>
    )
  }

  async function addEntry() {
    if (!newKey.trim() || !newValue.trim()) return
    setSaving(true)
    await fetch(`/api/guilds/${guildSlug}/memory/${encodeURIComponent(newKey)}`, {
      method: 'PUT',
      headers: formHeaders(),
      body: new URLSearchParams({ value: newValue, visibility: newVis }).toString(),
    })
    setSaving(false)
    setShowAdd(false); setNewKey(''); setNewValue(''); setNewVis('agent')
    load()
  }

  async function deleteEntry(key: string) {
    await fetch(`/api/guilds/${guildSlug}/memory/${encodeURIComponent(key)}`, {
      method: 'DELETE', headers: authHeaders(),
    })
    load()
  }

  async function saveEdit(key: string) {
    setEditSaving(true)
    await fetch(`/api/guilds/${guildSlug}/memory/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: formHeaders(),
      body: new URLSearchParams({ value: editValue, visibility: editVis }).toString(),
    })
    setEditSaving(false)
    setEditKey(null)
    load()
  }

  const visBadgeStyle = (v: string) => ({
    fontSize: 9, padding: '1px 5px', borderRadius: 3, fontWeight: 600,
    background: v === 'public' ? 'rgba(60,200,120,0.15)' : v === 'guild' ? 'rgba(138,75,255,0.15)' : 'rgba(255,255,255,0.08)',
    color: v === 'public' ? '#3cc878' : v === 'guild' ? '#a78bfa' : 'var(--muted)',
  })

  const truncate = (s: string, n = 150) => s.length > n ? s.slice(0, n) + '…' : s

  const tabData = tab === 'mine' ? entries : shared

  return (
    <div className="memory-viewer">
      {/* tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
        {(['mine', 'shared'] as const).map(t => (
          <button
            key={t}
            className={`btn btn-sm${tab === t ? ' btn-primary' : ''}`}
            onClick={() => setTab(t)}
            style={{ fontSize: 12, textTransform: 'capitalize' }}
          >
            {t === 'mine' ? 'Mine' : 'Shared'} ({t === 'mine' ? entries.length : shared.length})
          </button>
        ))}
        {tab === 'mine' && (
          <button className="btn btn-sm" style={{ marginLeft: 'auto', fontSize: 11 }} onClick={() => setShowAdd(s => !s)}>
            ＋ Add
          </button>
        )}
      </div>

      {/* add form */}
      {showAdd && tab === 'mine' && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: 12, marginBottom: 12 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <input
              placeholder="Key *"
              value={newKey}
              onChange={e => setNewKey(e.target.value)}
              style={{ flex: 1, padding: '6px 10px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 13 }}
            />
            <select value={newVis} onChange={e => setNewVis(e.target.value)} style={{ padding: '6px 8px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)' }}>
              <option value="agent">Private</option>
              <option value="guild">Guild</option>
              <option value="public">Public</option>
            </select>
          </div>
          <textarea
            placeholder="Value (JSON string) *"
            value={newValue}
            onChange={e => setNewValue(e.target.value)}
            rows={3}
            style={{ width: '100%', padding: '6px 10px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', resize: 'vertical', boxSizing: 'border-box', marginBottom: 8 }}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-sm btn-primary" disabled={saving || !newKey.trim() || !newValue.trim()} onClick={addEntry}>{saving ? 'Saving…' : 'Save'}</button>
            <button className="btn btn-sm" onClick={() => setShowAdd(false)}>Cancel</button>
          </div>
        </div>
      )}

      {loading && <div style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</div>}

      {!loading && tabData.length === 0 && (
        <div style={{ color: 'var(--muted)', fontSize: 13 }}>No entries.</div>
      )}

      {tabData.map(entry => (
        <div key={entry.id} className="memory-entry">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ fontWeight: 700, fontSize: 13, color: 'var(--cyan)' }}>{entry.key}</span>
            <span style={visBadgeStyle(entry.visibility)}>{entry.visibility}</span>
            {tab === 'shared' && (
              <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>{entry.agent_name}</span>
            )}
            {tab === 'mine' && (
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
                <button className="btn btn-ghost btn-xs" style={{ fontSize: 10 }} onClick={() => {
                  setEditKey(entry.key); setEditValue(entry.value); setEditVis(entry.visibility)
                }}>Edit</button>
                <button className="btn btn-ghost btn-xs" style={{ fontSize: 10, color: '#ef4444' }} onClick={() => deleteEntry(entry.key)}>Del</button>
              </div>
            )}
          </div>

          {editKey === entry.key && tab === 'mine' ? (
            <div>
              <textarea
                value={editValue}
                onChange={e => setEditValue(e.target.value)}
                rows={3}
                style={{ width: '100%', padding: '6px 10px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', resize: 'vertical', boxSizing: 'border-box', marginBottom: 6, fontSize: 12 }}
              />
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <select value={editVis} onChange={e => setEditVis(e.target.value)} style={{ padding: '4px 8px', background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)' }}>
                  <option value="agent">Private</option>
                  <option value="guild">Guild</option>
                  <option value="public">Public</option>
                </select>
                <button className="btn btn-sm btn-primary" disabled={editSaving} onClick={() => saveEdit(entry.key)}>{editSaving ? '…' : 'Save'}</button>
                <button className="btn btn-sm" onClick={() => setEditKey(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <code style={{ fontSize: 11, color: 'var(--muted-hi)', display: 'block', background: 'rgba(0,0,0,0.3)', borderRadius: 4, padding: '4px 8px', wordBreak: 'break-all' }}>
              {truncate(entry.value)}
            </code>
          )}
        </div>
      ))}
    </div>
  )
}
