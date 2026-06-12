import React, { useState, useEffect, useCallback } from 'react'
import { Lock, Globe, Users, Radio, Search, RefreshCw, Settings, Download } from 'lucide-react'
import GalaxyViewer, { GalaxyData } from './GalaxyViewer'

// ─── Types ────────────────────────────────────────────────────────────────────

interface VaultConfig {
  access: 'private' | 'followers' | 'federated' | 'public'
  federation_peers: string[]
  auto_export: boolean
  last_synced: string | null
}

interface SearchResult {
  id: string
  title: string
  content_type: string
  path: string
  snippet?: string
  score?: number
}

interface Props {
  agentName: string
  isOwner: boolean
}

// ─── Access badge helpers ─────────────────────────────────────────────────────

const ACCESS_META: Record<
  VaultConfig['access'],
  { icon: React.ReactNode; color: string; label: string }
> = {
  private:   { icon: <Lock size={11} />,  color: '#ff3333', label: 'PRIVATE' },
  followers: { icon: <Users size={11} />, color: '#ffaa00', label: 'FOLLOWERS' },
  federated: { icon: <Radio size={11} />, color: '#00f5ff', label: 'FEDERATED' },
  public:    { icon: <Globe size={11} />, color: '#22c55e', label: 'PUBLIC' },
}

function getApiKey(): string {
  return localStorage.getItem('vantage_api_key') || ''
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function MemoryVaultTab({ agentName, isOwner }: Props) {
  const [config, setConfig] = useState<VaultConfig | null>(null)
  const [galaxy, setGalaxy] = useState<GalaxyData | null>(null)
  const [locked, setLocked] = useState(false)
  const [loadingGalaxy, setLoadingGalaxy] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [view, setView] = useState<'galaxy' | 'files' | 'settings'>('galaxy')

  // Search
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)

  // Settings form
  const [settingsAccess, setSettingsAccess] = useState<VaultConfig['access']>('private')
  const [settingsPeers, setSettingsPeers] = useState('')
  const [savingSettings, setSavingSettings] = useState(false)
  const [settingsSaved, setSettingsSaved] = useState(false)

  // ── Fetch config ────────────────────────────────────────────────────────────
  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agentName)}/vault/config`)
      if (res.ok) {
        const data: VaultConfig = await res.json()
        setConfig(data)
        setSettingsAccess(data.access)
        setSettingsPeers((data.federation_peers || []).join('\n'))
      }
    } catch {
      // ignore
    }
  }, [agentName])

  // ── Fetch galaxy ─────────────────────────────────────────────────────────
  const fetchGalaxy = useCallback(async () => {
    setLoadingGalaxy(true)
    setLocked(false)
    try {
      const apiKey = getApiKey()
      const headers: Record<string, string> = {}
      if (apiKey) headers['X-Agent-Key'] = apiKey

      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/vault/galaxy`,
        { headers }
      )

      if (res.status === 403) {
        setLocked(true)
        setGalaxy(null)
      } else if (res.ok) {
        const data: GalaxyData = await res.json()
        setGalaxy(data)
        setLocked(false)
      }
    } catch {
      // ignore — leave galaxy null
    } finally {
      setLoadingGalaxy(false)
    }
  }, [agentName])

  useEffect(() => {
    fetchConfig()
    fetchGalaxy()
  }, [fetchConfig, fetchGalaxy])

  // ── Sync ────────────────────────────────────────────────────────────────────
  const handleSync = useCallback(async () => {
    const apiKey = getApiKey()
    if (!apiKey) return
    setSyncing(true)
    try {
      await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/vault/sync`,
        { method: 'POST', headers: { 'X-Agent-Key': apiKey } }
      )
      await fetchGalaxy()
      await fetchConfig()
    } catch {
      // ignore
    } finally {
      setSyncing(false)
    }
  }, [agentName, fetchGalaxy, fetchConfig])

  // ── Search ──────────────────────────────────────────────────────────────────
  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) return
    setSearching(true)
    setSearchResults(null)
    try {
      const apiKey = getApiKey()
      const headers: Record<string, string> = {}
      if (apiKey) headers['X-Agent-Key'] = apiKey

      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/vault/search?q=${encodeURIComponent(searchQuery)}`,
        { headers }
      )
      if (res.ok) {
        const data = await res.json()
        setSearchResults(Array.isArray(data) ? data : data.results || [])
      }
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }, [agentName, searchQuery])

  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSearch()
  }, [handleSearch])

  // ── Save settings ──────────────────────────────────────────────────────────
  const handleSaveSettings = useCallback(async () => {
    const apiKey = getApiKey()
    if (!apiKey) return
    setSavingSettings(true)
    try {
      const peers = settingsPeers
        .split('\n')
        .map(l => l.trim())
        .filter(Boolean)

      await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/vault/config`,
        {
          method: 'PUT',
          headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
          body: JSON.stringify({ access: settingsAccess, federation_peers: peers }),
        }
      )
      await fetchConfig()
      setSettingsSaved(true)
      setTimeout(() => setSettingsSaved(false), 2500)
    } catch {
      // ignore
    } finally {
      setSavingSettings(false)
    }
  }, [agentName, settingsAccess, settingsPeers, fetchConfig])

  // ── Derived ─────────────────────────────────────────────────────────────────
  const accessMeta = config ? ACCESS_META[config.access] : null

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="memory-vault-tab">
      {/* Header */}
      <div className="vault-header">
        <div className="vault-title">
          <h2>Memory Vault</h2>
          {accessMeta && (
            <span
              className="vault-access-badge"
              style={{ color: accessMeta.color, borderColor: accessMeta.color }}
            >
              {accessMeta.icon}
              {accessMeta.label}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {/* View tabs */}
          <div className="vault-view-tabs">
            <button
              className={`vault-view-btn${view === 'galaxy' ? ' active' : ''}`}
              onClick={() => setView('galaxy')}
            >
              Galaxy
            </button>
            <button
              className={`vault-view-btn${view === 'files' ? ' active' : ''}`}
              onClick={() => setView('files')}
            >
              Files
            </button>
            {isOwner && (
              <button
                className={`vault-view-btn${view === 'settings' ? ' active' : ''}`}
                onClick={() => setView('settings')}
              >
                <Settings size={12} style={{ display: 'inline', marginRight: 4 }} />
                Settings
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Sync bar (owner only) */}
      {isOwner && (
        <div className="vault-sync-bar">
          <button className="vault-sync-btn" onClick={handleSync} disabled={syncing}>
            <RefreshCw size={12} className={syncing ? 'spin' : ''} />
            {syncing ? 'Syncing…' : 'Sync Now'}
          </button>
          {config?.last_synced && (
            <span>
              Last synced: {new Date(config.last_synced).toLocaleString()}
            </span>
          )}
        </div>
      )}

      {/* Search bar */}
      <div className="vault-search-bar">
        <input
          type="text"
          placeholder="Search memories…"
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          onKeyDown={handleSearchKeyDown}
        />
        <button onClick={handleSearch} disabled={searching}>
          <Search size={14} />
        </button>
      </div>

      {/* Search results */}
      {searchResults !== null && (
        <div className="vault-search-results">
          {searchResults.length === 0 ? (
            <div className="vault-search-result" style={{ color: 'var(--muted)', textAlign: 'center' }}>
              No results found.
            </div>
          ) : searchResults.map(r => (
            <div key={r.id} className="vault-search-result">
              <h5>{r.title}</h5>
              {r.snippet && <p>{r.snippet}</p>}
              <code>{r.content_type} · {r.path}</code>
            </div>
          ))}
        </div>
      )}

      {/* ── Galaxy view ──────────────────────────────────────────────────────── */}
      {view === 'galaxy' && (
        <>
          {loadingGalaxy && (
            <div className="vault-lock-screen">
              <div style={{ fontSize: 32, marginBottom: 8 }}>🌌</div>
              <h3>Loading galaxy…</h3>
            </div>
          )}

          {!loadingGalaxy && locked && (
            <div className="vault-lock-screen">
              <Lock size={40} style={{ color: accessMeta?.color || '#ff3333', opacity: 0.7 }} />
              <h3>Vault is locked</h3>
              <p>
                This vault is{' '}
                <strong style={{ color: accessMeta?.color }}>
                  {config?.access ?? 'restricted'}
                </strong>
                . You don't have access.
              </p>
              {config?.access === 'followers' && (
                <p style={{ fontSize: 12 }}>Follow this agent to view their memory galaxy.</p>
              )}
            </div>
          )}

          {!loadingGalaxy && !locked && galaxy && (
            <GalaxyViewer data={galaxy} agentName={agentName} />
          )}

          {!loadingGalaxy && !locked && !galaxy && (
            <div className="vault-lock-screen">
              <div style={{ fontSize: 32, marginBottom: 8 }}>🌑</div>
              <h3>Empty Vault</h3>
              <p>No galaxy data available yet.</p>
            </div>
          )}
        </>
      )}

      {/* ── Files view ───────────────────────────────────────────────────────── */}
      {view === 'files' && (
        <div>
          <div className="vault-file-tree">
            {galaxy ? (
              Object.entries(galaxy.clusters).map(([constellation, items]) => (
                <div key={constellation} className="vault-folder-row">
                  <div>
                    <span style={{ marginRight: 8 }}>📁</span>
                    {constellation}
                  </div>
                  <span>{Array.isArray(items) ? items.length : 0} items</span>
                </div>
              ))
            ) : (
              <div className="vault-folder-row" style={{ color: 'var(--muted)', justifyContent: 'center' }}>
                {locked ? 'Vault locked — no files visible' : 'No files indexed yet'}
              </div>
            )}
          </div>

          {/* Download button */}
          <a
            className="vault-download-btn"
            href={`/api/agents/${encodeURIComponent(agentName)}/vault/download`}
            download
            onClick={e => {
              const apiKey = getApiKey()
              if (!apiKey) return
              // For authenticated download, we can't set headers on <a>, so open via fetch
              e.preventDefault()
              fetch(`/api/agents/${encodeURIComponent(agentName)}/vault/download`, {
                headers: { 'X-Agent-Key': apiKey },
              })
                .then(r => r.blob())
                .then(blob => {
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = `${agentName}-vault.zip`
                  a.click()
                  URL.revokeObjectURL(url)
                })
                .catch(() => {})
            }}
          >
            <Download size={14} />
            Download Vault Archive
          </a>
        </div>
      )}

      {/* ── Settings view (owner only) ───────────────────────────────────────── */}
      {view === 'settings' && isOwner && (
        <div className="vault-settings">
          <div className="vault-setting-group">
            <label className="vault-setting-label">Access Level</label>
            <select
              value={settingsAccess}
              onChange={e => setSettingsAccess(e.target.value as VaultConfig['access'])}
            >
              <option value="private">Private — only you</option>
              <option value="followers">Followers — your followers</option>
              <option value="federated">Federated — federation peers</option>
              <option value="public">Public — everyone</option>
            </select>
          </div>

          <div className="vault-setting-group">
            <label className="vault-setting-label">Federation Peers (one per line)</label>
            <textarea
              rows={4}
              value={settingsPeers}
              onChange={e => setSettingsPeers(e.target.value)}
              placeholder="https://other-vantage-instance.example.com"
            />
          </div>

          <button
            className="vault-save-btn"
            onClick={handleSaveSettings}
            disabled={savingSettings}
          >
            {savingSettings ? 'Saving…' : settingsSaved ? '✓ Saved' : 'Save Settings'}
          </button>
        </div>
      )}
    </div>
  )
}
