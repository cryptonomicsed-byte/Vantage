import React, { useState, useEffect } from 'react'
import { Brain, Loader, CheckCircle2, Circle, Sparkles, Link2, Unlink } from 'lucide-react'

interface MindStatus {
  connected: boolean
  cognition_url: string | null
  kind: 'omokoda' | 'custom' | null
}

// Powers Copilot with a real 'mind' instead of the built-in regex intent
// parser -- generic webhook contract (any agent framework), Omo-Koda2 is
// one convenience option, not the only path. See backend/mind_link.py's
// module docstring for the exact contract this agent's chat will speak.
export default function MindTab({ apiKey }: { apiKey: string }) {
  const [status, setStatus] = useState<MindStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [linking, setLinking] = useState(false)
  const [error, setError] = useState('')
  const [verifyReply, setVerifyReply] = useState<string | null>(null)

  const [customUrl, setCustomUrl] = useState('')
  const [customToken, setCustomToken] = useState('')

  function load() {
    setLoading(true)
    fetch('/api/agents/me/mind/status', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.json())
      .then(setStatus)
      .catch(() => setError('Could not load mind status.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function linkOmokoda() {
    setLinking(true)
    setError('')
    setVerifyReply(null)
    try {
      const r = await fetch('/api/agents/me/mind/link-omokoda', { method: 'POST', headers: { 'X-Agent-Key': apiKey } })
      const data = await r.json()
      if (!r.ok) { setError(data.detail || 'Link failed.'); return }
      if (data.verified) setVerifyReply(data.verify_reply)
      else setError('Linked, but the verification round trip did not get a reply -- the agent may be out of thinking budget or the kernel is unreachable. Chat will fall back to basic commands until this resolves.')
      load()
    } catch {
      setError('Network error linking to Omo-Koda2.')
    } finally {
      setLinking(false)
    }
  }

  async function connectCustom() {
    if (!customUrl.trim()) return
    setLinking(true)
    setError('')
    try {
      const r = await fetch('/api/agents/me/mind/connect', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ cognition_url: customUrl.trim(), cognition_auth_token: customToken.trim() || null }),
      })
      const data = await r.json()
      if (!r.ok) { setError(data.detail || 'Connect failed.'); return }
      setCustomUrl(''); setCustomToken('')
      load()
    } catch {
      setError('Network error connecting your webhook.')
    } finally {
      setLinking(false)
    }
  }

  async function disconnect() {
    setLinking(true)
    setError('')
    try {
      await fetch('/api/agents/me/mind/disconnect', { method: 'POST', headers: { 'X-Agent-Key': apiKey } })
      load()
    } finally {
      setLinking(false)
    }
  }

  if (loading) return <div className="empty-state" style={{ minHeight: '20vh' }}><Loader size={20} className="spin" /></div>

  return (
    <section className="profile-section">
      <h3 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Brain size={16} /> Mind
      </h3>
      <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 16, maxWidth: 560 }}>
        By default, Copilot chat runs a basic command parser (price checks, navigation, alerts). Connect a real
        agent brain here and Copilot routes chat straight to it instead -- any framework that implements the
        webhook contract works, not just Omo-Koda2.
      </p>

      {error && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>{error}</p>}
      {verifyReply && (
        <p style={{ fontSize: 12, color: '#4ade80', marginBottom: 12 }}>
          Verified -- real reply came back: "{verifyReply}"
        </p>
      )}

      {status && (
        <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 560, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            {status.connected
              ? <CheckCircle2 size={16} color="#4ade80" />
              : <Circle size={16} color="var(--muted)" />}
            <span style={{ fontSize: 14, fontWeight: 600 }}>
              {status.connected
                ? `Connected${status.kind === 'omokoda' ? ' — Omo-Koda2' : ' — custom webhook'}`
                : 'Not connected — using basic commands'}
            </span>
          </div>

          {status.connected && (
            <>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12, wordBreak: 'break-all' }}>
                {status.cognition_url}
              </div>
              <button className="btn btn-ghost btn-sm" disabled={linking} onClick={disconnect}>
                {linking ? <Loader size={12} className="spin" /> : <Unlink size={12} />} Disconnect
              </button>
            </>
          )}
        </div>
      )}

      {!status?.connected && (
        <>
          <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 560, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Sparkles size={13} /> Power this agent with a real mind (Omo-Koda2)
            </div>
            <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
              Births a real Omo-Koda2 kernel agent and wires it straight to this agent's chat. Fastest way to get a
              real thinking agent behind Copilot.
            </p>
            <button className="btn btn-primary" disabled={linking} onClick={linkOmokoda}>
              {linking ? <Loader size={14} className="spin" /> : <Sparkles size={14} />} Power with Omo-Koda2
            </button>
          </div>

          <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 560 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Link2 size={13} /> Connect your own agent
            </div>
            <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
              Any framework works -- your webhook just needs to accept <code>POST {'{'}agent_name, text, human_id{'}'}</code> and
              return <code>{'{'}reply{'}'}</code>.
            </p>
            <input
              placeholder="https://your-agent.example.com/cognition"
              value={customUrl}
              onChange={e => setCustomUrl(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', marginBottom: 8, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 12 }}
            />
            <input
              placeholder="Auth token (optional)"
              value={customToken}
              onChange={e => setCustomToken(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', marginBottom: 10, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 12 }}
            />
            <button className="btn btn-ghost btn-sm" disabled={linking || !customUrl.trim()} onClick={connectCustom}>
              {linking ? <Loader size={12} className="spin" /> : <Link2 size={12} />} Connect
            </button>
          </div>
        </>
      )}
    </section>
  )
}
