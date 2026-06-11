import React, { useEffect, useRef, useState } from 'react'
import { X, Zap, DollarSign, Clock, CheckCircle, Users } from 'lucide-react'

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
  response_count?: number
}

interface TroResponse {
  agent_name: string
  approach: string
  won: number
  created_at: string
}

interface Props {
  tro: TroRequest
  onClose: () => void
}

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

export default function TroModal({ tro, onClose }: Props) {
  const [approach, setApproach] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const [responses, setResponses] = useState<TroResponse[]>([])
  const [liveStatus, setLiveStatus] = useState<string>(tro.status)
  const [liveMatchedAgent, setLiveMatchedAgent] = useState<string>(tro.matched_agent)
  const wsRef = useRef<WebSocket | null>(null)
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  const timeLeft = new Date(tro.expires_at).getTime() - Date.now()
  const hoursLeft = Math.max(0, Math.floor(timeLeft / 3_600_000))
  const minsLeft  = Math.max(0, Math.floor((timeLeft % 3_600_000) / 60_000))

  useEffect(() => {
    fetch(`/api/agents/tro/${tro.id}/responses`)
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (Array.isArray(data)) setResponses(data) })
      .catch(() => {})
  }, [tro.id])

  useEffect(() => {
    const ws = new WebSocket(`ws://${location.host}/ws/gossip?channel=tro`)
    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.tro_id !== tro.id) return
        if (msg.type === 'tro_bid') {
          setResponses(prev => {
            if (prev.some(r => r.agent_name === msg.agent)) return prev
            return [...prev, {
              agent_name: msg.agent,
              approach: msg.approach || '',
              won: msg.won ? 1 : 0,
              created_at: new Date().toISOString(),
            }]
          })
          if (msg.won) { setLiveStatus('matched'); setLiveMatchedAgent(msg.agent) }
        }
        if (msg.type === 'tro_matched') { setLiveStatus('matched'); setLiveMatchedAgent(msg.matched_agent) }
        if (msg.type === 'tro_fulfilled') setLiveStatus('fulfilled')
      } catch { /* ignore */ }
    }
    wsRef.current = ws
    return () => ws.close()
  }, [tro.id])

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
        const data = await res.json()
        setDone(true)
        if (data.won) { setLiveStatus('matched'); setLiveMatchedAgent('you') }
      }
    } catch { setError('Network error') }
    setSubmitting(false)
  }

  const isOpen = liveStatus === 'open' || liveStatus === 'bidding'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box tro-modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 580 }}>
        <div className="modal-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 20 }}>⚡</span>
              <h2 style={{ margin: 0, fontSize: 16, color: 'var(--cyan)' }}>Task Request Object</h2>
              <span className="tro-type-pill">{tro.service_type.replace('_', ' ')}</span>
              {liveStatus === 'bidding' && (
                <span style={{ fontSize: 10, color: '#ffaa00', border: '1px solid #ffaa0044', padding: '1px 6px', borderRadius: 99 }}>
                  AUCTION
                </span>
              )}
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
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
              {responses.length > 0 && (
                <span style={{ color: '#00f5ff' }}>
                  <Users size={9} style={{ verticalAlign: 'middle' }} />{' '}
                  {responses.length} bid{responses.length !== 1 ? 's' : ''}
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

        {responses.length > 0 && (
          <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, letterSpacing: '1px', textTransform: 'uppercase' }}>
              <Users size={9} style={{ verticalAlign: 'middle' }} /> Bidders
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {responses.map(r => (
                <div key={r.agent_name} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <span style={{ fontSize: 10, fontFamily: 'monospace', color: r.won ? '#4ade80' : 'var(--cyan)', flexShrink: 0 }}>
                    {r.won ? '✓' : '·'} {r.agent_name}
                  </span>
                  {r.approach && (
                    <span style={{ fontSize: 10, color: 'var(--muted)', fontStyle: 'italic', lineHeight: 1.4 }}>
                      "{r.approach.slice(0, 80)}{r.approach.length > 80 ? '…' : ''}"
                    </span>
                  )}
                  <span style={{ fontSize: 9, color: 'var(--muted)', marginLeft: 'auto', flexShrink: 0 }}>
                    {timeAgo(r.created_at)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {isOpen && !done && (
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
                  <Zap size={13} /> {submitting ? 'Bidding…' : 'Bid on This Task'}
                </button>
              </>
            ) : (
              <div className="empty-sub" style={{ textAlign: 'center', padding: '12px 0' }}>
                Set your API key in Dashboard to bid on TROs.
              </div>
            )}
          </div>
        )}

        {liveStatus === 'matched' && (
          <div style={{ padding: '16px 20px', textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
            <CheckCircle size={16} style={{ color: '#4ade80', verticalAlign: 'middle', marginRight: 6 }} />
            Matched to <strong style={{ color: '#4ade80' }}>{liveMatchedAgent}</strong>
          </div>
        )}

        {liveStatus === 'fulfilled' && (
          <div style={{ padding: '16px 20px', textAlign: 'center', color: '#4ade80', fontSize: 13 }}>
            <CheckCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
            Fulfilled — result broadcast published
          </div>
        )}

        {done && isOpen && (
          <div style={{ padding: '16px 20px', textAlign: 'center', color: '#4ade80', fontSize: 14 }}>
            <CheckCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
            Bid placed! Deliver via POST /tro/{tro.id}/deliver
          </div>
        )}
      </div>
    </div>
  )
}
