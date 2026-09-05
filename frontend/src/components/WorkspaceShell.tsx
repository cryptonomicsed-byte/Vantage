/**
 * WorkspaceShell — Per-workspace operating environment.
 *
 * Renders a workspace channel with full tab navigation:
 * Overview · Chat · Tasks · Code · Artifacts · Memory · Agents · Activity
 */
import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Database, Eye, GitBranch, MessageSquare, Terminal, Users, Clock, Star } from 'lucide-react'
import GuildChat from './GuildChat'
import GuildForum from './GuildForum'
import WorkspaceCode from './WorkspaceCode'
import WorkspaceTaskBoard from './WorkspaceTaskBoard'
import WorkspaceMemoryViewer from './WorkspaceMemoryViewer'

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
]

// ── workspace overview ─────────────────────────────────────────────────────────

function WorkspaceOverview({
  workspace, guildSlug, roster, taskSummary,
}: {
  workspace: WorkspaceItem; guildSlug: string
  roster: RosterEntry[]; taskSummary: TaskSummary
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

      {/* Artifacts placeholder */}
      <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px dashed rgba(255,255,255,0.1)', borderRadius: 10, padding: '20px', textAlign: 'center' }}>
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 6 }}>Artifact provenance</div>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)' }}>
          Task → Claim → Execution → Artifact → ActReceipt → Verification
        </div>
      </div>
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
        return <WorkspaceOverview workspace={workspace} guildSlug={guildSlug} roster={roster} taskSummary={taskSummary} />
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
