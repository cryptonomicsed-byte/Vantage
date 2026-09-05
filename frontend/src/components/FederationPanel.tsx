/**
 * FederationPanel — Infrastructure Cockpit
 *
 * Polls the Vantage API for live status and renders the full
 * federation layer stack: Nostr · Freenet · Gitea · Ọmọ Kọ́dà2 · Sui · Arweave · Mesh
 */
import React, { useEffect, useRef, useState } from 'react'

// ── types ──────────────────────────────────────────────────────────────────────

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

// ── constants ──────────────────────────────────────────────────────────────────

const GREEN   = '#3cc878'
const DIM     = 'rgba(255,255,255,0.25)'
const AMBER   = '#f59e0b'
const CYAN    = 'var(--cyan)'

const POLL_MS = 30_000

// ── helpers ────────────────────────────────────────────────────────────────────

function dot(color: string, pulse = false) {
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

// ── sub-components ─────────────────────────────────────────────────────────────

const cardStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid var(--border)',
  borderRadius: 10,
  overflow: 'hidden',
}

const headerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '10px 16px',
  borderBottom: '1px solid var(--border)',
  background: 'rgba(0,0,0,0.25)',
}

const headerLabel: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: CYAN,
}

const bodyStyle: React.CSSProperties = {
  padding: '12px 16px',
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
}

const rowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 12,
  fontSize: 12,
}

const labelStyle: React.CSSProperties = {
  color: 'var(--muted)',
  flexShrink: 0,
  width: 110,
  paddingTop: 1,
}

const valueStyle: React.CSSProperties = {
  color: 'var(--text)',
  fontFamily: 'monospace',
  wordBreak: 'break-all',
}

function Row({ label, value, valueColor }: { label: string; value: React.ReactNode; valueColor?: string }) {
  return (
    <div style={rowStyle}>
      <span style={labelStyle}>{label}</span>
      <span style={{ ...valueStyle, color: valueColor || 'var(--text)' }}>{value}</span>
    </div>
  )
}

function StatusBadge({ connected, label }: { connected: boolean; label?: string }) {
  const color = connected ? GREEN : DIM
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {dot(color, connected)}
      <span style={{ fontSize: 11, color, fontWeight: 600 }}>
        {label ?? (connected ? 'Connected' : 'Not Connected')}
      </span>
    </div>
  )
}

// ── Layer cards ────────────────────────────────────────────────────────────────

function NostrCard({ agent }: { agent: AgentMe | null }) {
  const npub = agent?.npub || null
  const connected = !!npub

  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={headerLabel}>Nostr</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Identity + Federation</span>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge connected={connected} />
        </div>
      </div>
      <div style={bodyStyle}>
        <Row label="Relay" value="omokoda.duckdns.org:3443" valueColor={CYAN} />
        <Row
          label="npub"
          value={npub ? truncate(npub, 30) : 'not registered'}
          valueColor={npub ? 'var(--text)' : 'var(--muted)'}
        />
        <Row
          label="Write Relays"
          value="wss://relay.damus.io"
        />
        <Row
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
                    color: CYAN,
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

function FreenetCard() {
  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={headerLabel}>Freenet</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Decentralized State</span>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge connected={false} label="Not Connected" />
        </div>
      </div>
      <div style={bodyStyle}>
        <Row label="Status" value="Adapter not yet initialized" valueColor={AMBER} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[
            { label: 'Contracts', value: '0' },
            { label: 'Rooms', value: '0' },
            { label: 'Peers', value: '0' },
            { label: 'Subscriptions', value: '0' },
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
              <div style={{ fontSize: 18, fontWeight: 700, color: DIM }}>{s.value}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function GiteaCard() {
  // Static "configured" state — actual repo count would require a separate fetch
  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={headerLabel}>Gitea</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Source Code</span>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge connected={true} />
        </div>
      </div>
      <div style={bodyStyle}>
        <Row label="Host" value="localhost:3001" valueColor={GREEN} />
        <Row label="Repos" value="-" valueColor="var(--muted)" />
      </div>
    </div>
  )
}

function OmoKodaCard({ health }: { health: HealthData | null }) {
  const connected = health?.status === 'ok'

  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={headerLabel}>Ọmọ Kọ́dà2</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Sovereign Runtime</span>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge connected={connected} />
        </div>
      </div>
      <div style={bodyStyle}>
        <Row label="Host" value="localhost:7777" valueColor={connected ? GREEN : DIM} />
        <Row
          label="Status"
          value={connected ? 'Sovereign' : fmt(health?.status) || 'Unreachable'}
          valueColor={connected ? GREEN : AMBER}
        />
      </div>
    </div>
  )
}

function SuiCard({ agent }: { agent: AgentMe | null }) {
  const addr = agent?.sui_address || null
  const configured = !!addr

  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={headerLabel}>Sui</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Settlement</span>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge connected={configured} label={configured ? 'Configured' : 'Not Configured'} />
        </div>
      </div>
      <div style={bodyStyle}>
        <Row label="Network" value="testnet" />
        <Row
          label="Address"
          value={addr ? truncate(addr, 32) : 'none'}
          valueColor={addr ? CYAN : 'var(--muted)'}
        />
      </div>
    </div>
  )
}

function ArweaveCard() {
  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={headerLabel}>Arweave</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Permanent Archive</span>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge connected={false} label="Not Configured" />
        </div>
      </div>
      <div style={bodyStyle}>
        <Row label="Role" value="archival" />
        <Row label="Use" value="receipts, genesis records, governance" valueColor="var(--muted)" />
      </div>
    </div>
  )
}

function MeshCard() {
  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={headerLabel}>Meshtastic / Reticulum</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>Mesh</span>
        <div style={{ marginLeft: 'auto' }}>
          <StatusBadge connected={false} label="Not Configured" />
        </div>
      </div>
      <div style={bodyStyle}>
        <Row label="Role" value="off-grid agent comms" valueColor="var(--muted)" />
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function FederationPanel() {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [agent, setAgent]   = useState<AgentMe | null>(null)
  const [health, setHealth] = useState<HealthData | null>(null)
  const [lastPoll, setLastPoll] = useState<Date | null>(null)
  const [polling, setPolling]   = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const poll = React.useCallback(async () => {
    setPolling(true)
    try {
      const headers: Record<string, string> = {}
      if (apiKey) headers['X-Agent-Key'] = apiKey

      const [agentRes, healthRes] = await Promise.allSettled([
        fetch('/api/agents/me', { headers }),
        fetch('/api/health'),
      ])

      if (agentRes.status === 'fulfilled' && agentRes.value.ok) {
        const data = await agentRes.value.json()
        setAgent(data)
      }

      if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
        const data = await healthRes.value.json()
        setHealth(data)
      }

      setLastPoll(new Date())
    } finally {
      setPolling(false)
    }
  }, [apiKey])

  useEffect(() => {
    poll()
    timerRef.current = setInterval(poll, POLL_MS)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [poll])

  return (
    <div
      style={{
        padding: '24px 20px',
        overflowY: 'auto',
        height: '100%',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        gap: 24,
        maxWidth: 780,
        margin: '0 auto',
      }}
    >
      {/* Title row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.15em', textTransform: 'uppercase', color: CYAN, marginBottom: 2 }}>
            Federation Cockpit
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>
            Infrastructure layer status — agent ecosystem
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {polling && (
            <span style={{ fontSize: 10, color: AMBER }}>Polling…</span>
          )}
          {lastPoll && !polling && (
            <span style={{ fontSize: 10, color: 'var(--muted)' }}>
              Last sync {lastPoll.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={poll}
            disabled={polling}
            style={{
              fontSize: 11,
              padding: '4px 10px',
              borderRadius: 6,
              border: '1px solid var(--border)',
              background: 'rgba(255,255,255,0.04)',
              color: polling ? 'var(--muted)' : 'var(--text)',
              cursor: polling ? 'default' : 'pointer',
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Agent identity row */}
      {agent && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '10px 14px',
            borderRadius: 8,
            background: 'rgba(0,245,255,0.05)',
            border: '1px solid rgba(0,245,255,0.15)',
            fontSize: 12,
          }}
        >
          <span style={{ color: 'var(--muted)' }}>Authenticated as</span>
          <span style={{ fontWeight: 700, color: CYAN }}>{agent.agent_name}</span>
          <span style={{ color: 'var(--muted)', marginLeft: 'auto', fontFamily: 'monospace', fontSize: 11 }}>
            #{agent.agent_id}
          </span>
        </div>
      )}

      {/* Layer cards */}
      <NostrCard agent={agent} />
      <FreenetCard />
      <GiteaCard />
      <OmoKodaCard health={health} />
      <SuiCard agent={agent} />
      <ArweaveCard />
      <MeshCard />

      {/* Footer */}
      <div
        style={{
          fontSize: 10,
          color: 'rgba(255,255,255,0.18)',
          textAlign: 'center',
          paddingBottom: 8,
          letterSpacing: '0.06em',
        }}
      >
        Polls every 30 s · /api/agents/me · /api/health
      </div>
    </div>
  )
}
