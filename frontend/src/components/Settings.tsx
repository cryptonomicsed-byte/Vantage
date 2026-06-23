import React, { useState, useEffect, useCallback } from 'react'
import { NavLink } from 'react-router-dom'
import { BookOpen, Code, Copy, Check, Settings as SettingsIcon, Radio, Plus, Trash2, RefreshCw, ExternalLink, Wifi, WifiOff, AlertCircle } from 'lucide-react'

const TABS = ['General', 'Network', 'Developer'] as const
type Tab = typeof TABS[number]

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

export default function Settings() {
  const [tab, setTab]       = useState<Tab>('General')
  const [copied, setCopied] = useState(false)
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  // Federation state
  const [peers, setPeers]           = useState<FederationPeer[]>([])
  const [loadingPeers, setLoadingPeers] = useState(false)
  const [addUrl, setAddUrl]         = useState('')
  const [addName, setAddName]       = useState('')
  const [adding, setAdding]         = useState(false)
  const [addError, setAddError]     = useState('')
  const [addSuccess, setAddSuccess] = useState('')
  const [pingingId, setPingingId]   = useState<number | null>(null)
  const [federationEnabled, setFederationEnabled] = useState(false)

  function copyKey() {
    navigator.clipboard.writeText(apiKey).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const maskedKey = apiKey
    ? `${apiKey.slice(0, 12)}${'•'.repeat(16)}${apiKey.slice(-6)}`
    : ''

  // Load federation peers
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
    } catch (e) {
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

  function timeAgo(ts: string): string {
    if (!ts) return 'never'
    const diff = Date.now() - new Date(ts).getTime()
    const m = Math.floor(diff / 60000)
    if (m < 1)   return 'just now'
    if (m < 60)  return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24)  return `${h}h ago`
    return `${Math.floor(h / 24)}d ago`
  }

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
            {t === 'Network' && <Radio size={12} style={{ marginRight: 5 }} />}
            {t}
          </button>
        ))}
      </div>

      {/* ── General ── */}
      {tab === 'General' && (
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

      {/* ── Network (Federation) ── */}
      {tab === 'Network' && (
        <div className="settings-section">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
            <div>
              <h3 className="settings-section-title" style={{ marginBottom: 4 }}>Federation Network</h3>
              <p style={{ fontSize: 12, color: 'var(--muted)', margin: 0 }}>
                Connect to other Vantage instances to see their agents and broadcasts.
                Any user on any port can be connected.
              </p>
            </div>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6,
              fontSize: 11, color: federationEnabled ? '#22c55e' : '#ef4444',
            }}>
              {federationEnabled ? <Wifi size={13} /> : <WifiOff size={13} />}
              {federationEnabled ? 'ENABLED' : 'DISABLED'}
            </div>
          </div>

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
              <div style={{ fontSize: 12, color: '#22c55e', marginTop: 6 }}>
                {addSuccess}
              </div>
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
                    borderColor: peer.status === 'active' ? 'rgba(34,197,94,0.2)'
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
          <div className="stat-card">
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
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
