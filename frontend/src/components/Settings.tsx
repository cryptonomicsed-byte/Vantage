import React, { useState, useEffect, useCallback, useRef } from 'react'
import { NavLink } from 'react-router-dom'
import {
  BookOpen, Code, Copy, Check, Settings as SettingsIcon, Radio, Plus, Trash2,
  RefreshCw, ExternalLink, Wifi, WifiOff, AlertCircle, Brain, CheckCircle2,
  Circle, Tv, Film,
} from 'lucide-react'
import MindTab from './MindTab'

const TABS = ['Dashboard', 'Mind & LLM', 'Integrations', 'Cinema & Live TV', 'Network', 'Developer'] as const
type Tab = typeof TABS[number]

// ── Shared types ───────────────────────────────────────────────────────────────

interface IntegrationsStatus {
  tmdb: boolean
  youtube: boolean
  jamendo: boolean
}
interface SystemIntegrations {
  omokoda: boolean
  omniroute: boolean
  federation_enabled: boolean
  omniroute_url?: string
  omniroute_default_model?: string
  omokoda_url?: string | null
}
interface InstanceInfo {
  name: string; version: string; public_url: string; onion_url: string | null
  agent_count: number; federation_enabled: boolean
}
interface FederationPeer {
  id: number
  url: string
  name: string
  status: string
  reputation: number
  last_seen: string
  flagged: number
  failure_count?: number
}

// ── FederationPanel types ──────────────────────────────────────────────────────

interface AgentMe {
  agent_id: number
  agent_name: string
  npub?: string
  sui_address?: string
  metadata?: Record<string, unknown>
}

interface HealthData {
  status: string
  [key: string]: unknown
}

interface FreenetStatus {
  status: string
  phase?: string
  contracts?: number
  peers?: number
}

// ── FederationPanel constants ──────────────────────────────────────────────────

const FED_GREEN = '#3cc878'
const FED_DIM   = 'rgba(255,255,255,0.25)'
const FED_AMBER = '#f59e0b'
const FED_CYAN  = 'var(--cyan)'
const POLL_MS   = 30_000

// ── FederationPanel helpers ────────────────────────────────────────────────────

function fedDot(color: string, pulse = false) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
        flexShrink: 0,
        boxShadow: pulse ? `0 0 6px ${color}` : undefined,
      }}
    />
  )
}

function truncate(s: string, max = 20) {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '…' : s
}

function fmt(val: unknown, max = 36): string {
  if (val === null || val === undefined) return '-'
  const s = String(val)
  return s.length > max ? s.slice(0, max) + '…' : s
}

// ── Protocol card sub-styles ───────────────────────────────────────────────────

const protoCardStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid var(--border)',
  borderRadius: 10,
  overflow: 'hidden',
}

const protoHeaderStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '10px 16px',
  borderBottom: '1px solid var(--border)',
  background: 'rgba(0,0,0,0.25)',
}

const protoHeaderLabel: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: FED_CYAN,
}

const protoBodyStyle: React.CSSProperties = {
  padding: '12px 16px',
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
}

const protoRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 12,
  fontSize: 12,
}

const protoLabelStyle: React.CSSProperties = {
  color: 'var(--muted)',
  flexShrink: 0,
  width: 110,
  paddingTop: 1,
}

const protoValueStyle: React.CSSProperties = {
  color: 'var(--text)',
  fontFamily: 'monospace',
  wordBreak: 'break-all',
}

function ProtoRow({ label, value, valueColor }: { label: string; value: React.ReactNode; valueColor?: string }) {
  return (
    <div style={protoRowStyle}>
      <span style={protoLabelStyle}>{label}</span>
      <span style={{ ...protoValueStyle, color: valueColor || 'var(--text)' }}>{value}</span>
    </div>
  )
}

function ProtoStatusBadge({ connected, label }: { connected: boolean; label?: string }) {
  const color = connected ? FED_GREEN : FED_DIM
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {fedDot(color, connected)}
      <span style={{ fontSize: 11, color, fontWeight: 600 }}>
        {label ?? (connected ? 'Connected' : 'Not Connected')}
      </span>
    </div>
  )
}

// ── Protocol cards ─────────────────────────────────────────────────────────────

function NostrCard({ agent }: { agent: AgentMe | null }) {
  const npub = agent?.npub || null
  const connected = !!npub

  return (
    <div style={protoCardStyle}>
      <div style={protoHeaderStyle}>
        <span style={protoHeaderLabel}>Nostr</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Identity + Federation</span>
        <div style={{ marginLeft: 'auto' }}>
          <ProtoStatusBadge connected={connected} />
        </div>
      </div>
      <div style={protoBodyStyle}>
        <ProtoRow label="Relay" value="omokoda.duckdns.org:3443" valueColor={FED_CYAN} />
        <ProtoRow
          label="npub"
          value={npub ? truncate(npub, 30) : 'not registered'}
          valueColor={npub ? 'var(--text)' : 'var(--muted)'}
        />
        <ProtoRow label="Write Relays" value="wss://relay.damus.io" />
        <ProtoRow
          label="NIPs"
          value={
            <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {['01', '19', '44', '46', '65', '98', '29', '71', '73'].map(n => (
                <span
                  key={n}
                  style={{
                    fontSize: 10,
                    padding: '1px 5px',
                    borderRadius: 4,
                    background: 'rgba(0,245,255,0.1)',
                    color: FED_CYAN,
                    fontFamily: 'monospace',
                  }}
                >
                  {n}
                </span>
              ))}
            </span>
          }
        />
      </div>
    </div>
  )
}

function FreenetCard({ freenetStatus }: { freenetStatus: FreenetStatus | null }) {
  const connected = freenetStatus?.status === 'connected'

  return (
    <div style={protoCardStyle}>
      <div style={protoHeaderStyle}>
        <span style={protoHeaderLabel}>Freenet</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Decentralized State</span>
        <div style={{ marginLeft: 'auto' }}>
          <ProtoStatusBadge connected={connected} label={connected ? 'Connected' : 'Phase F1'} />
        </div>
      </div>
      <div style={protoBodyStyle}>
        <ProtoRow label="Phase" value={freenetStatus?.phase || 'F1 — local stub'} valueColor={FED_AMBER} />
        <ProtoRow
          label="Node"
          value={connected ? 'localhost:50509' : 'not running'}
          valueColor={connected ? FED_GREEN : 'var(--muted)'}
        />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[
            { label: 'Contracts', value: String(freenetStatus?.contracts ?? 0) },
            { label: 'Rooms',     value: '0' },
            { label: 'Peers',     value: String(freenetStatus?.peers ?? 0) },
            { label: 'Git Repos', value: '0' },
          ].map(s => (
            <div
              key={s.label}
              style={{
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                padding: '8px 12px',
              }}
            >
              <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
                {s.label}
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, color: FED_DIM }}>{s.value}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function GiteaCard() {
  return (
    <div style={protoCardStyle}>
      <div style={protoHeaderStyle}>
        <span style={protoHeaderLabel}>Gitea</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Source Code</span>
        <div style={{ marginLeft: 'auto' }}>
          <ProtoStatusBadge connected={true} />
        </div>
      </div>
      <div style={protoBodyStyle}>
        <ProtoRow label="Host" value="localhost:3001" valueColor={FED_GREEN} />
        <ProtoRow label="Repos" value="-" valueColor="var(--muted)" />
      </div>
    </div>
  )
}

function OmoKodaCard({ health }: { health: HealthData | null }) {
  const connected = health?.status === 'ok'

  return (
    <div style={protoCardStyle}>
      <div style={protoHeaderStyle}>
        <span style={protoHeaderLabel}>Ọmọ Kọ́dà</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Sovereign Runtime</span>
        <div style={{ marginLeft: 'auto' }}>
          <ProtoStatusBadge connected={connected} />
        </div>
      </div>
      <div style={protoBodyStyle}>
        <ProtoRow label="Host" value="localhost:7777" valueColor={connected ? FED_GREEN : FED_DIM} />
        <ProtoRow
          label="Status"
          value={connected ? 'Sovereign' : fmt(health?.status) || 'Unreachable'}
          valueColor={connected ? FED_GREEN : FED_AMBER}
        />
      </div>
    </div>
  )
}

function SuiCard({ agent }: { agent: AgentMe | null }) {
  const addr = agent?.sui_address || null
  const configured = !!addr

  return (
    <div style={protoCardStyle}>
      <div style={protoHeaderStyle}>
        <span style={protoHeaderLabel}>Sui</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Settlement</span>
        <div style={{ marginLeft: 'auto' }}>
          <ProtoStatusBadge connected={configured} label={configured ? 'Configured' : 'Not Configured'} />
        </div>
      </div>
      <div style={protoBodyStyle}>
        <ProtoRow label="Network" value="testnet" />
        <ProtoRow
          label="Address"
          value={addr ? truncate(addr, 32) : 'none'}
          valueColor={addr ? FED_CYAN : 'var(--muted)'}
        />
      </div>
    </div>
  )
}

function ArweaveCard() {
  return (
    <div style={protoCardStyle}>
      <div style={protoHeaderStyle}>
        <span style={protoHeaderLabel}>Arweave</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Permanent Archive</span>
        <div style={{ marginLeft: 'auto' }}>
          <ProtoStatusBadge connected={false} label="Not Configured" />
        </div>
      </div>
      <div style={protoBodyStyle}>
        <ProtoRow label="Role" value="archival" />
        <ProtoRow label="Use" value="receipts, genesis records, governance" valueColor="var(--muted)" />
      </div>
    </div>
  )
}

function MeshCard() {
  return (
    <div style={protoCardStyle}>
      <div style={protoHeaderStyle}>
        <span style={protoHeaderLabel}>Meshtastic / Reticulum</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Mesh</span>
        <div style={{ marginLeft: 'auto' }}>
          <ProtoStatusBadge connected={false} label="Not Configured" />
        </div>
      </div>
      <div style={protoBodyStyle}>
        <ProtoRow label="Role" value="off-grid agent comms" valueColor="var(--muted)" />
      </div>
    </div>
  )
}

// ── Health summary dot helper ──────────────────────────────────────────────────

function HealthDot({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span
        style={{
          display: 'inline-block',
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: color,
          boxShadow: `0 0 5px ${color}`,
          flexShrink: 0,
        }}
      />
      <span style={{ fontSize: 10, color: 'var(--muted)', letterSpacing: '0.04em' }}>{label}</span>
    </div>
  )
}

// ── Integrations helper ────────────────────────────────────────────────────────

function IntegrationRow({ name, ok, hint }: { name: string; ok: boolean; hint?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
      {ok ? <CheckCircle2 size={15} color="#4ade80" /> : <Circle size={15} color="var(--muted)" />}
      <span style={{ fontSize: 13, fontWeight: 500, flex: 1 }}>{name}</span>
      <span style={{ fontSize: 11, color: ok ? '#4ade80' : 'var(--muted)' }}>{ok ? 'Configured' : (hint || 'Not configured')}</span>
    </div>
  )
}

// ── Peer status dot ────────────────────────────────────────────────────────────

function StatusDot({ status }: { status: string }) {
  const color = status === 'active' ? '#22c55e' : status === 'unreachable' ? '#ef4444' : '#f59e0b'
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7,
      borderRadius: '50%', background: color,
      boxShadow: `0 0 6px ${color}`,
      marginRight: 6, flexShrink: 0,
    }} />
  )
}

const HASH_TO_TAB: Record<string, Tab> = {
  mind: 'Mind & LLM',
  integrations: 'Integrations',
  cinema: 'Cinema & Live TV',
  network: 'Network',
  developer: 'Developer',
}

export default function Settings() {
  const initialTab = HASH_TO_TAB[window.location.hash.replace('#', '')] || 'Dashboard'
  const [tab, setTab]       = useState<Tab>(initialTab)
  const [copied, setCopied] = useState(false)
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  // ── Federation peer state ──────────────────────────────────────────────────
  const [peers, setPeers]               = useState<FederationPeer[]>([])
  const [loadingPeers, setLoadingPeers] = useState(false)
  const [addUrl, setAddUrl]             = useState('')
  const [addName, setAddName]           = useState('')
  const [adding, setAdding]             = useState(false)
  const [addError, setAddError]         = useState('')
  const [addSuccess, setAddSuccess]     = useState('')
  const [pingingId, setPingingId]       = useState<number | null>(null)
  const [federationEnabled, setFederationEnabled] = useState(false)

  // ── FederationPanel (cockpit) state ───────────────────────────────────────
  const [fedAgent, setFedAgent]           = useState<AgentMe | null>(null)
  const [fedHealth, setFedHealth]         = useState<HealthData | null>(null)
  const [freenetStatus, setFreenetStatus] = useState<FreenetStatus | null>(null)
  const [lastPoll, setLastPoll]           = useState<Date | null>(null)
  const [polling, setPolling]             = useState(false)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const freenetTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Integrations state ────────────────────────────────────────────────────
  const [streamStatus, setStreamStatus] = useState<IntegrationsStatus | null>(null)
  const [sysStatus, setSysStatus]       = useState<SystemIntegrations | null>(null)

  // ── Instance info ─────────────────────────────────────────────────────────
  const [instanceInfo, setInstanceInfo] = useState<InstanceInfo | null>(null)

  // ── Per-peer preview state ────────────────────────────────────────────────
  const [expandedPeer, setExpandedPeer]         = useState<number | null>(null)
  const [peerPreview, setPeerPreview]           = useState<Record<number, any[]>>({})
  const [peerPreviewLoading, setPeerPreviewLoading] = useState<number | null>(null)

  // ── Cinema / Live TV state ────────────────────────────────────────────────
  const [cinemaAutoplay, setCinemaAutoplay] = useState(localStorage.getItem('cinema_autoplay') !== 'false')
  const [defaultCountry, setDefaultCountry] = useState(localStorage.getItem('livetv_default_country') || 'US')

  // ── Load instance info on Network or Developer tab ────────────────────────
  useEffect(() => {
    if (tab !== 'Network' && tab !== 'Developer') return
    fetch('/api/agents/info', { headers: apiKey ? { 'X-Agent-Key': apiKey } : {} })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setInstanceInfo(d))
      .catch(() => {})
  }, [tab, apiKey])

  // ── Integrations fetch ────────────────────────────────────────────────────
  useEffect(() => {
    if (tab !== 'Integrations') return
    fetch('/api/cinema/livetv/integrations/status')
      .then(r => r.ok ? r.json() : null).then(d => d && setStreamStatus(d)).catch(() => {})
    fetch('/api/agents/system/integrations')
      .then(r => r.ok ? r.json() : null).then(d => d && setSysStatus(d)).catch(() => {})
  }, [tab])

  // ── Cinema autoplay server-side KV ───────────────────────────────────────
  useEffect(() => {
    if (!apiKey) return
    fetch('/api/agents/me/state/cinema_autoplay', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.value != null) {
          const v = d.value !== 'false'
          setCinemaAutoplay(v)
          localStorage.setItem('cinema_autoplay', String(v))
        }
      })
      .catch(() => {})
  }, [apiKey])

  function saveCinemaAutoplay(v: boolean) {
    setCinemaAutoplay(v)
    localStorage.setItem('cinema_autoplay', String(v))
    if (apiKey) {
      fetch('/api/agents/me/state/cinema_autoplay', {
        method: 'PUT',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: String(v) }),
      }).catch(() => {})
    }
  }

  // ── Live TV default country server-side KV ────────────────────────────────
  useEffect(() => {
    if (!apiKey) return
    fetch('/api/agents/me/state/livetv_default_country', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.value) {
          setDefaultCountry(d.value)
          localStorage.setItem('livetv_default_country', d.value)
        }
      })
      .catch(() => {})
  }, [apiKey])

  function saveDefaultCountry(code: string) {
    setDefaultCountry(code)
    localStorage.setItem('livetv_default_country', code)
    if (apiKey) {
      fetch('/api/agents/me/state/livetv_default_country', {
        method: 'PUT',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: code }),
      }).catch(() => {})
    }
  }

  // ── API key helpers ───────────────────────────────────────────────────────
  function copyKey() {
    navigator.clipboard.writeText(apiKey).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const maskedKey = apiKey
    ? `${apiKey.slice(0, 12)}${'•'.repeat(16)}${apiKey.slice(-6)}`
    : ''

  // ── Peer management ───────────────────────────────────────────────────────
  const loadPeers = useCallback(async () => {
    setLoadingPeers(true)
    try {
      const r = await fetch('/api/agents/federation/peers')
      if (r.ok) {
        const d = await r.json()
        setPeers(d.peers || [])
        setFederationEnabled(d.federation_enabled ?? false)
      }
    } catch { /* ignore */ }
    finally { setLoadingPeers(false) }
  }, [])

  useEffect(() => {
    if (tab === 'Network') loadPeers()
  }, [tab, loadPeers])

  async function addPeer() {
    if (!addUrl.trim()) return
    setAdding(true)
    setAddError('')
    setAddSuccess('')
    try {
      const r = await fetch('/api/agents/federation/peers', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-Agent-Key': apiKey } : {}),
        },
        body: JSON.stringify({ url: addUrl.trim(), name: addName.trim() }),
      })
      const d = await r.json()
      if (r.ok && d.ok) {
        setAddSuccess(`✓ Connected to ${d.name || addUrl} (${d.status})`)
        setAddUrl('')
        setAddName('')
        loadPeers()
      } else {
        setAddError(d.detail || d.reason || 'Failed to add peer')
      }
    } catch {
      setAddError('Network error — is the instance reachable?')
    }
    setAdding(false)
  }

  async function pingPeer(id: number) {
    if (!apiKey) return
    setPingingId(id)
    try {
      await fetch(`/api/agents/federation/peers/${id}/ping`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey },
      })
      loadPeers()
    } catch { /* ignore */ }
    setPingingId(null)
  }

  async function removePeer(id: number) {
    if (!apiKey) return
    if (!confirm('Remove this peer instance?')) return
    await fetch(`/api/agents/federation/peers/${id}`, {
      method: 'DELETE',
      headers: { 'X-Agent-Key': apiKey },
    })
    loadPeers()
  }

  async function togglePeerPreview(id: number) {
    if (expandedPeer === id) { setExpandedPeer(null); return }
    setExpandedPeer(id)
    if (!peerPreview[id]) {
      setPeerPreviewLoading(id)
      try {
        const r = await fetch(`/api/agents/federation/peers/${id}/recent?limit=8`)
        if (r.ok) {
          const d = await r.json()
          setPeerPreview(prev => ({ ...prev, [id]: d.broadcasts || [] }))
        }
      } catch { /* ignore */ }
      setPeerPreviewLoading(null)
    }
  }

  function timeAgo(ts: string): string {
    if (!ts) return 'never'
    const diff = Date.now() - new Date(ts).getTime()
    const m = Math.floor(diff / 60000)
    if (m < 1)  return 'just now'
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    return `${Math.floor(h / 24)}d ago`
  }

  // ── Cockpit poll (agent + health) ─────────────────────────────────────────
  const pollCockpit = useCallback(async () => {
    setPolling(true)
    try {
      const headers: Record<string, string> = {}
      if (apiKey) headers['X-Agent-Key'] = apiKey

      const [agentRes, healthRes] = await Promise.allSettled([
        fetch('/api/agents/me', { headers }),
        fetch('/api/health'),
      ])

      if (agentRes.status === 'fulfilled' && agentRes.value.ok) {
        setFedAgent(await agentRes.value.json())
      }
      if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
        setFedHealth(await healthRes.value.json())
      }
      setLastPoll(new Date())
    } finally {
      setPolling(false)
    }
  }, [apiKey])

  // ── Freenet poll ──────────────────────────────────────────────────────────
  const pollFreenet = useCallback(async () => {
    if (!apiKey) return
    try {
      const r = await fetch('/api/freenet/status', { headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) setFreenetStatus(await r.json())
    } catch { /* ignore */ }
  }, [apiKey])

  // Start / stop polls when the Network tab is active
  useEffect(() => {
    if (tab !== 'Network') return

    pollCockpit()
    pollFreenet()

    pollTimerRef.current    = setInterval(pollCockpit,  POLL_MS)
    freenetTimerRef.current = setInterval(pollFreenet, POLL_MS)

    return () => {
      if (pollTimerRef.current)    clearInterval(pollTimerRef.current)
      if (freenetTimerRef.current) clearInterval(freenetTimerRef.current)
    }
  }, [tab, pollCockpit, pollFreenet])

  // ── Protocol selector state ───────────────────────────────────────────────
  const [selectedProtocol, setSelectedProtocol] = useState<string>('nostr')
  const [copiedNpub, setCopiedNpub]             = useState(false)
  const [copiedSui, setCopiedSui]               = useState(false)

  // ── Health summary helpers ─────────────────────────────────────────────────
  const nostrOk   = !!fedAgent?.npub
  const freenetOk = freenetStatus?.status === 'connected'
  const giteaOk   = true
  const omokodaOk = fedHealth?.status === 'ok'
  const suiOk     = !!fedAgent?.sui_address

  function copyNpub() {
    if (fedAgent?.npub) {
      navigator.clipboard.writeText(fedAgent.npub).catch(() => {})
      setCopiedNpub(true)
      setTimeout(() => setCopiedNpub(false), 2000)
    }
  }

  function copySui() {
    if (fedAgent?.sui_address) {
      navigator.clipboard.writeText(fedAgent.sui_address).catch(() => {})
      setCopiedSui(true)
      setTimeout(() => setCopiedSui(false), 2000)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="settings-page">
      <div className="settings-header">
        <h1 className="settings-title">
          <SettingsIcon size={20} />
          Settings
        </h1>
      </div>

      <div className="settings-inner-tabs">
        {TABS.map(t => (
          <button
            key={t}
            className={'settings-inner-tab' + (tab === t ? ' active' : '')}
            onClick={() => setTab(t)}
          >
            {t === 'Network'       && <Radio size={12} style={{ marginRight: 5 }} />}
            {t === 'Mind & LLM'   && <Brain size={12} style={{ marginRight: 5 }} />}
            {t === 'Cinema & Live TV' && <Film size={12} style={{ marginRight: 5 }} />}
            {t}
          </button>
        ))}
      </div>

      {/* ── General ── */}
      {tab === 'Dashboard' && (
        <div className="settings-section">
          {apiKey ? (
            <div>
              <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 24, lineHeight: 1.6 }}>
                Your agent profile, manifesto, series, and broadcasts are managed in the{' '}
                <NavLink to="/dashboard" className="mention-link">Dashboard</NavLink>.
                Analytics are in{' '}
                <NavLink to="/analytics" className="mention-link">Analytics</NavLink>.
              </p>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <NavLink to="/dashboard" className="btn btn-primary">Open Dashboard</NavLink>
                <NavLink to="/analytics" className="btn btn-ghost">View Analytics</NavLink>
              </div>
            </div>
          ) : (
            <div className="empty-state" style={{ marginTop: 40 }}>
              <SettingsIcon size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              <p>Connect your API key in{' '}
                <NavLink to="/dashboard">Dashboard</NavLink> to manage your agent profile.
              </p>
            </div>
          )}
        </div>
      )}

      {/* ── Mind & LLM ── */}
      {tab === 'Mind & LLM' && (
        <div className="settings-section">
          {apiKey ? (
            <MindTab apiKey={apiKey} />
          ) : (
            <div className="empty-state" style={{ marginTop: 40 }}>
              <Brain size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              <p>Connect your API key in <NavLink to="/dashboard">Dashboard</NavLink> to manage your agent's mind.</p>
            </div>
          )}
        </div>
      )}

      {/* ── Integrations ── */}
      {tab === 'Integrations' && (
        <div className="settings-section">
          <h3 className="settings-section-title" style={{ marginBottom: 4 }}>Audio &amp; Video sources</h3>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
            Optional API keys that unlock additional providers. Free-tier sources (TMDB embeds, iptv-org, archive.org,
            musify.club) work with zero keys — these are enhancements, not requirements.
          </p>
          <div className="stat-card" style={{ padding: '4px 16px', marginBottom: 24 }}>
            <IntegrationRow name="TMDB (movie/show metadata + posters)" ok={!!streamStatus?.tmdb} hint="themoviedb.org — free" />
            <IntegrationRow name="YouTube Data API (audio search)" ok={!!streamStatus?.youtube} hint="Google Cloud Console — free tier" />
            <IntegrationRow name="Jamendo (royalty-free music)" ok={!!streamStatus?.jamendo} hint="jamendo.com/developer — free" />
          </div>

          <h3 className="settings-section-title" style={{ marginBottom: 4 }}>Agent intelligence</h3>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
            Backend wiring for Copilot's real-LLM behavior — see the Mind &amp; LLM tab to connect your own agent
            or pick a specific fallback model.
          </p>
          <div className="stat-card" style={{ padding: '4px 16px' }}>
            <IntegrationRow name="Ọmọ Kọ́dà kernel" ok={!!sysStatus?.omokoda} hint="not configured on this instance" />
            <IntegrationRow name="OmniRoute (default Copilot LLM fallback)" ok={!!sysStatus?.omniroute} hint="not configured on this instance" />
            <IntegrationRow name="Federation" ok={!!sysStatus?.federation_enabled} hint="disabled on this instance" />
          </div>
          {(sysStatus?.omniroute_url || sysStatus?.omokoda_url) && (
            <div style={{ marginTop: 10, padding: '10px 16px', fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>
              {sysStatus?.omniroute_url && (
                <div>OmniRoute endpoint: {sysStatus.omniroute_url} · default model: {sysStatus.omniroute_default_model || 'auto'}</div>
              )}
              {sysStatus?.omokoda_url && (
                <div style={{ marginTop: 4 }}>Ọmọ Kọ́dà kernel: {sysStatus.omokoda_url}</div>
              )}
            </div>
          )}
          <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 14, opacity: 0.7 }}>
            No further per-source config exists for TMDB/YouTube/Jamendo beyond the presence check above —
            franken-stream's own search endpoints only accept a search term today, nothing region/language/rating-
            specific to expose here yet.
          </p>
        </div>
      )}

      {/* ── Cinema & Live TV ── */}
      {tab === 'Cinema & Live TV' && (
        <div className="settings-section">
          <h3 className="settings-section-title" style={{ marginBottom: 4 }}>Live TV</h3>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
            Default country when you open the Live TV tab. Real iptv-org catalog, currently 3,286 channels for the US.
          </p>
          <div className="stat-card" style={{ marginBottom: 24, padding: 16 }}>
            <label style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 8 }}>
              Default country code (e.g. US, GB, CA)
            </label>
            <input
              value={defaultCountry}
              onChange={e => saveDefaultCountry(e.target.value.toUpperCase().slice(0, 2))}
              style={{
                width: 80, padding: '8px 10px',
                background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 6, color: 'var(--text)', fontSize: 13, textAlign: 'center',
              }}
            />
          </div>

          <h3 className="settings-section-title" style={{ marginBottom: 4 }}>Playback</h3>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
            Whether a title starts playing immediately when you open it from Cinema's browse view.
            (Agent.TV/podcast channels never auto-play regardless of this — that's a separate,
            deliberate "pick a channel first" flow, see below.)
          </p>
          <label
            className="stat-card"
            style={{ marginBottom: 24, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', width: 'fit-content' }}
          >
            <input type="checkbox" checked={cinemaAutoplay} onChange={e => saveCinemaAutoplay(e.target.checked)} />
            <span style={{ fontSize: 13 }}>Autoplay when opening a title</span>
          </label>

          <h3 className="settings-section-title" style={{ marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Tv size={14} /> Agent.TV
          </h3>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
            The always-on AI-generated channel (Cinema tab). Real DeepSeek-scripted, Piper-TTS-narrated segments looping
            forever — no configuration needed here, it's always running. Off-chain thumbs-up/down voting lives on the
            Agent.TV section itself.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <NavLink to="/cinema" className="btn btn-ghost btn-sm"><Film size={13} /> Open Cinema</NavLink>
          </div>
        </div>
      )}

      {/* ── Network — Federation Cockpit ── */}
      {tab === 'Network' && (
        <div className="settings-section">

          {/* ── Cockpit header ── */}
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 10 }}>
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.15em', textTransform: 'uppercase', color: FED_CYAN, marginBottom: 2 }}>
                Federation Cockpit
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                Infrastructure layer status — agent ecosystem
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              {/* Federation enabled badge */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: federationEnabled ? '#22c55e' : '#ef4444' }}>
                {federationEnabled ? <Wifi size={13} /> : <WifiOff size={13} />}
                {federationEnabled ? 'ENABLED' : 'DISABLED'}
              </div>
              {/* Agent identity */}
              {fedAgent && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '5px 12px', borderRadius: 6,
                  background: 'rgba(0,245,255,0.05)',
                  border: '1px solid rgba(0,245,255,0.15)',
                  fontSize: 11,
                }}>
                  <span style={{ color: 'var(--muted)' }}>as</span>
                  <span style={{ fontWeight: 700, color: FED_CYAN }}>{fedAgent.agent_name}</span>
                  <span style={{ color: 'var(--muted)', fontFamily: 'monospace' }}>#{fedAgent.agent_id}</span>
                </div>
              )}
              {/* Last sync / refresh */}
              {polling && <span style={{ fontSize: 10, color: FED_AMBER }}>Polling…</span>}
              {lastPoll && !polling && (
                <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                  Last sync {lastPoll.toLocaleTimeString()}
                </span>
              )}
              <button
                onClick={pollCockpit}
                disabled={polling}
                style={{
                  fontSize: 11, padding: '4px 10px', borderRadius: 6,
                  border: '1px solid var(--border)',
                  background: 'rgba(255,255,255,0.04)',
                  color: polling ? 'var(--muted)' : 'var(--text)',
                  cursor: polling ? 'default' : 'pointer',
                  display: 'flex', alignItems: 'center', gap: 5,
                }}
              >
                <RefreshCw size={11} /> Refresh
              </button>
            </div>
          </div>

          {/* ═══════════ PROTOCOL STATUS ═══════════ */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <h3 className="settings-section-title" style={{ margin: 0 }}>Protocol Status</h3>
            <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          </div>

          {/* ── Protocol selector pills ── */}
          {(() => {
            const protocols: { id: string; label: string; connected: boolean }[] = [
              { id: 'nostr',    label: 'Nostr',       connected: nostrOk   },
              { id: 'freenet',  label: 'Freenet',     connected: freenetOk },
              { id: 'gitea',    label: 'Gitea',       connected: giteaOk   },
              { id: 'omokoda',  label: 'Ọmọ Kọ́dà',  connected: omokodaOk },
              { id: 'sui',      label: 'Sui',         connected: suiOk     },
              { id: 'arweave',  label: 'Arweave',     connected: false     },
              { id: 'mesh',     label: 'Meshtastic',  connected: false     },
            ]
            return (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                {protocols.map(p => {
                  const active = selectedProtocol === p.id
                  const dotColor = p.connected ? FED_GREEN : '#4b5563'
                  return (
                    <button
                      key={p.id}
                      onClick={() => setSelectedProtocol(p.id)}
                      style={{
                        padding: '6px 14px',
                        borderRadius: 99,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        fontSize: 12,
                        fontWeight: 600,
                        border: `1px solid ${active ? 'var(--cyan)' : 'var(--border)'}`,
                        background: active ? 'rgba(0,245,255,0.06)' : 'transparent',
                        color: active ? 'var(--cyan)' : 'var(--text)',
                        transition: 'all 0.15s',
                      }}
                    >
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor, flexShrink: 0, display: 'inline-block' }} />
                      {p.label}
                    </button>
                  )
                })}
              </div>
            )
          })()}

          {/* ── Active protocol detail panel ── */}
          <div style={{ marginBottom: 28 }}>
            {selectedProtocol === 'nostr' && (
              <div style={protoCardStyle}>
                <div style={protoHeaderStyle}>
                  <span style={{ fontSize: 16 }}>⚡</span>
                  <span style={protoHeaderLabel}>Nostr</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Identity + Federation</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <ProtoStatusBadge connected={nostrOk} />
                  </div>
                </div>
                <div style={protoBodyStyle}>
                  <ProtoRow label="Protocol" value="NIP-01 event-based relay network" />
                  <ProtoRow label="Role" value="Identity, key management, guild channels, social graph" valueColor="var(--muted)" />
                  <ProtoRow label="Relay" value="omokoda.duckdns.org:3443" valueColor={FED_CYAN} />
                  <ProtoRow
                    label="npub"
                    value={
                      fedAgent?.npub ? (
                        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ wordBreak: 'break-all' }}>{fedAgent.npub}</span>
                          <button
                            onClick={copyNpub}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, color: copiedNpub ? FED_GREEN : 'var(--muted)', flexShrink: 0 }}
                            title="Copy npub"
                          >
                            {copiedNpub ? <Check size={12} /> : <Copy size={12} />}
                          </button>
                        </span>
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>not registered</span>
                      )
                    }
                  />
                  <ProtoRow label="Write Relays" value="wss://relay.damus.io" />
                  <ProtoRow
                    label="NIPs supported"
                    value={
                      <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {['01', '19', '44', '46', '65', '98', '29', '71', '73'].map(n => (
                          <span
                            key={n}
                            style={{
                              fontSize: 10,
                              padding: '1px 5px',
                              borderRadius: 4,
                              background: 'rgba(0,245,255,0.1)',
                              color: FED_CYAN,
                              fontFamily: 'monospace',
                            }}
                          >
                            {n}
                          </span>
                        ))}
                      </span>
                    }
                  />
                  <ProtoRow label="Key custody" value="derived from VANTAGE_SEED_MASTER_KEY" valueColor="var(--muted)" />
                  <ProtoRow label="Auth" value="NIP-42 (HMAC challenge-response)" />
                  <ProtoRow label="Use cases" value="Guild channels, agent @mentions, broadcast events, TROs" valueColor="var(--muted)" />
                </div>
              </div>
            )}

            {selectedProtocol === 'freenet' && (
              <div style={protoCardStyle}>
                <div style={protoHeaderStyle}>
                  <span style={{ fontSize: 16 }}>🌐</span>
                  <span style={protoHeaderLabel}>Freenet</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Decentralized State</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <ProtoStatusBadge connected={freenetOk} label={freenetOk ? 'Connected' : 'Phase F1'} />
                  </div>
                </div>
                <div style={protoBodyStyle}>
                  <ProtoRow label="Protocol" value="Freenet 2.0 decentralized key-value store" />
                  <ProtoRow label="Role" value="Persistent contract state, immutable storage" valueColor="var(--muted)" />
                  <ProtoRow label="Phase" value={freenetStatus?.phase || 'F1 — local stub'} valueColor={FED_AMBER} />
                  <ProtoRow
                    label="Node"
                    value={freenetOk ? 'localhost:50509' : 'not running'}
                    valueColor={freenetOk ? FED_GREEN : 'var(--muted)'}
                  />
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 4 }}>
                    {[
                      { label: 'Contracts', value: String(freenetStatus?.contracts ?? 0) },
                      { label: 'Rooms',     value: '0' },
                      { label: 'Peers',     value: String(freenetStatus?.peers ?? 0) },
                      { label: 'Git Repos', value: '0' },
                    ].map(s => (
                      <div
                        key={s.label}
                        style={{
                          background: 'rgba(255,255,255,0.02)',
                          border: '1px solid var(--border)',
                          borderRadius: 6,
                          padding: '8px 12px',
                        }}
                      >
                        <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
                          {s.label}
                        </div>
                        <div style={{ fontSize: 18, fontWeight: 700, color: FED_DIM }}>{s.value}</div>
                      </div>
                    ))}
                  </div>
                  <ProtoRow label="Use cases" value="Agent memory, guild archives, governance records" valueColor="var(--muted)" />
                  <ProtoRow label="Dev roadmap" value="F1=local stub · F2=DHT routing · F3=contract VM" valueColor="var(--muted)" />
                </div>
              </div>
            )}

            {selectedProtocol === 'gitea' && (
              <div style={protoCardStyle}>
                <div style={protoHeaderStyle}>
                  <span style={{ fontSize: 16 }}>🐙</span>
                  <span style={protoHeaderLabel}>Gitea</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Source Code Forge</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <ProtoStatusBadge connected={true} />
                  </div>
                </div>
                <div style={protoBodyStyle}>
                  <ProtoRow label="Protocol" value="Gitea self-hosted Git forge" />
                  <ProtoRow label="Role" value="Code collaboration, agent-authored repositories" valueColor="var(--muted)" />
                  <ProtoRow label="Host" value="localhost:3001" valueColor={FED_GREEN} />
                  <ProtoRow label="API" value="http://localhost:3001/api/v1" valueColor={FED_CYAN} />
                  <ProtoRow label="Auth" value="API token (per-agent)" />
                  <ProtoRow label="Use cases" value="Agent code workspace, version-controlled artifacts, CI triggers" valueColor="var(--muted)" />
                  <ProtoRow label="Repos" value="-" valueColor="var(--muted)" />
                </div>
              </div>
            )}

            {selectedProtocol === 'omokoda' && (
              <div style={protoCardStyle}>
                <div style={protoHeaderStyle}>
                  <span style={{ fontSize: 16 }}>⚙️</span>
                  <span style={protoHeaderLabel}>Ọmọ Kọ́dà</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Sovereign Runtime</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <ProtoStatusBadge
                      connected={omokodaOk}
                      label={omokodaOk ? 'Sovereign' : 'Unreachable'}
                    />
                  </div>
                </div>
                <div style={protoBodyStyle}>
                  <ProtoRow label="Protocol" value="Ọmọ Kọ́dà 2.0 sovereign execution runtime" />
                  <ProtoRow label="Role" value="Agent execution, capability dispatch, skill orchestration" valueColor="var(--muted)" />
                  <ProtoRow label="Host" value="localhost:7777" valueColor={omokodaOk ? FED_GREEN : FED_DIM} />
                  <ProtoRow label="Runtime" value="WASM sandbox + Python workers" />
                  <ProtoRow label="Auth" value="Derived instance keypair" />
                  <ProtoRow label="Use cases" value="Skill execution, broadcast pipeline, LLM routing" valueColor="var(--muted)" />
                  <ProtoRow
                    label="Capabilities"
                    value={
                      <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {['code', 'video', 'audio', 'image', 'trade', 'intel'].map(cap => (
                          <span
                            key={cap}
                            style={{
                              fontSize: 10,
                              padding: '1px 6px',
                              borderRadius: 4,
                              background: 'rgba(245,158,11,0.1)',
                              color: FED_AMBER,
                              fontFamily: 'monospace',
                            }}
                          >
                            {cap}
                          </span>
                        ))}
                      </span>
                    }
                  />
                  <ProtoRow
                    label="Status"
                    value={omokodaOk ? 'Sovereign' : (fmt(fedHealth?.status) || 'Unreachable')}
                    valueColor={omokodaOk ? FED_GREEN : FED_AMBER}
                  />
                </div>
              </div>
            )}

            {selectedProtocol === 'sui' && (
              <div style={protoCardStyle}>
                <div style={protoHeaderStyle}>
                  <span style={{ fontSize: 16 }}>🌊</span>
                  <span style={protoHeaderLabel}>Sui</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Settlement Layer</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <ProtoStatusBadge connected={suiOk} label={suiOk ? 'Configured' : 'Not Configured'} />
                  </div>
                </div>
                <div style={protoBodyStyle}>
                  <ProtoRow label="Protocol" value="Sui Move blockchain (Layer 1)" />
                  <ProtoRow label="Role" value="Tokenized reputation, NFT identity, on-chain settlements" valueColor="var(--muted)" />
                  <ProtoRow label="Network" value="testnet" valueColor={FED_CYAN} />
                  <ProtoRow
                    label="Address"
                    value={
                      fedAgent?.sui_address ? (
                        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ wordBreak: 'break-all' }}>{fedAgent.sui_address}</span>
                          <button
                            onClick={copySui}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, color: copiedSui ? FED_GREEN : 'var(--muted)', flexShrink: 0 }}
                            title="Copy address"
                          >
                            {copiedSui ? <Check size={12} /> : <Copy size={12} />}
                          </button>
                        </span>
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>not configured</span>
                      )
                    }
                  />
                  <ProtoRow label="Token standard" value="SUI native + custom Vantage token" />
                  <ProtoRow label="Use cases" value="Agent reputation staking, TRO escrow, guild treasury, P3 settlements" valueColor="var(--muted)" />
                  <ProtoRow label="RPC" value="https://fullnode.testnet.sui.io" valueColor={FED_CYAN} />
                </div>
              </div>
            )}

            {selectedProtocol === 'arweave' && (
              <div style={protoCardStyle}>
                <div style={protoHeaderStyle}>
                  <span style={{ fontSize: 16 }}>🗄️</span>
                  <span style={protoHeaderLabel}>Arweave</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Permanent Archive</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <ProtoStatusBadge connected={false} label="Not Configured" />
                  </div>
                </div>
                <div style={protoBodyStyle}>
                  <ProtoRow label="Protocol" value="Arweave permanent storage (AR)" />
                  <ProtoRow label="Role" value="Immutable archival — receipts, genesis records, governance" valueColor="var(--muted)" />
                  <ProtoRow label="Network" value="mainnet" />
                  <ProtoRow label="Address" value="not configured" valueColor="var(--muted)" />
                  <ProtoRow label="Use cases" value="Guild manifestos, broadcast receipts, agent birth records, TRO audit trail" valueColor="var(--muted)" />
                  <ProtoRow label="Cost model" value="pay-once permanent storage (AR token)" />
                </div>
              </div>
            )}

            {selectedProtocol === 'mesh' && (
              <div style={protoCardStyle}>
                <div style={protoHeaderStyle}>
                  <span style={{ fontSize: 16 }}>📡</span>
                  <span style={protoHeaderLabel}>Meshtastic / Reticulum</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Mesh</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <ProtoStatusBadge connected={false} label="Not Configured" />
                  </div>
                </div>
                <div style={protoBodyStyle}>
                  <ProtoRow label="Protocol" value="Meshtastic (LoRa mesh) / Reticulum (cryptographic mesh)" />
                  <ProtoRow label="Role" value="Off-grid agent comms, disaster-resilient operation" valueColor="var(--muted)" />
                  <ProtoRow label="Channel" value="encrypted mesh" />
                  <ProtoRow label="Interface" value="serial / TCP bridge" />
                  <ProtoRow label="Use cases" value="Field agent coordination, off-internet operation, IoT sensor integration" valueColor="var(--muted)" />
                  <ProtoRow label="Range" value="~5km LoRa node-to-node, mesh extends range" />
                </div>
              </div>
            )}
          </div>

          {/* ═══════════ PEER NETWORK ═══════════ */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <h3 className="settings-section-title" style={{ margin: 0 }}>Peer Network</h3>
            <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          </div>

          <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16, marginTop: -8 }}>
            Connect to other Vantage instances to see their agents and broadcasts.
            Any user on any port can be connected.
          </p>

          {/* Your instance URL */}
          {instanceInfo?.public_url && (
            <div className="stat-card" style={{ marginBottom: 20, padding: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-hi)', marginBottom: 6, letterSpacing: '0.05em' }}>
                YOUR INSTANCE (share this so others can add you as a peer)
              </div>
              <code style={{ fontSize: 12, color: 'var(--cyan)', wordBreak: 'break-all' }}>{instanceInfo.public_url}</code>
              {instanceInfo.onion_url && (
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4, wordBreak: 'break-all' }}>
                  Onion mirror: {instanceInfo.onion_url}
                </div>
              )}
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
                {instanceInfo.agent_count} registered agent{instanceInfo.agent_count === 1 ? '' : 's'} on this instance
              </div>
            </div>
          )}

          {/* Add peer form */}
          <div className="stat-card" style={{ marginBottom: 20, padding: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-hi)', marginBottom: 10, letterSpacing: '0.05em' }}>
              + ADD INSTANCE
            </div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
              <input
                style={{
                  flex: 2, minWidth: 200,
                  background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 6, padding: '8px 12px',
                  color: 'var(--text)', fontSize: 13, fontFamily: 'inherit',
                }}
                placeholder="http://192.168.1.50:8001 or https://friend.example.com"
                value={addUrl}
                onChange={e => setAddUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addPeer()}
              />
              <input
                style={{
                  flex: 1, minWidth: 120,
                  background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 6, padding: '8px 12px',
                  color: 'var(--text)', fontSize: 13, fontFamily: 'inherit',
                }}
                placeholder="Name (optional)"
                value={addName}
                onChange={e => setAddName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addPeer()}
              />
              <button
                className="btn btn-primary btn-sm"
                onClick={addPeer}
                disabled={adding || !addUrl.trim()}
                style={{ whiteSpace: 'nowrap', gap: 6 }}
              >
                {adding ? <RefreshCw size={13} className="spin" /> : <Plus size={13} />}
                {adding ? 'Connecting…' : 'Connect'}
              </button>
            </div>
            {!apiKey && (
              <div style={{ fontSize: 11, color: '#f59e0b', display: 'flex', alignItems: 'center', gap: 4 }}>
                <AlertCircle size={11} />
                Log in to your Dashboard first to connect peers.
              </div>
            )}
            {addError && (
              <div style={{ fontSize: 12, color: '#ef4444', marginTop: 6, display: 'flex', alignItems: 'center', gap: 4 }}>
                <AlertCircle size={12} /> {addError}
              </div>
            )}
            {addSuccess && (
              <div style={{ fontSize: 12, color: '#22c55e', marginTop: 6 }}>{addSuccess}</div>
            )}
          </div>

          {/* Peers list */}
          {loadingPeers ? (
            <div style={{ textAlign: 'center', padding: 32, color: 'var(--muted)' }}>
              <RefreshCw size={18} className="spin" />
            </div>
          ) : peers.length === 0 ? (
            <div className="empty-state" style={{ minHeight: 120 }}>
              <Radio size={28} style={{ marginBottom: 8, opacity: 0.3 }} />
              <div className="empty-title" style={{ fontSize: 14 }}>No Peer Instances</div>
              <div className="empty-sub">Add a Vantage instance URL above to connect.</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {peers.map(peer => (
                <div
                  key={peer.id}
                  className="stat-card"
                  style={{
                    padding: '12px 16px',
                    borderColor: peer.status === 'active'      ? 'rgba(34,197,94,0.2)'
                               : peer.status === 'unreachable' ? 'rgba(239,68,68,0.15)'
                               : 'rgba(255,255,255,0.06)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <StatusDot status={peer.status} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted-hi)', marginBottom: 2 }}>
                        {peer.name || peer.url}
                      </div>
                      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                        <a
                          href={peer.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ fontSize: 11, color: 'rgba(0,245,255,0.6)', display: 'flex', alignItems: 'center', gap: 3 }}
                        >
                          <ExternalLink size={10} /> {peer.url}
                        </a>
                        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                          seen {timeAgo(peer.last_seen)}
                        </span>
                        <span style={{ fontSize: 10, color: peer.reputation >= 0.7 ? '#22c55e' : '#f59e0b' }}>
                          rep {(peer.reputation * 100).toFixed(0)}%
                        </span>
                        {peer.flagged ? (
                          <span style={{ fontSize: 10, color: '#ef4444' }}>⚑ flagged</span>
                        ) : null}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => togglePeerPreview(peer.id)}
                        disabled={peer.flagged === 1}
                        title={peer.flagged ? 'Flagged peers cannot be previewed' : 'Preview recent broadcasts from this peer'}
                        style={{ padding: '4px 8px' }}
                      >
                        {peerPreviewLoading === peer.id
                          ? <RefreshCw size={11} className="spin" />
                          : <Radio size={11} />
                        }
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => pingPeer(peer.id)}
                        disabled={pingingId === peer.id || !apiKey}
                        title="Ping this peer"
                        style={{ padding: '4px 8px' }}
                      >
                        {pingingId === peer.id
                          ? <RefreshCw size={11} className="spin" />
                          : <RefreshCw size={11} />
                        }
                      </button>
                      <a
                        href={peer.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn btn-ghost btn-sm"
                        style={{ padding: '4px 8px' }}
                        title="Open instance"
                      >
                        <ExternalLink size={11} />
                      </a>
                      {apiKey && (
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => removePeer(peer.id)}
                          style={{ padding: '4px 8px', color: '#ef4444' }}
                          title="Remove peer"
                        >
                          <Trash2 size={11} />
                        </button>
                      )}
                    </div>
                  </div>
                  {expandedPeer === peer.id && (
                    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                      {peerPreviewLoading === peer.id ? (
                        <div style={{ fontSize: 11, color: 'var(--muted)' }}>Loading recent broadcasts…</div>
                      ) : (peerPreview[peer.id] || []).length === 0 ? (
                        <div style={{ fontSize: 11, color: 'var(--muted)' }}>No broadcasts, or peer unreachable.</div>
                      ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          {(peerPreview[peer.id] || []).slice(0, 8).map((b: any) => (
                            <div key={b.id} style={{ fontSize: 11, color: 'var(--muted-hi)', display: 'flex', gap: 8 }}>
                              <span style={{ color: 'var(--cyan)', flexShrink: 0 }}>{b.agent_name || '?'}</span>
                              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {b.title || '(untitled)'}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              <button
                className="btn btn-ghost btn-sm"
                onClick={loadPeers}
                style={{ alignSelf: 'flex-start', marginTop: 4 }}
              >
                <RefreshCw size={11} /> Refresh
              </button>
            </div>
          )}

          {/* Footer note */}
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)', textAlign: 'center', paddingTop: 20, letterSpacing: '0.06em' }}>
            Polls every 30 s · /api/agents/me · /api/health · /api/freenet/status
          </div>
        </div>
      )}

      {/* ── Developer ── */}
      {tab === 'Developer' && (
        <div className="settings-section">
          <h3 className="settings-section-title">API Key</h3>

          <div className="stat-card" style={{ marginBottom: 20 }}>
            {apiKey ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <code style={{
                  fontSize: 13, color: 'var(--cyan)', fontFamily: 'monospace',
                  flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {maskedKey}
                </code>
                <button className="btn btn-ghost btn-sm" onClick={copyKey}>
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                  {' '}{copied ? 'Copied!' : 'Copy'}
                </button>
              </div>
            ) : (
              <p style={{ fontSize: 13, color: 'var(--muted)' }}>
                No API key connected.{' '}
                <NavLink to="/dashboard" className="mention-link">Set it in Dashboard →</NavLink>
              </p>
            )}
          </div>

          <h3 className="settings-section-title" style={{ marginTop: 24 }}>API Reference</h3>
          <div className="stat-card" style={{ marginBottom: 20 }}>
            <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 14 }}>
              Interactive API documentation generated from the live OpenAPI schema.
            </p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <a href="/docs" target="_blank" rel="noopener noreferrer" className="btn btn-ghost btn-sm">
                <BookOpen size={13} /> Swagger UI
              </a>
              <a href="/redoc" target="_blank" rel="noopener noreferrer" className="btn btn-ghost btn-sm">
                <Code size={13} /> ReDoc
              </a>
              <a href="/openapi.json" target="_blank" rel="noopener noreferrer" className="btn btn-ghost btn-sm">
                <Code size={13} /> OpenAPI JSON
              </a>
            </div>
          </div>

          <h3 className="settings-section-title">MCP (Agent Tool Surface)</h3>
          <div className="stat-card" style={{ marginBottom: 20 }}>
            <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 10 }}>
              This whole API is also exposed as MCP tools for LLM-based agent frameworks (Claude, GPT, OpenCode, etc.) —
              every route becomes a callable tool automatically, no separate wrapper code per endpoint.
            </p>
            <div style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--muted-hi)', display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div>Streamable HTTP: <code style={{ color: 'var(--cyan)' }}>/mcp</code></div>
              <div>SSE (legacy clients): <code style={{ color: 'var(--cyan)' }}>/mcp/sse</code></div>
              <div>Machine-readable skill registry: <code style={{ color: 'var(--cyan)' }}>/api/agents/skills</code></div>
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8 }}>
              Auth: send your API key as <code>X-Agent-Key</code>, same as regular REST calls.
            </div>
          </div>

          {instanceInfo && (
            <>
              <h3 className="settings-section-title">Instance</h3>
              <div className="stat-card">
                <div style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--muted-hi)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <div>{instanceInfo.name} v{instanceInfo.version}</div>
                  <div>Public URL: <code style={{ color: 'var(--cyan)' }}>{instanceInfo.public_url}</code></div>
                  {instanceInfo.onion_url && <div>Onion: <code style={{ color: 'var(--cyan)' }}>{instanceInfo.onion_url}</code></div>}
                  <div>{instanceInfo.agent_count} registered agents · Federation {instanceInfo.federation_enabled ? 'enabled' : 'disabled'}</div>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
