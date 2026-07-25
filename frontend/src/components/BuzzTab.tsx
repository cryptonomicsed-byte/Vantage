import React, { useState, useEffect } from 'react'
import { Radio, Loader, CheckCircle2, Circle, Copy } from 'lucide-react'

interface BuzzStatus {
  pubkey: string
  registered: boolean
  registered_at: string | null
  joined_channels: string[]
}

export default function BuzzTab({ apiKey }: { apiKey: string }) {
  const [status, setStatus] = useState<BuzzStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [registering, setRegistering] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  function load() {
    setLoading(true)
    fetch('/api/agents/me/buzz/status', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.json())
      .then(setStatus)
      .catch(() => setError('Could not load Buzz status.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function register() {
    setRegistering(true)
    setError('')
    try {
      const r = await fetch('/api/agents/me/buzz/register', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey },
      })
      const data = await r.json()
      if (!r.ok) { setError(data.detail || 'Registration failed.'); return }
      load()
    } catch {
      setError('Network error registering on Buzz.')
    } finally {
      setRegistering(false)
    }
  }

  function copyPubkey() {
    if (!status?.pubkey) return
    navigator.clipboard.writeText(status.pubkey).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  if (loading) return <div className="empty-state" style={{ minHeight: '20vh' }}><Loader size={20} className="spin" /></div>

  return (
    <section className="profile-section">
      <h3 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Radio size={16} /> Buzz Identity
      </h3>

      {error && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>{error}</p>}

      {status && (
        <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 560 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            {status.registered
              ? <CheckCircle2 size={16} color="#4ade80" />
              : <Circle size={16} color="var(--muted)" />}
            <span style={{ fontSize: 14, fontWeight: 600 }}>
              {status.registered ? 'Connected to Buzz' : 'Not yet registered'}
            </span>
          </div>

          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
              Nostr identity {status.registered ? '' : '(will be used on registration)'}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'monospace', fontSize: 12, background: 'rgba(8,8,16,0.6)', padding: '8px 10px', borderRadius: 6, wordBreak: 'break-all' }}>
              {status.pubkey}
              <button className="btn btn-ghost btn-sm" onClick={copyPubkey} style={{ flexShrink: 0 }}>
                <Copy size={12} /> {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              Derived deterministically from this agent's sealed seed -- same identity every time, never re-generated.
            </div>
          </div>

          {status.registered ? (
            <>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                Registered {status.registered_at ? new Date(status.registered_at + 'Z').toLocaleString() : ''}
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                Joined {status.joined_channels.length} channel{status.joined_channels.length === 1 ? '' : 's'}
              </div>
            </>
          ) : (
            <button className="btn btn-primary" disabled={registering} onClick={register}>
              {registering ? <Loader size={14} className="spin" /> : <Radio size={14} />} Register on Buzz
            </button>
          )}
        </div>
      )}

      <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 12, maxWidth: 560 }}>
        Buzz is a real-time agent/human chat relay (Nostr-based). Registering connects this agent's
        own signing identity to the relay and joins the default Vantage channel -- your agent can
        then be reached and reply to messages there, same identity every time.
      </p>
    </section>
  )
}
