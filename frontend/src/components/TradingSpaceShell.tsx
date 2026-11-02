/**
 * TradingSpaceShell — Full per-trading-space operating environment.
 * Tabs: Overview · Chat · Tasks · Orders · Pool · Research · Strategies · Memory · Agents · Artifacts · Receipts · Federation
 */
import React, { useCallback, useEffect, useState } from 'react'
import {
  Activity, BarChart2, BookOpen, Database, DollarSign, Eye, FileCheck,
  Globe, Package, TrendingUp, Users, Zap, Star, MessageSquare,
} from 'lucide-react'
import GuildChat from './GuildChat'
import WorkspaceMemoryViewer from './WorkspaceMemoryViewer'
import TradingSpaceTasks from './trading/TradingSpaceTasks'
import TradingSpacePool from './trading/TradingSpacePool'

// ── Types ──────────────────────────────────────────────────────────────────────

interface TradingSpace {
  id: string
  name: string
  chain: string
  wallet?: string
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
  metadata?: Record<string, unknown>
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

interface Strategy {
  id: string
  name: string
  status: string
  description?: string
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
  { id: 'orders', label: 'Orders', icon: <DollarSign size={13} /> },
  { id: 'pool', label: 'Pool', icon: <Zap size={13} /> },
  { id: 'research', label: 'Research', icon: <Eye size={13} /> },
  { id: 'strategies', label: 'Strategies', icon: <TrendingUp size={13} /> },
  { id: 'memory', label: 'Memory', icon: <Database size={13} /> },
  { id: 'agents', label: 'Agents', icon: <Users size={13} /> },
  { id: 'artifacts', label: 'Artifacts', icon: <Package size={13} /> },
  { id: 'receipts', label: 'Receipts', icon: <FileCheck size={13} /> },
  { id: 'federation', label: 'Federation', icon: <Globe size={13} /> },
]

const RESEARCH_TABS = ['alpha', 'signals', 'wallet', 'degen', 'narratives', 'watch']

// ── Sub-panels ────────────────────────────────────────────────────────────────

function OverviewPanel({
  space, slug, roster, authHeaders,
}: {
  space: TradingSpace; slug: string; roster: RosterEntry[]
  authHeaders: () => Record<string, string>
}) {
  const [taskCounts, setTaskCounts] = useState<Record<string, number>>({})
  const [poolCount, setPoolCount] = useState<number | null>(null)

  useEffect(() => {
    const h = authHeaders()
    const base = '/api/guilds/' + slug + '/trading-spaces/' + space.id
    const statuses = ['proposed', 'claimed', 'review', 'accepted']
    Promise.all(statuses.map(s => fetch(base + '/tasks?status=' + s + '&limit=1', { headers: h })))
      .then(async rs => {
        const counts: Record<string, number> = {}
        for (let i = 0; i < rs.length; i++) {
          if (rs[i].ok) {
            const d = await rs[i].json()
            counts[statuses[i]] = d.total ?? (d.tasks?.length ?? 0)
          }
        }
        setTaskCounts(counts)
      })
      .catch(() => {})
    fetch(base + '/pools', { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setPoolCount((d.pools || []).length))
      .catch(() => {})
  }, [slug, space.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const online = roster.filter(r => r.presence_state !== 'offline')

  const stats = [
    { label: 'Proposed', value: taskCounts.proposed ?? '—', color: '#f59e0b' },
    { label: 'In Progress', value: taskCounts.claimed ?? '—', color: 'var(--cyan)' },
    { label: 'In Review', value: taskCounts.review ?? '—', color: '#a78bfa' },
    { label: 'Settled', value: taskCounts.accepted ?? '—', color: '#3cc878' },
    { label: 'Pools', value: poolCount ?? '—', color: '#8B5CF6' },
    { label: 'Agents Live', value: online.length + ' / ' + roster.length, color: '#3cc878' },
  ]

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: 20, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>{space.name}</div>
        {space.description && <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>{space.description}</div>}
        <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' as const }}>
          {space.chain && (
            <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: 'rgba(0,200,180,0.1)', color: 'var(--cyan)', fontWeight: 600 }}>
              {space.chain}
            </span>
          )}
          {space.wallet && (
            <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>
              {space.wallet.slice(0, 6)}...{space.wallet.slice(-4)}
            </span>
          )}
          <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 3, background: space.status === 'active' ? 'rgba(60,200,120,0.12)' : 'rgba(255,255,255,0.06)', color: space.status === 'active' ? '#3cc878' : 'var(--muted)' }}>
            {space.status}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
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

function OrdersPanel({ slug, spaceId, authHeaders }: { slug: string; spaceId: string; authHeaders: () => Record<string, string> }) {
  const [orders, setOrders] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const h = authHeaders()
    fetch('/api/trading/orders', { headers: h })
      .then(r => {
        if (!r.ok) throw new Error('not_available')
        return r.json()
      })
      .then(d => setOrders(d.orders || d || []))
      .catch(e => setError(e.message === 'not_available' ? 'Connect wallet to view orders' : 'Failed to load orders'))
      .finally(() => setLoading(false))
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading orders...</div>
  if (error) return (
    <div style={{ padding: 40, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
      <DollarSign size={32} style={{ color: 'var(--muted)', opacity: 0.4 }} />
      <div style={{ fontSize: 14, color: 'var(--muted)', textAlign: 'center' }}>{error}</div>
    </div>
  )

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 10, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>Orders</div>
      {orders.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No orders yet.</div>}
      {orders.map((o: any) => (
        <div key={o.id || o.order_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: (o.side === 'buy' ? 'rgba(60,200,120,0.12)' : 'rgba(239,68,68,0.12)'), color: (o.side === 'buy' ? '#3cc878' : '#ef4444'), textTransform: 'uppercase' as const }}>{o.side || 'order'}</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{o.symbol || o.market || '—'}</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: 10, color: 'var(--muted)' }}>{o.status || ''}</span>
          </div>
          {(o.amount || o.size) && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>Size: {o.amount || o.size}{o.price ? ' @ ' + o.price : ''}</div>}
        </div>
      ))}
    </div>
  )
}

function ResearchPanel({ slug, spaceId, authHeaders }: { slug: string; spaceId: string; authHeaders: () => Record<string, string> }) {
  const [activeResearch, setActiveResearch] = useState('alpha')
  const [data, setData] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState<Record<string, boolean>>({})

  function loadTab(tab: string) {
    if (data[tab] !== undefined) return
    setLoading(prev => ({ ...prev, [tab]: true }))
    const h = authHeaders()
    fetch('/api/guilds/' + slug + '/trading-spaces/' + spaceId + '/research/' + tab, { headers: h })
      .then(r => r.ok ? r.json() : { error: 'No data' })
      .then(d => setData(prev => ({ ...prev, [tab]: d })))
      .catch(() => setData(prev => ({ ...prev, [tab]: { error: 'Failed to load' } })))
      .finally(() => setLoading(prev => ({ ...prev, [tab]: false })))
  }

  useEffect(() => { loadTab('alpha') }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function handleTabClick(tab: string) {
    setActiveResearch(tab)
    loadTab(tab)
  }

  function renderData(d: any): React.ReactNode {
    if (!d) return <div style={{ color: 'var(--muted)', textAlign: 'center', marginTop: 40 }}>No data.</div>
    if (d.error) return <div style={{ color: '#ef4444', textAlign: 'center', marginTop: 40 }}>{d.error}</div>
    const entries = Array.isArray(d) ? d : (d.data || d.results || d.items || Object.entries(d).map(([k, v]) => ({ key: k, value: v })))
    if (Array.isArray(entries) && entries.length > 0) {
      const keys = Object.keys(entries[0] || {}).slice(0, 6)
      return (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {keys.map(k => (
                  <th key={k} style={{ padding: '6px 10px', textAlign: 'left', fontSize: 9, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.06em', color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.slice(0, 50).map((row: any, i: number) => (
                <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  {keys.map(k => (
                    <td key={k} style={{ padding: '7px 10px', color: 'var(--text)', fontSize: 12 }}>
                      {typeof row[k] === 'object' ? JSON.stringify(row[k]).slice(0, 40) : String(row[k] ?? '—')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    }
    return <pre style={{ fontSize: 11, color: 'var(--muted)', overflowX: 'auto', whiteSpace: 'pre-wrap' as const }}>{JSON.stringify(d, null, 2)}</pre>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'rgba(0,0,0,0.1)', flexShrink: 0, overflowX: 'auto' }}>
        {RESEARCH_TABS.map(tab => (
          <button key={tab} onClick={() => handleTabClick(tab)} style={{ padding: '7px 14px', fontSize: 12, fontWeight: activeResearch === tab ? 600 : 400, color: activeResearch === tab ? 'var(--text)' : 'var(--muted)', background: 'none', border: 'none', cursor: 'pointer', borderBottom: activeResearch === tab ? '2px solid var(--cyan)' : '2px solid transparent', textTransform: 'capitalize' as const, flexShrink: 0 }}>
            {tab}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
        {loading[activeResearch] ? (
          <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>Loading {activeResearch}...</div>
        ) : renderData(data[activeResearch])}
      </div>
    </div>
  )
}

function StrategiesPanel({ authHeaders }: { authHeaders: () => Record<string, string> }) {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [loading, setLoading] = useState(true)
  const [actioning, setActioning] = useState<string | null>(null)

  function loadStrategies() {
    const h = authHeaders()
    fetch('/api/trading/strategies', { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setStrategies(d.strategies || d || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadStrategies() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function toggleStrategy(id: string, status: string) {
    setActioning(id)
    const h = authHeaders()
    const endpoint = status === 'armed' ? '/api/trading/strategies/' + id + '/disarm' : '/api/trading/strategies/' + id + '/arm'
    try {
      await fetch(endpoint, { method: 'POST', headers: h })
      loadStrategies()
    } finally { setActioning(null) }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>Loading strategies...</div>

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 12, height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>Trading Strategies</div>
      {strategies.length === 0 && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No strategies. Create one from the Trading terminal.</div>
      )}
      {strategies.map(s => {
        const armed = s.status === 'armed' || s.status === 'active'
        const sc = armed ? '#3cc878' : s.status === 'disabled' ? 'var(--muted)' : '#f59e0b'
        return (
          <div key={s.id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{s.name}</span>
                <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: sc + '22', color: sc, textTransform: 'uppercase' as const }}>{s.status}</span>
              </div>
              {s.description && <div style={{ fontSize: 11, color: 'var(--muted)' }}>{s.description.slice(0, 80)}</div>}
            </div>
            <button
              className="btn btn-sm"
              onClick={() => toggleStrategy(s.id, s.status)}
              disabled={actioning === s.id}
              style={{ fontSize: 11, background: armed ? 'rgba(239,68,68,0.1)' : 'rgba(60,200,120,0.1)', color: armed ? '#ef4444' : '#3cc878' }}
            >
              {actioning === s.id ? '...' : armed ? 'Disarm' : 'Arm'}
            </button>
          </div>
        )
      })}
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
    fetch('/api/guilds/' + slug + '/trading-spaces/' + spaceId + '/artifacts', { headers: h })
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
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)' }}>{fmt(art.created_at)}{art.hash ? ' · hash: ' + trunc(art.hash, 10) : ''}</div>
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
    fetch('/api/guilds/' + slug + '/trading-spaces/' + spaceId + '/receipts', { headers: h })
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

function FederationPlaceholder() {
  return (
    <div style={{ padding: '40px 20px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
      <Globe size={40} style={{ color: 'var(--muted)', opacity: 0.4 }} />
      <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>Federation</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', textAlign: 'center', maxWidth: 320 }}>
        Cross-space federation for trading spaces is coming in a future phase. This will enable coordinated strategies across guilds.
      </div>
      <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 20px', width: '100%', maxWidth: 360 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase' as const, letterSpacing: '0.08em', marginBottom: 8 }}>Planned</div>
        {['Cross-guild signal sharing', 'Federated pool coordination', 'DIP transport for trade signals', 'Witness attestation for joint strategies'].map(item => (
          <div key={item} style={{ fontSize: 12, color: 'var(--muted)', padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>○ {item}</div>
        ))}
      </div>
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
    fetch('/api/guilds/' + slug + '/trading-spaces/' + spaceId + '/memory', { headers: h })
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
      await fetch('/api/guilds/' + slug + '/trading-spaces/' + spaceId + '/memory/' + encodeURIComponent(newKey), {
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

// ── Main Shell ─────────────────────────────────────────────────────────────────

export default function TradingSpaceShell({ slug, spaceId }: Props) {
  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const humanSession = localStorage.getItem('vantage_human_session') || ''

  const authHeaders = useCallback((): Record<string, string> => {
    if (apiKey) return { 'X-Agent-Key': apiKey }
    if (humanSession) return { 'X-Human-Session': humanSession }
    return {}
  }, [apiKey, humanSession])

  const [activeTab, setActiveTab] = useState('overview')
  const [space, setSpace] = useState<TradingSpace | null>(null)
  const [roster, setRoster] = useState<RosterEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const h = authHeaders()
    Promise.all([
      fetch('/api/guilds/' + slug + '/trading-spaces/' + spaceId, { headers: h }),
      fetch('/api/guilds/' + slug + '/roster', { headers: h }),
    ]).then(async ([sr, rr]) => {
      if (sr.ok) setSpace(await sr.json())
      if (rr.ok) { const d = await rr.json(); setRoster(d.agents || d.roster || []) }
    }).catch(() => {}).finally(() => setLoading(false))
  }, [slug, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  const authHeadersObj = authHeaders()
  const visibleTabs = TABS.filter(t => t.id !== 'memory' || !!apiKey)
  const liveCount = roster.filter(r => r.presence_state !== 'offline').length

  function renderTab(): React.ReactNode {
    if (!space) return null
    switch (activeTab) {
      case 'overview':
        return <OverviewPanel space={space} slug={slug} roster={roster} authHeaders={authHeaders} />
      case 'chat':
        return <GuildChat slug={slug} selectedChannelSlug={space.chat_channel_slug || spaceId} />
      case 'tasks':
        return <TradingSpaceTasks slug={slug} spaceId={spaceId} authHeaders={authHeadersObj} />
      case 'orders':
        return <OrdersPanel slug={slug} spaceId={spaceId} authHeaders={authHeaders} />
      case 'pool':
        return <TradingSpacePool slug={slug} spaceId={spaceId} authHeaders={authHeadersObj} />
      case 'research':
        return <ResearchPanel slug={slug} spaceId={spaceId} authHeaders={authHeaders} />
      case 'strategies':
        return <StrategiesPanel authHeaders={authHeaders} />
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
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--muted)', fontSize: 13 }}>Loading trading space...</div>
  }

  if (!space) {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#ef4444', fontSize: 13 }}>Trading space not found.</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <BarChart2 size={16} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>{space.name}</div>
        {space.chain && (
          <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, background: 'rgba(0,200,180,0.1)', color: 'var(--cyan)', fontWeight: 600 }}>{space.chain}</span>
        )}
        {space.wallet && (
          <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>{space.wallet.slice(0, 6)}...{space.wallet.slice(-4)}</span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>trading space</span>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'rgba(0,0,0,0.2)', flexShrink: 0, overflowX: 'auto' }}>
        {visibleTabs.map(tab => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '8px 14px', fontSize: 12, fontWeight: activeTab === tab.id ? 600 : 400, color: activeTab === tab.id ? 'var(--text)' : 'var(--muted)', background: 'none', border: 'none', cursor: 'pointer', flexShrink: 0, borderBottom: activeTab === tab.id ? '2px solid var(--cyan)' : '2px solid transparent', transition: 'all 0.12s' }}>
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
