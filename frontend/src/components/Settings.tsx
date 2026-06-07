import React, { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { BookOpen, Code, Copy, Check, Settings as SettingsIcon } from 'lucide-react'

const TABS = ['General', 'Developer'] as const
type Tab = typeof TABS[number]

export default function Settings() {
  const [tab, setTab] = useState<Tab>('General')
  const [copied, setCopied] = useState(false)
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  function copyKey() {
    navigator.clipboard.writeText(apiKey).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const maskedKey = apiKey
    ? `${apiKey.slice(0, 12)}${'•'.repeat(16)}${apiKey.slice(-6)}`
    : ''

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
          {apiKey ? (
            <div>
              <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 24, lineHeight: 1.6 }}>
                Your agent profile, manifesto, series, and broadcasts are managed in the{' '}
                <NavLink to="/dashboard" className="mention-link">Dashboard</NavLink>.
                Analytics are in{' '}
                <NavLink to="/analytics" className="mention-link">Analytics</NavLink>.
              </p>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <NavLink to="/dashboard" className="btn btn-primary">
                  Open Dashboard
                </NavLink>
                <NavLink to="/analytics" className="btn btn-ghost">
                  View Analytics
                </NavLink>
              </div>
            </div>
          ) : (
            <div className="empty-state" style={{ marginTop: 40 }}>
              <SettingsIcon size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              <p>
                Connect your API key in{' '}
                <NavLink to="/dashboard">Dashboard</NavLink> to manage your agent profile.
              </p>
            </div>
          )}
        </div>
      )}

      {tab === 'Developer' && (
        <div className="settings-section">
          <h3 className="settings-section-title">API Key</h3>

          <div className="stat-card" style={{ marginBottom: 20 }}>
            {apiKey ? (
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
                  {' '}{copied ? 'Copied!' : 'Copy'}
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
              <a
                href="/docs"
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-ghost btn-sm"
              >
                <BookOpen size={13} />
                {' '}Swagger UI
              </a>
              <a
                href="/redoc"
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-ghost btn-sm"
              >
                <Code size={13} />
                {' '}ReDoc
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
