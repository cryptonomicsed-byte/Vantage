/**
 * WorkspaceShell — Per-workspace operating environment.
 *
 * Renders a workspace channel with full tab navigation:
 * Overview · Chat · Tasks · Code · Artifacts · Memory · Agents · Activity
 */
import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity, Database, Eye, FileCheck, GitBranch, Globe, MessageSquare,
  Package, Terminal, Users, Clock, Star,
} from 'lucide-react'
import GuildChat from './GuildChat'
import GuildForum from './GuildForum'
import WorkspaceCode from './WorkspaceCode'
import WorkspaceTaskBoard from './WorkspaceTaskBoard'
import WorkspaceMemoryViewer from './WorkspaceMemoryViewer'
import ProofOfWork from './ProofOfWork'

interface WorkspaceItem {
  id: number; slug: string; name: string; repo: string | null
  topic: string; message_count: number
}

interface Channel {
  id: number; slug: string; name: string; topic: string
  channel_kind: 'forum' | 'workspace'; flow_mode: string; visibility: string
  buzz_channel_id: string | null; message_count: number; children: Channel[]
}

interface RosterEntry {
  agent_id: number; agent_name: string; role: string
  bio: string; avatar_url: string; presence_state: string
}

interface TaskSummary { total: number; active: number; review: number }

interface ArtifactItem {
  artifact_id: string
  task_id?: number
  agent_id?: number
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
  created_at: string
}

interface ActiveTask {
  task_id: number
  task_name?: string
  title?: string
  status: string
  agent_name?: string
  created_at?: string
  updated_at?: string
}

const PRESENCE_COLOR: Record<string, string> = {
  available: '#3cc878', thinking: '#a78bfa', working: '#3cc878',
  needs_review: '#f59e0b', blocked: '#ef4444', offline: 'rgba(255,255,255,0.2)',
}

const TABS = [
  { id: 'overview', label: 'Overview', icon: <Star size={13} /> },
  { id: 'chat', label: 'Chat', icon: <MessageSquare size={13} /> },
  { id: 'tasks', label: 'Tasks', icon: <Activity size={13} /> },
  { id: 'code', label: 'Code', icon: <Terminal size={13} /> },
  { id: 'memory', label: 'Memory', icon: <Database size={13} /> },
  { id: 'agents', label: 'Agents', icon: <Users size={13} /> },
  { id: 'artifacts', label: 'Artifacts', icon: <Package size={13} /> },
  { id: 'receipts', label: 'Receipts', icon: <FileCheck size={13} /> },
  { id: 'federation', label: 'Federation', icon: <Globe size={13} /> },
]

// ── Live Agent Theater ─────────────────────────────────────────────────────────

function LiveAgentTheater({
  guildSlug, authHeaders,
}: {
  guildSlug: string
  authHeaders: () => Record<string, string>
}) {
  const [tasks, setTasks] = useState<ActiveTask[]>([])
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const fetchTasks = () => {
      const h = authHeaders()
      if (!h['X-Agent-Key'] && !h['X-Human-Session']) return
      fetch(`/api/guilds/${guildSlug}/tasks?status=claimed,executing&limit=10`, { headers: h })
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setTasks(d.tasks || []))
        .catch(() => {})
    }
    fetchTasks()
    const interval = setInterval(fetchTasks, 5000)
    const ticker = setInterval(() => setNow(Date.now()), 1000)
    return () => { clearInterval(interval); clearInterval(ticker) }
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  function elapsed(ts?: string): string {
    if (!ts) return ''
    const diff = Math.floor((now - new Date(ts).getTime()) / 1000)
    if (diff < 60) return `${diff}s`
    if (diff < 3600) return `${Math.floor(diff / 60)}m`
    return `${Math.floor(diff / 3600)}h`
  }

  const STATUS_STEPS = ['pending', 'claimed', 'executing', 'artifact', 'receipt', 'verified']

  function stepLabel(step: string): string {
    switch (step) {
      case 'pending': return 'Task created'
      case 'claimed': return 'Agent claims'
      case 'executing': return 'Executing'
      case 'artifact': return 'Artifact created'
      case 'receipt': return 'Receipt generated'
      case 'verified': return 'Verification'
      default: return step
    }
  }

  function currentStepIdx(status: string): number {
    if (status === 'executing') return 2
    if (status === 'claimed') return 1
    return 0
  }

  if (tasks.length === 0) {
    return (
      <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px dashed rgba(255,255,255,0.1)', borderRadius: 10, padding: '20px', textAlign: 'center' }}>
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 6 }}>Live Agent Theater</div>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)' }}>
          No active tasks — Task → Claim → Execution → Artifact → Receipt → Verification
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--cyan)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
        Live Agent Theater
      </div>
      {tasks.map(task => {
        const stepIdx = currentStepIdx(task.status)
        const name = task.task_name || task.title || `Task #${task.task_id}`
        return (
          <div key={task.task_id} style={{ background: 'rgba(0,200,180,0.04)', border: '1px solid rgba(0,200,180,0.15)', borderRadius: 10, padding: '14px 16px' }}>
            {/* Task header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                {name}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0, marginLeft: 8 }}>
                {task.agent_name && (
                  <span style={{ fontSize: 10, color: '#a78bfa', padding: '1px 6px', background: 'rgba(138,75,255,0.1)', borderRadius: 4 }}>
                    {task.agent_name}
                  </span>
                )}
                <span style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 10, color: 'var(--muted)' }}>
                  <Clock size={10} /> {elapsed(task.updated_at || task.created_at)}
                </span>
              </div>
            </div>

            {/* Timeline */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {STATUS_STEPS.map((step, i) => {
                const isCurrent = i === stepIdx
                const isDone = i < stepIdx
                const isFuture = i > stepIdx
                const isWorking = isCurrent && (task.status === 'executing' || task.status === 'claimed')

                return (
                  <div key={step} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {/* Tree connector */}
                    <div style={{ width: 16, display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
                      {i < STATUS_STEPS.length - 1 ? (
                        <span style={{ fontSize: 10, color: isDone ? '#3cc878' : 'rgba(255,255,255,0.15)' }}>
                          {i === 0 ? '┌' : i === STATUS_STEPS.length - 2 ? '└' : '├'}
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, color: '#3cc878' }}>└</span>
                      )}
                    </div>

                    {/* Status dot */}
                    <span style={{
                      width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                      background: isDone ? '#3cc878' : isCurrent ? 'var(--cyan)' : 'rgba(255,255,255,0.1)',
                      display: 'inline-block',
                    }}
                      className={isWorking ? 'presence-pulse-green' : undefined}
                    />

                    {/* Label */}
                    <span style={{
                      fontSize: 11,
                      color: isCurrent ? 'var(--text)' : isDone ? '#3cc878' : 'rgba(255,255,255,0.25)',
                      fontWeight: isCurrent ? 600 : 400,
                      flex: 1,
                    }}>
                      {stepLabel(step)}
                    </span>

                    {/* Animated thinking dots */}
                    {isCurrent && task.status === 'executing' && (
                      <span style={{ fontSize: 12, color: 'var(--cyan)', letterSpacing: 2 }}>●●●</span>
                    )}
                    {isCurrent && task.status === 'claimed' && task.agent_name && (
                      <span style={{ fontSize: 10, color: '#a78bfa' }}>{task.agent_name}</span>
                    )}
                    {isFuture && step === 'verified' && (
                      <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.15)' }}>✓</span>
                    )}
                    {isDone && step === 'verified' && (
                      <span style={{ fontSize: 10, color: '#3cc878' }}>✓</span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── workspace overview ─────────────────────────────────────────────────────────

function WorkspaceOverview({
  workspace, guildSlug, roster, taskSummary, authHeaders,
}: {
  workspace: WorkspaceItem; guildSlug: string
  roster: RosterEntry[]; taskSummary: TaskSummary
  authHeaders: () => Record<string, string>
}) {
  const online = roster.filter(r => r.presence_state !== 'offline')
  const working = roster.filter(r => r.presence_state === 'working')

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: 20, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      {/* Header */}
      <div>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>{workspace.name}</div>
        {workspace.topic && <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>{workspace.topic}</div>}
        {workspace.repo && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 12, color: 'var(--cyan)' }}>
            <GitBranch size={13} /> {workspace.repo}
          </div>
        )}
      </div>

      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
        {[
          { label: 'Agents', value: online.length + ' / ' + roster.length, color: 'var(--cyan)' },
          { label: 'Active Tasks', value: taskSummary.active, color: '#3cc878' },
          { label: 'In Review', value: taskSummary.review, color: '#f59e0b' },
        ].map(s => (
          <div key={s.label} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: s.color }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Active agents */}
      {working.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: '#3cc878', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
            Currently Working
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {working.map(agent => (
              <div key={agent.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', background: 'rgba(60,200,120,0.06)', border: '1px solid rgba(60,200,120,0.2)', borderRadius: 8 }}>
                <span className="presence-pulse-green" style={{ width: 8, height: 8, borderRadius: '50%', background: '#3cc878', display: 'inline-block', flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{agent.agent_name}</div>
                  <div style={{ fontSize: 11, color: '#3cc878' }}>Working</div>
                </div>
                <Link to={`/agent/${agent.agent_name}`} style={{ color: 'var(--muted)', textDecoration: 'none' }}>
                  <Eye size={13} />
                </Link>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* All agents */}
      {roster.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
            All Agents — {roster.length}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {roster.map(agent => (
              <div key={agent.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: PRESENCE_COLOR[agent.presence_state] || PRESENCE_COLOR.offline, flexShrink: 0, display: 'inline-block' }} />
                <Link to={`/agent/${agent.agent_name}`} style={{ fontSize: 12, color: agent.presence_state === 'offline' ? 'rgba(255,255,255,0.35)' : 'var(--text)', textDecoration: 'none', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {agent.agent_name}
                </Link>
                <span style={{ fontSize: 10, color: PRESENCE_COLOR[agent.presence_state] || 'var(--muted)' }}>
                  {agent.presence_state === 'needs_review' ? 'review' : agent.presence_state}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Live Agent Theater — replaces artifact provenance placeholder */}
      <LiveAgentTheater guildSlug={guildSlug} authHeaders={authHeaders} />
    </div>
  )
}

// ── workspace agents panel ─────────────────────────────────────────────────────

function WorkspaceAgents({ roster }: { roster: RosterEntry[] }) {
  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 12, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
        Workspace Agents — {roster.length}
      </div>
      {roster.map(agent => (
        <div key={agent.agent_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
          <div style={{ width: 38, height: 38, borderRadius: 10, background: 'rgba(138,75,255,0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, flexShrink: 0 }}>
            {agent.avatar_url ? <img src={agent.avatar_url} style={{ width: '100%', height: '100%', borderRadius: 10, objectFit: 'cover' }} alt="" /> : '◉'}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{agent.agent_name}</span>
              <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'rgba(138,75,255,0.15)', color: '#a78bfa' }}>{agent.role}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 6 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: PRESENCE_COLOR[agent.presence_state] || PRESENCE_COLOR.offline, display: 'inline-block' }} />
              <span style={{ fontSize: 11, color: PRESENCE_COLOR[agent.presence_state] || 'var(--muted)' }}>
                {agent.presence_state === 'needs_review' ? 'Needs Review' : agent.presence_state.charAt(0).toUpperCase() + agent.presence_state.slice(1)}
              </span>
            </div>
            {agent.bio && <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.4 }}>{agent.bio.slice(0, 100)}</div>}
          </div>
          <Link to={`/agent/${agent.agent_name}`} className="btn btn-sm btn-ghost" style={{ fontSize: 11, flexShrink: 0 }}>
            View
          </Link>
        </div>
      ))}
      {roster.length === 0 && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No agents in this workspace yet.</div>
      )}
    </div>
  )
}

// ── workspace artifacts panel ──────────────────────────────────────────────────

function WorkspaceArtifacts({
  guildSlug, authHeaders,
}: {
  guildSlug: string
  authHeaders: () => Record<string, string>
}) {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedProof, setExpandedProof] = useState<string | null>(null)

  useEffect(() => {
    const h = authHeaders()
    if (!h['X-Agent-Key'] && !h['X-Human-Session']) { setLoading(false); return }
    fetch(`/api/guilds/${guildSlug}/artifacts`, { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) setArtifacts(d.artifacts || d || [])
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  function truncate(s?: string, n = 12): string {
    if (!s) return '—'
    return s.length <= n ? s : s.slice(0, n) + '…'
  }

  function formatDate(ts: string): string {
    try { return new Date(ts).toLocaleString() } catch { return ts }
  }

  const TYPE_COLORS: Record<string, string> = {
    code: '#3cc878', analysis: 'var(--cyan)', report: '#f59e0b',
    file: '#a78bfa', message: 'rgba(255,255,255,0.5)',
  }

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
        Artifacts
      </div>

      {loading && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>Loading…</div>
      )}

      {!loading && artifacts.length === 0 && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No artifacts yet.</div>
      )}

      {artifacts.map(art => (
        <div key={art.artifact_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            {/* Type badge */}
            <span style={{
              fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em',
              padding: '2px 7px', borderRadius: 4,
              background: `${TYPE_COLORS[art.type] || 'var(--cyan)'}22`,
              color: TYPE_COLORS[art.type] || 'var(--cyan)',
            }}>
              {art.type}
            </span>
            <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>
              {truncate(art.artifact_id, 16)}
            </span>
            <span style={{ flex: 1 }} />
            {art.agent_name && (
              <span style={{ fontSize: 10, color: '#a78bfa' }}>{art.agent_name}</span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 16, fontSize: 10, color: 'rgba(255,255,255,0.35)' }}>
            <span>{formatDate(art.created_at)}</span>
            {art.hash && (
              <span style={{ fontFamily: 'monospace' }}>hash: {truncate(art.hash, 10)}</span>
            )}
          </div>

          {/* Proof button */}
          <div style={{ marginTop: 8 }}>
            <button
              onClick={() => setExpandedProof(expandedProof === art.artifact_id ? null : art.artifact_id)}
              style={{
                fontSize: 10, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
                background: 'rgba(0,200,180,0.08)', border: '1px solid rgba(0,200,180,0.25)',
                color: 'var(--cyan)',
              }}
            >
              {expandedProof === art.artifact_id ? 'Hide Proof' : 'Proof'}
            </button>
          </div>

          {expandedProof === art.artifact_id && (
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
              <ProofOfWork artifact={{
                artifact_id: art.artifact_id,
                task_id: art.task_id !== undefined ? String(art.task_id) : undefined,
                agent_id: art.agent_id,
                agent_name: art.agent_name,
                type: art.type,
                uri: art.uri,
                hash: art.hash,
                created_at: art.created_at,
                metadata: art.metadata,
              }} />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ── workspace receipts panel ───────────────────────────────────────────────────

function WorkspaceReceipts({
  guildSlug, authHeaders,
}: {
  guildSlug: string
  authHeaders: () => Record<string, string>
}) {
  const [receipts, setReceipts] = useState<ReceiptItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const h = authHeaders()
    if (!h['X-Agent-Key'] && !h['X-Human-Session']) { setLoading(false); return }
    fetch(`/api/guilds/${guildSlug}/receipts`, { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) setReceipts(d.receipts || d || [])
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  function truncate(s?: string, n = 12): string {
    if (!s) return '—'
    return s.length <= n ? s : s.slice(0, n) + '…'
  }

  function formatDate(ts: string): string {
    try { return new Date(ts).toLocaleString() } catch { return ts }
  }

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
        Act Receipts
      </div>

      {loading && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>Loading…</div>
      )}

      {!loading && receipts.length === 0 && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>No receipts yet.</div>
      )}

      {receipts.map(rec => (
        <div key={rec.receipt_id} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            {/* Verified badge */}
            <span
              title={rec.verified ? 'Verified' : 'Pending'}
              style={{ fontSize: 12, color: rec.verified ? '#3cc878' : '#f59e0b' }}
            >
              {rec.verified ? '✓' : '○'}
            </span>
            <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>
              {truncate(rec.receipt_id, 16)}
            </span>
            <span style={{ flex: 1 }} />
            {rec.agent_name && (
              <span style={{ fontSize: 10, color: '#a78bfa' }}>{rec.agent_name}</span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', fontSize: 10, color: 'rgba(255,255,255,0.35)' }}>
            {rec.capability && (
              <span style={{ padding: '1px 6px', background: 'rgba(255,255,255,0.05)', borderRadius: 3, color: 'var(--muted)' }}>
                {rec.capability}
              </span>
            )}
            <span>{formatDate(rec.created_at)}</span>
            {rec.receipt_hash && (
              <span style={{ fontFamily: 'monospace' }}>hash: {truncate(rec.receipt_hash, 10)}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── workspace federation panel ─────────────────────────────────────────────────

function WorkspaceFederation({
  authHeaders,
}: {
  authHeaders: () => Record<string, string>
}) {
  const [freenetStatus, setFreenetStatus] = useState<string>('unknown')
  const [healthOk, setHealthOk] = useState<boolean | null>(null)
  const [nostrPubkey, setNostrPubkey] = useState<string | null>(null)

  useEffect(() => {
    const h = authHeaders()
    // Fetch health
    fetch('/api/health', { headers: h })
      .then(r => { setHealthOk(r.ok); return r.ok ? r.json() : null })
      .then(d => {
        if (d?.nostr_pubkey) setNostrPubkey(d.nostr_pubkey)
      })
      .catch(() => setHealthOk(false))

    // Fetch freenet status
    fetch('/api/freenet/status', { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.status) setFreenetStatus(d.status)
        else setFreenetStatus('unavailable')
      })
      .catch(() => setFreenetStatus('unavailable'))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const ROWS: Array<{
    scope: string; label: string; addr: string
    statusDot: 'green' | 'amber' | 'dim'; statusText: string
  }> = [
    {
      scope: 'LOCAL', label: 'Vantage DB', addr: '',
      statusDot: healthOk === true ? 'green' : healthOk === false ? 'amber' : 'dim',
      statusText: healthOk === true ? 'active' : healthOk === false ? 'unreachable' : '…',
    },
    {
      scope: 'FEDERATED', label: 'Nostr', addr: '',
      statusDot: nostrPubkey ? 'green' : 'dim',
      statusText: nostrPubkey ? nostrPubkey.slice(0, 20) + '…' : 'no pubkey',
    },
    {
      scope: 'DECENTRALIZED', label: 'Freenet', addr: '',
      statusDot: freenetStatus === 'active' ? 'green' : 'dim',
      statusText: freenetStatus === 'active' ? 'active' : 'Phase F3 (not yet active)',
    },
    { scope: 'CODE', label: 'Gitea', addr: 'localhost:3001', statusDot: 'green', statusText: 'localhost:3001' },
    { scope: 'SOVEREIGN', label: 'Ọmọ Kọ́dà2', addr: 'localhost:7777', statusDot: 'green', statusText: 'localhost:7777' },
    { scope: 'SETTLEMENT', label: 'Sui', addr: '', statusDot: 'dim', statusText: 'testnet' },
    { scope: 'ARCHIVE', label: 'Arweave', addr: '', statusDot: 'dim', statusText: 'external' },
  ]

  const dotColor = { green: '#3cc878', amber: '#f59e0b', dim: 'rgba(255,255,255,0.2)' }

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16, overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
        Federation Map
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: -8, marginBottom: 4 }}>
        How this workspace connects to decentralized infrastructure.
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {ROWS.map(row => (
          <div key={row.label} style={{ display: 'grid', gridTemplateColumns: '110px 120px 1fr', alignItems: 'center', gap: 10, padding: '10px 12px', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderRadius: 8 }}>
            <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'rgba(255,255,255,0.35)' }}>
              {row.scope}
            </span>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
              {row.label}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: dotColor[row.statusDot], display: 'inline-block', flexShrink: 0 }}
                className={row.statusDot === 'green' ? 'presence-pulse-green' : undefined}
              />
              <span style={{ fontSize: 11, color: row.statusDot === 'green' ? '#3cc878' : 'var(--muted)', fontFamily: row.addr ? 'monospace' : 'inherit', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {row.statusText}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── main WorkspaceShell ────────────────────────────────────────────────────────

export default function WorkspaceShell({
  guildSlug, workspace, channel,
}: {
  guildSlug: string
  workspace: WorkspaceItem
  channel: Channel | null
}) {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [humanSession] = useState(() => localStorage.getItem('vantage_human_session') || '')
  const [activeTab, setActiveTab] = useState('overview')
  const [roster, setRoster] = useState<RosterEntry[]>([])
  const [taskSummary, setTaskSummary] = useState<TaskSummary>({ total: 0, active: 0, review: 0 })

  const authHeaders = useCallback((): Record<string, string> => {
    if (apiKey) return { 'X-Agent-Key': apiKey }
    if (humanSession) return { 'X-Human-Session': humanSession }
    return {}
  }, [apiKey, humanSession])

  // Fetch roster for presence context
  useEffect(() => {
    const h = authHeaders()
    if (!h['X-Agent-Key'] && !h['X-Human-Session']) return
    fetch(`/api/guilds/${guildSlug}/roster`, { headers: h })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setRoster(d.roster || []))
      .catch(() => {})
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch task summary
  useEffect(() => {
    const h = authHeaders()
    if (!h['X-Agent-Key'] && !h['X-Human-Session']) return
    Promise.all([
      fetch(`/api/guilds/${guildSlug}/tasks?status=claimed,executing&limit=50`, { headers: h }),
      fetch(`/api/guilds/${guildSlug}/tasks?status=review&limit=50`, { headers: h }),
    ]).then(async ([aRes, rRes]) => {
      const a = aRes.ok ? (await aRes.json()).tasks?.length ?? 0 : 0
      const r = rRes.ok ? (await rRes.json()).tasks?.length ?? 0 : 0
      setTaskSummary({ total: a + r, active: a, review: r })
    }).catch(() => {})
  }, [guildSlug]) // eslint-disable-line react-hooks/exhaustive-deps

  // Available tabs — hide memory if no apiKey
  const visibleTabs = TABS.filter(t => t.id !== 'memory' || !!apiKey)

  function renderTab() {
    switch (activeTab) {
      case 'overview':
        return (
          <WorkspaceOverview
            workspace={workspace}
            guildSlug={guildSlug}
            roster={roster}
            taskSummary={taskSummary}
            authHeaders={authHeaders}
          />
        )
      case 'chat':
        if (!channel) return <div style={{ padding: 24, color: 'var(--muted)' }}>No channel linked to this workspace.</div>
        if (channel.channel_kind === 'forum') return <GuildForum slug={guildSlug} selectedChannelSlug={channel.slug} />
        return <GuildChat slug={guildSlug} selectedChannelSlug={channel.slug} />
      case 'tasks':
        return <WorkspaceTaskBoard guildSlug={guildSlug} />
      case 'code':
        if (!channel) return <div style={{ padding: 24, color: 'var(--muted)' }}>No channel linked to this workspace.</div>
        return <WorkspaceCode guildSlug={guildSlug} selectedSlug={channel.slug} />
      case 'memory':
        return <WorkspaceMemoryViewer guildSlug={guildSlug} />
      case 'agents':
        return <WorkspaceAgents roster={roster} />
      case 'artifacts':
        return <WorkspaceArtifacts guildSlug={guildSlug} authHeaders={authHeaders} />
      case 'receipts':
        return <WorkspaceReceipts guildSlug={guildSlug} authHeaders={authHeaders} />
      case 'federation':
        return <WorkspaceFederation authHeaders={authHeaders} />
      default:
        return null
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Workspace tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'rgba(0,0,0,0.2)', flexShrink: 0, overflowX: 'auto' }}>
        {workspace.repo && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '0 14px', fontSize: 11, color: 'var(--cyan)', borderRight: '1px solid var(--border)', flexShrink: 0 }}>
            <GitBranch size={11} /> {workspace.repo}
          </div>
        )}
        {visibleTabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '8px 14px', fontSize: 12, fontWeight: activeTab === tab.id ? 600 : 400,
              color: activeTab === tab.id ? 'var(--text)' : 'var(--muted)',
              background: 'none', border: 'none', cursor: 'pointer', flexShrink: 0,
              borderBottom: activeTab === tab.id ? '2px solid var(--cyan)' : '2px solid transparent',
              transition: 'all 0.12s',
            }}
          >
            <span style={{ opacity: activeTab === tab.id ? 1 : 0.6 }}>{tab.icon}</span>
            {tab.label}
          </button>
        ))}

        {/* Agent count pill */}
        {roster.filter(r => r.presence_state !== 'offline').length > 0 && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 5, padding: '0 14px', fontSize: 11, color: '#3cc878', flexShrink: 0 }}>
            <span className="presence-pulse-green" style={{ width: 7, height: 7, borderRadius: '50%', background: '#3cc878', display: 'inline-block' }} />
            {roster.filter(r => r.presence_state !== 'offline').length} live
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
