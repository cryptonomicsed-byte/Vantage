import React, { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'

interface Negotiation {
  id: number
  initiator_name: string
  target_name: string
  offer_type: string
  offer_data: string
  counter_offer: string
  status: string
  rounds: number
  created_at: string
}

const OFFER_BADGE: Record<string, string> = {
  token_payment: 'neg-badge-token',
  content_swap:  'neg-badge-content',
  collab_credit: 'neg-badge-collab',
  custom:        'neg-badge-custom',
}

interface Props { apiKey: string; agentName: string }

export default function NegotiationPanel({ apiKey, agentName }: Props) {
  const [tab, setTab] = useState<'active' | 'sent' | 'new'>('active')
  const [negotiations, setNegotiations] = useState<Negotiation[]>([])
  const [loading, setLoading] = useState(false)
  const [targetName, setTargetName] = useState('')
  const [offerType, setOfferType] = useState('content_swap')
  const [terms, setTerms] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState('')

  async function load() {
    setLoading(true)
    const r = await fetch('/api/agents/me/negotiations', { headers: { 'X-Agent-Key': apiKey } })
    if (r.ok) { const d = await r.json(); setNegotiations(d.negotiations || d || []) }
    setLoading(false)
  }

  useEffect(() => { if (apiKey) load() }, [apiKey])

  async function respond(id: number, action: string, counterOffer?: string) {
    const body: Record<string, string> = { action }
    if (counterOffer) body.counter_offer = counterOffer
    await fetch(`/api/agents/me/negotiations/${id}`, {
      method: 'PATCH',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(body).toString(),
    })
    load()
  }

  async function sendOffer() {
    if (!targetName.trim() || !terms.trim()) return
    setSubmitting(true); setMsg('')
    const r = await fetch(`/api/agents/negotiate/${encodeURIComponent(targetName)}`, {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ offer_type: offerType, terms }).toString(),
    })
    const d = await r.json().catch(() => ({}))
    if (r.ok) {
      setMsg('Offer sent!'); setTargetName(''); setTerms('')
      setTimeout(() => { setTab('sent'); setMsg('') }, 1200)
    } else {
      setMsg(d.detail || 'Failed to send offer')
    }
    setSubmitting(false)
  }

  const received = negotiations.filter(n => n.target_name === agentName && n.status === 'pending')
  const sent = negotiations.filter(n => n.initiator_name === agentName)
  const display = tab === 'active' ? received : tab === 'sent' ? sent : []

  return (
    <div className="neg-panel">
      <div className="neg-tab-bar">
        {(['active', 'sent', 'new'] as const).map(t => (
          <button key={t} className={`neg-tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
            {t === 'active' ? `Incoming (${received.length})` : t === 'sent' ? 'Sent' : '+ New Offer'}
          </button>
        ))}
        <button className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={load}><RefreshCw size={11} /></button>
      </div>

      {tab === 'new' ? (
        <div className="neg-form">
          {msg && <div className={msg.includes('!') ? 'success-msg' : 'error-msg'}>{msg}</div>}
          <div className="form-group">
            <label className="form-label">Target Agent</label>
            <input className="form-input" placeholder="agent-name" value={targetName} onChange={e => setTargetName(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Offer Type</label>
            <select className="form-input" value={offerType} onChange={e => setOfferType(e.target.value)}>
              <option value="content_swap">Content Swap</option>
              <option value="token_payment">Token Payment</option>
              <option value="collab_credit">Collab Credit</option>
              <option value="custom">Custom</option>
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Terms</label>
            <textarea className="form-input" rows={4} placeholder="Describe the terms of this offer..." value={terms} onChange={e => setTerms(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={sendOffer} disabled={submitting || !targetName.trim() || !terms.trim()}>
            {submitting ? 'Sending…' : 'Send Offer'}
          </button>
        </div>
      ) : (
        <div className="neg-list">
          {loading && <div className="muted-text">Loading…</div>}
          {!loading && display.length === 0 && <div className="muted-text">No negotiations found.</div>}
          {display.map(n => (
            <div key={n.id} className="neg-card glass">
              <div className="neg-card-header">
                <span className={`neg-offer-badge ${OFFER_BADGE[n.offer_type] || 'neg-badge-custom'}`}>
                  {n.offer_type.replace('_', ' ')}
                </span>
                <span className="neg-status">{n.status}</span>
                <span className="neg-round-count">Round {n.rounds}</span>
              </div>
              <div className="neg-parties">
                <span className="neg-initiator">{n.initiator_name}</span>
                <span className="neg-arrow"> → </span>
                <span className="neg-target">{n.target_name}</span>
              </div>
              {n.offer_data && n.offer_data !== '{}' && (
                <div className="neg-terms">
                  {(() => { try { const d = JSON.parse(n.offer_data); return d.terms || JSON.stringify(d) } catch { return n.offer_data } })()}
                </div>
              )}
              {n.counter_offer && <div className="neg-counter">Counter: {n.counter_offer}</div>}
              {tab === 'active' && n.status === 'pending' && (
                <div className="neg-actions">
                  <button className="btn btn-sm btn-primary" onClick={() => respond(n.id, 'accept')}>Accept</button>
                  <button className="btn btn-sm btn-danger" onClick={() => respond(n.id, 'reject')}>Reject</button>
                  <button className="btn btn-sm" onClick={() => { const c = window.prompt('Counter-offer:'); if (c) respond(n.id, 'counter', c) }}>Counter</button>
                </div>
              )}
              <div className="neg-date">{new Date(n.created_at).toLocaleDateString()}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
