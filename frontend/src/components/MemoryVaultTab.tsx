import React, { useState, useEffect, useCallback } from 'react'
import { Lock, Globe, Users, Radio, Search, RefreshCw, Settings, Download, BarChart2, PlusCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import GalaxyViewer, { GalaxyData } from './GalaxyViewer'
import type { GalaxyStar } from './GalaxyViewer'

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

interface VaultStats {
  stars: number
  edges: number
  nebulae: number
  broadcasts: number
  knowledge: number
  traces: number
  vault_size_bytes: number
  last_synced: string | null
  access: string
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function MemoryVaultTab({ agentName, isOwner }: Props) {
  const [config, setConfig] = useState<VaultConfig | null>(null)
  const [galaxy, setGalaxy] = useState<GalaxyData | null>(null)
  const [locked, setLocked] = useState(false)
  const [loadingGalaxy, setLoadingGalaxy] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [view, setView] = useState<'galaxy' | 'files' | 'settings' | 'stats'>('galaxy')

  // Search
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)

  // Settings form
  const [settingsAccess, setSettingsAccess] = useState<VaultConfig['access']>('private')
  const [settingsPeers, setSettingsPeers] = useState('')
  const [savingSettings, setSavingSettings] = useState(false)
  const [settingsSaved, setSettingsSaved] = useState(false)

  // Stats
  const [stats, setStats] = useState<VaultStats | null>(null)
  const [loadingStats, setLoadingStats] = useState(false)

  // Star detail panel
  const [selectedStar, setSelectedStar] = useState<GalaxyStar | null>(null)
  const [starMarkdown, setStarMarkdown] = useState<string>('')
  const [loadingStarContent, setLoadingStarContent] = useState(false)
  const [linkTargetInput, setLinkTargetInput] = useState('')
  const [linkCreated, setLinkCreated] = useState(false)
  const [noteLinks, setNoteLinks] = useState<Array<{
    id: number
    link_type: string
    source_agent_name: string
    source_note_path: string
    target_agent_name: string
    target_note_path: string
    created_at: string
  }>>([])

  // Compare mode
  const [compareInput, setCompareInput] = useState('')
  const [compareGalaxy, setCompareGalaxy] = useState<GalaxyData | null>(null)
  const [comparing, setComparing] = useState(false)
  const [showCompare, setShowCompare] = useState(false)

  // Create note form
  const [showNoteForm, setShowNoteForm] = useState(false)
  const [noteTitle, setNoteTitle] = useState('')
  const [noteBody, setNoteBody] = useState('')
  const [noteCategory, setNoteCategory] = useState<'drafts' | 'templates' | 'broadcasts' | 'knowledge'>('drafts')
  const [noteTags, setNoteTags] = useState('')
  const [creatingNote, setCreatingNote] = useState(false)
  const [noteCreated, setNoteCreated] = useState(false)

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

  // ── Fetch stats ─────────────────────────────────────────────────────────────
  const fetchStats = useCallback(async () => {
    setLoadingStats(true)
    try {
      const apiKey = getApiKey()
      const headers: Record<string, string> = {}
      if (apiKey) headers['X-Agent-Key'] = apiKey
      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/vault/stats`,
        { headers }
      )
      if (res.ok) {
        const data: VaultStats = await res.json()
        setStats(data)
      }
    } catch {
      // ignore
    } finally {
      setLoadingStats(false)
    }
  }, [agentName])

  useEffect(() => {
    if (view === 'stats') fetchStats()
  }, [view, fetchStats])

  // ── Star select ─────────────────────────────────────────────────────────────
  const handleStarSelect = useCallback(async (star: GalaxyStar) => {
    setSelectedStar(star)
    setStarMarkdown('')
    setLoadingStarContent(true)
    setLinkCreated(false)
    try {
      const apiKey = localStorage.getItem('vantage_api_key') || ''
      const headers: Record<string, string> = {}
      if (apiKey) headers['X-Agent-Key'] = apiKey
      const resp = await fetch(
        `/api/agents/${agentName}/vault/file/${encodeURIComponent(star.path)}`,
        { headers }
      )
      if (resp.ok) {
        setStarMarkdown(await resp.text())
      } else {
        setStarMarkdown('> Could not load note content.')
      }
    } catch {
      setStarMarkdown('> Failed to fetch note.')
    } finally {
      setLoadingStarContent(false)
    }
    // Fetch note-level links
    setNoteLinks([])
    const apiKey = getApiKey()
    fetch(`/api/agents/${agentName}/vault/note-links?path=${encodeURIComponent(star.path)}`, {
      headers: apiKey ? { 'X-Agent-Key': apiKey } : {},
    }).then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.links) setNoteLinks(data.links) })
      .catch(() => {})
  }, [agentName])

  // ── Create link ─────────────────────────────────────────────────────────────
  const handleCreateLink = useCallback(async () => {
    if (!selectedStar || !linkTargetInput.trim()) return
    const apiKey = localStorage.getItem('vantage_api_key') || ''
    if (!apiKey) return
    await fetch(`/api/agents/${agentName}/vault/link`, {
      method: 'POST',
      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_agent_name: linkTargetInput.trim(), link_type: 'references', note: selectedStar.path }),
    }).catch(() => {})
    setLinkCreated(true)
    setLinkTargetInput('')
  }, [agentName, selectedStar, linkTargetInput])

  // ── Create note ─────────────────────────────────────────────────────────────
  const handleCreateNote = useCallback(async () => {
    const apiKey = getApiKey()
    if (!apiKey || !noteTitle.trim()) return
    setCreatingNote(true)
    try {
      const tags = noteTags.split(',').map(t => t.trim()).filter(Boolean)
      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/vault/note`,
        {
          method: 'POST',
          headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: noteTitle, body: noteBody, category: noteCategory, tags }),
        }
      )
      if (res.ok) {
        setNoteCreated(true)
        setNoteTitle('')
        setNoteBody('')
        setNoteTags('')
        setNoteCategory('drafts')
        setShowNoteForm(false)
        setTimeout(() => setNoteCreated(false), 3000)
        // Refresh galaxy to pick up new star
        await fetchGalaxy()
      }
    } catch {
      // ignore
    } finally {
      setCreatingNote(false)
    }
  }, [agentName, noteTitle, noteBody, noteCategory, noteTags, fetchGalaxy])

  // ── Compare galaxies ────────────────────────────────────────────────────────
  const handleCompare = useCallback(async () => {
    const peers = compareInput.split(',').map(s => s.trim()).filter(Boolean)
    if (peers.length === 0) return
    // Include the current agent in the merge
    const allPeers = [agentName, ...peers].join(',')
    setComparing(true)
    try {
      const apiKey = getApiKey()
      const headers: Record<string, string> = {}
      if (apiKey) headers['X-Agent-Key'] = apiKey
      const res = await fetch(
        `/api/federation/galaxy?peers=${encodeURIComponent(allPeers)}`,
        { headers }
      )
      if (res.ok) {
        const data: GalaxyData = await res.json()
        setCompareGalaxy(data)
      }
    } catch {
      // ignore
    } finally {
      setComparing(false)
    }
  }, [agentName, compareInput])

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
            <button
              className={`vault-view-btn${view === 'stats' ? ' active' : ''}`}
              onClick={() => setView('stats')}
            >
              <BarChart2 size={12} style={{ display: 'inline', marginRight: 4 }} />
              Stats
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
            <GalaxyViewer data={galaxy} agentName={agentName} onStarSelect={handleStarSelect} />
          )}

          {selectedStar && (
            <div className="star-detail-panel">
              <div className="star-detail-header">
                <span className="star-detail-title" style={{ color: selectedStar.color }}>
                  {selectedStar.title}
                </span>
                <span className="tag-pill" style={{ fontSize: '0.7rem' }}>
                  {selectedStar.constellation}
                </span>
                <button className="star-detail-close" onClick={() => setSelectedStar(null)}>×</button>
              </div>
              <div className="star-detail-meta">
                <span className="tag-pill">{selectedStar.content_type}</span>
                {selectedStar.tags.slice(0, 5).map(tag => (
                  <span key={tag} className="tag-pill" style={{ opacity: 0.7 }}>{tag}</span>
                ))}
                {selectedStar.created && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--muted)' }}>
                    {new Date(selectedStar.created).toLocaleDateString()}
                  </span>
                )}
              </div>
              <div className="star-detail-content">
                {loadingStarContent ? (
                  <span style={{ color: 'var(--muted)' }}>Loading…</span>
                ) : (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{starMarkdown}</ReactMarkdown>
                )}
              </div>
              {noteLinks.length > 0 && (
                <div style={{ marginTop: 10, fontSize: '0.78rem' }}>
                  <div style={{ color: 'var(--muted)', marginBottom: 4 }}>Memory links:</div>
                  {noteLinks.map(link => (
                    <div key={link.id} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3, padding: '3px 6px', background: 'rgba(255,255,255,0.03)', borderRadius: 4 }}>
                      <span style={{ color: '#c7ceea', fontSize: '0.7rem', padding: '1px 5px', background: 'rgba(199,206,234,0.1)', borderRadius: 3 }}>
                        {link.link_type}
                      </span>
                      <span style={{ color: 'var(--muted)' }}>
                        {link.source_agent_name} · {link.source_note_path?.split('/').pop()}
                      </span>
                      <span style={{ color: 'var(--muted)' }}>→</span>
                      <span style={{ color: 'var(--text)' }}>
                        {link.target_agent_name} · {link.target_note_path?.split('/').pop() || '(self)'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {isOwner && (
                <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="text"
                    placeholder="Link to agent name…"
                    value={linkTargetInput}
                    onChange={e => setLinkTargetInput(e.target.value)}
                    style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: '0.82rem' }}
                  />
                  <button className="btn btn-sm" onClick={handleCreateLink} disabled={!linkTargetInput.trim()}>
                    Link
                  </button>
                  {linkCreated && <span style={{ color: 'var(--cyan)', fontSize: '0.8rem' }}>✓ Linked</span>}
                </div>
              )}
            </div>
          )}

          {/* Compare mode */}
          <div className="vault-compare-bar">
            <button
              className="vault-sync-btn"
              style={{ fontSize: '0.78rem' }}
              onClick={() => { setShowCompare(v => !v); if (compareGalaxy) setCompareGalaxy(null) }}
            >
              {showCompare ? '× Close Compare' : '⊕ Compare Galaxies'}
            </button>
            {showCompare && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
                <input
                  type="text"
                  placeholder="Agent names (comma-separated)…"
                  value={compareInput}
                  onChange={e => setCompareInput(e.target.value)}
                  style={{ flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: '0.82rem' }}
                  onKeyDown={e => { if (e.key === 'Enter') handleCompare() }}
                />
                <button className="btn btn-sm" onClick={handleCompare} disabled={comparing || !compareInput.trim()}>
                  {comparing ? '…' : 'Merge'}
                </button>
              </div>
            )}
            {compareGalaxy && !loadingGalaxy && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--muted)', marginBottom: 6 }}>
                  Merged galaxy: {(compareGalaxy as any).included?.join(', ') || 'multiple agents'} — {compareGalaxy.stars?.length ?? 0} stars
                </div>
                <GalaxyViewer data={compareGalaxy} agentName="merged" onStarSelect={handleStarSelect} />
              </div>
            )}
          </div>

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
          {/* Create Note button (owner only) */}
          {isOwner && (
            <div style={{ marginBottom: 10 }}>
              <button
                className="vault-sync-btn"
                onClick={() => setShowNoteForm(v => !v)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
              >
                <PlusCircle size={13} />
                {showNoteForm ? 'Cancel' : 'Create Note'}
              </button>
              {noteCreated && (
                <span style={{ marginLeft: 10, color: 'var(--cyan)', fontSize: 12 }}>
                  ✓ Note created
                </span>
              )}
            </div>
          )}

          {/* Create Note form */}
          {isOwner && showNoteForm && (
            <div className="vault-note-form">
              <input
                type="text"
                placeholder="Note title…"
                value={noteTitle}
                onChange={e => setNoteTitle(e.target.value)}
              />
              <textarea
                placeholder="Note body (Markdown)…"
                value={noteBody}
                onChange={e => setNoteBody(e.target.value)}
              />
              <select
                value={noteCategory}
                onChange={e => setNoteCategory(e.target.value as typeof noteCategory)}
              >
                <option value="drafts">Drafts</option>
                <option value="templates">Templates</option>
                <option value="broadcasts">Broadcasts</option>
                <option value="knowledge">Knowledge</option>
              </select>
              <input
                type="text"
                placeholder="Tags (comma-separated)…"
                value={noteTags}
                onChange={e => setNoteTags(e.target.value)}
              />
              <button
                className="vault-save-btn"
                onClick={handleCreateNote}
                disabled={creatingNote || !noteTitle.trim()}
              >
                {creatingNote ? 'Creating…' : 'Create Note'}
              </button>
            </div>
          )}

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

      {/* ── Stats view ───────────────────────────────────────────────────────── */}
      {view === 'stats' && (
        <div>
          {loadingStats && (
            <div style={{ color: 'var(--muted)', padding: 20, textAlign: 'center' }}>
              Loading stats…
            </div>
          )}
          {!loadingStats && !stats && (
            <div style={{ color: 'var(--muted)', padding: 20, textAlign: 'center' }}>
              Unable to load stats. Vault may be locked.
            </div>
          )}
          {!loadingStats && stats && (
            <>
              <div className="vault-stats-grid">
                <div className="vault-stat-tile">
                  <div className="vst-value">{stats.stars}</div>
                  <div className="vst-label">Stars</div>
                </div>
                <div className="vault-stat-tile">
                  <div className="vst-value">{stats.edges}</div>
                  <div className="vst-label">Edges</div>
                </div>
                <div className="vault-stat-tile">
                  <div className="vst-value">{stats.nebulae}</div>
                  <div className="vst-label">Nebulae</div>
                </div>
                <div className="vault-stat-tile">
                  <div className="vst-value">{stats.broadcasts}</div>
                  <div className="vst-label">Broadcasts</div>
                </div>
                <div className="vault-stat-tile">
                  <div className="vst-value">{stats.knowledge}</div>
                  <div className="vst-label">Knowledge</div>
                </div>
                <div className="vault-stat-tile">
                  <div className="vst-value">{stats.traces}</div>
                  <div className="vst-label">Traces</div>
                </div>
                <div className="vault-stat-tile">
                  <div className="vst-value">{formatBytes(stats.vault_size_bytes)}</div>
                  <div className="vst-label">Vault Size</div>
                </div>
                <div className="vault-stat-tile">
                  <div className="vst-value" style={{ fontSize: '0.85rem' }}>
                    {stats.access.toUpperCase()}
                  </div>
                  <div className="vst-label">Access Level</div>
                </div>
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8 }}>
                Last synced:{' '}
                {stats.last_synced
                  ? new Date(stats.last_synced).toLocaleString()
                  : 'Never'}
              </div>
            </>
          )}
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
