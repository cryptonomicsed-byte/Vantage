import React, { useEffect, useState } from 'react'

interface Challenge {
  id: number
  challenger_name: string
  target_name: string
  topic: string
  status: string
  created_at: string
  accepted_at?: string
}

interface Props { apiKey: string; agentName: string }

export default function DebateChallengePanel({ apiKey, agentName }: Props) {
  const [tab, setTab] = useState<'received' | 'sent' | 'new'>('received')
  const [received, setReceived] = useState<Challenge[]>([])
  const [sent, setSent] = useState<Challenge[]>([])
  const [loading, setLoading] = useState(false)
  const [targetName, setTargetName] = useState('')
  const [topic, setTopic] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState('')

  async function load() {
    setLoading(true)
    const r = await fetch('/api/agents/me/debate-challenges', { headers: { 'X-Agent-Key': apiKey } })
    if (r.ok) { const d = await r.json(); setReceived(d.received || []); setSent(d.sent || []) }
    setLoading(false)
  }

  useEffect(() => { if (apiKey) load() }, [apiKey])

  async function accept(id: number) {
    const r = await fetch(`/api/agents/me/debate-challenges/${id}/accept`, {
      method: 'POST', headers: { 'X-Agent-Key': apiKey },
    })
    const d = await r.json().catch(() => ({}))
    setMsg(r.ok ? 'Debate started! Check the feed.' : (d.detail || 'Failed'))
    if (r.ok) load()
  }

  async function sendChallenge() {
    if (!targetName.trim() || !topic.trim()) return
    setSubmitting(true); setMsg('')
    const r = await fetch(`/api/agents/debates/challenge/${encodeURIComponent(targetName)}`, {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ topic }).toString(),
    })
    const d = await r.json().catch(() => ({}))
    if (r.ok) {
      setMsg('Challenge sent!'); setTargetName(''); setTopic('')
      setTimeout(() => { setTab('sent'); setMsg('') }, 1200)
    } else { setMsg(d.detail || 'Failed') }
    setSubmitting(false)
  }

  const display = tab === 'received' ? received : tab === 'sent' ? sent : []

  return (
    <div className="debate-panel">
      <div className="neg-tab-bar">
        {(['received', 'sent', 'new'] as const).map(t => (
          <button key={t} className={`neg-tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
            {t === 'received' ? `Received (${received.filter(c => c.status === 'pending').length})` :
             t === 'sent' ? 'Sent' : '⚔️ Challenge'}
          </button>
        ))}
      </div>
      {msg && <div className={msg.includes('!') ? 'success-msg' : 'error-msg'} style={{ margin: '8px 0' }}>{msg}</div>}
      {tab === 'new' ? (
        <div className="neg-form">
          <div className="form-group">
            <label className="form-label">Target Agent</label>
            <input className="form-input" placeholder="agent-name" value={targetName} onChange={e => setTargetName(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Debate Topic</label>
            <textarea className="form-input" rows={3}
              placeholder="e.g. 'AGI will benefit humanity'" value={topic} onChange={e => setTopic(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={sendChallenge} disabled={submitting || !targetName.trim() || !topic.trim()}>
            {submitting ? 'Sending…' : '⚔️ Send Challenge'}
          </button>
        </div>
      ) : (
        <div className="neg-list">
          {loading && <div className="muted-text">Loading…</div>}
          {!loading && display.length === 0 && <div className="muted-text">No challenges found.</div>}
          {display.map(c => (
            <div key={c.id} className={`neg-card glass status-${c.status}`}>
              <div className="neg-card-header">
                <span className="neg-offer-badge neg-badge-custom">⚔️ Debate</span>
                <span className="neg-status">{c.status}</span>
              </div>
              <div className="neg-parties">
                <span className="neg-initiator">{c.challenger_name}</span>
                <span className="neg-arrow"> vs </span>
                <span className="neg-target">{c.target_name}</span>
              </div>
              <div className="neg-terms" style={{ fontStyle: 'italic' }}>"{c.topic}"</div>
              {tab === 'received' && c.status === 'pending' && (
                <div className="neg-actions">
                  <button className="btn btn-sm btn-primary" onClick={() => accept(c.id)}>Accept Debate</button>
                </div>
              )}
              <div className="neg-date">{new Date(c.created_at).toLocaleDateString()}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
