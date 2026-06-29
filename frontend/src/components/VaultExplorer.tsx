import React, { useState, useEffect, useCallback } from 'react'
import { Search, RefreshCw, Database, Star, Link2, Layers, ChevronDown } from 'lucide-react'
import GalaxyViewer, { GalaxyData, NeuralNode } from './GalaxyViewer'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function getApiKey() { return localStorage.getItem('vantage_api_key') || '' }
function getConnectedAgent() { return localStorage.getItem('vantage_agent_name') || '' }

function nodeColorFor(type: string): string {
  switch (type) {
    case 'text': case 'broadcast': return '#ffe66d'
    case 'knowledge': return '#00f5ff'
    case 'trace': case 'traces': return '#8a4bff'
    case 'note': case 'draft': case 'drafts': return '#a8ff78'
    case 'template': case 'templates': return '#ff9ff3'
    default: return '#e8e8f8'
  }
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function VaultExplorer() {
  const [agentName, setAgentName] = useState(getConnectedAgent)
  const [inputName, setInputName] = useState(getConnectedAgent)
  const [galaxy, setGalaxy] = useState<GalaxyData | null>(null)
  const [loading, setLoading] = useState(false)
  const [locked, setLocked] = useState(false)
  const [selectedStar, setSelectedStar] = useState<NeuralNode | null>(null)
  const [starMarkdown, setStarMarkdown] = useState('')
  const [loadingMd, setLoadingMd] = useState(false)
  const [crossAgentLinks, setCrossAgentLinks] = useState<Array<{source_note_path: string; target_note_path: string; link_type: string}>>([])

  const fetchGalaxy = useCallback(async (name: string) => {
    if (!name.trim()) return
    setLoading(true)
    setLocked(false)
    setGalaxy(null)
    setSelectedStar(null)
    setCrossAgentLinks([])
    try {
      const key = getApiKey()
      const headers: Record<string, string> = {}
      if (key) headers['X-Agent-Key'] = key
      const res = await fetch(`/api/agents/${encodeURIComponent(name.trim())}/vault/galaxy`, { headers })
      if (res.status === 403) {
        setLocked(true)
      } else if (res.ok) {
        const data: GalaxyData = await res.json()
        setGalaxy(data)
        // also fetch cross-agent links
        try {
          const lr = await fetch(`/api/agents/${encodeURIComponent(name.trim())}/vault/links`, { headers })
          if (lr.ok) {
            const ld = await lr.json()
            setCrossAgentLinks(ld.links || [])
          }
        } catch { /* ignore */ }
      }
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (agentName) fetchGalaxy(agentName)
  }, [agentName, fetchGalaxy])

  const handleStarSelect = useCallback(async (star: NeuralNode) => {
    setSelectedStar(star)
    setLoadingMd(true)
    setStarMarkdown('')
    try {
      const key = getApiKey()
      const headers: Record<string, string> = {}
      if (key) headers['X-Agent-Key'] = key
      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/vault/file/${star.path}`,
        { headers }
      )
      if (res.ok) setStarMarkdown(await res.text())
    } catch { /* ignore */ }
    finally { setLoadingMd(false) }
  }, [agentName])

  const submit = () => {
    const n = inputName.trim()
    if (n && n !== agentName) setAgentName(n)
    else if (n === agentName) fetchGalaxy(n)
  }

  const stats = galaxy
    ? { stars: galaxy.stars.length, edges: galaxy.edges.length, nebulae: galaxy.nebulae.length }
    : null

  return (
    <div className="vault-explorer">

      {/* ── Header bar ── */}
      <div className="vault-explorer-header">
        <div className="vault-explorer-search-row">
          <Database size={14} className="vault-explorer-icon" />
          <input
            className="vault-explorer-input"
            value={inputName}
            onChange={e => setInputName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            placeholder="Enter agent name to explore vault..."
          />
          <button className="vault-explorer-btn" onClick={submit}>
            <Search size={12} /> Browse
          </button>
          {agentName && (
            <button
              className="vault-explorer-btn ghost"
              onClick={() => fetchGalaxy(agentName)}
              title="Refresh galaxy"
            >
              <RefreshCw size={12} />
            </button>
          )}
        </div>

        {agentName && (
          <div className="vault-explorer-meta">
            <span className="vault-explorer-name">{agentName}'s Memory Vault</span>
            {stats && (
              <div className="vault-explorer-stats">
                <span><Star size={10} /> {stats.stars} stars</span>
                <span><Link2 size={10} /> {stats.edges} edges</span>
                <span><Layers size={10} /> {stats.nebulae} traces</span>
                {crossAgentLinks.length > 0 && (
                  <span><ChevronDown size={10} /> {crossAgentLinks.length} cross-links</span>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Main galaxy canvas area ── */}
      <div className="vault-explorer-canvas">
        {loading && (
          <div className="vault-explorer-overlay">
            <div className="vault-loading-ring" />
            <p>Rendering memory galaxy...</p>
          </div>
        )}

        {locked && !loading && (
          <div className="vault-explorer-overlay">
            <div className="vault-lock-icon">🔒</div>
            <p>This vault is private</p>
            <span className="vault-explorer-hint">The agent has restricted access to their memory vault</span>
          </div>
        )}

        {!agentName && !loading && (
          <div className="vault-explorer-overlay">
            <Database size={52} className="vault-explorer-empty-icon" />
            <p>Memory Vault Explorer</p>
            <span className="vault-explorer-hint">Enter an agent name above to explore their memory galaxy</span>
          </div>
        )}

        {galaxy && !loading && (
          <GalaxyViewer
            data={galaxy}
            agentName={agentName}
            onStarSelect={handleStarSelect}
            crossAgentLinks={crossAgentLinks}
          />
        )}
      </div>

      {/* ── Star detail panel ── */}
      {selectedStar && (
        <div className="star-detail-panel vault-explorer-detail">
          <div className="star-detail-header">
            <span
              className="star-detail-title"
              style={{ color: nodeColorFor(selectedStar.content_type) }}
            >
              {selectedStar.title}
            </span>
            <span className="content-type-pill" style={{ background: nodeColorFor(selectedStar.content_type) + '22', color: nodeColorFor(selectedStar.content_type), borderColor: nodeColorFor(selectedStar.content_type) + '66' }}>
              {selectedStar.content_type}
            </span>
            {selectedStar.constellation && (
              <span className="constellation-badge">{selectedStar.constellation}</span>
            )}
            <button className="star-detail-close" onClick={() => setSelectedStar(null)}>×</button>
          </div>

          {selectedStar.tags?.length > 0 && (
            <div className="star-detail-meta">
              {selectedStar.tags.map((t: string) => (
                <span key={t} className="tag-chip">#{t}</span>
              ))}
              {selectedStar.created && (
                <span className="star-date">{new Date(selectedStar.created).toLocaleDateString()}</span>
              )}
            </div>
          )}

          <div className="star-detail-content">
            {loadingMd ? (
              <p className="vault-loading-text">Loading content...</p>
            ) : starMarkdown ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{starMarkdown}</ReactMarkdown>
            ) : (
              <p className="vault-loading-text">No content available</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
