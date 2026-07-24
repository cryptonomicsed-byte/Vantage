import React, { useEffect, useState } from 'react'
import { Tv, Send, ThumbsUp, ThumbsDown } from 'lucide-react'

// AgentTV -- Vantage's window into Seemplify (Agent.TV2), a separate
// agentic pipeline: submit a pilot idea, it runs Researcher -> Scriptor ->
// VideoGen -> Streamer agents, the result goes up for token-weighted
// community vote, and passing pilots become 24/7 channels. Proxied through
// backend/routers/agenttv_proxy.py rather than calling Seemplify directly.
//
// Known, disclosed limitation: Seemplify's real LLM/video-gen/Theta/Solana
// integrations are currently mocked upstream -- submissions run the full
// pipeline shape, but scripts/video/streams are placeholders until those
// are wired with real credentials.

const KEY = () => localStorage.getItem('vantage_api_key') || ''

interface Proposal {
  id: string
  title: string
  status: string
  votes: { yes: number; no: number; abstain: number }
  yesPercent?: string
  passed?: boolean
}

interface Channel {
  id: string
  title: string
  active?: boolean
}

export default function AgentTVSection() {
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [channels, setChannels] = useState<Channel[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState('')

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [tone, setTone] = useState('casual')

  function load() {
    Promise.all([
      fetch('/api/cinema/agenttv/governance/proposals').then(r => r.ok ? r.json() : []),
      fetch('/api/cinema/agenttv/channels/featured?limit=10').then(r => r.ok ? r.json() : []),
    ])
      .then(([p, c]) => { setProposals(Array.isArray(p) ? p : []); setChannels(Array.isArray(c) ? c : []) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function submitPilot() {
    if (!title.trim() || !description.trim()) return
    setSubmitting(true)
    setNotice('')
    try {
      const r = await fetch('/api/cinema/agenttv/pilots/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': KEY() },
        body: JSON.stringify({ title, description, tone, duration: 300, tags: [] }),
      })
      const data = await r.json()
      if (!r.ok) { setNotice(data.detail || 'Submission failed'); return }
      setNotice(`Submitted "${title}" — now in the pipeline.`)
      setTitle(''); setDescription('')
      load()
    } catch {
      setNotice('Network error reaching AgentTV.')
    } finally {
      setSubmitting(false)
    }
  }

  async function vote(proposalId: string, choice: 'yes' | 'no') {
    try {
      await fetch('/api/cinema/agenttv/governance/vote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': KEY() },
        body: JSON.stringify({ proposalId, voterTokenBalance: 100, voteChoice: choice }),
      })
      load()
    } catch {}
  }

  if (loading) return <div className="cin-empty">Loading AgentTV…</div>

  return (
    <div>
      <div className="glass" style={{ padding: 18, marginBottom: 24 }}>
        <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Tv size={16} /> Submit a pilot
        </h3>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
          Agents research, script, generate, and stream it automatically — the community votes to greenlight.
        </p>
        <input placeholder="Show title" value={title} onChange={e => setTitle(e.target.value)}
               style={{ display: 'block', width: '100%', marginBottom: 8, padding: '8px 10px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 13 }} />
        <textarea placeholder="What's this show about?" value={description} onChange={e => setDescription(e.target.value)}
                  style={{ display: 'block', width: '100%', marginBottom: 8, padding: '8px 10px', minHeight: 60, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 13 }} />
        <select value={tone} onChange={e => setTone(e.target.value)}
                style={{ marginBottom: 10, padding: '6px 10px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 13 }}>
          <option value="casual">Casual</option>
          <option value="serious">Serious</option>
          <option value="comedic">Comedic</option>
        </select>
        {notice && <p style={{ fontSize: 12, color: 'var(--cyan)' }}>{notice}</p>}
        <div>
          <button className="btn btn-primary btn-sm" disabled={submitting || !title.trim()} onClick={submitPilot}>
            <Send size={12} /> Submit pilot
          </button>
        </div>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>Governance — vote on pilots</h3>
      {proposals.length === 0 ? (
        <div className="cin-empty" style={{ marginBottom: 24 }}>No proposals up for vote right now.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 24 }}>
          {proposals.map(p => (
            <div key={p.id} className="glass" style={{ padding: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{p.title}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                  {p.votes?.yes ?? 0} yes / {p.votes?.no ?? 0} no · {p.status}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => vote(p.id, 'yes')}><ThumbsUp size={12} /></button>
                <button className="btn btn-ghost btn-sm" onClick={() => vote(p.id, 'no')}><ThumbsDown size={12} /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>Live channels</h3>
      {channels.length === 0 ? (
        <div className="cin-empty">No channels deployed yet.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
          {channels.map(c => (
            <div key={c.id} className="glass" style={{ padding: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{c.title}</div>
              <div style={{ fontSize: 11, color: c.active ? 'var(--cyan)' : 'var(--muted)' }}>{c.active ? 'Active' : 'Paused'}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
