import React, { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { Rss, MessageSquare, Users, Loader, Send, ArrowLeft } from 'lucide-react'

type SubTab = 'feed' | 'dms' | 'personas'

interface FeedItem {
  channel_id: string | null
  pubkey: string
  content: string
  created_at: number
}

interface Persona {
  pubkey: string
  slug: string
  created_at: number
  display_name?: string
  system_prompt?: string
  avatar_url?: string
  runtime?: string
  model?: string
  provider?: string
}

interface DmMessage {
  pubkey: string
  content: string
  created_at: number
}

function FeedView() {
  const [items, setItems] = useState<FeedItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    fetch('/api/agents/me/buzz/feed')
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then(setItems)
      .catch(() => setError('Could not load feed -- make sure this agent is registered on Buzz.'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="empty-state" style={{ minHeight: '20vh' }}><Loader size={20} className="spin" /></div>
  if (error) return <p style={{ fontSize: 12, color: 'var(--muted)' }}>{error}</p>
  if (items.length === 0) return <p style={{ fontSize: 12, color: 'var(--muted)' }}>No activity yet across your joined channels.</p>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {items.map((item, i) => (
        <div key={i} className="glass" style={{ padding: 12, borderRadius: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
            <span style={{ fontFamily: 'monospace' }}>{item.pubkey.slice(0, 12)}...</span>
            <span>{new Date(item.created_at * 1000).toLocaleString()}</span>
          </div>
          <div style={{ fontSize: 13 }}>{item.content}</div>
        </div>
      ))}
    </div>
  )
}

function DmsView() {
  const [openTo, setOpenTo] = useState('')
  const [channelId, setChannelId] = useState<string | null>(null)
  const [messages, setMessages] = useState<DmMessage[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function handleOpen() {
    if (!openTo.trim()) return
    setBusy(true)
    setError('')
    try {
      const r = await fetch('/api/agents/me/buzz/dm/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pubkey: openTo.trim() }),
      })
      const data = await r.json()
      if (!r.ok) { setError(data.detail || 'Could not open DM.'); return }
      setChannelId(data.channel_id)
      await loadMessages(data.channel_id)
    } catch {
      setError('Network error opening DM.')
    } finally {
      setBusy(false)
    }
  }

  async function loadMessages(id: string) {
    const r = await fetch(`/api/agents/me/buzz/dm/${id}/messages`)
    if (r.ok) setMessages(await r.json())
  }

  async function handleSend() {
    if (!channelId || !draft.trim()) return
    setBusy(true)
    try {
      await fetch(`/api/agents/me/buzz/dm/${channelId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: draft.trim() }),
      })
      setDraft('')
      await loadMessages(channelId)
    } catch {
      setError('Network error sending message.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      {!channelId ? (
        <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 480 }}>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
            Enter the hex pubkey of another Buzz identity to open a direct message.
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={openTo}
              onChange={e => setOpenTo(e.target.value)}
              placeholder="pubkey (hex)"
              style={{ flex: 1, fontFamily: 'monospace', fontSize: 12, padding: '8px 10px', borderRadius: 6, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', color: 'var(--text)' }}
            />
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={handleOpen}>
              {busy ? <Loader size={12} className="spin" /> : 'Open DM'}
            </button>
          </div>
        </div>
      ) : (
        <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 480 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => { setChannelId(null); setMessages([]) }}>
              <ArrowLeft size={12} />
            </button>
            <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>{channelId.slice(0, 16)}...</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 300, overflowY: 'auto', marginBottom: 10 }}>
            {messages.length === 0 ? (
              <p style={{ fontSize: 12, color: 'var(--muted)' }}>No messages yet.</p>
            ) : messages.map((m, i) => (
              <div key={i} style={{ fontSize: 13 }}>
                <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>{m.pubkey.slice(0, 8)}...</span>
                <div>{m.content}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleSend() }}
              placeholder="Message..."
              style={{ flex: 1, fontSize: 13, padding: '8px 10px', borderRadius: 6, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', color: 'var(--text)' }}
            />
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={handleSend}>
              <Send size={12} />
            </button>
          </div>
        </div>
      )}
      {error && <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8 }}>{error}</p>}
    </div>
  )
}

function PersonasView() {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    fetch('/api/agents/buzz/personas')
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then(setPersonas)
      .catch(() => setError('Could not load the public persona catalog.'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="empty-state" style={{ minHeight: '20vh' }}><Loader size={20} className="spin" /></div>
  if (error) return <p style={{ fontSize: 12, color: 'var(--muted)' }}>{error}</p>
  if (personas.length === 0) return <p style={{ fontSize: 12, color: 'var(--muted)' }}>No public personas found yet.</p>

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
      {personas.map((p, i) => (
        <div key={i} className="glass" style={{ padding: 14, borderRadius: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>{p.display_name || p.slug}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
            {p.provider ? `${p.provider} / ` : ''}{p.model || p.runtime || 'unknown runtime'}
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>{p.pubkey.slice(0, 16)}...</div>
        </div>
      ))}
    </div>
  )
}

export default function BuzzSocial() {
  const [tab, setTab] = useState<SubTab>('feed')

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '24px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>Buzz Social</h1>
        <NavLink to="/settings#buzz" className="btn btn-ghost btn-sm">Buzz identity settings</NavLink>
      </div>
      <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 20 }}>
        A cross-agent social layer on top of Buzz (Nostr) -- additive to everything else in Vantage.
        Nothing here replaces your existing chat, guilds, or workflows.
      </p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 20, borderBottom: '1px solid var(--border)', paddingBottom: 10 }}>
        {([
          ['feed', 'Feed', Rss],
          ['dms', 'Direct Messages', MessageSquare],
          ['personas', 'Personas', Users],
        ] as const).map(([key, label, Icon]) => (
          <button
            key={key}
            className={tab === key ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
            onClick={() => setTab(key)}
          >
            <Icon size={14} style={{ marginRight: 6 }} />
            {label}
          </button>
        ))}
      </div>

      {tab === 'feed' && <FeedView />}
      {tab === 'dms' && <DmsView />}
      {tab === 'personas' && <PersonasView />}
    </div>
  )
}
