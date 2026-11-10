/**
 * ContentCreationShell — Full per-content-space operating environment.
 * Tabs: Overview · Chat · Tasks · Projects · Assets · Providers · Publish · Memory · Agents · Artifacts · Receipts · Federation
 */
import React, { useCallback, useEffect, useState } from 'react'
import {
  Activity, Database, FileCheck, Film, Globe, Package,
  Star, Users, MessageSquare, BookOpen, Layers, Radio,
} from 'lucide-react'
import GuildChat from './GuildChat'
import ContentSpaceTasks from './content/ContentSpaceTasks'
import ContentProjectBoard from './content/ContentProjectBoard'
import ContentAssetLibrary from './content/ContentAssetLibrary'

// ── Types ──────────────────────────────────────────────────────────────────────

interface ContentSpace {
  id: string
  name: string
  mcp_provider?: string
  series_name?: string
  description?: string
  status: string
  chat_channel_slug?: string
}

interface RosterEntry {
  agent_id: number
  agent_name: string
  role: string
  bio: string
  avatar_url: string
  presence_state: string
}

interface ArtifactItem {
  artifact_id: string
  task_id?: number
  agent_name?: string
  type: string
  uri?: string
  hash?: string
  created_at: string
}

interface ReceiptItem {
  receipt_id: string
  task_id?: number
  agent_name?: string
  capability?: string
  verified?: boolean
  receipt_hash?: string
  sui_tx_digest?: string | null
  created_at: string
}

interface ContentAsset {
  id: string
  kind: string
  title: string
  status: string
  content_uri?: string | null
}

interface Props {
  slug: string
  spaceId: string
}

// ── Constants ─────────────────────────────────────────────────────────────────

const PRESENCE_COLOR: Record<string, string> = {
  available: '#3cc878', thinking: '#a78bfa', working: '#3cc878',
  needs_review: '#f59e0b', blocked: '#ef4444', offline: 'rgba(255,255,255,0.2)',
}

const TABS = [
  { id: 'overview', label: 'Overview', icon: <Star size={13} /> },
  { id: 'chat', label: 'Chat', icon: <MessageSquare size={13} /> },
  { id: 'tasks', label: 'Tasks', icon: <Activity size={13} /> },
  { id: 'projects', label: 'Projects', icon: <Layers size={13} /> },
  { id: 'assets', label: 'Assets', icon: <Film size={13} /> },
  { id: 'providers', label: 'Providers', icon: <Radio size={13} /> },
  { id: 'publish', label: 'Publish', icon: <BookOpen size={13} /> },
  { id: 'memory', label: 'Memory', icon: <Database size={13} /> },
  { id: 'agents', label: 'Agents', icon: <Users size={13} /> },
  { id: 'artifacts', label: 'Artifacts', icon: <Package size={13} /> },
  { id: 'receipts', label: 'Receipts', icon: <FileCheck size={13} /> },
  { id: 'federation', label: 'Federation', icon: <Globe size={13} /> },
]

const PROVIDERS = [
  { id: 'vantage_anvil', name: 'Vantage Anvil', icon: '⚡', desc: 'Native AI video generation pipeline' },
  { id: 'higgsfield', name: 'Higgsfield', icon: '🎬', desc: 'Cinematic AI video production' },
  { id: 'heygen', name: 'HeyGen', icon: '🎤', desc: 'AI avatar & presenter videos' },
]

// ── Sub-panels ────────────────────────────────────────────────────────────────

function OverviewPanel({
  space, slug, roster, authHeaders,
}: {
  space: ContentSpace; slug: string; roster: RosterEntry[]
  authHeaders: () => Record<string, string>
}) {
  const [projectCount, setProjectCount] = useState<number | null>(null)
  const [taskCount, setTaskCount] = useState<number | null>(null)
  const [assetCount, setAssetCount] = useState<number | null>(null)

  useEffect(() => {
    const h = authHeaders()
    const base = '/api/guilds/' + slug + '/content-spaces/' + space.id
    Promise.all([
      fetch(base + '/projects', { headers: h }),
      fetch(base + '/tasks', { headers: h }),
      fetch(base + '/assets', { headers: h }),
    ]).then(async ([pr, tr, ar]) => {
      if (pr.ok) { const d = await pr.json(); setProjectCount((d.projects || []).length) }
      if (tr.ok) { const d = await tr.json(); setTaskCount(d.total ?? (d.tasks || []).length) }
      if (ar.ok) { const d = await ar.json(); setAssetCount((d.assets || []).length) }
    }).catch(() => {})
  }, [slug, space.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const online = roster.filter(r => r.presence_state !== 'offline')

  const stats = [
    { label: 'Projects', value: projectCount ?? '—', color: 'var(--cyan)' },
    { label: 'Tasks', value: taskCount ?? '—', color: '#f59e0b' },
    { label: 'Assets', value: assetCount ?? '—', color: '#8B5CF6' },
    { label: 'Agents Live', value: online.length + ' / ' + roster.length, color: '#3cc878' },
  ]

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: 20, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>{space.name}</div>
        {space.description && <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>{space.description}</div>}
        <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' as const }}>
          {space.mcp_provider && (
            <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: 'rgba(139,92,246,0.12)', color: '#8B5CF6', fontWeight: 600 }}>{space.mcp_provider}</span>
          )}
          {space.series_name && (
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>Series: {space.series_name}</span>
          )}
          <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 3, background: space.status === 'active' ? 'rgba(60,200,120,0.12)' : 'rgba(255,255,255,0.06)', color: space.status === 'active' ? '#3cc878' : 'var(--muted)' }}>
            {space.status}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
        {stats.map(s => (
          <div key={s.label} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase' as const, letterSpacing: '0.08em', marginBottom: 6 }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: s.color }}>{String(s.value)}</div>
          </div>
        ))}
      </div>

      {roster.filter(r => r.presence_state === 'working').length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: '#3cc878', textTransform: 'uppercase' as const, letterSpacing: '0.1em', marginBottom: 10 }}>Currently Working</div>
          {roster.filter(r => r.presence_state === 'working').map(agent => (
            <div key={agent.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', background: 'rgba(60,200,120,0.06)', border: '1px solid rgba(60,200,120,0.2)', borderRadius: 8, marginBottom: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#3cc878', display: 'inline-block', flexShrink: 0 }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{agent.agent_name}</div>
                <div style={{ fontSize: 11, color: '#3cc878' }}>Working</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ProvidersPanel({
  slug, spaceId, space, authHeaders, onProviderChange,
}: {
  slug: string; spaceId: string; space: ContentSpace
  authHeaders: () => Record<string, string>
  onProviderChange: (provider: string) => void
}) {
  const [selecting, setSelecting] = useState<string | null>(null)

  async function selectProvider(providerId: string) {
    setSelecting(providerId)
    try {
      const h = { ...authHeaders(), 'Content-Type': 'application/x-www-form-urlencoded' }
      const body = new URLSearchParams({ mcp_provider: providerId })
      const r = await fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId, {
        method: 'PATCH', headers: h, body: body.toString(),
      })
      if (r.ok) onProviderChange(providerId)
    } finally { setSelecting(null) }
  }

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 14, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Content Providers</div>
      <div style={{ fontSize: 12, color: 'var(--muted)' }}>Select the MCP provider for this content space. The active provider will receive generation requests.</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {PROVIDERS.map(p => {
          const active = space.mcp_provider === p.id
          return (
            <div key={p.id} style={{ background: active ? 'rgba(139,92,246,0.08)' : 'rgba(255,255,255,0.03)', border: '1px solid ' + (active ? 'rgba(139,92,246,0.4)' : 'var(--border)'), borderRadius: 10, padding: '16px', display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ fontSize: 28, flexShrink: 0 }}>{p.icon}</div>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{p.name}</span>
                  {active && <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: 'rgba(60,200,120,0.15)', color: '#3cc878', textTransform: 'uppercase' as const }}>Active</span>}
                </div>
                <div style={{ fontSize: 12, color: 'var(--muted)' }}>{p.desc}</div>
              </div>
              {!active && (
                <button
                  className="btn btn-sm"
                  onClick={() => selectProvider(p.id)}
                  disabled={selecting === p.id}
                  style={{ fontSize: 11, flexShrink: 0 }}
                >
                  {selecting === p.id ? '...' : 'Select'}
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function PublishPanel({
  slug, spaceId, authHeaders,
}: {
  slug: string; spaceId: string; authHeaders: () => Record<string, string>
}) {
  const [drafts, setDrafts] = useState<ContentAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [publishing, setPublishing] = useState<string | null>(null)
  const [schedules, setSchedules] = useState<Record<string, string>>({})

  function loadDrafts() {
    const h = authHeaders()
    fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId + '/assets?status=draft', { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setDrafts(d.assets || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadDrafts() }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  async function publishAsset(id: string) {
    setPublishing(id)
    try {
      const h = { ...authHeaders(), 'Content-Type': 'application/x-www-form-urlencoded' }
      const body = new URLSearchParams({ status: 'published' })
      if (schedules[id]) body.set('scheduled_at', schedules[id])
      await fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId + '/assets/' + id, {
        method: 'PATCH', headers: h, body: body.toString(),
      })
      loadDrafts()
    } finally { setPublishing(null) }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading drafts...</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 14, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Publish Pipeline</div>
      <div style={{ fontSize: 12, color: 'var(--muted)' }}>Draft assets ready for publication. Schedule or publish immediately.</div>

      {drafts.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, marginTop: 40 }}>
          <BookOpen size={32} style={{ color: 'var(--muted)', opacity: 0.4 }} />
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>No draft assets. Create assets in the Assets tab.</div>
        </div>
      )}

      {drafts.map(asset => (
        <div key={asset.id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: 'rgba(245,158,11,0.15)', color: '#f59e0b', textTransform: 'uppercase' as const }}>{asset.kind}</span>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{asset.title}</span>
          </div>
          {asset.content_uri && (
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, fontFamily: 'monospace', wordBreak: 'break-all' as const }}>
              {asset.content_uri}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' as const }}>
            <input
              type="datetime-local"
              value={schedules[asset.id] || ''}
              onChange={e => setSchedules(prev => ({ ...prev, [asset.id]: e.target.value }))}
              style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 11, outline: 'none' }}
            />
            <button
              className="btn btn-sm"
              onClick={() => publishAsset(asset.id)}
              disabled={publishing === asset.id}
              style={{ fontSize: 11, background: 'rgba(60,200,120,0.1)', color: '#3cc878', border: '1px solid rgba(60,200,120,0.3)' }}
            >
              {publishing === asset.id ? '...' : schedules[asset.id] ? 'Schedule' : 'Publish Now'}
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

function AgentsPanel({ roster }: { roster: RosterEntry[] }) {
  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 12, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>Space Agents — {roster.length}</div>
      {roster.map(agent => (
        <div key={agent.agent_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
          <div style={{ width: 38, height: 38, borderRadius: 10, background: 'rgba(138,75,255,0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, flexShrink: 0 }}>
            {agent.avatar_url ? <img src={agent.avatar_url} style={{ width: '100%', height: '100%', borderRadius: 10, objectFit: 'cover' as const }} alt="" /> : '◉'}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{agent.agent_name}</span>
              <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'rgba(138,75,255,0.15)', color: '#a78bfa' }}>{agent.role}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: PRESENCE_COLOR[agent.presence_state] || PRESENCE_COLOR.offline, display: 'inline-block' }} />
              <span style={{ fontSize: 11, color: PRESENCE_COLOR[agent.presence_state] || 'var(--muted)' }}>
                {agent.presence_state === 'needs_review' ? 'Needs Review' : agent.presence_state.charAt(0).toUpperCase() + agent.presence_state.slice(1)}
              </span>
            </div>
          </div>
        </div>
      ))}
      {roster.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No agents in this space yet.</div>}
    </div>
  )
}

function ArtifactsPanel({ slug, spaceId, authHeaders }: { slug: string; spaceId: string; authHeaders: () => Record<string, string> }) {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const h = authHeaders()
    fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId + '/artifacts', { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setArtifacts(d.artifacts || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  const TYPE_COLORS: Record<string, string> = { code: '#3cc878', analysis: 'var(--cyan)', report: '#f59e0b', file: '#a78bfa', message: 'rgba(255,255,255,0.5)' }
  function trunc(s?: string, n = 16): string { return !s ? '—' : s.length <= n ? s : s.slice(0, n) + '...' }
  function fmt(ts: string): string { try { return new Date(ts).toLocaleString() } catch { return ts } }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading artifacts...</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>Artifacts</div>
      {artifacts.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No artifacts yet.</div>}
      {artifacts.map(art => (
        <div key={art.artifact_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.08em', padding: '2px 7px', borderRadius: 4, background: (TYPE_COLORS[art.type] || 'var(--cyan)') + '22', color: TYPE_COLORS[art.type] || 'var(--cyan)' }}>{art.type}</span>
            <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>{trunc(art.artifact_id)}</span>
            <span style={{ flex: 1 }} />
            {art.agent_name && <span style={{ fontSize: 10, color: '#a78bfa' }}>{art.agent_name}</span>}
          </div>
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)' }}>{fmt(art.created_at)}</div>
        </div>
      ))}
    </div>
  )
}

function ReceiptsPanel({ slug, spaceId, authHeaders }: { slug: string; spaceId: string; authHeaders: () => Record<string, string> }) {
  const [receipts, setReceipts] = useState<ReceiptItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const h = authHeaders()
    fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId + '/receipts', { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setReceipts(d.receipts || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  function trunc(s?: string, n = 16): string { return !s ? '—' : s.length <= n ? s : s.slice(0, n) + '...' }
  function fmt(ts: string): string { try { return new Date(ts).toLocaleString() } catch { return ts } }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading receipts...</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>Act Receipts</div>
      {receipts.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No receipts yet.</div>}
      {receipts.map(rec => (
        <div key={rec.receipt_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: rec.verified ? '#3cc878' : '#f59e0b' }}>{rec.verified ? '✓' : '○'}</span>
            <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>{trunc(rec.receipt_id)}</span>
            <span style={{ flex: 1 }} />
            {rec.agent_name && <span style={{ fontSize: 10, color: '#a78bfa' }}>{rec.agent_name}</span>}
          </div>
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', display: 'flex', gap: 12, flexWrap: 'wrap' as const }}>
            {rec.capability && <span style={{ padding: '1px 6px', background: 'rgba(255,255,255,0.05)', borderRadius: 3, color: 'var(--muted)' }}>{rec.capability}</span>}
            <span>{fmt(rec.created_at)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

function InlineMemoryViewer({ slug, spaceId, authHeaders }: { slug: string; spaceId: string; authHeaders: () => Record<string, string> }) {
  const [entries, setEntries] = useState<Array<{ key: string; value: any }>>([])
  const [loading, setLoading] = useState(true)
  const [newKey, setNewKey] = useState('')
  const [newVal, setNewVal] = useState('')
  const [saving, setSaving] = useState(false)

  function loadMemory() {
    const h = authHeaders()
    fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId + '/memory', { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) {
          const arr = Array.isArray(d) ? d : Object.entries(d).map(([key, value]) => ({ key, value }))
          setEntries(arr)
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadMemory() }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  async function saveKey() {
    if (!newKey.trim()) return
    setSaving(true)
    try {
      const h = { ...authHeaders(), 'Content-Type': 'application/x-www-form-urlencoded' }
      await fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId + '/memory/' + encodeURIComponent(newKey), {
        method: 'PUT', headers: h, body: new URLSearchParams({ value: newVal }).toString(),
      })
      setNewKey(''); setNewVal(''); loadMemory()
    } finally { setSaving(false) }
  }

  const inputStyle: React.CSSProperties = { background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12, outline: 'none' }

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 14, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Space Memory</div>
      <div style={{ display: 'flex', gap: 8 }}>
        <input value={newKey} onChange={e => setNewKey(e.target.value)} placeholder="key" style={{ ...inputStyle, flex: 1 }} />
        <input value={newVal} onChange={e => setNewVal(e.target.value)} placeholder="value" style={{ ...inputStyle, flex: 2 }} />
        <button className="btn btn-sm" onClick={saveKey} disabled={saving || !newKey.trim()} style={{ fontSize: 11 }}>{saving ? '...' : 'Save'}</button>
      </div>
      {loading && <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading...</div>}
      {!loading && entries.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 20 }}>No memory entries yet.</div>}
      {entries.map(({ key, value }) => (
        <div key={key} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--cyan)', marginBottom: 4, fontFamily: 'monospace' }}>{key}</div>
          <div style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'monospace', wordBreak: 'break-all' as const }}>
            {typeof value === 'object' ? JSON.stringify(value) : String(value)}
          </div>
        </div>
      ))}
    </div>
  )
}

function FederationPlaceholder() {
  return (
    <div style={{ padding: '40px 20px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
      <Globe size={40} style={{ color: 'var(--muted)', opacity: 0.4 }} />
      <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>Federation</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', textAlign: 'center', maxWidth: 320 }}>
        Cross-space content federation is coming soon. Coordinate publishing pipelines across guilds and nodes.
      </div>
      <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 20px', width: '100%', maxWidth: 360 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase' as const, letterSpacing: '0.08em', marginBottom: 8 }}>Planned</div>
        {['Federated content distribution', 'Cross-guild asset sharing', 'DIP-based publish receipts', 'Witness attestation for content'].map(item => (
          <div key={item} style={{ fontSize: 12, color: 'var(--muted)', padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>○ {item}</div>
        ))}
      </div>
    </div>
  )
}

// ── Main Shell ─────────────────────────────────────────────────────────────────

export default function ContentCreationShell({ slug, spaceId }: Props) {
  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const humanSession = localStorage.getItem('vantage_human_session') || ''

  const authHeaders = useCallback((): Record<string, string> => {
    if (apiKey) return { 'X-Agent-Key': apiKey }
    if (humanSession) return { 'X-Human-Session': humanSession }
    return {}
  }, [apiKey, humanSession])

  const [activeTab, setActiveTab] = useState('overview')
  const [space, setSpace] = useState<ContentSpace | null>(null)
  const [roster, setRoster] = useState<RosterEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const h = authHeaders()
    Promise.all([
      fetch('/api/guilds/' + slug + '/content-spaces/' + spaceId, { headers: h }),
      fetch('/api/guilds/' + slug + '/roster', { headers: h }),
    ]).then(async ([sr, rr]) => {
      if (sr.ok) setSpace(await sr.json())
      if (rr.ok) { const d = await rr.json(); setRoster(d.agents || d.roster || []) }
    }).catch(() => {}).finally(() => setLoading(false))
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  const authHeadersObj = authHeaders()
  // G12: no placeholder tab ships. Federation returns when cross-space federation is real.
  const visibleTabs = TABS.filter(t => (t.id !== 'memory' || !!apiKey) && t.id !== 'federation')
  const liveCount = roster.filter(r => r.presence_state !== 'offline').length

  function handleProviderChange(provider: string) {
    setSpace(prev => prev ? { ...prev, mcp_provider: provider } : prev)
  }

  function renderTab(): React.ReactNode {
    if (!space) return null
    switch (activeTab) {
      case 'overview':
        return <OverviewPanel space={space} slug={slug} roster={roster} authHeaders={authHeaders} />
      case 'chat':
        return <GuildChat slug={slug} selectedChannelSlug={space.chat_channel_slug || spaceId} />
      case 'tasks':
        return <ContentSpaceTasks slug={slug} spaceId={spaceId} authHeaders={authHeadersObj} />
      case 'projects':
        return <ContentProjectBoard slug={slug} spaceId={spaceId} authHeaders={authHeadersObj} />
      case 'assets':
        return <ContentAssetLibrary slug={slug} spaceId={spaceId} authHeaders={authHeadersObj} />
      case 'providers':
        return <ProvidersPanel slug={slug} spaceId={spaceId} space={space} authHeaders={authHeaders} onProviderChange={handleProviderChange} />
      case 'publish':
        return <PublishPanel slug={slug} spaceId={spaceId} authHeaders={authHeaders} />
      case 'memory':
        return <InlineMemoryViewer slug={slug} spaceId={spaceId} authHeaders={authHeaders} />
      case 'agents':
        return <AgentsPanel roster={roster} />
      case 'artifacts':
        return <ArtifactsPanel slug={slug} spaceId={spaceId} authHeaders={authHeaders} />
      case 'receipts':
        return <ReceiptsPanel slug={slug} spaceId={spaceId} authHeaders={authHeaders} />
      case 'federation':
        return <FederationPlaceholder />
      default:
        return null
    }
  }

  if (loading) {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--muted)', fontSize: 13 }}>Loading content space...</div>
  }

  if (!space) {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#ef4444', fontSize: 13 }}>Content space not found.</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <Film size={16} style={{ color: '#8B5CF6', flexShrink: 0 }} />
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>{space.name}</div>
        {space.mcp_provider && (
          <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, background: 'rgba(139,92,246,0.12)', color: '#8B5CF6', fontWeight: 600 }}>{space.mcp_provider}</span>
        )}
        {space.series_name && (
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>{space.series_name}</span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>content space</span>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'rgba(0,0,0,0.2)', flexShrink: 0, overflowX: 'auto' }}>
        {visibleTabs.map(tab => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '8px 14px', fontSize: 12, fontWeight: activeTab === tab.id ? 600 : 400, color: activeTab === tab.id ? 'var(--text)' : 'var(--muted)', background: 'none', border: 'none', cursor: 'pointer', flexShrink: 0, borderBottom: activeTab === tab.id ? '2px solid #8B5CF6' : '2px solid transparent', transition: 'all 0.12s' }}>
            <span style={{ opacity: activeTab === tab.id ? 1 : 0.6 }}>{tab.icon}</span>
            {tab.label}
          </button>
        ))}
        {liveCount > 0 && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 5, padding: '0 14px', fontSize: 11, color: '#3cc878', flexShrink: 0 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#3cc878', display: 'inline-block' }} />
            {liveCount} live
          </div>
        )}
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {renderTab()}
      </div>
    </div>
  )
}
