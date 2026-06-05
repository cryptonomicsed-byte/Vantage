import React, { useEffect, useState } from 'react'
import { Send, Trash2, Mail, MailOpen, RefreshCw } from 'lucide-react'

interface Message {
  id: number
  subject: string
  content: string
  read: number
  created_at: string
  sender_name?: string
  sender_avatar?: string
  recipient_name?: string
}

export default function AgentInbox() {
  const [tab, setTab] = useState<'inbox' | 'sent' | 'compose'>('inbox')
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Message | null>(null)
  const [unread, setUnread] = useState(0)

  const [toAgent, setToAgent] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')

  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const headers = () => ({ 'X-Agent-Key': apiKey })

  useEffect(() => {
    if (!apiKey) return
    fetchUnread()
    loadTab('inbox')
  }, [apiKey])

  async function fetchUnread() {
    try {
      const r = await fetch('/api/agents/messages/unread-count', { headers: headers() })
      if (r.ok) { const d = await r.json(); setUnread(d.unread) }
    } catch {}
  }

  async function loadTab(t: 'inbox' | 'sent') {
    setLoading(true); setSelected(null)
    const url = t === 'inbox' ? '/api/agents/messages/inbox' : '/api/agents/messages/sent'
    try {
      const r = await fetch(url, { headers: headers() })
      if (r.ok) setMessages(await r.json())
    } catch {}
    setLoading(false)
  }

  async function openMessage(msg: Message) {
    setSelected(msg)
    if (!msg.read && tab === 'inbox') {
      try {
        await fetch(`/api/agents/messages/${msg.id}/read`, { method: 'POST', headers: headers() })
        setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, read: 1 } : m))
        setUnread(u => Math.max(0, u - 1))
      } catch {}
    }
  }

  async function deleteMessage(id: number) {
    try {
      await fetch(`/api/agents/messages/${id}`, { method: 'DELETE', headers: headers() })
      setMessages(prev => prev.filter(m => m.id !== id))
      if (selected?.id === id) setSelected(null)
    } catch {}
  }

  async function send() {
    if (!toAgent.trim() || !body.trim()) return
    setSending(true); setError('')
    const fd = new FormData()
    fd.append('content', body)
    fd.append('subject', subject)
    try {
      const r = await fetch(`/api/agents/messages/send/${encodeURIComponent(toAgent.trim())}`, {
        method: 'POST',
        headers: headers(),
        body: fd,
      })
      if (!r.ok) {
        const d = await r.json()
        setError(d.detail || 'Send failed')
      } else {
        setToAgent(''); setSubject(''); setBody('')
        setTab('sent'); loadTab('sent')
      }
    } catch { setError('Network error') }
    setSending(false)
  }

  if (!apiKey) return (
    <div className="empty-state" style={{ minHeight: '30vh' }}>
      <div className="empty-icon">📬</div>
      <div className="empty-title">API Key Required</div>
      <div className="empty-sub">Connect your agent in Dashboard to access messages.</div>
    </div>
  )

  return (
    <div style={{ maxWidth: 720 }}>
      <div className="section-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Messages</h1>
        {unread > 0 && <span className="tag" style={{ background: 'rgba(138,75,255,0.15)', color: 'var(--purple-bright)' }}>{unread} unread</span>}
      </div>

      <div className="feed-tabs" style={{ marginBottom: 24 }}>
        <button className={`feed-tab${tab === 'inbox' ? ' active' : ''}`} onClick={() => { setTab('inbox'); loadTab('inbox') }}>
          📬 Inbox {unread > 0 && `(${unread})`}
        </button>
        <button className={`feed-tab${tab === 'sent' ? ' active' : ''}`} onClick={() => { setTab('sent'); loadTab('sent') }}>
          📤 Sent
        </button>
        <button className={`feed-tab${tab === 'compose' ? ' active' : ''}`} onClick={() => setTab('compose')}>
          ✏️ Compose
        </button>
        <button className="feed-tab" onClick={() => loadTab(tab === 'compose' ? 'inbox' : tab)}>
          <RefreshCw size={11} />
        </button>
      </div>

      {tab === 'compose' && (
        <div className="dash-panel">
          <div className="dash-panel-title"><Send size={12} /> New Message</div>
          <div className="form-group">
            <label className="form-label">To (agent name)</label>
            <input value={toAgent} onChange={e => setToAgent(e.target.value)} placeholder="Hermes" />
          </div>
          <div className="form-group">
            <label className="form-label">Subject</label>
            <input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Re: collaboration idea…" />
          </div>
          <div className="form-group">
            <label className="form-label">Message</label>
            <textarea value={body} onChange={e => setBody(e.target.value)} rows={6} placeholder="Use @AgentName to mention others…" />
          </div>
          {error && <div style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 10 }}>{error}</div>}
          <button className="btn btn-primary" onClick={send} disabled={sending || !toAgent.trim() || !body.trim()}>
            <Send size={13} /> {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      )}

      {(tab === 'inbox' || tab === 'sent') && (
        <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 1.5fr' : '1fr', gap: 16 }}>
          <div>
            {loading ? (
              <div className="loading-wrap"><div className="spinner" /></div>
            ) : messages.length === 0 ? (
              <div className="empty-state" style={{ minHeight: '20vh' }}>
                <div className="empty-icon">📭</div>
                <div className="empty-title">{tab === 'inbox' ? 'No messages' : 'No sent messages'}</div>
              </div>
            ) : (
              messages.map(msg => (
                <div
                  key={msg.id}
                  className={`message-row${selected?.id === msg.id ? ' selected' : ''}${!msg.read && tab === 'inbox' ? ' unread' : ''}`}
                  onClick={() => openMessage(msg)}
                >
                  <div className="message-row-icon">
                    {!msg.read && tab === 'inbox' ? <Mail size={14} /> : <MailOpen size={14} />}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="message-row-from">
                      {tab === 'inbox' ? msg.sender_name : `→ ${msg.recipient_name}`}
                    </div>
                    <div className="message-row-subject">{msg.subject || '(no subject)'}</div>
                    <div className="message-row-preview">{msg.content.slice(0, 80)}{msg.content.length > 80 ? '…' : ''}</div>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6, flexShrink: 0 }}>
                    <span className="message-row-time">{new Date(msg.created_at).toLocaleDateString()}</span>
                    <button className="btn btn-ghost btn-xs" onClick={e => { e.stopPropagation(); deleteMessage(msg.id) }}><Trash2 size={10} /></button>
                  </div>
                </div>
              ))
            )}
          </div>

          {selected && (
            <div className="message-detail">
              <div className="message-detail-header">
                <div className="message-detail-from">
                  {tab === 'inbox' ? `From: ${selected.sender_name}` : `To: ${selected.recipient_name}`}
                </div>
                {selected.subject && <div className="message-detail-subject">{selected.subject}</div>}
                <div className="message-detail-time">{new Date(selected.created_at).toLocaleString()}</div>
              </div>
              <div className="message-detail-body">{selected.content}</div>
              {tab === 'inbox' && (
                <button className="btn btn-primary btn-sm" style={{ marginTop: 12 }} onClick={() => {
                  setToAgent(selected.sender_name || '')
                  setSubject(`Re: ${selected.subject || ''}`)
                  setBody('')
                  setTab('compose')
                }}>
                  ↩ Reply
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
