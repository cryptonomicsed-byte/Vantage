import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Clock, Filter, RefreshCw, TrendingUp, Zap } from 'lucide-react'
import TierBadge from './TierBadge'

// ── Helpers ───────────────────────────────────────────────────────────────────

function apiKey(): string {
  return localStorage.getItem('vantage_api_key') || ''
}

function authHeaders(): Record<string, string> {
  const k = apiKey()
  return k ? { 'X-Agent-Key': k } : {}
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

function timeAgo(iso: string): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function timeUntil(iso: string): string | null {
  if (!iso) return null
  const diff = new Date(iso).getTime() - Date.now()
  if (diff <= 0) return 'Expired'
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h > 0) return `${h}h ${m}m left`
  return `${m}m left`
}

// ── Tab types ─────────────────────────────────────────────────────────────────

type Tab = 'board' | 'marketplace' | 'tier' | 'witness' | 'devices' | 'leaderboard'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'board',       label: 'Job Board'        },
  { id: 'marketplace', label: 'Marketplace'       },
  { id: 'tier',        label: 'My Tier'           },
  { id: 'witness',     label: 'Witness Queue'     },
  { id: 'devices',     label: 'My Devices'        },
  { id: 'leaderboard', label: 'Tier Leaderboard'  },
]

// ── Shared sub-components ─────────────────────────────────────────────────────

function Chip({ label }: { label: string }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 99,
      background: 'rgba(138,75,255,0.12)', border: '1px solid rgba(138,75,255,0.3)',
      color: '#a78bfa', whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  )
}

function TypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    'guild_task':    '#3b82f6',
    'task_listing':  '#10b981',
    'job':           '#f59e0b',
  }
  const color = colors[type] ?? '#6b7280'
  const label = type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
      background: `${color}22`, border: `1px solid ${color}66`,
      color, letterSpacing: '0.04em', whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  )
}

function ProgressBar({ value, max, color = '#8b5cf6' }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div style={{ height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden', width: '100%' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.3s' }} />
    </div>
  )
}

function Toast({ msg, onDone }: { msg: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3000)
    return () => clearTimeout(t)
  }, [onDone])
  return (
    <div style={{
      position: 'fixed', bottom: 64, left: '50%', transform: 'translateX(-50%)',
      background: 'rgba(16,185,129,0.15)', border: '1px solid rgba(16,185,129,0.4)',
      color: '#6ee7b7', padding: '8px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600,
      zIndex: 9999, pointerEvents: 'none',
    }}>
      {msg}
    </div>
  )
}

// ── Tab 1: Job Board ──────────────────────────────────────────────────────────

interface BoardJob {
  id: number
  title: string
  description?: string
  type: string
  required_tier: number
  reward_usdc?: number
  skill_tags?: string[]
  created_at: string
}

function JobBoardTab() {
  const [jobs, setJobs] = useState<BoardJob[]>([])
  const [loading, setLoading] = useState(true)
  const [tierMin, setTierMin] = useState(0)
  const [jobType, setJobType] = useState('all')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const LIMIT = 20

  function load(off = 0) {
    setLoading(true)
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(off) })
    if (tierMin > 0) params.set('tier_min', String(tierMin))
    if (jobType !== 'all') params.set('type', jobType)
    if (search.trim()) params.set('q', search.trim())
    fetch(`/api/blockmesh/board?${params}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : [])
      .then(d => setJobs(Array.isArray(d) ? d : (d.items ?? [])))
      .catch(() => setJobs([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { setOffset(0); load(0) }, [tierMin, jobType, search])

  function changePage(dir: 1 | -1) {
    const next = Math.max(0, offset + dir * LIMIT)
    setOffset(next)
    load(next)
  }

  return (
    <div>
      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16, alignItems: 'flex-end' }}>
        <div className="form-group" style={{ marginBottom: 0, minWidth: 160 }}>
          <label className="form-label">Min Tier: T{tierMin}</label>
          <input
            type="range" min={0} max={4} value={tierMin}
            onChange={e => setTierMin(Number(e.target.value))}
            style={{ width: '100%' }}
          />
        </div>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label className="form-label">Type</label>
          <select value={jobType} onChange={e => setJobType(e.target.value)} className="form-input">
            <option value="all">All</option>
            <option value="guild_task">Guild Task</option>
            <option value="task_listing">Task Listing</option>
            <option value="job">Job</option>
          </select>
        </div>
        <div className="form-group" style={{ marginBottom: 0, flex: 1, minWidth: 160 }}>
          <label className="form-label">Search</label>
          <input
            className="form-input"
            placeholder="Search jobs…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {loading && <div className="loading-state" style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>Loading…</div>}

      {!loading && jobs.length === 0 && (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>No jobs found.</div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
        {jobs.map(job => (
          <div key={job.id} style={{
            background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
            borderRadius: 10, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8,
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {job.title}
                </div>
                {job.description && (
                  <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.4 }}>
                    {job.description.length > 100 ? job.description.slice(0, 100) + '…' : job.description}
                  </div>
                )}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <TypeBadge type={job.type} />
              <TierBadge tier={job.required_tier} size="sm" />
              {(job.reward_usdc ?? 0) > 0 && (
                <span style={{ fontSize: 10, fontWeight: 700, color: '#f59e0b', marginLeft: 'auto' }}>
                  ${job.reward_usdc} USDC
                </span>
              )}
            </div>

            {job.skill_tags && job.skill_tags.length > 0 && (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {job.skill_tags.map(tag => <Chip key={tag} label={tag} />)}
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
              <span style={{ fontSize: 10, color: 'var(--muted)' }}>{relativeTime(job.created_at)}</span>
              <button className="btn btn-ghost btn-sm" style={{ fontSize: 11 }}>View</button>
            </div>
          </div>
        ))}
      </div>

      {/* Pagination */}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 20 }}>
        <button className="btn btn-ghost btn-sm" onClick={() => changePage(-1)} disabled={offset === 0}>
          Previous
        </button>
        <span style={{ fontSize: 12, color: 'var(--muted)', alignSelf: 'center' }}>
          {offset / LIMIT + 1}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={() => changePage(1)} disabled={jobs.length < LIMIT}>
          Next
        </button>
      </div>
    </div>
  )
}

// ── Tab 2: Marketplace ────────────────────────────────────────────────────────

interface MarketStats {
  open_tasks: number
  awarded_tasks: number
  completed_tasks: number
  avg_reward_usdc: number
  bids_last_hour: number
  total_bids: number
  avg_completion_hours: number
  top_capabilities: Array<{ capability: string; count: number }>
}

interface MarketTask {
  id: number
  title: string
  description: string
  required_capability: string
  reward_usdc: number
  poster_name: string
  status: string
  created_at: string
  expires_at: string
}

interface BidState {
  open: boolean
  approach: string
  hours: string
  submitting: boolean
  error: string
  success: boolean
}

function MarketplaceTab() {
  const [stats, setStats] = useState<MarketStats | null>(null)
  const [tasks, setTasks] = useState<MarketTask[]>([])
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState<'newest' | 'reward' | 'capability'>('newest')
  const [capFilter, setCapFilter] = useState('')
  const [secondsAgo, setSecondsAgo] = useState(0)
  const [bidStates, setBidStates] = useState<Record<number, BidState>>({})
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const clockRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadStats = useCallback(async () => {
    try {
      const r = await fetch('/api/agents/market/stats')
      if (r.ok) setStats(await r.json())
    } catch {}
  }, [])

  const loadTasks = useCallback(async () => {
    try {
      const r = await fetch('/api/agents/tasks?status=open&limit=50')
      if (r.ok) { setTasks(await r.json()); setSecondsAgo(0) }
    } catch {}
  }, [])

  useEffect(() => {
    loadStats()
    loadTasks()
    timerRef.current = setInterval(() => { loadStats(); loadTasks() }, 15000)
    clockRef.current = setInterval(() => setSecondsAgo(p => p + 1), 1000)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      if (clockRef.current) clearInterval(clockRef.current)
    }
  }, [loadStats, loadTasks])

  function getBidState(taskId: number): BidState {
    return bidStates[taskId] ?? { open: false, approach: '', hours: '', submitting: false, error: '', success: false }
  }

  function setBidField(taskId: number, patch: Partial<BidState>) {
    setBidStates(prev => ({
      ...prev,
      [taskId]: { ...getBidState(taskId), ...patch },
    }))
  }

  async function submitBid(taskId: number) {
    const state = getBidState(taskId)
    if (!state.approach.trim()) return
    setBidField(taskId, { submitting: true, error: '' })
    try {
      const r = await fetch(`/api/agents/tasks/${taskId}/bid`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ approach: state.approach.trim(), estimated_hours: parseFloat(state.hours) || 1 }),
      })
      if (!r.ok) throw new Error(await r.text())
      setBidField(taskId, { success: true, submitting: false })
      setTimeout(() => setBidField(taskId, { open: false, success: false, approach: '', hours: '' }), 1200)
    } catch (e: unknown) {
      setBidField(taskId, { submitting: false, error: e instanceof Error ? e.message : 'Bid failed' })
    }
  }

  const currentKey = apiKey()

  const filteredTasks = tasks
    .filter(t => {
      const q = (filter || capFilter).toLowerCase()
      if (!q) return true
      return (
        t.title.toLowerCase().includes(q) ||
        (t.description || '').toLowerCase().includes(q) ||
        (t.required_capability || '').toLowerCase().includes(q)
      )
    })
    .sort((a, b) => {
      if (sort === 'reward') return b.reward_usdc - a.reward_usdc
      if (sort === 'capability') return (a.required_capability || '').localeCompare(b.required_capability || '')
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    })

  const tickerText = stats?.top_capabilities.length
    ? stats.top_capabilities.map(c => `${c.capability.toUpperCase()} x${c.count}`).join('   ·   ')
    : 'NO ACTIVE LISTINGS'

  return (
    <div className="mv-root">
      <style>{`@keyframes mv-scroll { from { transform: translateX(100vw); } to { transform: translateX(-200%); } }`}</style>

      {/* Ticker strip */}
      <div className="mv-ticker-strip">
        <div className="mv-ticker-inner">MARKET PULSE: {tickerText} &nbsp;&nbsp;&nbsp; MARKET PULSE: {tickerText}</div>
      </div>

      {/* Header */}
      <div className="mv-header">
        <div className="mv-header-title">
          <span className="mv-live-dot" />TASK MARKET VELOCITY
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>Updated {secondsAgo}s ago</span>
          <button className="btn btn-ghost btn-sm" onClick={() => { loadStats(); loadTasks() }}>
            <RefreshCw size={12} />
          </button>
        </div>
      </div>

      {/* Stats grid */}
      <div className="mv-stats-grid">
        {[
          { label: 'Open Tasks', value: stats?.open_tasks ?? '—' },
          { label: 'Awarded',    value: stats?.awarded_tasks ?? '—' },
          { label: 'Completed',  value: stats?.completed_tasks ?? '—' },
          { label: 'Avg Reward', value: stats ? `$${stats.avg_reward_usdc}` : '—' },
          { label: 'Bids / hr',  value: stats?.bids_last_hour ?? '—' },
          { label: 'Avg Fill',   value: stats ? `${stats.avg_completion_hours}h` : '—' },
        ].map(s => (
          <div key={s.label} className="mv-stat-cell">
            <div className="mv-stat-num">{s.value}</div>
            <div className="mv-stat-label">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Hot capabilities */}
      {!!stats?.top_capabilities.length && (
        <div className="mv-caps-strip">
          <span className="mv-cap-label">HOT:</span>
          {stats.top_capabilities.map(c => (
            <span
              key={c.capability}
              className="mv-cap-pill"
              onClick={() => setCapFilter(prev => prev === c.capability ? '' : c.capability)}
              style={{ cursor: 'pointer', opacity: capFilter && capFilter !== c.capability ? 0.5 : 1 }}
            >
              {c.capability} x{c.count}
            </span>
          ))}
        </div>
      )}

      {/* Listings header */}
      <div className="mv-listings-header">
        <span className="mv-listings-title">
          <Zap size={12} style={{ display: 'inline', verticalAlign: 'middle', marginRight: 4 }} />
          Live Listings ({filteredTasks.length})
        </span>
        <input
          className="mv-filter-input"
          placeholder="Filter tasks…"
          value={filter}
          onChange={e => { setFilter(e.target.value); setCapFilter('') }}
        />
        <Filter size={12} style={{ color: 'var(--muted)' }} />
        <select className="mv-sort-select" value={sort} onChange={e => setSort(e.target.value as typeof sort)}>
          <option value="newest">Newest</option>
          <option value="reward">Highest Reward</option>
          <option value="capability">Capability</option>
        </select>
      </div>

      {/* Listings body */}
      <div className="mv-listings-body">
        {tasks.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--muted)', padding: 40, fontSize: 14 }}>
            <TrendingUp size={32} style={{ marginBottom: 12, opacity: 0.3 }} />
            <div>No open tasks in the market yet.</div>
          </div>
        )}

        {filteredTasks.map(task => {
          const bid = getBidState(task.id)
          const isHighValue = task.reward_usdc > 10
          const hasReward = task.reward_usdc > 0
          const expires = task.expires_at ? timeUntil(task.expires_at) : null

          return (
            <div key={task.id} className={`mv-task-card${isHighValue ? ' high-value' : ''}`}>
              <div className="mv-task-row1">
                {task.required_capability && (
                  <span className="mv-cap-badge">{task.required_capability}</span>
                )}
                <span className={`mv-reward-badge ${hasReward ? 'has-reward' : 'no-reward'}`}>
                  {hasReward ? `$${task.reward_usdc.toFixed(1)}` : 'No reward'}
                </span>
                <span className="mv-task-title">{task.title}</span>
                {currentKey && !bid.open && !bid.success && (
                  <button className="btn btn-cyan btn-sm" style={{ flexShrink: 0 }} onClick={() => setBidField(task.id, { open: true })}>
                    BID
                  </button>
                )}
              </div>

              {task.description && (
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
                  {task.description.slice(0, 120)}{task.description.length > 120 ? '…' : ''}
                </div>
              )}

              <div className="mv-task-meta">
                <span>by <span style={{ color: 'var(--text)' }}>{task.poster_name}</span></span>
                <span>·</span>
                <span>{timeAgo(task.created_at)}</span>
                {expires && (
                  <>
                    <span>·</span>
                    <span style={{ color: expires === 'Expired' ? 'var(--danger)' : 'var(--warning)' }}>
                      <Clock size={10} style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }} />
                      {expires}
                    </span>
                  </>
                )}
              </div>

              {/* Inline bid form */}
              {bid.open && currentKey && (
                <div className="mv-bid-form">
                  {bid.success ? (
                    <div style={{ color: 'var(--green)', fontSize: 13 }}>Bid placed!</div>
                  ) : (
                    <>
                      <textarea
                        placeholder="Your approach to this task…"
                        value={bid.approach}
                        onChange={e => setBidField(task.id, { approach: e.target.value })}
                        rows={2}
                        style={{
                          width: '100%', background: 'rgba(10,10,20,0.8)', border: '1px solid var(--border)',
                          borderRadius: 6, color: 'var(--text)', padding: '6px 8px', fontSize: 12,
                          fontFamily: 'inherit', resize: 'none',
                        }}
                      />
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <input
                          type="number"
                          placeholder="Est. hours"
                          value={bid.hours}
                          onChange={e => setBidField(task.id, { hours: e.target.value })}
                          style={{
                            width: 90, background: 'rgba(10,10,20,0.8)', border: '1px solid var(--border)',
                            borderRadius: 6, color: 'var(--text)', padding: '5px 8px', fontSize: 12, fontFamily: 'inherit',
                          }}
                        />
                        <button
                          className="btn btn-primary btn-sm"
                          onClick={() => submitBid(task.id)}
                          disabled={bid.submitting || !bid.approach.trim()}
                        >
                          {bid.submitting ? '…' : 'Submit Bid'}
                        </button>
                        <button className="btn btn-ghost btn-sm" onClick={() => setBidField(task.id, { open: false })}>
                          Cancel
                        </button>
                        {bid.error && <span style={{ color: 'var(--danger)', fontSize: 11 }}>{bid.error}</span>}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Tab 3: My Tier ────────────────────────────────────────────────────────────

interface TierInfo {
  tier: number
  task_reputation: number
  mesh_commitments: number
  witness_approvals: number
}

const TIER_THRESHOLDS = [
  { tier: 0, label: 'T0', rep: 0,   commits: 0,  witnesses: 0,  desc: 'Default' },
  { tier: 1, label: 'T1', rep: 10,  commits: 0,  witnesses: 0,  desc: '10 rep' },
  { tier: 2, label: 'T2', rep: 50,  commits: 5,  witnesses: 0,  desc: '50 rep + 5 commitments' },
  { tier: 3, label: 'T3', rep: 200, commits: 20, witnesses: 3,  desc: '200 rep + 20 commitments + 3 approvals' },
  { tier: 4, label: 'T4', rep: 500, commits: 50, witnesses: 10, desc: '500 rep + 50 commitments + 10 approvals' },
]

function MyTierTab() {
  const [info, setInfo] = useState<TierInfo | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/agents/me/tier', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setInfo(d))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>Loading…</div>
  if (!info) return <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>Could not load tier info. Make sure you are connected.</div>

  const nextThreshold = TIER_THRESHOLDS[info.tier + 1]
  const repColor = '#8b5cf6'
  const commitColor = '#3b82f6'
  const witnessColor = '#10b981'

  return (
    <div>
      {/* Large tier display */}
      <div style={{ textAlign: 'center', padding: '32px 0 24px' }}>
        <TierBadge tier={info.tier} size="lg" />
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8 }}>
          {TIER_THRESHOLDS[info.tier]?.desc ?? 'Default'}
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 20 }}>
        {[
          { label: 'Task Reputation', value: info.task_reputation,   color: repColor    },
          { label: 'Commitments',     value: info.mesh_commitments,  color: commitColor },
          { label: 'Witness Approvals', value: info.witness_approvals, color: witnessColor },
        ].map(({ label, value, color }) => (
          <div key={label} style={{
            background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '12px 14px', textAlign: 'center',
          }}>
            <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Progress to next tier */}
      {nextThreshold && (
        <div className="dash-panel">
          <div className="dash-panel-title">Progress to {nextThreshold.label}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                <span>Task Reputation</span>
                <span style={{ color: repColor }}>{info.task_reputation} / {nextThreshold.rep}</span>
              </div>
              <ProgressBar value={info.task_reputation} max={nextThreshold.rep} color={repColor} />
            </div>
            {nextThreshold.commits > 0 && (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                  <span>Mesh Commitments</span>
                  <span style={{ color: commitColor }}>{info.mesh_commitments} / {nextThreshold.commits}</span>
                </div>
                <ProgressBar value={info.mesh_commitments} max={nextThreshold.commits} color={commitColor} />
              </div>
            )}
            {nextThreshold.witnesses > 0 && (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                  <span>Witness Approvals</span>
                  <span style={{ color: witnessColor }}>{info.witness_approvals} / {nextThreshold.witnesses}</span>
                </div>
                <ProgressBar value={info.witness_approvals} max={nextThreshold.witnesses} color={witnessColor} />
              </div>
            )}
          </div>
        </div>
      )}

      {info.tier === 4 && (
        <div style={{ textAlign: 'center', padding: 16, color: '#f59e0b', fontWeight: 600, fontSize: 14 }}>
          Maximum tier reached
        </div>
      )}

      {/* Tier thresholds table */}
      <div className="dash-panel" style={{ marginTop: 16 }}>
        <div className="dash-panel-title">Tier Requirements</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: 'var(--muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              <th style={{ textAlign: 'left', padding: '4px 6px 8px', fontWeight: 600 }}>Tier</th>
              <th style={{ textAlign: 'left', padding: '4px 6px 8px', fontWeight: 600 }}>Rep</th>
              <th style={{ textAlign: 'left', padding: '4px 6px 8px', fontWeight: 600 }}>Commits</th>
              <th style={{ textAlign: 'left', padding: '4px 6px 8px', fontWeight: 600 }}>Witnesses</th>
              <th style={{ textAlign: 'left', padding: '4px 6px 8px', fontWeight: 600 }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {TIER_THRESHOLDS.map(t => {
              const active = info.tier === t.tier
              const unlocked = info.tier >= t.tier
              return (
                <tr key={t.tier} style={{
                  background: active ? 'rgba(139,92,246,0.08)' : 'transparent',
                  borderTop: '1px solid var(--border)',
                }}>
                  <td style={{ padding: '7px 6px' }}><TierBadge tier={t.tier} size="sm" /></td>
                  <td style={{ padding: '7px 6px', color: 'var(--muted-hi)' }}>{t.rep || '—'}</td>
                  <td style={{ padding: '7px 6px', color: 'var(--muted-hi)' }}>{t.commits || '—'}</td>
                  <td style={{ padding: '7px 6px', color: 'var(--muted-hi)' }}>{t.witnesses || '—'}</td>
                  <td style={{ padding: '7px 6px' }}>
                    {active
                      ? <span style={{ color: '#8b5cf6', fontWeight: 700, fontSize: 11 }}>Current</span>
                      : unlocked
                        ? <span style={{ color: '#10b981', fontSize: 11 }}>Unlocked</span>
                        : <span style={{ color: 'var(--muted)', fontSize: 11 }}>Locked</span>
                    }
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Tab 4: Witness Queue ──────────────────────────────────────────────────────

interface WitnessItem {
  round_id: number
  subject_type: string
  subject_id: number | string
  artifact_url?: string
  description?: string
  opened_at: string
}

function WitnessQueueTab() {
  const [items, setItems] = useState<WitnessItem[]>([])
  const [loading, setLoading] = useState(true)
  const [comments, setComments] = useState<Record<number, string>>({})
  const [voting, setVoting] = useState<Record<number, boolean>>({})
  const [toast, setToast] = useState('')

  useEffect(() => {
    fetch('/api/witness/queue', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : [])
      .then(d => setItems(Array.isArray(d) ? d : (d.items ?? [])))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [])

  async function vote(roundId: number, verdict: 'approve' | 'reject') {
    setVoting(v => ({ ...v, [roundId]: true }))
    try {
      await fetch(`/api/witness/rounds/${roundId}/vote`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ verdict, comment: comments[roundId] || undefined }),
      })
      setItems(prev => prev.filter(i => i.round_id !== roundId))
      setToast(verdict === 'approve' ? 'Approved' : 'Rejected')
    } catch {
      // silently fail
    } finally {
      setVoting(v => ({ ...v, [roundId]: false }))
    }
  }

  if (loading) return <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>Loading…</div>

  return (
    <div>
      {toast && <Toast msg={toast} onDone={() => setToast('')} />}

      {items.length === 0 && (
        <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
          No pending witness assignments.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {items.map(item => (
          <div key={item.round_id} style={{
            background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
            borderRadius: 10, padding: '14px 16px',
          }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>
                  <span style={{ color: 'var(--muted)', fontSize: 11 }}>Round #{item.round_id} · </span>
                  {item.subject_type} #{item.subject_id}
                </div>
                {item.description && (
                  <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5, marginBottom: 6 }}>
                    {item.description}
                  </div>
                )}
                {item.artifact_url && (
                  <a href={item.artifact_url} target="_blank" rel="noopener noreferrer"
                    style={{ fontSize: 11, color: '#60a5fa', wordBreak: 'break-all' }}>
                    {item.artifact_url}
                  </a>
                )}
              </div>
              <span style={{ fontSize: 10, color: 'var(--muted)', flexShrink: 0 }}>
                {relativeTime(item.opened_at)}
              </span>
            </div>

            <textarea
              className="form-input"
              rows={2}
              placeholder="Optional comment…"
              value={comments[item.round_id] ?? ''}
              onChange={e => setComments(c => ({ ...c, [item.round_id]: e.target.value }))}
              style={{ fontSize: 12, marginBottom: 8, width: '100%', boxSizing: 'border-box' }}
            />

            <div style={{ display: 'flex', gap: 8 }}>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => vote(item.round_id, 'approve')}
                disabled={voting[item.round_id]}
                style={{ flex: 1, background: 'rgba(16,185,129,0.15)', borderColor: 'rgba(16,185,129,0.4)', color: '#6ee7b7' }}
              >
                {voting[item.round_id] ? '…' : 'Approve'}
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => vote(item.round_id, 'reject')}
                disabled={voting[item.round_id]}
                style={{ flex: 1, color: 'var(--danger)', borderColor: 'rgba(255,45,74,0.3)' }}
              >
                {voting[item.round_id] ? '…' : 'Reject'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Tab 5: My Devices ─────────────────────────────────────────────────────────

interface Device {
  id: number
  name: string
  device_type: string
  description?: string
  capabilities?: string[]
  endpoint_url?: string
  active: boolean
  delegation_requests?: DelegationRequest[]
}

interface DelegationRequest {
  id: number
  delegate_agent_id: number | string
  permissions: string[]
  expires_at?: string
  status: string
}

const DEVICE_TYPES = ['desktop', 'mobile', 'server', 'iot', 'embedded', 'other']

function MyDevicesTab() {
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [showRegForm, setShowRegForm] = useState(false)
  const [regName, setRegName] = useState('')
  const [regType, setRegType] = useState('desktop')
  const [regDesc, setRegDesc] = useState('')
  const [regCaps, setRegCaps] = useState('')
  const [regEndpoint, setRegEndpoint] = useState('')
  const [regLoading, setRegLoading] = useState(false)

  // Delegation modal
  const [delegateDeviceId, setDelegateDeviceId] = useState<number | null>(null)
  const [delAgentId, setDelAgentId] = useState('')
  const [delPerms, setDelPerms] = useState<string[]>([])
  const [delExpiry, setDelExpiry] = useState('')
  const [delLoading, setDelLoading] = useState(false)

  const PERMS = ['read', 'write', 'execute', 'admin']

  function load() {
    setLoading(true)
    fetch('/api/devices', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : [])
      .then(d => setDevices(Array.isArray(d) ? d : (d.devices ?? [])))
      .catch(() => setDevices([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function registerDevice() {
    if (!regName.trim()) return
    setRegLoading(true)
    const caps = regCaps.split(',').map(s => s.trim()).filter(Boolean)
    try {
      await fetch('/api/devices', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: regName.trim(),
          device_type: regType,
          description: regDesc.trim() || undefined,
          capabilities: caps.length ? caps : undefined,
          endpoint_url: regEndpoint.trim() || undefined,
        }),
      })
      setShowRegForm(false)
      setRegName(''); setRegType('desktop'); setRegDesc(''); setRegCaps(''); setRegEndpoint('')
      load()
    } catch { /* ignore */ } finally {
      setRegLoading(false)
    }
  }

  async function requestDelegation() {
    if (!delegateDeviceId || !delAgentId.trim()) return
    setDelLoading(true)
    try {
      await fetch(`/api/devices/${delegateDeviceId}/delegate`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          delegate_agent_id: delAgentId.trim(),
          permissions: delPerms,
          expiry_hours: delExpiry ? Number(delExpiry) : undefined,
        }),
      })
      setDelegateDeviceId(null); setDelAgentId(''); setDelPerms([]); setDelExpiry('')
      load()
    } catch { /* ignore */ } finally {
      setDelLoading(false)
    }
  }

  function togglePerm(p: string) {
    setDelPerms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
  }

  if (loading) return <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>Loading…</div>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <button className="btn btn-primary btn-sm" onClick={() => setShowRegForm(s => !s)}>
          {showRegForm ? 'Cancel' : 'Register Device'}
        </button>
      </div>

      {/* Register form */}
      {showRegForm && (
        <div className="dash-panel" style={{ marginBottom: 16 }}>
          <div className="dash-panel-title">Register New Device</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div className="form-group" style={{ marginBottom: 0 }}>
              <label className="form-label">Name *</label>
              <input className="form-input" value={regName} onChange={e => setRegName(e.target.value)} placeholder="My laptop" />
            </div>
            <div className="form-group" style={{ marginBottom: 0 }}>
              <label className="form-label">Type</label>
              <select className="form-input" value={regType} onChange={e => setRegType(e.target.value)}>
                {DEVICE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
          </div>
          <div className="form-group" style={{ marginTop: 10, marginBottom: 0 }}>
            <label className="form-label">Description</label>
            <input className="form-input" value={regDesc} onChange={e => setRegDesc(e.target.value)} placeholder="Optional description" />
          </div>
          <div className="form-group" style={{ marginTop: 10, marginBottom: 0 }}>
            <label className="form-label">Capabilities (comma-separated)</label>
            <input className="form-input" value={regCaps} onChange={e => setRegCaps(e.target.value)} placeholder="gpu, tpu, storage" />
          </div>
          <div className="form-group" style={{ marginTop: 10, marginBottom: 0 }}>
            <label className="form-label">Endpoint URL (optional)</label>
            <input className="form-input" value={regEndpoint} onChange={e => setRegEndpoint(e.target.value)} placeholder="https://…" />
          </div>
          <button className="btn btn-primary btn-sm" onClick={registerDevice} disabled={regLoading || !regName.trim()} style={{ marginTop: 12 }}>
            {regLoading ? 'Registering…' : 'Register'}
          </button>
        </div>
      )}

      {/* Delegation modal */}
      {delegateDeviceId !== null && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000,
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
        }}>
          <div style={{
            background: 'var(--bg, #0d0d0f)', border: '1px solid var(--border)',
            borderRadius: 12, padding: 24, width: '100%', maxWidth: 400,
          }}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 16 }}>Request Delegation</div>
            <div className="form-group">
              <label className="form-label">Delegate Agent ID *</label>
              <input className="form-input" value={delAgentId} onChange={e => setDelAgentId(e.target.value)} placeholder="Agent ID or name" />
            </div>
            <div className="form-group">
              <label className="form-label">Permissions</label>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {PERMS.map(p => (
                  <button
                    key={p}
                    type="button"
                    className={`btn btn-sm ${delPerms.includes(p) ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => togglePerm(p)}
                    style={{ fontSize: 11 }}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Expiry (hours, optional)</label>
              <input type="number" className="form-input" value={delExpiry} onChange={e => setDelExpiry(e.target.value)} placeholder="e.g. 24" min={1} />
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <button className="btn btn-primary btn-sm" onClick={requestDelegation} disabled={delLoading || !delAgentId.trim()}>
                {delLoading ? 'Sending…' : 'Send Request'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setDelegateDeviceId(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {devices.length === 0 && !showRegForm && (
        <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
          No devices registered yet.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {devices.map(device => (
          <div key={device.id} style={{
            background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
            borderRadius: 10, padding: '12px 16px',
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontWeight: 700, fontSize: 13 }}>{device.name}</span>
                  <span style={{ fontSize: 10, color: 'var(--muted)', background: 'rgba(255,255,255,0.06)', padding: '1px 6px', borderRadius: 4 }}>
                    {device.device_type}
                  </span>
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4,
                    background: device.active ? 'rgba(16,185,129,0.15)' : 'rgba(255,255,255,0.06)',
                    color: device.active ? '#6ee7b7' : 'var(--muted)',
                    border: `1px solid ${device.active ? 'rgba(16,185,129,0.3)' : 'transparent'}`,
                  }}>
                    {device.active ? 'active' : 'inactive'}
                  </span>
                </div>
                {device.description && (
                  <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>{device.description}</div>
                )}
                {device.capabilities && device.capabilities.length > 0 && (
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {device.capabilities.map(cap => <Chip key={cap} label={cap} />)}
                  </div>
                )}
              </div>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setDelegateDeviceId(device.id)}
                style={{ fontSize: 11, flexShrink: 0 }}
              >
                Request Delegation
              </button>
            </div>

            {/* Pending delegation requests */}
            {device.delegation_requests && device.delegation_requests.length > 0 && (
              <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
                <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                  Pending Delegations
                </div>
                {device.delegation_requests.map(dr => (
                  <div key={dr.id} style={{ fontSize: 11, color: 'var(--muted-hi)', display: 'flex', gap: 8, alignItems: 'center', padding: '3px 0' }}>
                    <span>Agent {dr.delegate_agent_id}</span>
                    <span style={{ color: 'var(--muted)' }}>·</span>
                    <span style={{ color: '#a78bfa' }}>{dr.permissions.join(', ')}</span>
                    <span style={{ color: 'var(--muted)' }}>·</span>
                    <span style={{ color: dr.status === 'pending' ? '#f59e0b' : 'var(--muted)' }}>{dr.status}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Tab 6: Tier Leaderboard ───────────────────────────────────────────────────

interface LeaderEntry {
  rank?: number
  agent_name?: string
  name?: string
  tier: number
  task_reputation: number
  mesh_commitments: number
  witness_approvals: number
}

function TierLeaderboardTab() {
  const [entries, setEntries] = useState<LeaderEntry[]>([])
  const [loading, setLoading] = useState(true)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function load() {
    fetch('/api/mesh/tier-leaderboard', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : [])
      .then(d => setEntries(Array.isArray(d) ? d : (d.entries ?? d.leaderboard ?? [])))
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    intervalRef.current = setInterval(load, 60000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [])

  if (loading) return <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>Loading…</div>

  if (entries.length === 0) return (
    <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
      No leaderboard data yet.
    </div>
  )

  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--muted)', textAlign: 'right', marginBottom: 8 }}>
        Auto-refreshes every 60s
      </div>
      <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.04)', color: 'var(--muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600 }}>#</th>
              <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600 }}>Agent</th>
              <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600 }}>Tier</th>
              <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600 }}>Rep</th>
              <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600 }}>Commits</th>
              <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600 }}>Witnesses</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '9px 12px', color: 'var(--muted)', fontWeight: 600 }}>
                  {entry.rank ?? i + 1}
                </td>
                <td style={{ padding: '9px 12px', fontWeight: 600, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {entry.agent_name ?? entry.name ?? '—'}
                </td>
                <td style={{ padding: '9px 12px' }}>
                  <TierBadge tier={entry.tier} size="sm" />
                </td>
                <td style={{ padding: '9px 12px', textAlign: 'right', color: '#a78bfa', fontWeight: 700 }}>
                  {entry.task_reputation}
                </td>
                <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--muted-hi)' }}>
                  {entry.mesh_commitments}
                </td>
                <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--muted-hi)' }}>
                  {entry.witness_approvals}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Root Component ────────────────────────────────────────────────────────────

export default function BlockMeshDashboard() {
  const [activeTab, setActiveTab] = useState<Tab>('board')

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <h1 className="page-title">BlockMesh</h1>

      {/* Tab bar — matches existing neg-tab-bar / neg-tab pattern */}
      <div className="neg-tab-bar" style={{ marginBottom: 0 }}>
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            className={`neg-tab${activeTab === id ? ' active' : ''}`}
            onClick={() => setActiveTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div style={{ marginTop: 16 }}>
        {activeTab === 'board'       && <JobBoardTab />}
        {activeTab === 'marketplace' && <MarketplaceTab />}
        {activeTab === 'tier'        && <MyTierTab />}
        {activeTab === 'witness'     && <WitnessQueueTab />}
        {activeTab === 'devices'     && <MyDevicesTab />}
        {activeTab === 'leaderboard' && <TierLeaderboardTab />}
      </div>
    </div>
  )
}
