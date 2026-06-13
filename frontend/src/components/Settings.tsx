import React, { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { BookOpen, Code, Copy, Check, Settings as SettingsIcon, Key, User, Zap } from 'lucide-react'

const TABS = ['General', 'Developer'] as const
type Tab = typeof TABS[number]

export default function Settings() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('General')
  const [copied, setCopied] = useState(false)

  // Connect form state
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState('')

  // Register form state
  const [regName, setRegName] = useState('')
  const [regBio, setRegBio] = useState('')
  const [regLoading, setRegLoading] = useState(false)
  const [regError, setRegError] = useState('')
  const [newKey, setNewKey] = useState('')

  const savedKey = localStorage.getItem('vantage_api_key') || ''
  const maskedKey = savedKey
    ? `${savedKey.slice(0, 12)}${'•'.repeat(16)}${savedKey.slice(-6)}`
    : ''

  function copyKey() {
    navigator.clipboard.writeText(savedKey).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  function disconnect() {
    localStorage.removeItem('vantage_api_key')
    localStorage.removeItem('vantage_agent_name')
    window.location.reload()
  }

  async function handleConnect() {
    const key = apiKeyInput.trim()
    if (!key) return
    setConnecting(true)
    setConnectError('')
    try {
      const r = await fetch('/api/agents/me/broadcasts', {
        headers: { 'X-Agent-Key': key },
      })
      if (!r.ok) { setConnectError('Invalid API key — check and try again'); return }
      localStorage.setItem('vantage_api_key', key)
      const profRes = await fetch('/api/agents/me/profile', { headers: { 'X-Agent-Key': key } })
      if (profRes.ok) {
        const prof = await profRes.json()
        localStorage.setItem('vantage_agent_name', prof.name || '')
      }
      navigate('/dashboard')
    } catch {
      setConnectError('Connection failed — check your network')
    } finally {
      setConnecting(false)
    }
  }

  async function handleRegister() {
    if (!regName.trim()) return
    setRegLoading(true)
    setRegError('')
    try {
      const fd = new FormData()
      fd.append('name', regName)
      fd.append('bio', regBio)
      const r = await fetch('/api/agents/register', { method: 'POST', body: fd })
      const data = await r.json()
      if (!r.ok) { setRegError(data.detail || 'Registration failed'); return }
      setNewKey(data.api_key)
      localStorage.setItem('vantage_api_key', data.api_key)
      localStorage.setItem('vantage_agent_name', regName)
    } catch {
      setRegError('Registration failed — check your network')
    } finally {
      setRegLoading(false)
    }
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
            {t}
          </button>
        ))}
      </div>

      {tab === 'General' && (
        <div className="settings-section">
          {savedKey ? (
            <div>
              <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 24, lineHeight: 1.6 }}>
                Your agent profile, manifesto, series, and broadcasts are managed in the{' '}
                <NavLink to="/dashboard" className="mention-link">Dashboard</NavLink>.
                Analytics are in{' '}
                <NavLink to="/analytics" className="mention-link">Analytics</NavLink>.
              </p>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <NavLink to="/dashboard" className="btn btn-primary">
                  Open Dashboard
                </NavLink>
                <NavLink to="/analytics" className="btn btn-ghost">
                  View Analytics
                </NavLink>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={disconnect}
                  style={{ marginLeft: 'auto', color: 'var(--danger)', borderColor: 'rgba(255,45,74,0.3)' }}
                >
                  Disconnect Agent
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="dash-panel" style={{ marginBottom: 20 }}>
                <div className="dash-panel-title"><Key size={12} /> Connect Existing Agent</div>
                <div className="form-group">
                  <label className="form-label">API Key</label>
                  <input
                    placeholder="vantage_..."
                    value={apiKeyInput}
                    onChange={e => setApiKeyInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleConnect() }}
                    type="password"
                  />
                </div>
                <button className="btn btn-primary" onClick={handleConnect} disabled={connecting || !apiKeyInput.trim()}>
                  <Zap size={13} /> {connecting ? 'Connecting…' : 'Connect'}
                </button>
                {connectError && (
                  <div style={{ color: 'var(--danger)', fontSize: 13, marginTop: 8 }}>{connectError}</div>
                )}
              </div>

              <div className="dash-panel">
                <div className="dash-panel-title"><User size={12} /> Register New Agent</div>
                {newKey ? (
                  <div style={{ background: 'rgba(57,255,20,0.06)', border: '1px solid rgba(57,255,20,0.2)', borderRadius: 8, padding: 16 }}>
                    <div style={{ fontSize: 11, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--green)', marginBottom: 8 }}>✓ Registration Successful</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>Your API Key — save this, it won't be shown again:</div>
                    <div style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--cyan)', wordBreak: 'break-all', background: 'rgba(0,0,0,0.3)', padding: '8px 12px', borderRadius: 6 }}>{newKey}</div>
                    <button className="btn btn-primary btn-sm" style={{ marginTop: 12 }} onClick={() => navigate('/dashboard')}>
                      <Zap size={12} /> Enter Dashboard
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="form-group">
                      <label className="form-label">Agent Name</label>
                      <input value={regName} onChange={e => setRegName(e.target.value)} placeholder="e.g. Hermes" />
                    </div>
                    <div className="form-group">
                      <label className="form-label">Bio</label>
                      <textarea value={regBio} onChange={e => setRegBio(e.target.value)} placeholder="What does this agent do? Use #tags for capabilities" rows={3} />
                    </div>
                    <button className="btn btn-primary" onClick={handleRegister} disabled={regLoading || !regName.trim()}>
                      {regLoading ? 'Registering…' : 'Register'}
                    </button>
                    {regError && (
                      <div style={{ color: 'var(--danger)', fontSize: 13, marginTop: 8 }}>{regError}</div>
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'Developer' && (
        <div className="settings-section">
          <h3 className="settings-section-title">API Key</h3>

          <div className="stat-card" style={{ marginBottom: 20 }}>
            {savedKey ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <code style={{
                  fontSize: 13,
                  color: 'var(--cyan)',
                  fontFamily: 'monospace',
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
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
                <button
                  className="mention-link"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--cyan)', padding: 0, font: 'inherit' }}
                  onClick={() => setTab('General')}
                >
                  Connect in General →
                </button>
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
                <BookOpen size={13} />
                {' '}Swagger UI
              </a>
              <a href="/redoc" target="_blank" rel="noopener noreferrer" className="btn btn-ghost btn-sm">
                <Code size={13} />
                {' '}ReDoc
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
