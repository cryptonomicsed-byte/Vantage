import React, { useEffect, useState } from 'react'

interface Handshake {
  id: number
  initiator_name: string
  recipient_name: string
  terms_json: string
  status: string
  created_at: string
}

interface Props { apiKey: string; agentName: string }

export default function HandshakePanel({ apiKey, agentName }: Props) {
  const [tab, setTab] = useState<'pending' | 'history' | 'new'>('pending')
  const [handshakes, setHandshakes] = useState<Handshake[]>([])
  const [loading, setLoading] = useState(false)
  const [recipientName, setRecipientName] = useState('')
  const [terms, setTerms] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState('')

  async function load() {
    setLoading(true)
    const r = await fetch('/api/agents/me/handshakes', { headers: { 'X-Agent-Key': apiKey } })
    if (r.ok) { const d = await r.json(); setHandshakes(d.handshakes || d || []) }
    setLoading(false)
  }

  useEffect(() => { if (apiKey) load() }, [apiKey])

  async function respond(id: number, action: 'accept' | 'reject') {
    await fetch(`/api/agents/me/handshakes/${id}/${action}`, {
      method: 'POST', headers: { 'X-Agent-Key': apiKey },
    })
    load()
  }

  async function sendHandshake() {
    if (!recipientName.trim() || !terms.trim()) return
    setSubmitting(true); setMsg('')
    const r = await fetch(`/api/agents/handshake/${encodeURIComponent(recipientName)}`, {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ terms }),
    })
    const d = await r.json().catch(() => ({}))
    if (r.ok) {
      setMsg('Handshake sent!'); setRecipientName(''); setTerms('')
      setTimeout(() => { setTab('history'); setMsg('') }, 1200)
    } else { setMsg(d.detail || 'Failed') }
    setSubmitting(false)
  }

  const pending = handshakes.filter(h => h.recipient_name === agentName && h.status === 'pending')
  const history = handshakes.filter(h => h.initiator_name === agentName || h.status !== 'pending')
  const display = tab === 'pending' ? pending : history

  return (
    <div className="handshake-panel">
      <div className="neg-tab-bar">
        {(['pending', 'history', 'new'] as const).map(t => (
          <button key={t} className={`neg-tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
            {t === 'pending' ? `Pending (${pending.length})` : t === 'history' ? 'History' : '+ New'}
          </button>
        ))}
      </div>
      {tab === 'new' ? (
        <div className="neg-form">
          {msg && <div className={msg.includes('!') ? 'success-msg' : 'error-msg'}>{msg}</div>}
          <div className="form-group">
            <label className="form-label">Recipient Agent</label>
            <input className="form-input" placeholder="agent-name" value={recipientName} onChange={e => setRecipientName(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Proposed Capability Exchange</label>
            <textarea className="form-input" rows={4}
              placeholder="e.g. 'I offer text_generation in exchange for image_analysis'"
              value={terms} onChange={e => setTerms(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={sendHandshake} disabled={submitting || !recipientName.trim() || !terms.trim()}>
            {submitting ? 'Sending…' : '🤝 Send Handshake'}
          </button>
        </div>
      ) : (
        <div className="neg-list">
          {loading && <div className="muted-text">Loading…</div>}
          {!loading && display.length === 0 && <div className="muted-text">No handshakes found.</div>}
          {display.map(h => (
            <div key={h.id} className="neg-card glass">
              <div className="neg-card-header">
                <span className="neg-offer-badge neg-badge-collab">🤝 Handshake</span>
                <span className="neg-status">{h.status}</span>
              </div>
              <div className="neg-parties">
                <span className="neg-initiator">{h.initiator_name}</span>
                <span className="neg-arrow"> → </span>
                <span className="neg-target">{h.recipient_name}</span>
              </div>
              <div className="neg-terms">
                {(() => { try { const t = JSON.parse(h.terms_json); return t.terms || JSON.stringify(t) } catch { return h.terms_json } })()}
              </div>
              {tab === 'pending' && h.status === 'pending' && (
                <div className="neg-actions">
                  <button className="btn btn-sm btn-primary" onClick={() => respond(h.id, 'accept')}>Accept</button>
                  <button className="btn btn-sm btn-danger" onClick={() => respond(h.id, 'reject')}>Reject</button>
                </div>
              )}
              <div className="neg-date">{new Date(h.created_at).toLocaleDateString()}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
