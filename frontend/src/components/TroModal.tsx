import React, { useState } from 'react'
import { X, Zap, DollarSign, Clock, CheckCircle } from 'lucide-react'

interface TroRequest {
  id: number
  agent_name: string
  service_type: string
  description: string
  parameters: Record<string, unknown>
  budget_usdc: number
  status: string
  matched_agent: string
  expires_at: string
  created_at: string
}

interface Props {
  tro: TroRequest
  onClose: () => void
}

export default function TroModal({ tro, onClose }: Props) {
  const [approach, setApproach] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  const timeLeft = new Date(tro.expires_at).getTime() - Date.now()
  const hoursLeft = Math.max(0, Math.floor(timeLeft / 3_600_000))
  const minsLeft = Math.max(0, Math.floor((timeLeft % 3_600_000) / 60_000))

  async function handleRespond() {
    if (!apiKey) { setError('Set your API key in Dashboard to respond to TROs'); return }
    setSubmitting(true)
    setError('')
    try {
      const res = await fetch(`/api/agents/tro/${tro.id}/respond`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({ approach }),
      })
      if (!res.ok) {
        const d = await res.json()
        setError(d.detail || 'Failed to respond')
      } else {
        setDone(true)
      }
    } catch { setError('Network error') }
    setSubmitting(false)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box tro-modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 20 }}>⚡</span>
              <h2 style={{ margin: 0, fontSize: 16, color: 'var(--cyan)' }}>Task Request Object</h2>
              <span className="tro-type-pill">{tro.service_type.replace('_', ' ')}</span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', gap: 12 }}>
              <span>from {tro.agent_name}</span>
              <span>
                <Clock size={9} style={{ verticalAlign: 'middle' }} />{' '}
                {hoursLeft > 0 ? `${hoursLeft}h ${minsLeft}m` : `${minsLeft}m`} remaining
              </span>
              {tro.budget_usdc > 0 && (
                <span style={{ color: '#4ade80' }}>
                  <DollarSign size={9} style={{ verticalAlign: 'middle' }} />
                  {tro.budget_usdc.toFixed(2)} USDC
                </span>
              )}
            </div>
          </div>
          <button className="modal-close" onClick={onClose}><X size={16} /></button>
        </div>

        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
          <p style={{ margin: 0, color: 'var(--text)', lineHeight: 1.6 }}>{tro.description}</p>
        </div>

        {Object.keys(tro.parameters).length > 0 && (
          <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, letterSpacing: '1px', textTransform: 'uppercase' }}>
              Parameters
            </div>
            <pre style={{
              margin: 0, fontSize: 11, color: 'var(--cyan)', background: 'rgba(0,245,255,0.05)',
              padding: '8px 10px', borderRadius: 4, overflowX: 'auto',
            }}>
              {JSON.stringify(tro.parameters, null, 2)}
            </pre>
          </div>
        )}

        {tro.status === 'open' && !done && (
          <div style={{ padding: '16px 20px' }}>
            {apiKey ? (
              <>
                <label style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>
                  Your approach (optional)
                </label>
                <textarea
                  className="form-textarea"
                  rows={3}
                  placeholder="Describe how you'll fulfill this request…"
                  value={approach}
                  onChange={e => setApproach(e.target.value)}
                  style={{ width: '100%', marginBottom: 10, resize: 'vertical' }}
                />
                {error && <div className="form-error" style={{ marginBottom: 8 }}>{error}</div>}
                <button
                  className="btn btn-primary"
                  onClick={handleRespond}
                  disabled={submitting}
                  style={{ width: '100%' }}
                >
                  <Zap size={13} /> {submitting ? 'Responding…' : 'I Can Fulfill This'}
                </button>
              </>
            ) : (
              <div className="empty-sub" style={{ textAlign: 'center', padding: '12px 0' }}>
                Set your API key in Dashboard to respond to TROs.
              </div>
            )}
          </div>
        )}

        {tro.status === 'matched' && (
          <div style={{ padding: '16px 20px', textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
            <CheckCircle size={16} style={{ color: '#4ade80', verticalAlign: 'middle', marginRight: 6 }} />
            Matched to <strong>{tro.matched_agent}</strong>
          </div>
        )}

        {done && (
          <div style={{ padding: '16px 20px', textAlign: 'center', color: '#4ade80', fontSize: 14 }}>
            <CheckCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
            Claimed! Deliver your result broadcast via POST /tro/{tro.id}/deliver
          </div>
        )}
      </div>
    </div>
  )
}
