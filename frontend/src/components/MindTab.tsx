import React, { useState, useEffect } from 'react'
import { Brain, Loader, CheckCircle2, Circle, Sparkles, Link2, Unlink } from 'lucide-react'

interface MindStatus {
  connected: boolean
  cognition_url: string | null
  kind: 'omokoda' | 'custom' | null
  fallback_model?: string
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

  const [fallbackModel, setFallbackModel] = useState('')
  const [savingModel, setSavingModel] = useState(false)
  const [availableModels, setAvailableModels] = useState<string[]>([])
  const [modelsError, setModelsError] = useState('')

  async function saveFallbackModel(model: string) {
    setSavingModel(true)
    try {
      await fetch('/api/agents/me/mind/fallback-model', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      })
      load()
    } finally {
      setSavingModel(false)
    }
  }

  function load() {
    setLoading(true)
    fetch('/api/agents/me/mind/status', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.json())
      .then(d => { setStatus(d); setFallbackModel(d.fallback_model || '') })
      .catch(() => setError('Could not load mind status.'))
      .finally(() => setLoading(false))
    // Real available models from OmniRoute's own catalog (GET /v1/models),
    // not a free-text field the user had to fill in blind -- see
    // backend/agents.py's list_omniroute_models for where this comes from.
    fetch('/api/agents/me/mind/models', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d.models) && d.models.length) setAvailableModels(d.models)
        else setModelsError(d.error || 'No models returned')
      })
      .catch(() => setModelsError('Could not reach OmniRoute for the model list.'))
  }

  // Group by provider prefix (auto/, aug/, oc/, tllm/, ddgw/, etc.) -- a
  // flat 90+ item dropdown is unusable, but the prefixes are real,
  // meaningful groupings OmniRoute itself uses (aggregator vs specific
  // upstream), not an arbitrary categorization invented here.
  const modelGroups = availableModels.reduce<Record<string, string[]>>((acc, id) => {
    const group = id.includes('/') ? id.split('/')[0] : 'other'
    ;(acc[group] ||= []).push(id)
    return acc
  }, {})

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
        By default, Copilot chat is backed by a real LLM (OmniRoute) for basic conversation, price checks, and
        navigation. Connect a real agent brain here and Copilot routes chat straight to it instead -- any framework
        that implements the webhook contract works, not just Omo-Koda2.
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
                : 'Not connected — using default LLM fallback'}
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

      <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 560, marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Default LLM fallback model</div>
        <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
          Which OmniRoute model answers Copilot chat when {status?.connected ? "your connected mind doesn't respond" : 'no mind is connected'}.
          Leave on "auto" to use the instance default.
        </p>
        {modelsError && (
          <div style={{ fontSize: 11, color: 'var(--danger, #ff6666)', marginBottom: 8 }}>{modelsError}</div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <select
            value={fallbackModel}
            onChange={e => { setFallbackModel(e.target.value); saveFallbackModel(e.target.value) }}
            disabled={savingModel}
            style={{ flex: 1, padding: '8px 10px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 12 }}
          >
            <option value="">auto (instance default)</option>
            {Object.entries(modelGroups).sort(([a], [b]) => a.localeCompare(b)).map(([group, ids]) => (
              <optgroup key={group} label={group}>
                {ids.map(id => <option key={id} value={id}>{id}</option>)}
              </optgroup>
            ))}
          </select>
          {savingModel && <Loader size={14} className="spin" style={{ marginTop: 8 }} />}
        </div>
        <p style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6 }}>
          {availableModels.length > 0
            ? `${availableModels.length} real models fetched live from OmniRoute -- saves automatically on selection.`
            : 'Loading model catalog from OmniRoute…'}
        </p>
      </div>

      <PodcastVoicesSection apiKey={apiKey} />

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

interface Voice { id: string; gender: string }

// Real provider choice for podcast generation -- free edge-tts voices
// (47 real English neural voices, confirmed live), not a fake dropdown.
// Applies to Collab's "Create Podcast" and (for the flagship channel
// only, other agents' channels just replay what they've already
// published) whatever content this agent creates. Works identically for
// a human who hasn't created their own agent -- every browser visitor
// already has a real, auto-provisioned agent identity (see
// ensureAgentKey.ts), so this setting is never gated behind "does this
// human own a named agent."
//
// Honest scope note: LLM (above) and Voice (below) are the two real,
// wired provider choices in Vantage today. Image/video generation
// providers aren't a real feature yet -- Cinema/Audio publishing takes
// externally-hosted media URLs rather than generating images/video
// in-app, so a provider picker for those would be decorative. Not built
// here; flagged rather than faked.
function PodcastVoicesSection({ apiKey }: { apiKey: string }) {
  const [voices, setVoices] = useState<Voice[]>([])
  const [hostA, setHostA] = useState('')
  const [hostB, setHostB] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    Promise.all([
      fetch('/api/podcast/voices').then(r => r.json()),
      fetch('/api/podcast/voices/mine', { headers: { 'X-Agent-Key': apiKey } }).then(r => r.json()),
    ]).then(([all, mine]) => {
      setVoices(all)
      setHostA(mine.A || 'en-US-GuyNeural')
      setHostB(mine.B || 'en-US-JennyNeural')
    }).catch(() => {}).finally(() => setLoading(false))
  }, [apiKey])

  async function save() {
    setSaving(true)
    setSaved(false)
    try {
      await fetch('/api/podcast/voices/mine', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ A: hostA, B: hostB }),
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 560, marginBottom: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Podcast voices</div>
      <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
        Which real edge-tts voice powers each of your two podcast hosts when you create one in Collab.
        Free, no API key — defaults to Guy/Jenny if you don't choose your own.
      </p>
      {loading ? (
        <Loader size={14} className="spin" />
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
            <div>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Host A</label>
              <select value={hostA} onChange={e => setHostA(e.target.value)}
                style={{ width: '100%', padding: '7px 8px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 12 }}>
                {voices.map(v => <option key={v.id} value={v.id}>{v.id} ({v.gender})</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Host B</label>
              <select value={hostB} onChange={e => setHostB(e.target.value)}
                style={{ width: '100%', padding: '7px 8px', background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', fontSize: 12 }}>
                {voices.map(v => <option key={v.id} value={v.id}>{v.id} ({v.gender})</option>)}
              </select>
            </div>
          </div>
          <button className="btn btn-ghost btn-sm" disabled={saving} onClick={save}>
            {saving ? <Loader size={12} className="spin" /> : (saved ? 'Saved!' : 'Save')}
          </button>
        </>
      )}
    </div>
  )
}
