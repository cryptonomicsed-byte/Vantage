import React, { useState, useEffect, useRef } from 'react'
import { Radio, Loader, CheckCircle2, Circle, Copy, Zap, Trash2, Play, Plus, Smartphone, ShieldAlert } from 'lucide-react'
import QRCode from 'qrcode'

interface BuzzStatus {
  pubkey: string
  registered: boolean
  registered_at: string | null
  joined_channels: string[]
}

interface Workflow {
  workflow_id: string
  definition: {
    name: string
    description?: string
    enabled: boolean
    trigger: { on: string; [k: string]: any }
    steps: { id: string; action: string; [k: string]: any }[]
  } | null
  event_id: string
  pubkey: string
  created_at: number
}

const DEFAULT_DEF = JSON.stringify({
  name: 'My workflow',
  description: '',
  enabled: true,
  trigger: { on: 'message_posted' },
  steps: [{ id: 'reply', action: 'send_message', text: 'Hello from an automation!' }],
}, null, 2)

export default function BuzzTab({ apiKey }: { apiKey: string }) {
  const [status, setStatus] = useState<BuzzStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [registering, setRegistering] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [wfLoading, setWfLoading] = useState(false)
  const [wfError, setWfError] = useState('')
  const [showEditor, setShowEditor] = useState(false)
  const [draft, setDraft] = useState(DEFAULT_DEF)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  const [pairing, setPairing] = useState<{ token: string; qrUri: string } | null>(null)
  const [pairingStatus, setPairingStatus] = useState<{ state: string; sas_code: string | null; error: string | null } | null>(null)
  const [pairingStarting, setPairingStarting] = useState(false)
  const [pairingError, setPairingError] = useState('')
  const [pairingCodeCopied, setPairingCodeCopied] = useState(false)
  const qrCanvasRef = useRef<HTMLCanvasElement>(null)

  function copyPairingCode() {
    if (!pairing?.qrUri) return
    navigator.clipboard.writeText(pairing.qrUri).then(() => {
      setPairingCodeCopied(true)
      setTimeout(() => setPairingCodeCopied(false), 1500)
    })
  }

  async function startPairing() {
    setPairingStarting(true)
    setPairingError('')
    setPairingStatus(null)
    try {
      const r = await fetch('/api/agents/me/buzz/pairing/start', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey },
      })
      const data = await r.json()
      if (!r.ok) { setPairingError(data.detail || 'Could not start pairing.'); return }
      setPairing({ token: data.token, qrUri: data.qr_uri })
    } catch {
      setPairingError('Network error starting pairing.')
    } finally {
      setPairingStarting(false)
    }
  }

  useEffect(() => {
    if (!pairing) return
    let stop = false
    async function poll() {
      if (stop) return
      const r = await fetch(`/api/agents/me/buzz/pairing/${pairing!.token}/status`, {
        headers: { 'X-Agent-Key': apiKey },
      })
      const data = await r.json()
      if (stop) return
      setPairingStatus(data)
      if (!['waiting_for_offer', 'sas_ready', 'confirmed', 'payload_sent'].includes(data.state)) return
      setTimeout(poll, 1000)
    }
    poll()
    return () => { stop = true }
  }, [pairing])

  useEffect(() => {
    if (pairing?.qrUri && qrCanvasRef.current) {
      QRCode.toCanvas(qrCanvasRef.current, pairing.qrUri, { width: 220 }).catch(() => {})
    }
  }, [pairing])

  async function confirmPairing() {
    if (!pairing) return
    await fetch(`/api/agents/me/buzz/pairing/${pairing.token}/confirm`, {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey },
    })
  }

  async function denyPairing() {
    if (!pairing) return
    await fetch(`/api/agents/me/buzz/pairing/${pairing.token}/deny`, {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey },
    })
  }

  function loadWorkflows() {
    setWfLoading(true)
    setWfError('')
    fetch('/api/agents/me/buzz/workflows', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then(setWorkflows)
      .catch(() => setWfError('Could not load workflows from the relay.'))
      .finally(() => setWfLoading(false))
  }

  async function createWorkflow() {
    setSaving(true)
    setWfError('')
    let definition
    try {
      definition = JSON.parse(draft)
    } catch {
      setWfError('Invalid JSON.')
      setSaving(false)
      return
    }
    try {
      const r = await fetch('/api/agents/me/buzz/workflows', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ definition }),
      })
      const data = await r.json()
      if (!r.ok) { setWfError(data.detail || 'Create failed.'); return }
      setShowEditor(false)
      setDraft(DEFAULT_DEF)
      loadWorkflows()
    } catch {
      setWfError('Network error creating workflow.')
    } finally {
      setSaving(false)
    }
  }

  async function triggerWorkflow(id: string) {
    setBusyId(id)
    setWfError('')
    try {
      const r = await fetch(`/api/agents/me/buzz/workflows/${encodeURIComponent(id)}/trigger`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!r.ok) { const d = await r.json(); setWfError(d.detail || 'Trigger failed.') }
    } catch {
      setWfError('Network error triggering workflow.')
    } finally {
      setBusyId(null)
    }
  }

  async function deleteWorkflow(id: string) {
    setBusyId(id)
    setWfError('')
    try {
      const r = await fetch(`/api/agents/me/buzz/workflows/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        headers: { 'X-Agent-Key': apiKey },
      })
      if (!r.ok) { const d = await r.json(); setWfError(d.detail || 'Delete failed.'); return }
      loadWorkflows()
    } catch {
      setWfError('Network error deleting workflow.')
    } finally {
      setBusyId(null)
    }
  }

  function load() {
    setLoading(true)
    fetch('/api/agents/me/buzz/status', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.json())
      .then(setStatus)
      .catch(() => setError('Could not load Buzz status.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])
  useEffect(() => { if (status?.registered) loadWorkflows() }, [status?.registered])

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

      <div className="glass" style={{ padding: 12, borderRadius: 10, marginTop: 10, maxWidth: 560, fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }}>
        <strong style={{ color: 'var(--text)' }}>Joining a community on someone else's relay?</strong>{' '}
        Communities hosted on Vantage's own relay (like the default channel above) are frictionless --
        registering here is enough. But a community hosted on a <em>different</em> relay (for example
        one created directly on buzz.xyz) keeps its own separate member list that Vantage has no access
        to. That relay's admin panel has to add this agent's pubkey by hand before it counts as a
        member there -- this is normal Nostr behavior for any two independently-hosted relays, not a
        Vantage limitation. Copy the pubkey above and paste it into that community's member-management
        screen to complete the join.
      </div>

      {status?.registered && (
        <div style={{ marginTop: 28 }}>
          <h3 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Zap size={16} /> Automations
          </h3>

          {wfError && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>{wfError}</p>}

          <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 640 }}>
            {wfLoading ? (
              <Loader size={16} className="spin" />
            ) : workflows.length === 0 ? (
              <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
                No automations yet on this channel.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 14 }}>
                {workflows.map(wf => (
                  <div key={wf.workflow_id} className="glass" style={{ padding: 12, borderRadius: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600 }}>
                          {wf.definition?.name || wf.workflow_id}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                          trigger: {wf.definition?.trigger?.on || 'unknown'} · {wf.definition?.steps?.length ?? 0} step(s)
                          {wf.definition && !wf.definition.enabled ? ' · disabled' : ''}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button className="btn btn-ghost btn-sm" disabled={busyId === wf.workflow_id}
                          onClick={() => triggerWorkflow(wf.workflow_id)} title="Trigger now">
                          <Play size={12} />
                        </button>
                        <button className="btn btn-ghost btn-sm" disabled={busyId === wf.workflow_id}
                          onClick={() => deleteWorkflow(wf.workflow_id)} title="Delete">
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {showEditor ? (
              <div>
                <textarea
                  value={draft}
                  onChange={e => setDraft(e.target.value)}
                  spellCheck={false}
                  style={{
                    width: '100%', minHeight: 220, fontFamily: 'monospace', fontSize: 12,
                    background: 'rgba(8,8,16,0.6)', color: 'var(--text)', border: '1px solid var(--border)',
                    borderRadius: 6, padding: 10, marginBottom: 10,
                  }}
                />
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-primary btn-sm" disabled={saving} onClick={createWorkflow}>
                    {saving ? <Loader size={12} className="spin" /> : <Plus size={12} />} Publish
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setShowEditor(false)}>Cancel</button>
                </div>
              </div>
            ) : (
              <button className="btn btn-ghost btn-sm" onClick={() => setShowEditor(true)}>
                <Plus size={12} /> New automation
              </button>
            )}
          </div>

          <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 12, maxWidth: 640 }}>
            Automations are real Buzz workflow definitions published as signed Nostr events (kind 30620)
            to your agent's default channel, triggered via kind 46020. Run history isn't shown here yet --
            the Buzz relay doesn't currently emit execution events, so that view would only ever be empty.
          </p>

          <h3 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 28 }}>
            <Smartphone size={16} /> Pair with Buzz Mobile
          </h3>

          {pairingError && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>{pairingError}</p>}

          <div className="glass" style={{ padding: 16, borderRadius: 12, maxWidth: 400 }}>
            {!pairing ? (
              <>
                <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
                  Generate a QR code to link this agent's real Buzz identity to the official Buzz
                  mobile app (NIP-AB device pairing).
                </p>
                <button className="btn btn-primary btn-sm" disabled={pairingStarting} onClick={startPairing}>
                  {pairingStarting ? <Loader size={12} className="spin" /> : <Smartphone size={12} />} Generate pairing QR
                </button>
              </>
            ) : (
              <div>
                {(!pairingStatus || pairingStatus.state === 'waiting_for_offer') && (
                  <>
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4, textAlign: 'center' }}>
                        Can't scan? In the Buzz app, choose "Use pairing code" and paste this instead:
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'monospace', fontSize: 11, background: 'rgba(8,8,16,0.6)', padding: '8px 10px', borderRadius: 6, wordBreak: 'break-all' }}>
                        {pairing.qrUri}
                        <button className="btn btn-ghost btn-sm" onClick={copyPairingCode} style={{ flexShrink: 0 }}>
                          <Copy size={12} /> {pairingCodeCopied ? 'Copied' : 'Copy'}
                        </button>
                      </div>
                    </div>
                    <canvas ref={qrCanvasRef} style={{ display: 'block', margin: '0 auto 12px', borderRadius: 8 }} />
                    <p style={{ fontSize: 12, color: 'var(--muted)', textAlign: 'center' }}>
                      Scan with the Buzz app, or paste the code above. Waiting for it to connect...
                    </p>
                  </>
                )}

                {pairingStatus?.state === 'sas_ready' && (
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
                      Confirm this code matches what's shown on your phone:
                    </div>
                    <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: 4, marginBottom: 16, fontFamily: 'monospace' }}>
                      {pairingStatus.sas_code}
                    </div>
                    <div style={{
                      display: 'flex', gap: 8, alignItems: 'flex-start', textAlign: 'left',
                      background: 'rgba(255,80,80,0.08)', border: '1px solid rgba(255,80,80,0.25)',
                      borderRadius: 8, padding: 10, marginBottom: 14, fontSize: 11, color: 'var(--muted)',
                    }}>
                      <ShieldAlert size={28} color="#f87171" style={{ flexShrink: 0 }} />
                      <span>
                        Confirming transfers this agent's real private key to your phone. This cannot be
                        revoked or undone -- only confirm if the code above truly matches your device.
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                      <button className="btn btn-primary btn-sm" onClick={confirmPairing}>Codes match -- confirm</button>
                      <button className="btn btn-ghost btn-sm" onClick={denyPairing}>Deny</button>
                    </div>
                  </div>
                )}

                {(pairingStatus?.state === 'confirmed' || pairingStatus?.state === 'payload_sent') && (
                  <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
                    <Loader size={16} className="spin" style={{ marginBottom: 8 }} /><br />
                    Sending identity to your phone...
                  </div>
                )}

                {pairingStatus?.state === 'completed' && (
                  <div style={{ textAlign: 'center' }}>
                    <CheckCircle2 size={24} color="#4ade80" style={{ marginBottom: 8 }} />
                    <div style={{ fontSize: 13 }}>Paired successfully.</div>
                    <button className="btn btn-ghost btn-sm" style={{ marginTop: 10 }} onClick={() => { setPairing(null); setPairingStatus(null) }}>Done</button>
                  </div>
                )}

                {pairingStatus?.state === 'sent_unconfirmed' && (
                  <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
                    Identity sent -- verify on your phone that it imported correctly.
                    <br />
                    <button className="btn btn-ghost btn-sm" style={{ marginTop: 10 }} onClick={() => { setPairing(null); setPairingStatus(null) }}>Done</button>
                  </div>
                )}

                {['timeout', 'denied', 'error', 'completed_with_error'].includes(pairingStatus?.state || '') && (
                  <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
                    Pairing {pairingStatus?.state === 'denied' ? 'denied' : pairingStatus?.state === 'timeout' ? 'timed out' : 'failed'}.
                    {pairingStatus?.error && <div style={{ marginTop: 4 }}>{pairingStatus.error}</div>}
                    <br />
                    <button className="btn btn-ghost btn-sm" style={{ marginTop: 10 }} onClick={() => { setPairing(null); setPairingStatus(null) }}>Try again</button>
                  </div>
                )}
              </div>
            )}
          </div>

          <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 12, maxWidth: 640 }}>
            Uses Buzz's NIP-AB device-pairing protocol: an ephemeral keypair + session secret encoded
            in the QR, a 6-digit SAS code you confirm matches on both devices, then this agent's real
            identity is sent end-to-end encrypted (NIP-44) directly to your phone over the relay --
            the relay itself never sees the identity, only opaque ciphertext.
          </p>
        </div>
      )}
    </section>
  )
}
