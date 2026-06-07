import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Zap, TrendingUp, Clock, RefreshCw, Filter } from 'lucide-react'

interface Stats {
  open_tasks: number; awarded_tasks: number; completed_tasks: number;
  avg_reward_usdc: number; bids_last_hour: number; total_bids: number;
  avg_completion_hours: number;
  top_capabilities: Array<{ capability: string; count: number }>;
}

interface Task {
  id: number; title: string; description: string;
  required_capability: string; reward_usdc: number;
  poster_name: string; status: string;
  created_at: string; expires_at: string;
}

function timeAgo(iso: string) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function timeUntil(iso: string) {
  if (!iso) return null
  const diff = new Date(iso).getTime() - Date.now()
  if (diff <= 0) return 'Expired'
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  if (h > 0) return `${h}h ${m}m left`
  return `${m}m left`
}

interface BidFormProps {
  taskId: number; apiKey: string;
  onDone: () => void; onCancel: () => void;
}
function BidForm({ taskId, apiKey, onDone, onCancel }: BidFormProps) {
  const [approach, setApproach] = useState('')
  const [hours, setHours] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)

  const submit = async () => {
    if (!approach.trim()) return
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`/api/agents/tasks/${taskId}/bid`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ approach: approach.trim(), estimated_hours: parseFloat(hours) || 1 }),
      })
      if (!r.ok) throw new Error(await r.text())
      setSuccess(true)
      setTimeout(onDone, 1200)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Bid failed')
    }
    setLoading(false)
  }

  if (success) return (
    <div className="mv-bid-form" style={{ color: 'var(--green)', fontSize: 13 }}>Bid placed!</div>
  )

  return (
    <div className="mv-bid-form">
      <textarea
        placeholder="Your approach to this task…"
        value={approach}
        onChange={e => setApproach(e.target.value)}
        rows={2}
        style={{ width: '100%', background: 'rgba(10,10,20,0.8)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', padding: '6px 8px', fontSize: 12, fontFamily: 'inherit', resize: 'none' }}
      />
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <input type="number" placeholder="Est. hours" value={hours} onChange={e => setHours(e.target.value)}
          style={{ width: 90, background: 'rgba(10,10,20,0.8)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', padding: '5px 8px', fontSize: 12, fontFamily: 'inherit' }} />
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={loading || !approach.trim()}>
          {loading ? '…' : 'Submit Bid'}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        {error && <span style={{ color: 'var(--danger)', fontSize: 11 }}>{error}</span>}
      </div>
    </div>
  )
}

interface TaskCardProps { task: Task; apiKey?: string }
function TaskCard({ task, apiKey }: TaskCardProps) {
  const [showBid, setShowBid] = useState(false)
  const isHighValue = task.reward_usdc > 10
  const hasReward = task.reward_usdc > 0
  const expires = task.expires_at ? timeUntil(task.expires_at) : null

  return (
    <div className={`mv-task-card${isHighValue ? ' high-value' : ''}`}>
      <div className="mv-task-row1">
        {task.required_capability && <span className="mv-cap-badge">{task.required_capability}</span>}
        <span className={`mv-reward-badge ${hasReward ? 'has-reward' : 'no-reward'}`}>
          {hasReward ? `$${task.reward_usdc.toFixed(1)}` : 'No reward'}
        </span>
        <span className="mv-task-title">{task.title}</span>
        {apiKey && !showBid && (
          <button className="btn btn-cyan btn-sm" style={{ flexShrink: 0 }} onClick={() => setShowBid(true)}>BID</button>
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
              <Clock size={10} style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }} />{expires}
            </span>
          </>
        )}
      </div>
      {showBid && apiKey && (
        <BidForm taskId={task.id} apiKey={apiKey} onDone={() => setShowBid(false)} onCancel={() => setShowBid(false)} />
      )}
    </div>
  )
}

export default function MarketVelocity({ apiKey }: { apiKey?: string }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState<'newest' | 'reward' | 'capability'>('newest')
  const [secondsAgo, setSecondsAgo] = useState(0)
  const timerId = useRef<ReturnType<typeof setInterval> | null>(null)
  const clockId = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadStats = useCallback(async () => {
    try { const r = await fetch('/api/agents/market/stats'); if (r.ok) setStats(await r.json()) } catch {}
  }, [])

  const loadTasks = useCallback(async () => {
    try {
      const r = await fetch('/api/agents/tasks?status=open&limit=50')
      if (r.ok) { setTasks(await r.json()); setSecondsAgo(0) }
    } catch {}
  }, [])

  useEffect(() => {
    loadStats(); loadTasks()
    timerId.current = setInterval(() => { loadStats(); loadTasks() }, 15000)
    clockId.current = setInterval(() => setSecondsAgo(p => p + 1), 1000)
    return () => {
      if (timerId.current) clearInterval(timerId.current)
      if (clockId.current) clearInterval(clockId.current)
    }
  }, [loadStats, loadTasks])

  const filteredTasks = tasks
    .filter(t => {
      if (!filter) return true
      const q = filter.toLowerCase()
      return t.title.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q) || (t.required_capability || '').toLowerCase().includes(q)
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

      <div className="mv-ticker-strip">
        <div className="mv-ticker-inner">MARKET PULSE: {tickerText} &nbsp;&nbsp;&nbsp; MARKET PULSE: {tickerText}</div>
      </div>

      <div className="mv-header">
        <div className="mv-header-title">
          <span className="mv-live-dot" />TASK MARKET VELOCITY
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>Updated {secondsAgo}s ago</span>
          <button className="btn btn-ghost btn-sm" onClick={() => { loadStats(); loadTasks() }}><RefreshCw size={12} /></button>
        </div>
      </div>

      <div className="mv-stats-grid">
        {[
          { label: 'Open Tasks', value: stats?.open_tasks ?? '—' },
          { label: 'Awarded', value: stats?.awarded_tasks ?? '—' },
          { label: 'Completed', value: stats?.completed_tasks ?? '—' },
          { label: 'Avg Reward', value: stats ? `$${stats.avg_reward_usdc}` : '—' },
          { label: 'Bids / hr', value: stats?.bids_last_hour ?? '—' },
          { label: 'Avg Fill', value: stats ? `${stats.avg_completion_hours}h` : '—' },
        ].map(s => (
          <div key={s.label} className="mv-stat-cell">
            <div className="mv-stat-num">{s.value}</div>
            <div className="mv-stat-label">{s.label}</div>
          </div>
        ))}
      </div>

      {!!stats?.top_capabilities.length && (
        <div className="mv-caps-strip">
          <span className="mv-cap-label">HOT:</span>
          {stats.top_capabilities.map(c => (
            <span key={c.capability} className="mv-cap-pill" onClick={() => setFilter(c.capability)} style={{ cursor: 'pointer' }}>
              {c.capability} x{c.count}
            </span>
          ))}
        </div>
      )}

      <div className="mv-listings-header">
        <span className="mv-listings-title">
          <Zap size={12} style={{ display: 'inline', verticalAlign: 'middle', marginRight: 4 }} />
          Live Listings ({filteredTasks.length})
        </span>
        <input className="mv-filter-input" placeholder="Filter tasks…" value={filter} onChange={e => setFilter(e.target.value)} />
        <Filter size={12} style={{ color: 'var(--muted)' }} />
        <select className="mv-sort-select" value={sort} onChange={e => setSort(e.target.value as typeof sort)}>
          <option value="newest">Newest</option>
          <option value="reward">Highest Reward</option>
          <option value="capability">Capability</option>
        </select>
      </div>

      <div className="mv-listings-body">
        {tasks.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--muted)', padding: 40, fontSize: 14 }}>
            <TrendingUp size={32} style={{ marginBottom: 12, opacity: 0.3 }} />
            <div>No open tasks in the market yet.</div>
          </div>
        )}
        {filteredTasks.map(task => <TaskCard key={task.id} task={task} apiKey={apiKey} />)}
      </div>
    </div>
  )
}
