import React, { useState, useEffect, useRef } from 'react'
import {
  Radio, Wifi, Globe, Plus, Trash2, RefreshCw, Send, Copy, Check,
  AlertCircle, CheckCircle2, Circle, Server, Key, Users, Zap,
  MessageSquare, MapPin, Battery, Signal, Settings2, Database,
  Lock, Unlock, Network, Activity, ChevronDown, ChevronRight, Eye, EyeOff,
} from 'lucide-react'

type Proto = 'nostr' | 'meshtastic' | 'freenet'

// ── shared helpers ─────────────────────────────────────────────────────────────
function StatusDot({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--muted)', display: 'inline-block' }} />
  return <span style={{ width: 8, height: 8, borderRadius: '50%', background: ok ? '#4ade80' : '#f87171', display: 'inline-block', boxShadow: ok ? '0 0 6px #4ade8066' : 'none' }} />
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="stat-card" style={{ flex: '1 1 120px', minWidth: 100 }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--cyan)', fontFamily: 'monospace' }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 style={{ fontSize: 11, fontWeight: 700, color: 'var(--muted)', letterSpacing: '0.08em', textTransform: 'uppercase', margin: '24px 0 10px' }}>{children}</h3>
}

// ── NOSTR PROTOCOL CENTER ──────────────────────────────────────────────────────
interface NostrRelay { url: string; status: 'connected' | 'disconnected' | 'error'; latency: number | null; read: boolean; write: boolean }
interface NostrEvent { id: string; pubkey: string; kind: number; content: string; created_at: number; tags: string[][] }

function NostrCenter({ apiKey }: { apiKey: string }) {
  const [tab, setTab] = useState<'identity' | 'relays' | 'feed' | 'publish' | 'zaps' | 'nips'>('identity')
  const [status, setStatus] = useState<any>(null)
  const [relays, setRelays] = useState<NostrRelay[]>([
    { url: 'wss://omokoda.duckdns.org:3443', status: 'disconnected', latency: null, read: true, write: true },
    { url: 'wss://relay.damus.io', status: 'disconnected', latency: null, read: true, write: false },
    { url: 'wss://nos.lol', status: 'disconnected', latency: null, read: true, write: false },
    { url: 'wss://relay.nostr.band', status: 'disconnected', latency: null, read: true, write: false },
    { url: 'wss://nostr.wine', status: 'disconnected', latency: null, read: false, write: false },
  ])
  const [newRelay, setNewRelay] = useState('')
  const [events, setEvents] = useState<NostrEvent[]>([])
  const [noteContent, setNoteContent] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [pubResult, setPubResult] = useState<string | null>(null)
  const [showPrivKey, setShowPrivKey] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!apiKey) { setLoading(false); return }
    fetch('/api/agents/me/buzz/status', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { setStatus(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [apiKey])

  useEffect(() => {
    if (!apiKey || !status?.registered) return
    fetch('/api/agents/me/buzz/events?limit=20', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => d?.events && setEvents(d.events))
      .catch(() => {})
  }, [apiKey, status])

  function copy(val: string, key: string) {
    navigator.clipboard.writeText(val).catch(() => {})
    setCopied(key)
    setTimeout(() => setCopied(null), 1800)
  }

  function addRelay() {
    const url = newRelay.trim()
    if (!url || !url.startsWith('wss://')) return
    setRelays(r => [...r, { url, status: 'disconnected', latency: null, read: true, write: false }])
    setNewRelay('')
  }

  function pingRelay(url: string) {
    const start = Date.now()
    const ws = new WebSocket(url)
    ws.onopen = () => {
      const ms = Date.now() - start
      setRelays(r => r.map(x => x.url === url ? { ...x, status: 'connected', latency: ms } : x))
      ws.close()
    }
    ws.onerror = () => setRelays(r => r.map(x => x.url === url ? { ...x, status: 'error', latency: null } : x))
  }

  async function publishNote() {
    if (!noteContent.trim() || !apiKey) return
    setPublishing(true); setPubResult(null)
    try {
      const r = await fetch('/api/agents/me/buzz/publish', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: noteContent, kind: 1 }),
      })
      const d = await r.json()
      setPubResult(r.ok ? `✓ Published · event ${d.event_id?.slice(0, 12)}…` : `✗ ${d.detail || 'Failed'}`)
      if (r.ok) setNoteContent('')
    } catch { setPubResult('✗ Network error') }
    setPublishing(false)
  }

  const TABS: Array<{ key: typeof tab; label: string }> = [
    { key: 'identity', label: 'Identity' },
    { key: 'relays',   label: 'Relays'   },
    { key: 'feed',     label: 'Feed'     },
    { key: 'publish',  label: 'Publish'  },
    { key: 'zaps',     label: 'Zaps'     },
    { key: 'nips',     label: 'NIPs'     },
  ]

  const NIP_SUPPORT = [
    { nip: 'NIP-01', name: 'Basic protocol', supported: true, desc: 'Event publishing and subscription' },
    { nip: 'NIP-02', name: 'Follow list', supported: true, desc: 'Kind 3 contact list events' },
    { nip: 'NIP-04', name: 'Encrypted DMs', supported: true, desc: 'Shared-secret DM encryption' },
    { nip: 'NIP-05', name: 'DNS verification', supported: status?.registered, desc: 'Internet identifier mapping' },
    { nip: 'NIP-09', name: 'Event deletion', supported: true, desc: 'Kind 5 deletion requests' },
    { nip: 'NIP-10', name: 'Reply threading', supported: true, desc: 'e/p tag conventions for threads' },
    { nip: 'NIP-11', name: 'Relay information', supported: true, desc: 'Relay metadata document' },
    { nip: 'NIP-13', name: 'Proof of work', supported: false, desc: 'Event difficulty targeting' },
    { nip: 'NIP-19', name: 'bech32 entities', supported: true, desc: 'npub/nsec/note/nprofile/nevent' },
    { nip: 'NIP-25', name: 'Reactions', supported: true, desc: 'Kind 7 reaction events' },
    { nip: 'NIP-28', name: 'Public chat', supported: true, desc: 'Channels — kind 40/41/42/43/44' },
    { nip: 'NIP-42', name: 'Auth', supported: true, desc: 'Relay authentication challenge' },
    { nip: 'NIP-57', name: 'Zaps', supported: false, desc: 'Lightning zap receipts' },
    { nip: 'NIP-65', name: 'Relay list', supported: true, desc: 'Kind 10002 relay list metadata' },
  ]

  return (
    <div>
      {/* Sub-tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`btn btn-sm ${tab === t.key ? 'btn-primary' : 'btn-ghost'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Stats row */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 20 }}>
        <StatCard label="Status" value={status?.registered ? 'Online' : 'Offline'} sub="Nostr identity" />
        <StatCard label="Relays" value={relays.filter(r => r.status === 'connected').length + '/' + relays.length} sub="connected" />
        <StatCard label="Events" value={events.length} sub="cached notes" />
        <StatCard label="Kind 1" value={events.filter(e => e.kind === 1).length} sub="text notes" />
      </div>

      {/* ── Identity ── */}
      {tab === 'identity' && (
        <div>
          <SectionTitle>Nostr Identity</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>Registration</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <StatusDot ok={status?.registered ?? null} />
                <span style={{ fontSize: 12 }}>{status?.registered ? 'Registered' : 'Not registered'}</span>
              </div>
            </div>
            {status?.pubkey && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>Public Key (npub)</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <code style={{ fontSize: 11, color: 'var(--cyan)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {status.pubkey}
                  </code>
                  <button className="btn btn-ghost btn-sm" onClick={() => copy(status.pubkey, 'npub')}>
                    {copied === 'npub' ? <Check size={11} /> : <Copy size={11} />}
                  </button>
                </div>
              </div>
            )}
            <div>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>Private Key (nsec)</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <code style={{ fontSize: 11, color: '#f87171', flex: 1 }}>
                  {showPrivKey ? 'nsec1•••••••••••••••••••••••••••••••••••••••••••••••••••••' : '••••••••••••••••••••••••••••••••'}
                </code>
                <button className="btn btn-ghost btn-sm" onClick={() => setShowPrivKey(v => !v)}>
                  {showPrivKey ? <EyeOff size={11} /> : <Eye size={11} />}
                </button>
              </div>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                Never share your nsec. Stored encrypted in agent vault.
              </div>
            </div>
          </div>

          <SectionTitle>NIP-05 Verification</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
              Internet identifier in the format <code style={{ color: 'var(--cyan)' }}>name@domain</code> mapped to your pubkey via DNS .well-known.
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="search-page-input" placeholder="name@yourdomain.com" style={{ flex: 1, fontSize: 12 }} />
              <button className="btn btn-primary btn-sm">Verify</button>
            </div>
          </div>

          <SectionTitle>Profile Metadata (Kind 0)</SectionTitle>
          <div className="stat-card">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { label: 'Display name', placeholder: 'Your Nostr name' },
                { label: 'About', placeholder: 'Bio / about text' },
                { label: 'Picture URL', placeholder: 'https://...' },
                { label: 'Website', placeholder: 'https://...' },
                { label: 'Lightning address', placeholder: 'you@getalby.com' },
              ].map(f => (
                <div key={f.label}>
                  <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>{f.label}</div>
                  <input className="search-page-input" placeholder={f.placeholder} style={{ fontSize: 12, width: '100%' }} />
                </div>
              ))}
              <button className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start', marginTop: 4 }}>
                Publish Kind 0 Event
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Relays ── */}
      {tab === 'relays' && (
        <div>
          <SectionTitle>Connected Relays</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {relays.map(relay => (
              <div key={relay.url} className="stat-card" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <StatusDot ok={relay.status === 'connected' ? true : relay.status === 'error' ? false : null} />
                <code style={{ fontSize: 11, color: 'var(--cyan)', flex: 1 }}>{relay.url}</code>
                <span style={{ fontSize: 10, color: 'var(--muted)', minWidth: 36 }}>
                  {relay.latency !== null ? `${relay.latency}ms` : '—'}
                </span>
                <span style={{ fontSize: 9, color: relay.read ? '#4ade80' : 'var(--muted)', background: 'var(--surface)', padding: '1px 5px', borderRadius: 4 }}>R</span>
                <span style={{ fontSize: 9, color: relay.write ? '#4ade80' : 'var(--muted)', background: 'var(--surface)', padding: '1px 5px', borderRadius: 4 }}>W</span>
                <button className="btn btn-ghost btn-sm" onClick={() => pingRelay(relay.url)} title="Ping relay">
                  <RefreshCw size={11} />
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => setRelays(r => r.filter(x => x.url !== relay.url))}>
                  <Trash2 size={11} />
                </button>
              </div>
            ))}
          </div>

          <SectionTitle>Add Relay</SectionTitle>
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="search-page-input" value={newRelay} onChange={e => setNewRelay(e.target.value)}
              placeholder="wss://relay.example.com" style={{ flex: 1, fontSize: 12 }}
              onKeyDown={e => e.key === 'Enter' && addRelay()} />
            <button className="btn btn-primary btn-sm" onClick={addRelay}><Plus size={11} /> Add</button>
          </div>

          <SectionTitle>Relay Policies</SectionTitle>
          <div className="stat-card">
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
              NIP-65 Relay List Metadata — publish your preferred read/write relay list to the network.
            </div>
            <button className="btn btn-ghost btn-sm">Publish Kind 10002 (NIP-65)</button>
          </div>
        </div>
      )}

      {/* ── Feed ── */}
      {tab === 'feed' && (
        <div>
          <SectionTitle>Global Feed</SectionTitle>
          {events.length === 0 ? (
            <div className="empty-state" style={{ marginTop: 20 }}>
              <Radio size={28} style={{ marginBottom: 10, opacity: 0.4 }} />
              <p style={{ fontSize: 13 }}>No events cached yet — connect to relays and subscribe.</p>
              <button className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={() => setTab('relays')}>
                Manage Relays →
              </button>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {events.map(ev => (
                <div key={ev.id} className="stat-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <code style={{ fontSize: 10, color: 'var(--purple-bright)' }}>{ev.pubkey.slice(0, 16)}…</code>
                    <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                      {new Date(ev.created_at * 1000).toLocaleTimeString()} · kind:{ev.kind}
                    </span>
                  </div>
                  <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5 }}>{ev.content}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Publish ── */}
      {tab === 'publish' && (
        <div>
          <SectionTitle>Publish Note (Kind 1)</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <textarea
              value={noteContent} onChange={e => setNoteContent(e.target.value)}
              placeholder="What's on your mind? (Nostr Kind 1 text note)"
              style={{ width: '100%', minHeight: 100, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 10, fontSize: 13, color: 'var(--text)', resize: 'vertical', fontFamily: 'inherit' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>{noteContent.length}/280</span>
              <button className="btn btn-primary btn-sm" onClick={publishNote} disabled={publishing || !noteContent.trim()}>
                <Send size={11} /> {publishing ? 'Publishing…' : 'Publish'}
              </button>
            </div>
            {pubResult && <div style={{ marginTop: 8, fontSize: 12, color: pubResult.startsWith('✓') ? '#4ade80' : '#f87171' }}>{pubResult}</div>}
          </div>

          <SectionTitle>Raw Event Builder</SectionTitle>
          <div className="stat-card">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Kind</div>
                <select className="search-select" style={{ fontSize: 12 }}>
                  {[1,3,4,5,6,7,40,41,42,10002].map(k => <option key={k} value={k}>{k}</option>)}
                </select>
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Tags (JSON array)</div>
                <input className="search-page-input" placeholder='[["e","<event-id>"],["p","<pubkey>"]]' style={{ fontSize: 11, width: '100%', fontFamily: 'monospace' }} />
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Content</div>
                <textarea placeholder="Event content" style={{ width: '100%', minHeight: 60, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: 8, fontSize: 12, color: 'var(--text)', resize: 'vertical', fontFamily: 'monospace' }} />
              </div>
              <button className="btn btn-ghost btn-sm" style={{ alignSelf: 'flex-start' }}>Sign &amp; Broadcast</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Zaps ── */}
      {tab === 'zaps' && (
        <div>
          <SectionTitle>Lightning / Zaps (NIP-57)</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
              Zaps require a lightning wallet with LNURL support. Connect a lightning address to enable receiving zaps.
            </div>
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Lightning Address</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="search-page-input" placeholder="you@getalby.com" style={{ flex: 1, fontSize: 12 }} />
                <button className="btn btn-primary btn-sm">Save</button>
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>LNURL</div>
              <input className="search-page-input" placeholder="lnurl1dp..." style={{ width: '100%', fontSize: 11, fontFamily: 'monospace' }} />
            </div>
          </div>

          <SectionTitle>Send Zap</SectionTitle>
          <div className="stat-card">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Recipient (npub or lightning address)</div>
                <input className="search-page-input" placeholder="npub1..." style={{ width: '100%', fontSize: 12 }} />
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Amount (sats)</div>
                <input className="search-page-input" type="number" placeholder="21" style={{ fontSize: 12, width: 120 }} />
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Message (optional)</div>
                <input className="search-page-input" placeholder="⚡" style={{ width: '100%', fontSize: 12 }} />
              </div>
              <button className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start' }}><Zap size={11} /> Zap</button>
            </div>
          </div>
        </div>
      )}

      {/* ── NIPs ── */}
      {tab === 'nips' && (
        <div>
          <SectionTitle>NIP Support Matrix</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {NIP_SUPPORT.map(n => (
              <div key={n.nip} className="stat-card" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px' }}>
                <StatusDot ok={n.supported} />
                <code style={{ fontSize: 11, color: 'var(--cyan)', minWidth: 56 }}>{n.nip}</code>
                <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{n.name}</span>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{n.desc}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── MESHTASTIC PROTOCOL CENTER ─────────────────────────────────────────────────
interface MeshNode { id: string; short_name: string; long_name: string; snr: number; last_heard: number; battery: number | null; lat: number | null; lon: number | null; hop_limit: number }
interface MeshChannel { index: number; name: string; role: 'PRIMARY' | 'SECONDARY' | 'DISABLED' }
interface MeshMessage { id: string; from: string; channel: number; text: string; ts: number }

function MeshtasticCenter({ apiKey }: { apiKey: string }) {
  const [tab, setTab] = useState<'nodes' | 'channels' | 'messages' | 'telemetry' | 'config'>('nodes')
  const [connected, setConnected] = useState(false)
  const [connType, setConnType] = useState<'tcp' | 'ble' | 'serial'>('tcp')
  const [host, setHost] = useState('192.168.1.x')
  const [port, setPort] = useState('4403')
  const [nodes, setNodes] = useState<MeshNode[]>([])
  const [channels, setChannels] = useState<MeshChannel[]>([
    { index: 0, name: 'LongFast', role: 'PRIMARY' },
    { index: 1, name: 'Admin', role: 'SECONDARY' },
  ])
  const [messages, setMessages] = useState<MeshMessage[]>([])
  const [msgText, setMsgText] = useState('')
  const [msgChannel, setMsgChannel] = useState(0)
  const [connecting, setConnecting] = useState(false)
  const [newChan, setNewChan] = useState('')

  useEffect(() => {
    if (!apiKey) return
    fetch('/api/mesh/nodes', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.nodes) { setNodes(d.nodes); setConnected(true) } })
      .catch(() => {})
  }, [apiKey])

  function connect() {
    setConnecting(true)
    fetch('/api/mesh/connect', {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: connType, host, port: parseInt(port) }),
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.ok) setConnected(true) })
      .catch(() => {})
      .finally(() => setConnecting(false))
  }

  function sendMessage() {
    if (!msgText.trim()) return
    const msg: MeshMessage = { id: Date.now().toString(), from: 'me', channel: msgChannel, text: msgText, ts: Date.now() }
    setMessages(m => [...m, msg])
    setMsgText('')
    fetch('/api/mesh/send', {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: msgChannel, text: msgText }),
    }).catch(() => {})
  }

  const TABS: Array<{ key: typeof tab; label: string }> = [
    { key: 'nodes',    label: 'Nodes'    },
    { key: 'channels', label: 'Channels' },
    { key: 'messages', label: 'Messages' },
    { key: 'telemetry',label: 'Telemetry'},
    { key: 'config',   label: 'Config'   },
  ]

  return (
    <div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`btn btn-sm ${tab === t.key ? 'btn-primary' : 'btn-ghost'}`}>
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 20 }}>
        <StatCard label="Connection" value={connected ? 'Online' : 'Offline'} sub={connected ? connType.toUpperCase() : 'disconnected'} />
        <StatCard label="Nodes" value={nodes.length} sub="in mesh" />
        <StatCard label="Channels" value={channels.filter(c => c.role !== 'DISABLED').length} sub="active" />
        <StatCard label="Messages" value={messages.length} sub="this session" />
      </div>

      {/* Connection panel */}
      {!connected && (
        <div className="stat-card" style={{ marginBottom: 20, background: 'var(--surface)' }}>
          <SectionTitle>Connect Device</SectionTitle>
          <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
            {(['tcp', 'ble', 'serial'] as const).map(t => (
              <button key={t} onClick={() => setConnType(t)}
                className={`btn btn-sm ${connType === t ? 'btn-primary' : 'btn-ghost'}`}>
                {t.toUpperCase()}
              </button>
            ))}
          </div>
          {connType === 'tcp' && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
              <input className="search-page-input" value={host} onChange={e => setHost(e.target.value)} placeholder="IP address" style={{ flex: 1, fontSize: 12 }} />
              <input className="search-page-input" value={port} onChange={e => setPort(e.target.value)} placeholder="4403" style={{ width: 70, fontSize: 12 }} />
            </div>
          )}
          {connType === 'ble' && <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>BLE scanning requires browser BLE API (Chrome/Edge on desktop or Android).</div>}
          {connType === 'serial' && <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>Serial port access requires Web Serial API (Chrome/Edge desktop).</div>}
          <button className="btn btn-primary btn-sm" onClick={connect} disabled={connecting}>
            <Wifi size={11} /> {connecting ? 'Connecting…' : 'Connect'}
          </button>
        </div>
      )}

      {/* ── Nodes ── */}
      {tab === 'nodes' && (
        <div>
          <SectionTitle>Mesh Nodes</SectionTitle>
          {nodes.length === 0 ? (
            <div className="empty-state" style={{ marginTop: 16 }}>
              <Network size={28} style={{ marginBottom: 10, opacity: 0.4 }} />
              <p style={{ fontSize: 13 }}>No nodes visible — connect a Meshtastic device.</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {nodes.map(n => (
                <div key={n.id} className="stat-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <div>
                      <span style={{ fontWeight: 700, fontSize: 13 }}>{n.long_name}</span>
                      <code style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 8 }}>!{n.id}</code>
                    </div>
                    <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                      {Math.round((Date.now() - n.last_heard) / 60000)}m ago
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 16, fontSize: 11, color: 'var(--muted)' }}>
                    <span><Signal size={10} style={{ marginRight: 3 }} />SNR {n.snr}dB</span>
                    {n.battery !== null && <span><Battery size={10} style={{ marginRight: 3 }} />{n.battery}%</span>}
                    {n.lat !== null && <span><MapPin size={10} style={{ marginRight: 3 }} />{n.lat.toFixed(4)}, {n.lon?.toFixed(4)}</span>}
                    <span>Hops: {n.hop_limit}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Channels ── */}
      {tab === 'channels' && (
        <div>
          <SectionTitle>Channel List</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
            {channels.map(ch => (
              <div key={ch.index} className="stat-card" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: 11, color: 'var(--muted)', minWidth: 16 }}>#{ch.index}</span>
                <span style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>{ch.name || '(unnamed)'}</span>
                <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 4,
                  background: ch.role === 'PRIMARY' ? '#8a4bff22' : ch.role === 'SECONDARY' ? '#00f5ff22' : 'var(--surface)',
                  color: ch.role === 'PRIMARY' ? 'var(--purple-bright)' : ch.role === 'SECONDARY' ? 'var(--cyan)' : 'var(--muted)' }}>
                  {ch.role}
                </span>
                <button className="btn btn-ghost btn-sm" onClick={() => setChannels(c => c.filter(x => x.index !== ch.index))}>
                  <Trash2 size={11} />
                </button>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="search-page-input" value={newChan} onChange={e => setNewChan(e.target.value)}
              placeholder="Channel name" style={{ flex: 1, fontSize: 12 }} />
            <button className="btn btn-primary btn-sm" onClick={() => {
              if (!newChan.trim()) return
              setChannels(c => [...c, { index: c.length, name: newChan.trim(), role: 'SECONDARY' }])
              setNewChan('')
            }}><Plus size={11} /> Add</button>
          </div>
        </div>
      )}

      {/* ── Messages ── */}
      {tab === 'messages' && (
        <div>
          <SectionTitle>Mesh Broadcast</SectionTitle>
          <div style={{ minHeight: 200, maxHeight: 300, overflow: 'auto', marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {messages.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--muted)', padding: 20, textAlign: 'center' }}>No messages yet</div>
            ) : messages.map(m => (
              <div key={m.id} className="stat-card" style={{ padding: '6px 10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <code style={{ fontSize: 10, color: m.from === 'me' ? 'var(--cyan)' : 'var(--purple-bright)' }}>{m.from}</code>
                  <span style={{ fontSize: 10, color: 'var(--muted)' }}>ch:{m.channel}</span>
                </div>
                <div style={{ fontSize: 13 }}>{m.text}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <select className="search-select" value={msgChannel} onChange={e => setMsgChannel(Number(e.target.value))} style={{ fontSize: 12 }}>
              {channels.filter(c => c.role !== 'DISABLED').map(c => (
                <option key={c.index} value={c.index}>#{c.index} {c.name}</option>
              ))}
            </select>
            <input className="search-page-input" value={msgText} onChange={e => setMsgText(e.target.value)}
              placeholder="Broadcast to mesh…" style={{ flex: 1, fontSize: 12 }}
              onKeyDown={e => e.key === 'Enter' && sendMessage()} />
            <button className="btn btn-primary btn-sm" onClick={sendMessage}><Send size={11} /></button>
          </div>
        </div>
      )}

      {/* ── Telemetry ── */}
      {tab === 'telemetry' && (
        <div>
          <SectionTitle>Device Telemetry</SectionTitle>
          {nodes.length === 0 ? (
            <div className="empty-state" style={{ marginTop: 16 }}>
              <Activity size={28} style={{ marginBottom: 10, opacity: 0.4 }} />
              <p style={{ fontSize: 13 }}>Connect a device to see live telemetry.</p>
            </div>
          ) : nodes.map(n => (
            <div key={n.id} className="stat-card" style={{ marginBottom: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 10 }}>{n.long_name}</div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <StatCard label="SNR" value={`${n.snr}dB`} />
                {n.battery !== null && <StatCard label="Battery" value={`${n.battery}%`} />}
                {n.lat !== null && <StatCard label="Latitude" value={n.lat.toFixed(5)} />}
                {n.lon !== null && <StatCard label="Longitude" value={n.lon.toFixed(5)} />}
                <StatCard label="Hop Limit" value={n.hop_limit} />
              </div>
            </div>
          ))}

          <SectionTitle>Environment Metrics</SectionTitle>
          <div className="stat-card">
            <div style={{ fontSize: 12, color: 'var(--muted)' }}>
              Temperature, humidity, and pressure sensors reported via Meshtastic telemetry module — displayed here when a device with env sensors is connected.
            </div>
          </div>
        </div>
      )}

      {/* ── Config ── */}
      {tab === 'config' && (
        <div>
          <SectionTitle>Device Configuration</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { label: 'Long name', placeholder: 'My Meshtastic Node' },
                { label: 'Short name', placeholder: 'NODE' },
                { label: 'Region', placeholder: 'US / EU868 / AU915 / …' },
              ].map(f => (
                <div key={f.label}>
                  <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>{f.label}</div>
                  <input className="search-page-input" placeholder={f.placeholder} style={{ width: '100%', fontSize: 12 }} />
                </div>
              ))}
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Modem preset</div>
                <select className="search-select" style={{ fontSize: 12 }}>
                  {['LongFast','LongSlow','MediumSlow','MediumFast','ShortFast','ShortSlow'].map(p => <option key={p}>{p}</option>)}
                </select>
              </div>
            </div>
          </div>

          <SectionTitle>LoRa Radio</SectionTitle>
          <div className="stat-card">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Transmit power (dBm)</div>
                <input className="search-page-input" type="number" placeholder="17" style={{ fontSize: 12, width: 100 }} />
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Hop limit</div>
                <input className="search-page-input" type="number" placeholder="3" style={{ fontSize: 12, width: 100 }} />
              </div>
              <button className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start' }}>Write Config</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── FREENET PROTOCOL CENTER ────────────────────────────────────────────────────
interface FreenetPeer { id: string; addr: string; connected: boolean; latency: number | null }
interface FreenetContract { key: string; label: string; size: number; last_update: number; state: 'active' | 'pending' | 'error' }

function FreenetCenter({ apiKey }: { apiKey: string }) {
  const [tab, setTab] = useState<'network' | 'contracts' | 'datastore' | 'identity' | 'peers'>('network')
  const [status, setStatus] = useState<any>(null)
  const [peers, setPeers] = useState<FreenetPeer[]>([])
  const [contracts, setContracts] = useState<FreenetContract[]>([])
  const [putKey, setPutKey] = useState('')
  const [putValue, setPutValue] = useState('')
  const [getKey, setGetKey] = useState('')
  const [getResult, setGetResult] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!apiKey) { setLoading(false); return }
    fetch('/api/freenet/status', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) { setStatus(d); setPeers(d.peers || []); setContracts(d.contracts || []) } })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [apiKey])

  async function doPut() {
    if (!putKey || !putValue) return
    const r = await fetch('/api/freenet/put', {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: putKey, value: putValue }),
    }).catch(() => null)
    if (r?.ok) { setPutKey(''); setPutValue('') }
  }

  async function doGet() {
    if (!getKey) return
    const r = await fetch(`/api/freenet/get?key=${encodeURIComponent(getKey)}`, { headers: { 'X-Agent-Key': apiKey } }).catch(() => null)
    const d = r?.ok ? await r.json() : null
    setGetResult(d ? JSON.stringify(d.value, null, 2) : 'Not found')
  }

  const TABS: Array<{ key: typeof tab; label: string }> = [
    { key: 'network',   label: 'Network'   },
    { key: 'contracts', label: 'Contracts' },
    { key: 'datastore', label: 'Datastore' },
    { key: 'identity',  label: 'Identity'  },
    { key: 'peers',     label: 'Peers'     },
  ]

  return (
    <div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`btn btn-sm ${tab === t.key ? 'btn-primary' : 'btn-ghost'}`}>
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 20 }}>
        <StatCard label="Network" value={status?.connected ? 'Online' : 'Offline'} sub="Freenet node" />
        <StatCard label="Peers" value={peers.filter(p => p.connected).length + '/' + peers.length} sub="connected" />
        <StatCard label="Contracts" value={contracts.filter(c => c.state === 'active').length} sub="active" />
        <StatCard label="Storage" value={status?.storage_used_mb ? `${status.storage_used_mb}MB` : '—'} sub="used" />
      </div>

      {/* ── Network ── */}
      {tab === 'network' && (
        <div>
          <SectionTitle>Node Status</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>Node</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <StatusDot ok={status?.connected ?? null} />
                  <span style={{ fontSize: 12 }}>{status?.connected ? 'Connected to Freenet' : 'Disconnected'}</span>
                </div>
              </div>
              {status?.node_id && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 12, color: 'var(--muted)' }}>Node ID</span>
                  <code style={{ fontSize: 11, color: 'var(--cyan)' }}>{status.node_id}</code>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>Mode</span>
                <span style={{ fontSize: 12 }}>{status?.mode || 'Network'}</span>
              </div>
            </div>
          </div>

          <SectionTitle>Bootstrap Nodes</SectionTitle>
          <div className="stat-card">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {[
                'freenet.freenetproject.org:29760',
                'freenet2.freenetproject.org:29760',
              ].map(addr => (
                <div key={addr} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <code style={{ color: 'var(--cyan)' }}>{addr}</code>
                  <StatusDot ok={status?.connected ?? null} />
                </div>
              ))}
            </div>
          </div>

          <SectionTitle>Network Stats</SectionTitle>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <StatCard label="Uptime" value={status?.uptime_hours ? `${status.uptime_hours}h` : '—'} />
            <StatCard label="Bandwidth In" value={status?.bandwidth_in_kbps ? `${status.bandwidth_in_kbps}kbps` : '—'} />
            <StatCard label="Bandwidth Out" value={status?.bandwidth_out_kbps ? `${status.bandwidth_out_kbps}kbps` : '—'} />
            <StatCard label="Data Store" value={status?.datastore_size_mb ? `${status.datastore_size_mb}MB` : '—'} sub="allocated" />
          </div>
        </div>
      )}

      {/* ── Contracts ── */}
      {tab === 'contracts' && (
        <div>
          <SectionTitle>Deployed Contracts</SectionTitle>
          {contracts.length === 0 ? (
            <div className="empty-state" style={{ marginTop: 16 }}>
              <Database size={28} style={{ marginBottom: 10, opacity: 0.4 }} />
              <p style={{ fontSize: 13 }}>No contracts deployed yet.</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {contracts.map(c => (
                <div key={c.key} className="stat-card" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <StatusDot ok={c.state === 'active' ? true : c.state === 'error' ? false : null} />
                  <code style={{ fontSize: 11, color: 'var(--cyan)', flex: 1 }}>{c.key.slice(0, 24)}…</code>
                  <span style={{ fontSize: 12 }}>{c.label}</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>{c.size}B</span>
                  <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                    {new Date(c.last_update * 1000).toLocaleDateString()}
                  </span>
                </div>
              ))}
            </div>
          )}

          <SectionTitle>Deploy Contract</SectionTitle>
          <div className="stat-card">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Contract WASM</div>
                <input className="search-page-input" type="file" style={{ fontSize: 12, width: '100%' }} />
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Initial state (JSON)</div>
                <textarea placeholder='{"key": "value"}' style={{ width: '100%', minHeight: 60, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: 8, fontSize: 12, color: 'var(--text)', resize: 'vertical', fontFamily: 'monospace' }} />
              </div>
              <button className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start' }}>
                <Database size={11} /> Deploy
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Datastore ── */}
      {tab === 'datastore' && (
        <div>
          <SectionTitle>Put Data</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Key</div>
                <input className="search-page-input" value={putKey} onChange={e => setPutKey(e.target.value)}
                  placeholder="freenet://key/path" style={{ width: '100%', fontSize: 12, fontFamily: 'monospace' }} />
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Value (JSON or text)</div>
                <textarea value={putValue} onChange={e => setPutValue(e.target.value)}
                  placeholder='{"data": "…"}' style={{ width: '100%', minHeight: 80, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: 8, fontSize: 12, color: 'var(--text)', resize: 'vertical', fontFamily: 'monospace' }} />
              </div>
              <button className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start' }} onClick={doPut}>
                <Lock size={11} /> Put
              </button>
            </div>
          </div>

          <SectionTitle>Get Data</SectionTitle>
          <div className="stat-card">
            <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
              <input className="search-page-input" value={getKey} onChange={e => setGetKey(e.target.value)}
                placeholder="freenet://key/path" style={{ flex: 1, fontSize: 12, fontFamily: 'monospace' }}
                onKeyDown={e => e.key === 'Enter' && doGet()} />
              <button className="btn btn-primary btn-sm" onClick={doGet}><Unlock size={11} /> Get</button>
            </div>
            {getResult && (
              <pre style={{ background: 'var(--surface)', borderRadius: 6, padding: 10, fontSize: 11, color: 'var(--cyan)', overflow: 'auto', maxHeight: 200, fontFamily: 'monospace' }}>
                {getResult}
              </pre>
            )}
          </div>
        </div>
      )}

      {/* ── Identity ── */}
      {tab === 'identity' && (
        <div>
          <SectionTitle>Freenet Identity</SectionTitle>
          <div className="stat-card" style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12, lineHeight: 1.6 }}>
              Freenet uses public-key cryptography for identity. Your identity keypair signs requests and is used to access private data you've published.
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Public Key</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <code style={{ fontSize: 11, color: 'var(--cyan)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {status?.public_key || 'No identity generated'}
                  </code>
                  {status?.public_key && <button className="btn btn-ghost btn-sm"><Copy size={11} /></button>}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>Key Algorithm</div>
                <span style={{ fontSize: 12 }}>Ed25519</span>
              </div>
            </div>
          </div>

          {!status?.public_key && (
            <button className="btn btn-primary btn-sm"><Key size={11} /> Generate Identity</button>
          )}

          <SectionTitle>Access Control</SectionTitle>
          <div className="stat-card">
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
              ACL rules for your published contracts — specify which pubkeys can read or write.
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="search-page-input" placeholder="Pubkey to grant access" style={{ flex: 1, fontSize: 12 }} />
              <select className="search-select" style={{ fontSize: 12 }}>
                <option>Read</option>
                <option>Write</option>
                <option>Admin</option>
              </select>
              <button className="btn btn-primary btn-sm"><Plus size={11} /></button>
            </div>
          </div>
        </div>
      )}

      {/* ── Peers ── */}
      {tab === 'peers' && (
        <div>
          <SectionTitle>Connected Peers</SectionTitle>
          {peers.length === 0 ? (
            <div className="empty-state" style={{ marginTop: 16 }}>
              <Globe size={28} style={{ marginBottom: 10, opacity: 0.4 }} />
              <p style={{ fontSize: 13 }}>No peers connected — join the Freenet network first.</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {peers.map(p => (
                <div key={p.id} className="stat-card" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <StatusDot ok={p.connected} />
                  <code style={{ fontSize: 11, color: 'var(--cyan)', flex: 1 }}>{p.addr}</code>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                    {p.latency !== null ? `${p.latency}ms` : '—'}
                  </span>
                </div>
              ))}
            </div>
          )}

          <SectionTitle>Add Peer</SectionTitle>
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="search-page-input" placeholder="host:port" style={{ flex: 1, fontSize: 12 }} />
            <button className="btn btn-primary btn-sm"><Plus size={11} /> Connect</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── PROTOCOL HUB ROOT ──────────────────────────────────────────────────────────
export default function ProtocolHub() {
  const [proto, setProto] = useState<Proto>('nostr')
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  const PROTOS: Array<{ key: Proto; label: string; icon: React.ReactNode; desc: string }> = [
    { key: 'nostr',      label: 'Nostr',      icon: <Radio size={14} />,   desc: 'Decentralised social protocol' },
    { key: 'meshtastic', label: 'Meshtastic', icon: <Wifi size={14} />,    desc: 'LoRa mesh radio networking'    },
    { key: 'freenet',    label: 'Freenet',    icon: <Globe size={14} />,   desc: 'Censorship-resistant datastore' },
  ]

  return (
    <div style={{ padding: '16px 20px', maxWidth: 860, margin: '0 auto' }}>
      {/* Protocol selector */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 28, flexWrap: 'wrap' }}>
        {PROTOS.map(p => (
          <button key={p.key} onClick={() => setProto(p.key)}
            className={`btn ${proto === p.key ? 'btn-primary' : 'btn-ghost'}`}
            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px' }}>
            {p.icon}
            <span style={{ fontWeight: 700 }}>{p.label}</span>
            <span style={{ fontSize: 10, color: proto === p.key ? 'rgba(255,255,255,0.7)' : 'var(--muted)' }}>{p.desc}</span>
          </button>
        ))}
      </div>

      {proto === 'nostr'      && <NostrCenter      apiKey={apiKey} />}
      {proto === 'meshtastic' && <MeshtasticCenter apiKey={apiKey} />}
      {proto === 'freenet'    && <FreenetCenter    apiKey={apiKey} />}
    </div>
  )
}
