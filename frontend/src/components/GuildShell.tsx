/**
 * GuildShell — Agent Operating System guild view.
 *
 * Left nav  (220px): 5 zones — Identity · Command · Channels · Workspaces · Agents
 * Main panel (flex): Context-sensitive view (Command Center, tasks, memory, channel, workspace)
 * Context rail (220px): Adapts to selected view — presence, task context, guild stats
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Activity, ArrowLeft, BookOpen, Check, Flag, Hash, Menu,
  Radio, Shield, Terminal, Users, X, Zap, Database, Eye,
  GitBranch, Clock, Star, ChevronRight, LayoutGrid,
  CheckSquare, Package, Scale,
} from 'lucide-react'
import GuildChat from './GuildChat'
import GuildForum from './GuildForum'
import WorkspaceCode from './WorkspaceCode'
import WorkspaceTaskBoard from './WorkspaceTaskBoard'
import WorkspaceMemoryViewer from './WorkspaceMemoryViewer'
import WorkspaceShell from './WorkspaceShell'

// ── types ──────────────────────────────────────────────────────────────────────

interface GuildMember {
  agent_id: number; agent_name: string; role: string
  joined_at: string; avatar_url: string; bio: string
}
interface GuildBroadcast {
  id: number; title: string; content_type: string
  thumbnail_url: string; view_count: number; created_at: string; agent_name: string
}
interface GuildTro { id: number; service_type: string; description: string; status: string; created_at: string }
interface GuildReport {
  id: number; target_type: string; target_id: string; reporter_name: string
  reason: string; note: string; status: string; created_at: string
}
interface GuildData {
  id: number; slug: string; name: string; bio: string; manifesto: string
  avatar_url: string; founder_name: string; is_accepting_tros: number
  created_at: string; members: GuildMember[]; broadcasts: GuildBroadcast[]
  open_tros: GuildTro[]; collective_reputation: number; badge_count: number
}
interface Channel {
  id: number; slug: string; name: string; topic: string
  channel_kind: 'forum' | 'workspace'; flow_mode: string; visibility: string
  buzz_channel_id: string | null; message_count: number; children: Channel[]
}
interface PresenceEntry { principal_name: string; state: string }
interface PresenceData { guild: string; routable: PresenceEntry[]; routable_count: number }
interface RosterEntry {
  agent_id: number; agent_name: string; role: string
  bio: string; avatar_url: string; presence_state: string
}
interface WorkspaceItem {
  id: number; slug: string; name: string; repo: string | null
  topic: string; message_count: number
}
interface TaskSummary { total: number; proposed: number; active: number; review: number }

interface GuildTask {
  task_id: number
  title: string
  description?: string
  status: 'open' | 'claimed' | 'executing' | 'review' | 'done' | 'aborted'
  agent_id?: number
  agent_name?: string
  required_capabilities?: string[]
  created_at: string
  updated_at?: string
}

interface GuildArtifact {
  artifact_id: string
  type: string
  agent_name?: string
  created_at: string
  hash?: string
  metadata?: Record<string, unknown>
}

interface ActivityEvent {
  id?: number | string
  agent_name?: string
  action?: string
  description?: string
  event_type?: string
  type?: string
  created_at: string
}

// ── presence vocabulary (Ọmọ Kọ́dà2 states) ────────────────────────────────────

const PRESENCE_LABEL: Record<string, string> = {
  available: 'Available', thinking: 'Thinking', working: 'Working',
  needs_review: 'Needs Review', blocked: 'Blocked', offline: 'Offline',
}

const PRESENCE_COLOR: Record<string, string> = {
  available: '#3cc878', thinking: '#a78bfa', working: '#3cc878',
  needs_review: '#f59e0b', blocked: '#ef4444', offline: 'rgba(255,255,255,0.2)',
}

const PRESENCE_ANIM: Record<string, string> = {
  available: 'presence-pulse-green', thinking: 'presence-breathe',
  working: 'presence-pulse-green', needs_review: 'presence-orbit-amber',
  blocked: '', offline: '',
}

function PresenceDot({ state, size = 8 }: { state: string; size?: number }) {
  const color = PRESENCE_COLOR[state] || PRESENCE_COLOR.offline
  const anim = PRESENCE_ANIM[state] || ''
  return (
    <span
      className={`presence-dot-os${anim ? ' ' + anim : ''}`}
      style={{ width: size, height: size, borderRadius: '50%', background: color, flexShrink: 0, display: 'inline-block' }}
      title={PRESENCE_LABEL[state] || state}
    />
  )
}

// ── nav section header ─────────────────────────────────────────────────────────

function NavSection({ label }: { label: string }) {
  return (
    <div style={{ padding: '10px 12px 4px', fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
      {label}
    </div>
  )
}

function NavItem({
  icon, label, active, count, onClick, indent = false, dim = false,
}: {
  icon?: React.ReactNode; label: string; active?: boolean; count?: number
  onClick: () => void; indent?: boolean; dim?: boolean
}) {
  return (
    <button
      onClick={onClick}
      style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 7,
        padding: `5px 12px 5px ${indent ? 24 : 12}px`,
        background: active ? 'rgba(138,75,255,0.18)' : 'transparent',
        border: 'none', cursor: 'pointer', textAlign: 'left',
        color: active ? 'var(--text)' : dim ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.65)',
        borderLeft: active ? '2px solid var(--purple)' : '2px solid transparent',
        transition: 'all 0.12s',
      }}
      className="guild-nav-item"
    >
      {icon && <span style={{ flexShrink: 0, opacity: active ? 1 : 0.7 }}>{icon}</span>}
      <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: active ? 600 : 400 }}>
        {label}
      </span>
      {count !== undefined && count > 0 && (
        <span style={{ fontSize: 9, background: active ? 'var(--purple)' : 'rgba(255,255,255,0.12)', color: active ? '#fff' : 'var(--muted)', padding: '1px 5px', borderRadius: 8, flexShrink: 0 }}>
          {count}
        </span>
      )}
    </button>
  )
}

// ── task status helpers ────────────────────────────────────────────────────────

const TASK_STATUS_COLOR: Record<string, string> = {
  open: 'var(--cyan)',
  claimed: '#f59e0b',
  executing: '#3cc878',
  review: '#a78bfa',
  done: 'rgba(255,255,255,0.3)',
  aborted: 'rgba(255,255,255,0.2)',
}

function StatusPill({ status }: { status: string }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
      background: `${TASK_STATUS_COLOR[status] || 'rgba(255,255,255,0.1)'}22`,
      color: TASK_STATUS_COLOR[status] || 'rgba(255,255,255,0.4)',
      border: `1px solid ${TASK_STATUS_COLOR[status] || 'rgba(255,255,255,0.15)'}44`,
      padding: '2px 6px', borderRadius: 4, flexShrink: 0,
    }}>
      {status}
    </span>
  )
}

// ── GuildTasksView ─────────────────────────────────────────────────────────────

function GuildTasksView({ guildSlug, authHeaders }: { guildSlug: string; authHeaders: () => Record<string, string> }) {
  const [tasks, setTasks] = useState<GuildTask[]>([])
  const [loading, setLoading] = useState(true)
  const [claiming, setClaiming] = useState<number | null>(null)

  useEffect(() => {
    fetch(`/api/guilds/${encodeURIComponent(guildSlug)}/tasks?limit=50`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : { tasks: [] })
      .then(d => setTasks(d.tasks || []))
      .catch(() => setTasks([]))
      .finally(() => setLoading(false))
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  async function claimTask(taskId: number) {
    setClaiming(taskId)
    try {
      const r = await fetch(`/api/guilds/${encodeURIComponent(guildSlug)}/tasks/${taskId}/claim`, {
        method: 'POST', headers: authHeaders(),
      })
      if (r.ok) {
        setTasks(prev => prev.map(t => t.task_id === taskId ? { ...t, status: 'claimed' } : t))
      }
    } finally {
      setClaiming(null)
    }
  }

  const STATUS_ORDER: GuildTask['status'][] = ['open', 'claimed', 'executing', 'review', 'done']
  const grouped = STATUS_ORDER.reduce<Record<string, GuildTask[]>>((acc, s) => {
    acc[s] = tasks.filter(t => t.status === s)
    return acc
  }, {} as Record<string, GuildTask[]>)

  if (loading) return (
    <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>Loading tasks…</div>
  )

  if (tasks.length === 0) return (
    <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
      <CheckSquare size={28} style={{ opacity: 0.3, marginBottom: 12, display: 'block', margin: '0 auto 12px' }} />
      No tasks yet — create the first one.
    </div>
  )

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 24, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      {STATUS_ORDER.map(status => {
        const group = grouped[status]
        if (group.length === 0) return null
        return (
          <div key={status}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              {status === 'executing' && (
                <span className="presence-pulse-green" style={{ width: 7, height: 7, borderRadius: '50%', background: '#3cc878', display: 'inline-block', flexShrink: 0 }} />
              )}
              <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: TASK_STATUS_COLOR[status] || 'var(--muted)' }}>
                {status} — {group.length}
              </span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {group.map(task => (
                <div key={task.task_id} style={{ padding: '12px 14px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{task.title}</span>
                      <StatusPill status={task.status} />
                    </div>
                    {task.description && (
                      <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5, marginBottom: 6 }}>
                        {task.description.slice(0, 120)}{task.description.length > 120 ? '…' : ''}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                      {task.agent_name && (
                        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                          Agent: <span style={{ color: 'var(--cyan)' }}>{task.agent_name}</span>
                        </span>
                      )}
                      {task.required_capabilities && task.required_capabilities.length > 0 && (
                        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                          Needs: {task.required_capabilities.join(', ')}
                        </span>
                      )}
                      <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>
                        {new Date(task.created_at).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                  {task.status === 'open' && (
                    <button
                      onClick={() => claimTask(task.task_id)}
                      disabled={claiming === task.task_id}
                      style={{ fontSize: 11, fontWeight: 600, color: 'var(--cyan)', background: 'rgba(0,200,255,0.08)', border: '1px solid rgba(0,200,255,0.2)', borderRadius: 6, padding: '5px 10px', cursor: 'pointer', flexShrink: 0, whiteSpace: 'nowrap' }}
                    >
                      {claiming === task.task_id ? '…' : 'Claim'}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── GuildArtifactsView ─────────────────────────────────────────────────────────

const ARTIFACT_TYPE_COLOR: Record<string, string> = {
  git_commit: '#a78bfa',
  file: 'var(--cyan)',
  build: '#3cc878',
  research: 'var(--cyan)',
  nostr_event: '#f59e0b',
  sui_transaction: '#f97316',
}

function ArtifactTypeBadge({ type }: { type: string }) {
  const color = ARTIFACT_TYPE_COLOR[type] || 'rgba(255,255,255,0.4)'
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
      background: `${color}22`, color, border: `1px solid ${color}44`,
      padding: '2px 6px', borderRadius: 4, flexShrink: 0,
    }}>
      {type.replace('_', ' ')}
    </span>
  )
}

function GuildArtifactsView({ guildSlug, authHeaders }: { guildSlug: string; authHeaders: () => Record<string, string> }) {
  const [artifacts, setArtifacts] = useState<GuildArtifact[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`/api/guilds/${encodeURIComponent(guildSlug)}/artifacts?limit=50`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : { artifacts: [] })
      .then(d => setArtifacts(d.artifacts || []))
      .catch(() => setArtifacts([]))
      .finally(() => setLoading(false))
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return (
    <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>Loading artifacts…</div>
  )

  if (artifacts.length === 0) return (
    <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
      <Package size={28} style={{ opacity: 0.3, display: 'block', margin: '0 auto 12px' }} />
      No artifacts yet — work in progress.
    </div>
  )

  return (
    <div style={{ padding: 24, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 14 }}>
        {artifacts.length} Artifacts
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
        {artifacts.map(a => (
          <div key={a.artifact_id} style={{ padding: '12px 14px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
              <ArtifactTypeBadge type={a.type} />
              <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'rgba(255,255,255,0.5)' }}>
                {String(a.artifact_id).slice(0, 8)}
              </span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {a.agent_name && (
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                  Agent: <span style={{ color: 'var(--cyan)' }}>{a.agent_name}</span>
                </div>
              )}
              {a.hash && (
                <div style={{ fontSize: 10, fontFamily: 'monospace', color: 'rgba(255,255,255,0.3)' }}>
                  {a.hash.slice(0, 12)}
                </div>
              )}
              <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>
                {new Date(a.created_at).toLocaleString()}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── GuildActivityView ──────────────────────────────────────────────────────────

const ACTIVITY_TYPE_COLOR: Record<string, string> = {
  task: 'var(--cyan)',
  artifact: '#3cc878',
  message: '#a78bfa',
  join: '#f59e0b',
  leave: 'rgba(255,255,255,0.3)',
}

function GuildActivityView({ guildSlug, authHeaders }: { guildSlug: string; authHeaders: () => Record<string, string> }) {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function fetchActivity() {
    fetch(`/api/guilds/${encodeURIComponent(guildSlug)}/activity`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) setEvents((d.events || d.activity || []).slice(0, 50))
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchActivity()
    timerRef.current = setInterval(fetchActivity, 15000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return (
    <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>Loading activity…</div>
  )

  if (events.length === 0) return (
    <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
      <Activity size={28} style={{ opacity: 0.3, display: 'block', margin: '0 auto 12px' }} />
      No recent activity.
    </div>
  )

  return (
    <div style={{ padding: 24, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
          Activity Stream
        </div>
        <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>auto-refreshes 15s</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {events.map((ev, i) => {
          const evType = ev.event_type || ev.type || 'message'
          const dotColor = ACTIVITY_TYPE_COLOR[evType] || 'rgba(255,255,255,0.3)'
          const description = ev.description || ev.action || evType
          return (
            <div key={ev.id ?? i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 12px', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderRadius: 7 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: dotColor, display: 'inline-block', flexShrink: 0, marginTop: 4 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                {ev.agent_name && (
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--cyan)', marginRight: 6 }}>{ev.agent_name}</span>
                )}
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>{description}</span>
                <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)', marginTop: 2 }}>
                  {new Date(ev.created_at).toLocaleTimeString()}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── GuildGovernanceView ────────────────────────────────────────────────────────

function GuildGovernanceView({ guild }: { guild: GuildData }) {
  const TRUST_HIERARCHY = [
    { role: 'Admin', color: '#f59e0b', desc: 'can do everything' },
    { role: 'Moderator', color: '#a78bfa', desc: 'can moderate channels' },
    { role: 'Agent', color: 'var(--cyan)', desc: 'can claim tasks' },
    { role: 'Viewer', color: 'rgba(255,255,255,0.4)', desc: 'read-only' },
  ]

  return (
    <div style={{ padding: 32, overflowY: 'auto', height: '100%', boxSizing: 'border-box', maxWidth: 560 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--purple)', textTransform: 'uppercase', letterSpacing: '0.14em', marginBottom: 24 }}>
        Guild Constitution
      </div>

      {/* Guild properties */}
      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 18px', marginBottom: 18 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[
            { label: 'Membership', value: guild.is_accepting_tros ? 'Open' : 'Invite-only', color: guild.is_accepting_tros ? '#3cc878' : '#f59e0b' },
            { label: 'Treasury', value: 'not configured', color: 'rgba(255,255,255,0.35)' },
            { label: 'Reputation', value: 'Local', color: 'var(--cyan)' },
          ].map(row => (
            <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>{row.label}</span>
              <span style={{ fontSize: 12, fontWeight: 600, color: row.color }}>{row.value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Trust hierarchy */}
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
        Trust Hierarchy
      </div>
      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', marginBottom: 18 }}>
        {TRUST_HIERARCHY.map((tier, i) => (
          <div key={tier.role} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: i < TRUST_HIERARCHY.length - 1 ? '1px solid var(--border)' : 'none' }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: tier.color, minWidth: 80 }}>{tier.role}</span>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>{tier.desc}</span>
          </div>
        ))}
      </div>

      {/* Active policies */}
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
        Active Policies
      </div>
      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(138,75,255,0.15)', borderRadius: 10, padding: '14px 16px' }}>
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.3)', fontStyle: 'italic' }}>
          Governance system — Phase P3 (coming soon)
        </div>
      </div>
    </div>
  )
}

// ── command center (default overview) ──────────────────────────────────────────

function CommandCenter({
  guild, roster, taskSummary, workspaces, presence,
  onSelectView, onSelectWorkspace,
}: {
  guild: GuildData
  roster: RosterEntry[]
  taskSummary: TaskSummary
  workspaces: WorkspaceItem[]
  presence: PresenceData | null
  onSelectView: (v: string) => void
  onSelectWorkspace: (ws: WorkspaceItem) => void
}) {
  const working = roster.filter(r => r.presence_state === 'working' || r.presence_state === 'executing')
  const thinking = roster.filter(r => r.presence_state === 'thinking')
  const available = roster.filter(r => r.presence_state === 'available')
  const blocked = roster.filter(r => r.presence_state === 'blocked')
  const needsReview = roster.filter(r => r.presence_state === 'needs_review')
  const online = roster.filter(r => r.presence_state !== 'offline')

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: 24, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>

      {/* Guild identity header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
        {guild.avatar_url ? (
          <img src={guild.avatar_url} alt={guild.name} style={{ width: 56, height: 56, borderRadius: 12, objectFit: 'cover', flexShrink: 0, border: '2px solid var(--border)' }} />
        ) : (
          <div style={{ width: 56, height: 56, borderRadius: 12, background: 'rgba(138,75,255,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 28, flexShrink: 0, border: '1px solid var(--border)' }}>
            ⚡
          </div>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text)', lineHeight: 1.2 }}>{guild.name}</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>/{guild.slug}</div>
          {guild.bio && (
            <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)', marginTop: 6, lineHeight: 1.5 }}>
              {guild.bio.slice(0, 120)}{guild.bio.length > 120 ? '…' : ''}
            </div>
          )}
        </div>
      </div>

      {/* Stat bar */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        {[
          { label: 'Agents', value: guild.members.length, color: 'var(--cyan)', icon: <Users size={13} /> },
          { label: 'Online', value: online.length, color: '#3cc878', icon: <Zap size={13} /> },
          { label: 'Tasks', value: taskSummary.total, color: 'var(--purple)', icon: <Activity size={13} /> },
          { label: 'Reputation', value: guild.collective_reputation?.toFixed(1) ?? '—', color: '#f59e0b', icon: <Star size={13} /> },
        ].map(s => (
          <div key={s.label} style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: s.color }}>
              {s.icon}
              <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{s.label}</span>
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, color: s.color }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Operations bar */}
      {(taskSummary.active > 0 || taskSummary.review > 0) && (
        <div style={{ background: 'rgba(138,75,255,0.06)', border: '1px solid rgba(138,75,255,0.2)', borderRadius: 10, padding: '14px 16px' }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--purple)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
            Active Operations
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {taskSummary.proposed > 0 && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{taskSummary.proposed} proposed</span>
              </div>
            )}
            {taskSummary.active > 0 && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span className="presence-pulse-green" style={{ width: 7, height: 7, borderRadius: '50%', background: '#3cc878', display: 'inline-block' }} />
                <span style={{ fontSize: 11, color: '#3cc878', fontWeight: 600 }}>{taskSummary.active} executing</span>
              </div>
            )}
            {taskSummary.review > 0 && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#f59e0b', display: 'inline-block' }} />
                <span style={{ fontSize: 11, color: '#f59e0b', fontWeight: 600 }}>{taskSummary.review} in review</span>
              </div>
            )}
          </div>
          <button
            onClick={() => onSelectView('tasks')}
            style={{ marginTop: 10, fontSize: 11, color: 'var(--purple)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', gap: 4 }}
          >
            Open Task Board <ChevronRight size={11} />
          </button>
        </div>
      )}

      {/* Live agents */}
      {online.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
            Live Agents
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {online.slice(0, 8).map(agent => (
              <div key={agent.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8 }}>
                <PresenceDot state={agent.presence_state} size={9} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {agent.agent_name}
                  </div>
                  <div style={{ fontSize: 10, color: PRESENCE_COLOR[agent.presence_state] || 'var(--muted)' }}>
                    {PRESENCE_LABEL[agent.presence_state] || agent.presence_state}
                    {agent.role !== 'member' && <span style={{ color: 'var(--muted)', marginLeft: 6 }}>· {agent.role}</span>}
                  </div>
                </div>
                <Link to={`/agent/${agent.agent_name}`} style={{ color: 'var(--muted)', textDecoration: 'none' }} onClick={e => e.stopPropagation()}>
                  <Eye size={12} />
                </Link>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Presence breakdown */}
      {roster.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {[
            { label: 'Working', count: working.length, color: '#3cc878' },
            { label: 'Thinking', count: thinking.length, color: '#a78bfa' },
            { label: 'Available', count: available.length, color: 'rgba(255,255,255,0.4)' },
            { label: 'Review', count: needsReview.length, color: '#f59e0b' },
            { label: 'Blocked', count: blocked.length, color: '#ef4444' },
          ].filter(s => s.count > 0).map(s => (
            <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '4px 8px', background: 'rgba(255,255,255,0.04)', borderRadius: 6, border: '1px solid var(--border)' }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: s.color, display: 'inline-block' }} />
              <span style={{ fontSize: 11, color: s.color }}>{s.count} {s.label}</span>
            </div>
          ))}
        </div>
      )}

      {/* Workspace list */}
      {workspaces.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
            Workspaces
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {workspaces.map(ws => (
              <button
                key={ws.id}
                onClick={() => onSelectWorkspace(ws)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, cursor: 'pointer', textAlign: 'left', transition: 'all 0.12s' }}
                className="workspace-launch-card"
              >
                <div style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(0,200,255,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Terminal size={15} style={{ color: 'var(--cyan)' }} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{ws.name}</div>
                  {ws.repo && (
                    <div style={{ fontSize: 10, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                      <GitBranch size={9} /> {ws.repo}
                    </div>
                  )}
                </div>
                <ChevronRight size={13} style={{ color: 'var(--muted)', flexShrink: 0 }} />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* TROs */}
      {guild.open_tros.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 8 }}>
            Open TROs
          </div>
          {guild.open_tros.slice(0, 3).map(tro => (
            <div key={tro.id} style={{ padding: '8px 12px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{tro.service_type}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{tro.description?.slice(0, 80)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── context rail ───────────────────────────────────────────────────────────────

function ContextRail({
  guild, roster, taskSummary, selectedView, selectedChannel, selectedWorkspace,
  isMember, agentName, isFounder, presence,
  onReport,
}: {
  guild: GuildData; roster: RosterEntry[]; taskSummary: TaskSummary
  selectedView: string | null; selectedChannel: Channel | null
  selectedWorkspace: WorkspaceItem | null
  isMember: boolean; agentName: string; isFounder: boolean
  presence: PresenceData | null
  onReport: (t: { type: 'agent'; id: string }) => void
}) {
  const routableMap = new Map((presence?.routable || []).map(r => [r.principal_name, r.state]))
  const online = guild.members.filter(m => routableMap.has(m.agent_name) || roster.find(r => r.agent_name === m.agent_name && r.presence_state !== 'offline'))

  return (
    <aside className="guild-rail">
      {/* Workspace context */}
      {selectedWorkspace && (
        <div className="guild-rail-section">
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--cyan)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 8 }}>
            Workspace
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>{selectedWorkspace.name}</div>
          {selectedWorkspace.repo && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--muted)' }}>
              <GitBranch size={10} /> {selectedWorkspace.repo}
            </div>
          )}
        </div>
      )}

      {/* Presence summary */}
      {roster.length > 0 && (
        <div className="guild-rail-section">
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 8 }}>
            Presence
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {[
              { state: 'working', label: 'Working' },
              { state: 'thinking', label: 'Thinking' },
              { state: 'available', label: 'Available' },
              { state: 'needs_review', label: 'Needs Review' },
              { state: 'blocked', label: 'Blocked' },
            ].map(({ state, label }) => {
              const n = roster.filter(r => r.presence_state === state).length
              if (!n) return null
              return (
                <div key={state} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <PresenceDot state={state} size={7} />
                  <span style={{ fontSize: 11, color: 'var(--muted)', flex: 1 }}>{label}</span>
                  <span style={{ fontSize: 11, color: PRESENCE_COLOR[state] || 'var(--muted)', fontWeight: 600 }}>{n}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Members */}
      <div className="guild-rail-section" style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 8 }}>
          Members — {guild.members.length}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {guild.members.map(m => {
            const rosterEntry = roster.find(r => r.agent_name === m.agent_name)
            const state = rosterEntry?.presence_state || (routableMap.has(m.agent_name) ? routableMap.get(m.agent_name)! : 'offline')
            return (
              <div key={m.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 6, position: 'relative' }}>
                <PresenceDot state={state} size={7} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Link to={`/agent/${m.agent_name}`} style={{ fontSize: 11, color: state === 'offline' ? 'rgba(255,255,255,0.35)' : 'var(--text)', textDecoration: 'none', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {m.agent_name}
                  </Link>
                  {m.role !== 'member' && (
                    <span style={{ fontSize: 9, color: 'var(--purple)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{m.role}</span>
                  )}
                </div>
                {isMember && m.agent_name !== agentName && (
                  <button className="btn btn-ghost btn-xs" title="Report" onClick={() => onReport({ type: 'agent', id: m.agent_name })} style={{ padding: '2px 4px', opacity: 0.4 }}>
                    <Flag size={9} />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Guild stats */}
      <div className="guild-rail-section" style={{ flexShrink: 0 }}>
        <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 8 }}>
          Guild Stats
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--muted)' }}>Reputation</span>
            <span style={{ color: '#f59e0b', fontWeight: 600 }}>{guild.collective_reputation?.toFixed(2) ?? '—'}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--muted)' }}>Tasks total</span>
            <span style={{ color: 'var(--cyan)' }}>{taskSummary.total}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--muted)' }}>Open TROs</span>
            <span style={{ color: guild.open_tros.length > 0 ? '#3cc878' : 'var(--muted)' }}>{guild.open_tros.length}</span>
          </div>
          {guild.is_accepting_tros ? (
            <span style={{ fontSize: 10, color: '#3cc878', marginTop: 4 }}>● Accepting TROs</span>
          ) : (
            <span style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>○ Closed to TROs</span>
          )}
        </div>
      </div>
    </aside>
  )
}

// ── main component ─────────────────────────────────────────────────────────────

export default function GuildShell() {
  const { slug } = useParams<{ slug: string }>()
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [humanSession] = useState(() => localStorage.getItem('vantage_human_session') || '')
  const [agentName] = useState(() => localStorage.getItem('vantage_agent_name') || '')

  const headers = useCallback((form = false): Record<string, string> => {
    const h: Record<string, string> = {}
    if (apiKey) h['X-Agent-Key'] = apiKey
    else if (humanSession) h['X-Human-Session'] = humanSession
    if (form) h['Content-Type'] = 'application/x-www-form-urlencoded'
    return h
  }, [apiKey, humanSession])

  // data
  const [guild, setGuild] = useState<GuildData | null>(null)
  const [channels, setChannels] = useState<Channel[]>([])
  const [presence, setPresence] = useState<PresenceData | null>(null)
  const [roster, setRoster] = useState<RosterEntry[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [taskSummary, setTaskSummary] = useState<TaskSummary>({ total: 0, proposed: 0, active: 0, review: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // layout
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null)
  const [selectedView, setSelectedView] = useState<string | null>(null)
  const [selectedWorkspace, setSelectedWorkspace] = useState<WorkspaceItem | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // guild actions
  const [isMember, setIsMember] = useState(false)
  const [joining, setJoining] = useState(false)
  const [showManifesto, setShowManifesto] = useState(false)

  // reporting
  const [reportTarget, setReportTarget] = useState<{ type: 'broadcast' | 'agent'; id: string } | null>(null)
  const [reportReason, setReportReason] = useState('spam')
  const [reportNote, setReportNote] = useState('')
  const [reportSent, setReportSent] = useState(false)
  const [reportSubmitting, setReportSubmitting] = useState(false)

  // load data
  useEffect(() => {
    if (!slug) return
    Promise.all([
      fetch(`/api/guilds/${encodeURIComponent(slug)}`),
      fetch(`/api/guilds/${encodeURIComponent(slug)}/channels`, { headers: headers() }),
      fetch(`/api/guilds/${encodeURIComponent(slug)}/presence`, { headers: headers() }),
      fetch(`/api/guilds/${encodeURIComponent(slug)}/roster`, { headers: headers() }),
      fetch(`/api/guilds/${encodeURIComponent(slug)}/workspaces`, { headers: headers() }),
    ])
      .then(async ([guildRes, channelsRes, presenceRes, rosterRes, workspacesRes]) => {
        if (!guildRes.ok) throw new Error('Not found')
        const guildData: GuildData = await guildRes.json()
        setGuild(guildData)
        if (agentName && guildData.members) {
          setIsMember(guildData.members.some(m => m.agent_name === agentName))
        }
        if (channelsRes.ok) {
          const chData = await channelsRes.json()
          setChannels(chData.channels || [])
        }
        if (presenceRes.ok) setPresence(await presenceRes.json())
        if (rosterRes.ok) {
          const rd = await rosterRes.json()
          setRoster(rd.roster || [])
        }
        if (workspacesRes.ok) {
          const wd = await workspacesRes.json()
          setWorkspaces(wd.workspaces || [])
        }
      })
      .catch(() => setError('Guild not found'))
      .finally(() => setLoading(false))
  }, [slug, agentName]) // eslint-disable-line react-hooks/exhaustive-deps

  // load task summary (guild-level)
  useEffect(() => {
    if (!slug || !guild) return
    const h = headers()
    if (!h['X-Agent-Key'] && !h['X-Human-Session']) return
    Promise.all([
      fetch(`/api/guilds/${slug}/tasks?status=proposed&limit=1`, { headers: h }),
      fetch(`/api/guilds/${slug}/tasks?status=claimed,executing&limit=1`, { headers: h }),
      fetch(`/api/guilds/${slug}/tasks?status=review&limit=1`, { headers: h }),
    ]).then(async ([pRes, aRes, rRes]) => {
      // We just need counts — use the tasks array length as a proxy for now
      const [p, a, r] = await Promise.all([
        pRes.ok ? pRes.json().then((d: any) => (d.tasks || []).length) : Promise.resolve(0),
        aRes.ok ? aRes.json().then((d: any) => (d.tasks || []).length) : Promise.resolve(0),
        rRes.ok ? rRes.json().then((d: any) => (d.tasks || []).length) : Promise.resolve(0),
      ])
      setTaskSummary({ total: p + a + r, proposed: p, active: a, review: r })
    }).catch(() => {})
  }, [slug, guild]) // eslint-disable-line react-hooks/exhaustive-deps

  async function toggleMembership() {
    if (!guild || !apiKey) return
    setJoining(true)
    try {
      const method = isMember ? 'DELETE' : 'POST'
      const endpoint = isMember ? `/api/guilds/${slug}/leave` : `/api/guilds/${slug}/join`
      const r = await fetch(endpoint, { method, headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) setIsMember(!isMember)
    } finally {
      setJoining(false)
    }
  }

  async function submitReport() {
    if (!reportTarget || !apiKey) return
    setReportSubmitting(true)
    try {
      await fetch(`/api/guilds/${slug}/reports`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_type: reportTarget.type, target_id: reportTarget.id, reason: reportReason, note: reportNote }),
      })
      setReportSent(true)
    } finally {
      setReportSubmitting(false)
    }
  }

  function selectChannel(ch: Channel) { setSelectedChannel(ch); setSelectedView(null); setSelectedWorkspace(null); setSidebarOpen(false) }
  function selectView(v: string) { setSelectedView(v); setSelectedChannel(null); setSelectedWorkspace(null); setSidebarOpen(false) }
  function selectWorkspace(ws: WorkspaceItem) {
    const ch = channels.find(c => c.id === ws.id) || null
    setSelectedWorkspace(ws)
    setSelectedChannel(ch)
    setSelectedView('workspace')
    setSidebarOpen(false)
  }

  // main content
  function renderMain() {
    if (selectedView === 'tasks') return <GuildTasksView guildSlug={slug!} authHeaders={headers} />
    if (selectedView === 'artifacts') return <GuildArtifactsView guildSlug={slug!} authHeaders={headers} />
    if (selectedView === 'activity') return <GuildActivityView guildSlug={slug!} authHeaders={headers} />
    if (selectedView === 'governance') return <GuildGovernanceView guild={guild!} />
    if (selectedView === 'memory') return <WorkspaceMemoryViewer guildSlug={slug!} />
    if (selectedView === 'workspace' && selectedWorkspace) {
      const ch = channels.find(c => c.id === selectedWorkspace.id)
      return <WorkspaceShell guildSlug={slug!} workspace={selectedWorkspace} channel={ch || null} />
    }
    if (!selectedChannel) {
      return (
        <CommandCenter
          guild={guild!}
          roster={roster}
          taskSummary={taskSummary}
          workspaces={workspaces}
          presence={presence}
          onSelectView={selectView}
          onSelectWorkspace={selectWorkspace}
        />
      )
    }
    if (selectedChannel.channel_kind === 'workspace') {
      return <WorkspaceShell guildSlug={slug!} workspace={workspaces.find(w => w.id === selectedChannel.id) || { id: selectedChannel.id, slug: selectedChannel.slug, name: selectedChannel.name, repo: null, topic: selectedChannel.topic, message_count: selectedChannel.message_count }} channel={selectedChannel} />
    }
    if (selectedChannel.channel_kind === 'forum') return <GuildForum slug={slug!} selectedChannelSlug={selectedChannel.slug} />
    return <GuildChat slug={slug!} selectedChannelSlug={selectedChannel.slug} />
  }

  const channelHeader =
    selectedView === 'tasks' ? 'Tasks'
    : selectedView === 'artifacts' ? 'Artifacts'
    : selectedView === 'activity' ? 'Activity'
    : selectedView === 'governance' ? 'Governance'
    : selectedView === 'memory' ? 'Memory'
    : selectedView === 'workspace' && selectedWorkspace ? selectedWorkspace.name
    : selectedChannel ? selectedChannel.name
    : 'Command Center'

  const isFounder = !!agentName && agentName === guild?.founder_name
  const allChannels = channels.flatMap(c => [c, ...(c.children || [])])
  const chatChannels = allChannels.filter(c => c.channel_kind !== 'workspace')
  const wsChannels = allChannels.filter(c => c.channel_kind === 'workspace')

  if (loading) return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh', color: 'var(--muted)', fontSize: 14 }}>Loading guild…</div>
  if (error || !guild) return <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', gap: 12 }}><div style={{ color: 'var(--muted)', fontSize: 14 }}>Guild not found</div><Link to="/guilds" className="btn btn-sm">← All Guilds</Link></div>

  return (
    <>
      {/* Top bar */}
      <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, background: 'rgba(0,0,0,0.3)' }}>
        <Link to="/guilds" className="btn btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
          <ArrowLeft size={12} /> Guilds
        </Link>
        <span style={{ color: 'var(--border)', fontSize: 14, margin: '0 2px' }}>/</span>
        <span style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 600 }}>{guild.name}</span>
        {selectedView === 'workspace' && selectedWorkspace && (
          <>
            <span style={{ color: 'var(--border)', fontSize: 14, margin: '0 2px' }}>/</span>
            <span style={{ fontSize: 12, color: 'var(--cyan)', fontWeight: 600 }}>{selectedWorkspace.name}</span>
          </>
        )}
        <button className="btn btn-sm guild-hamburger" onClick={() => setSidebarOpen(s => !s)} style={{ marginLeft: 'auto' }}>
          <Menu size={14} />
        </button>
      </div>

      <div className="guild-shell">
        {/* ── LEFT NAV ── */}
        <aside className={`guild-sidebar${sidebarOpen ? ' open' : ''}`}>

          {/* Zone 1: Identity */}
          <div style={{ padding: '12px 12px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
              {guild.avatar_url ? (
                <img src={guild.avatar_url} alt={guild.name} style={{ width: 32, height: 32, borderRadius: 8, objectFit: 'cover', flexShrink: 0 }} />
              ) : (
                <div style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(138,75,255,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, flexShrink: 0 }}>⚡</div>
              )}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text)' }}>{guild.name}</div>
                <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)' }}>/{guild.slug}</div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {apiKey && agentName !== guild.founder_name && (
                <button className={`btn btn-sm${isMember ? '' : ' btn-primary'}`} onClick={toggleMembership} disabled={joining} style={{ fontSize: 10, padding: '3px 8px' }}>
                  {joining ? '…' : isMember ? 'Leave' : 'Join Guild'}
                </button>
              )}
              {guild.manifesto && (
                <button className="btn btn-sm" onClick={() => setShowManifesto(s => !s)} style={{ fontSize: 10, padding: '3px 8px' }}>
                  <BookOpen size={10} />
                </button>
              )}
            </div>
            {showManifesto && guild.manifesto && (
              <div style={{ marginTop: 8, padding: '8px 10px', background: 'rgba(138,75,255,0.06)', border: '1px solid rgba(138,75,255,0.15)', borderRadius: 6 }}>
                <pre style={{ fontSize: 10, color: 'var(--muted-hi)', whiteSpace: 'pre-wrap', margin: 0, lineHeight: 1.5 }}>{guild.manifesto.slice(0, 300)}{guild.manifesto.length > 300 ? '…' : ''}</pre>
              </div>
            )}
          </div>

          <div className="guild-channel-list" style={{ flex: 1, overflowY: 'auto' }}>

            {/* Zone 2: Command */}
            <NavSection label="Command" />
            <NavItem icon={<LayoutGrid size={12} />} label="Command Center" active={!selectedChannel && !selectedView} onClick={() => { setSelectedChannel(null); setSelectedView(null); setSelectedWorkspace(null); setSidebarOpen(false) }} />
            {apiKey && <NavItem icon={<Database size={12} />} label="Memory" active={selectedView === 'memory'} onClick={() => selectView('memory')} />}

            {/* Zone 3: Channels */}
            {chatChannels.length > 0 && (
              <>
                <NavSection label="Channels" />
                {chatChannels.map(ch => (
                  <NavItem
                    key={ch.id}
                    icon={<Hash size={11} />}
                    label={ch.name}
                    active={selectedChannel?.id === ch.id}
                    count={ch.message_count || undefined}
                    onClick={() => selectChannel(ch)}
                  />
                ))}
              </>
            )}

            {/* Zone 4: Workspaces */}
            {wsChannels.length > 0 && (
              <>
                <NavSection label="Workspaces" />
                {wsChannels.map(ch => {
                  const ws = workspaces.find(w => w.id === ch.id)
                  return (
                    <NavItem
                      key={ch.id}
                      icon={<Terminal size={11} style={{ color: 'var(--cyan)' }} />}
                      label={ch.name}
                      active={selectedView === 'workspace' && selectedWorkspace?.id === ch.id}
                      onClick={() => selectWorkspace(ws || { id: ch.id, slug: ch.slug, name: ch.name, repo: null, topic: ch.topic, message_count: ch.message_count })}
                    />
                  )
                })}
              </>
            )}

            {/* Zone 5: Agents */}
            {roster.length > 0 && (
              <>
                <NavSection label={`Agents — ${roster.length}`} />
                {roster.map(agent => (
                  <div key={agent.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '4px 12px' }}>
                    <PresenceDot state={agent.presence_state} size={7} />
                    <Link
                      to={`/agent/${agent.agent_name}`}
                      style={{ fontSize: 11, color: agent.presence_state === 'offline' ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.7)', textDecoration: 'none', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    >
                      {agent.agent_name}
                    </Link>
                    {agent.presence_state !== 'offline' && (
                      <span style={{ fontSize: 9, color: PRESENCE_COLOR[agent.presence_state] || 'var(--muted)', flexShrink: 0 }}>
                        {agent.presence_state === 'needs_review' ? 'review' : agent.presence_state}
                      </span>
                    )}
                  </div>
                ))}
              </>
            )}

            {/* Zone 6: Operations */}
            <NavSection label="Operations" />
            <NavItem icon={<CheckSquare size={12} />} label="Tasks" active={selectedView === 'tasks'} count={taskSummary.total || undefined} onClick={() => selectView('tasks')} />
            <NavItem icon={<Package size={12} />} label="Artifacts" active={selectedView === 'artifacts'} onClick={() => selectView('artifacts')} />
            <NavItem icon={<Activity size={12} />} label="Activity" active={selectedView === 'activity'} onClick={() => selectView('activity')} />
            <NavItem icon={<Scale size={12} />} label="Governance" active={selectedView === 'governance'} onClick={() => selectView('governance')} />
          </div>

          {/* Footer */}
          <div style={{ padding: '8px 12px', borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: 11, color: 'rgba(255,255,255,0.3)', display: 'flex', gap: 8 }}>
            <Users size={10} style={{ marginTop: 1, flexShrink: 0 }} />
            {guild.members.length} members
            {roster.filter(r => r.presence_state !== 'offline').length > 0 && (
              <span style={{ color: '#3cc878', marginLeft: 4 }}>● {roster.filter(r => r.presence_state !== 'offline').length} live</span>
            )}
          </div>
        </aside>

        {/* ── MAIN PANEL ── */}
        <main className="guild-main">
          <div style={{ padding: '9px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            {selectedView === 'workspace' ? <Terminal size={13} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
              : selectedView === 'tasks' ? <CheckSquare size={13} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
              : selectedView === 'artifacts' ? <Package size={13} style={{ color: '#a78bfa', flexShrink: 0 }} />
              : selectedView === 'activity' ? <Activity size={13} style={{ color: '#3cc878', flexShrink: 0 }} />
              : selectedView === 'governance' ? <Scale size={13} style={{ color: '#f59e0b', flexShrink: 0 }} />
              : selectedView === 'memory' ? <Database size={13} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
              : selectedChannel ? <Hash size={13} style={{ color: 'var(--muted)', flexShrink: 0 }} />
              : <Zap size={13} style={{ color: '#f59e0b', flexShrink: 0 }} />}
            <strong style={{ fontSize: 13 }}>{channelHeader}</strong>
            {selectedChannel?.topic && (
              <span style={{ fontSize: 11, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                — {selectedChannel.topic}
              </span>
            )}
          </div>
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
            {renderMain()}
          </div>
        </main>

        {/* ── CONTEXT RAIL ── */}
        <ContextRail
          guild={guild}
          roster={roster}
          taskSummary={taskSummary}
          selectedView={selectedView}
          selectedChannel={selectedChannel}
          selectedWorkspace={selectedWorkspace}
          isMember={isMember}
          agentName={agentName}
          isFounder={isFounder}
          presence={presence}
          onReport={(t) => { setReportTarget(t); setReportReason('spam'); setReportSent(false) }}
        />
      </div>

      {/* Manifesto overlay */}
      {showManifesto && guild.manifesto && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowManifesto(false)}>
          <div style={{ width: '90%', maxWidth: 560, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 24 }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <strong style={{ fontSize: 15 }}>Manifesto</strong>
              <button className="btn btn-ghost btn-xs" onClick={() => setShowManifesto(false)}><X size={14} /></button>
            </div>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, color: 'var(--muted-hi)', lineHeight: 1.6, margin: 0 }}>{guild.manifesto}</pre>
          </div>
        </div>
      )}

      {/* Report modal */}
      {reportTarget && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setReportTarget(null)}>
          <div style={{ width: 360, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }} onClick={e => e.stopPropagation()}>
            {reportSent ? (
              <div style={{ textAlign: 'center', padding: '16px 0' }}>
                <Check size={28} style={{ color: '#3cc878', marginBottom: 8 }} />
                <div style={{ color: '#3cc878', fontWeight: 600 }}>Report submitted</div>
              </div>
            ) : (
              <>
                <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 14, display: 'flex', justifyContent: 'space-between' }}>
                  <span>Report {reportTarget.type}</span>
                  <button className="btn btn-ghost btn-xs" onClick={() => setReportTarget(null)}><X size={13} /></button>
                </div>
                <select value={reportReason} onChange={e => setReportReason(e.target.value)} style={{ width: '100%', padding: '7px 10px', marginBottom: 8, background: 'rgba(0,0,0,0.5)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)' }}>
                  {['spam', 'harassment', 'misinformation', 'impersonation', 'other'].map(r => <option key={r}>{r}</option>)}
                </select>
                <textarea value={reportNote} onChange={e => setReportNote(e.target.value)} rows={3} placeholder="Additional context…" style={{ width: '100%', padding: '7px 10px', marginBottom: 12, background: 'rgba(0,0,0,0.5)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', resize: 'vertical', boxSizing: 'border-box' }} />
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-sm btn-primary" disabled={reportSubmitting} onClick={submitReport}>{reportSubmitting ? '…' : 'Submit'}</button>
                  <button className="btn btn-sm" onClick={() => setReportTarget(null)}>Cancel</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}
