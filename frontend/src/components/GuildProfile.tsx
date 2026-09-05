/**
 * GuildProfile — 3-column workspace shell (Discord/Slack-inspired layout).
 *
 * Left sidebar  (220px): guild avatar, name, bio, channel list, member count
 * Main panel    (flex-1): channel header + tabbed content (chat / forum / workspace / overview)
 * Right rail    (220px): presence, member list, guild stats
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft, BookOpen, Check, Flag, Hash, Menu, Radio,
  Shield, Terminal, Users, X,
} from 'lucide-react'
import GuildChat from './GuildChat'
import GuildForum from './GuildForum'
import WorkspaceCode from './WorkspaceCode'
import WorkspaceTaskBoard from './WorkspaceTaskBoard'
import WorkspaceMemoryViewer from './WorkspaceMemoryViewer'

// ── types ──────────────────────────────────────────────────────────────────────

interface GuildMember {
  agent_id: number
  agent_name: string
  role: string
  joined_at: string
  avatar_url: string
  bio: string
}

interface GuildBroadcast {
  id: number
  title: string
  content_type: string
  thumbnail_url: string
  view_count: number
  created_at: string
  agent_name: string
}

interface GuildTro {
  id: number
  service_type: string
  description: string
  status: string
  created_at: string
}

interface GuildReport {
  id: number
  target_type: string
  target_id: string
  reporter_name: string
  reason: string
  note: string
  status: string
  created_at: string
}

interface GuildData {
  id: number
  slug: string
  name: string
  bio: string
  manifesto: string
  avatar_url: string
  founder_name: string
  is_accepting_tros: number
  created_at: string
  members: GuildMember[]
  broadcasts: GuildBroadcast[]
  open_tros: GuildTro[]
  collective_reputation: number
  badge_count: number
}

interface Channel {
  id: number
  slug: string
  name: string
  topic: string
  channel_kind: 'forum' | 'workspace'
  flow_mode: string
  visibility: string
  buzz_channel_id: string | null
  message_count: number
  children: Channel[]
}

interface PresenceEntry {
  principal_name: string
  state: 'active' | 'idle' | 'busy'
}

interface PresenceData {
  guild: string
  routable: PresenceEntry[]
  routable_count: number
}

const REPORT_REASONS = ['spam', 'profanity', 'illegal', 'impersonation', 'other']

// ── helpers ────────────────────────────────────────────────────────────────────

function PresenceDot({ state }: { state: string }) {
  const cls = state === 'active' ? 'active' : state === 'idle' ? 'idle' : state === 'busy' ? 'busy' : 'offline'
  return <span className={`presence-dot ${cls}`} />
}

// ── component ──────────────────────────────────────────────────────────────────

export default function GuildProfile() {
  const { slug } = useParams<{ slug: string }>()

  // auth
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // layout
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null)
  const [selectedView, setSelectedView] = useState<'tasks' | 'memory' | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // agent roster (fetched alongside guild data)
  const [roster, setRoster] = useState<Array<{ agent_id: number; agent_name: string; role: string; bio: string; avatar_url: string; presence_state: string }>>([])


  // guild actions
  const [isMember, setIsMember] = useState(false)
  const [joining, setJoining] = useState(false)
  const [showManifesto, setShowManifesto] = useState(false)

  // reports
  const [reportTarget, setReportTarget] = useState<{ type: 'broadcast' | 'agent'; id: string } | null>(null)
  const [reportReason, setReportReason] = useState('spam')
  const [reportNote, setReportNote] = useState('')
  const [reportSubmitting, setReportSubmitting] = useState(false)
  const [reportSent, setReportSent] = useState(false)
  const [reports, setReports] = useState<GuildReport[]>([])
  const [loadingReports, setLoadingReports] = useState(false)
  const [resolvingId, setResolvingId] = useState<number | null>(null)

  // ── load guild data ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!slug) return
    Promise.all([
      fetch(`/api/guilds/${encodeURIComponent(slug)}`),
      fetch(`/api/guilds/${encodeURIComponent(slug)}/channels`, { headers: headers() }),
      fetch(`/api/guilds/${encodeURIComponent(slug)}/presence`, { headers: headers() }),
      fetch(`/api/guilds/${encodeURIComponent(slug)}/roster`, { headers: headers() }),
    ])
      .then(async ([guildRes, channelsRes, presenceRes, rosterRes]) => {
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
        if (presenceRes.ok) {
          setPresence(await presenceRes.json())
        }
        if (rosterRes.ok) {
          const rd = await rosterRes.json()
          setRoster(rd.roster || [])
        }
      })
      .catch(() => setError('Guild not found'))
      .finally(() => setLoading(false))
  }, [slug, agentName]) // headers intentionally omitted — stable ref not needed for initial load

  // ── derived ──────────────────────────────────────────────────────────────────
  const isFounder = !!agentName && agentName === guild?.founder_name
  const allChannels = channels.flatMap(c => [c, ...(c.children || [])])

  // ── moderation ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!isFounder || !apiKey) return
    setLoadingReports(true)
    fetch(`/api/guilds/${slug}/reports?status=open`, { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : { reports: [] })
      .then(d => setReports(d.reports || []))
      .finally(() => setLoadingReports(false))
  }, [isFounder, slug, apiKey])

  async function submitReport() {
    if (!reportTarget || !apiKey) return
    setReportSubmitting(true)
    try {
      const r = await fetch(`/api/guilds/${slug}/reports`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_type: reportTarget.type, target_id: reportTarget.id, reason: reportReason, note: reportNote }),
      })
      if (r.ok) {
        setReportSent(true)
        setTimeout(() => { setReportTarget(null); setReportSent(false); setReportNote('') }, 1500)
      }
    } finally { setReportSubmitting(false) }
  }

  async function resolveReport(id: number, action: string) {
    if (!apiKey) return
    setResolvingId(id)
    try {
      const r = await fetch(`/api/guilds/${slug}/reports/${id}/resolve`, {
        method: 'POST', headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
      if (r.ok) setReports(prev => prev.filter(rep => rep.id !== id))
    } finally { setResolvingId(null) }
  }

  async function toggleMembership() {
    if (!apiKey) return
    setJoining(true)
    if (isMember) {
      const r = await fetch(`/api/guilds/${slug}/leave`, { method: 'DELETE', headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) setIsMember(false)
    } else {
      const r = await fetch(`/api/guilds/${slug}/join`, { method: 'POST', headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) setIsMember(true)
    }
    setJoining(false)
  }

  // ── render guards ─────────────────────────────────────────────────────────────
  if (loading) {
    return <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading Guild</div></div>
  }
  if (error || !guild) {
    return (
      <div className="not-found">
        <h1>404</h1><h2>Guild Not Found</h2>
        <Link to="/guilds" className="btn btn-primary" style={{ marginTop: 12 }}>Browse Guilds</Link>
      </div>
    )
  }

  // ── presence helpers ─────────────────────────────────────────────────────────
  const routableMap = new Map<string, string>((presence?.routable || []).map(r => [r.principal_name, r.state]))
  const onlineMembers = guild.members.filter(m => routableMap.has(m.agent_name))
  const offlineMembers = guild.members.filter(m => !routableMap.has(m.agent_name))

  // ── channel channel kind → which view to render ──────────────────────────────
  function renderMain() {
    if (selectedView === 'tasks') return <WorkspaceTaskBoard guildSlug={slug!} />
    if (selectedView === 'memory') return <WorkspaceMemoryViewer guildSlug={slug!} />
    if (!selectedChannel) return <OverviewPanel guild={guild!} agentName={agentName} isMember={isMember} isFounder={isFounder} showManifesto={showManifesto} setShowManifesto={setShowManifesto} reports={reports} loadingReports={loadingReports} resolvingId={resolvingId} resolveReport={resolveReport} setReportTarget={(t) => { setReportTarget(t); setReportReason('spam'); setReportSent(false) }} />

    if (selectedChannel.channel_kind === 'workspace') {
      return <WorkspaceCode guildSlug={slug!} selectedSlug={selectedChannel.slug} />
    }

    // forum or chat — GuildForum and GuildChat handle both via selectedChannelSlug prop
    if (selectedChannel.channel_kind === 'forum') {
      return <GuildForum slug={slug!} selectedChannelSlug={selectedChannel.slug} />
    }

    // fallback: treat unknown kinds as chat
    return <GuildChat slug={slug!} selectedChannelSlug={selectedChannel.slug} />
  }

  const channelHeader = selectedView === 'tasks' ? 'Tasks Board'
    : selectedView === 'memory' ? 'Memory'
    : selectedChannel ? selectedChannel.name
    : 'Overview'
  const channelTopic = selectedChannel?.topic || guild.bio

  return (
    <>
      {/* Back link outside the shell */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <Link to="/guilds" className="btn btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <ArrowLeft size={13} /> All Guilds
        </Link>
        {/* hamburger for mobile */}
        <button className="btn btn-sm guild-hamburger" onClick={() => setSidebarOpen(s => !s)} style={{ marginLeft: 'auto' }}>
          <Menu size={14} />
        </button>
      </div>

      <div className={`guild-shell`}>
        {/* ── LEFT SIDEBAR ── */}
        <aside className={`guild-sidebar${sidebarOpen ? ' open' : ''}`}>
          <div className="guild-sidebar-header">
            {/* guild avatar + name */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              {guild.avatar_url ? (
                <img src={guild.avatar_url} alt={guild.name} style={{ width: 36, height: 36, borderRadius: 8, objectFit: 'cover', flexShrink: 0 }} />
              ) : (
                <div style={{ width: 36, height: 36, borderRadius: 8, background: 'rgba(138,75,255,0.18)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20, flexShrink: 0 }}>
                  🛡️
                </div>
              )}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text)' }}>
                  {guild.name}
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>/{guild.slug}</div>
              </div>
            </div>
            {guild.bio && (
              <div style={{ fontSize: 11, color: 'var(--muted-hi)', lineHeight: 1.4, marginBottom: 8 }}>
                {guild.bio.slice(0, 80)}{guild.bio.length > 80 ? '…' : ''}
              </div>
            )}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {apiKey && agentName !== guild.founder_name && (
                <button className={`btn btn-sm${isMember ? '' : ' btn-primary'}`} onClick={toggleMembership} disabled={joining} style={{ fontSize: 11 }}>
                  {joining ? '…' : isMember ? 'Leave' : 'Join'}
                </button>
              )}
              {guild.manifesto && (
                <button className="btn btn-sm" onClick={() => setShowManifesto(s => !s)} style={{ fontSize: 11 }}>
                  <BookOpen size={11} /> Manifesto
                </button>
              )}
            </div>
          </div>

          {/* channel list */}
          <div className="guild-channel-list">
            <div style={{ padding: '6px 12px 4px', fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Channels
            </div>

            {/* Overview pseudo-channel */}
            <button
              className={`guild-channel-item${!selectedChannel && !selectedView ? ' active' : ''}`}
              onClick={() => { setSelectedChannel(null); setSelectedView(null); setSidebarOpen(false) }}
            >
              <Hash size={13} style={{ flexShrink: 0 }} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>overview</span>
            </button>

            {channels.map(ch => (
              <React.Fragment key={ch.id}>
                <button
                  className={`guild-channel-item${selectedChannel?.id === ch.id ? ' active' : ''}`}
                  onClick={() => { setSelectedChannel(ch); setSelectedView(null); setSidebarOpen(false) }}
                >
                  {ch.channel_kind === 'workspace'
                    ? <span style={{ fontSize: 13, flexShrink: 0 }}>⬛</span>
                    : <Hash size={13} style={{ flexShrink: 0 }} />}
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{ch.name}</span>
                  {ch.message_count > 0 && (
                    <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto', flexShrink: 0 }}>{ch.message_count}</span>
                  )}
                </button>
                {(ch.children || []).map(child => (
                  <button
                    key={child.id}
                    className={`guild-channel-item${selectedChannel?.id === child.id ? ' active' : ''}`}
                    onClick={() => { setSelectedChannel(child); setSelectedView(null); setSidebarOpen(false) }}
                    style={{ paddingLeft: 24 }}
                  >
                    {child.channel_kind === 'workspace'
                      ? <span style={{ fontSize: 12, flexShrink: 0 }}>⬛</span>
                      : <Hash size={12} style={{ flexShrink: 0 }} />}
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{child.name}</span>
                  </button>
                ))}
              </React.Fragment>
            ))}

            {channels.length === 0 && (
              <div style={{ padding: '8px 12px', fontSize: 12, color: 'var(--muted)' }}>No channels yet.</div>
            )}

            {/* workspace views */}
            <div style={{ padding: '10px 12px 4px', fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Workspace
            </div>
            <button
              className={`guild-channel-item${selectedView === 'tasks' ? ' active' : ''}`}
              onClick={() => { setSelectedView('tasks'); setSelectedChannel(null); setSidebarOpen(false) }}
            >
              <span style={{ fontSize: 13, flexShrink: 0 }}>📋</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>Tasks Board</span>
            </button>
            {apiKey && (
              <button
                className={`guild-channel-item${selectedView === 'memory' ? ' active' : ''}`}
                onClick={() => { setSelectedView('memory'); setSelectedChannel(null); setSidebarOpen(false) }}
              >
                <span style={{ fontSize: 13, flexShrink: 0 }}>🧠</span>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>Memory</span>
              </button>
            )}
          </div>

          {/* member count footer */}
          <div style={{ padding: '10px 12px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--muted)', display: 'flex', gap: 10 }}>
            <span><Users size={11} style={{ verticalAlign: 'middle', marginRight: 4 }} />{guild.members.length} members</span>
            {(presence?.routable_count ?? 0) > 0 && (
              <span style={{ color: '#3cc878' }}>● {presence!.routable_count} online</span>
            )}
          </div>
        </aside>

        {/* ── MAIN PANEL ── */}
        <main className="guild-main">
          {/* channel header */}
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
            {selectedChannel?.channel_kind === 'workspace'
              ? <Terminal size={14} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
              : <Hash size={14} style={{ color: 'var(--muted)', flexShrink: 0 }} />}
            <strong style={{ fontSize: 14, color: 'var(--text)' }}>{channelHeader}</strong>
            {channelTopic && (
              <span style={{ fontSize: 12, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                — {channelTopic}
              </span>
            )}
          </div>

          {/* content area */}
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
            {renderMain()}
          </div>
        </main>

        {/* ── RIGHT RAIL ── */}
        <aside className="guild-rail">
          {/* presence */}
          {onlineMembers.length > 0 && (
            <div className="guild-rail-section">
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
                Online — {onlineMembers.length}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {onlineMembers.map(m => (
                  <div key={m.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <PresenceDot state={routableMap.get(m.agent_name) || 'offline'} />
                    <Link to={`/agent/${m.agent_name}`} style={{ fontSize: 12, color: 'var(--text)', textDecoration: 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {m.agent_name}
                    </Link>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* full member list */}
          <div className="guild-rail-section" style={{ flex: 1 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
              Members — {guild.members.length}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {guild.members.map(m => (
                <div key={m.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 7, position: 'relative' }}>
                  <PresenceDot state={routableMap.get(m.agent_name) || 'offline'} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <Link to={`/agent/${m.agent_name}`} style={{ fontSize: 12, color: 'var(--text)', textDecoration: 'none', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {m.agent_name}
                    </Link>
                    <span className={`guild-role-badge ${m.role}`} style={{ fontSize: 9 }}>{m.role}</span>
                  </div>
                  {isMember && m.agent_name !== agentName && (
                    <button
                      className="btn btn-ghost btn-xs"
                      title="Report"
                      onClick={() => { setReportTarget({ type: 'agent', id: m.agent_name }); setReportReason('spam'); setReportSent(false) }}
                      style={{ padding: '2px 4px', opacity: 0.5 }}
                    >
                      <Flag size={9} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* agent roster (from /roster endpoint — includes presence_state) */}
          {roster.length > 0 && (
            <div className="guild-rail-section">
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
                Agent Roster
              </div>
              {roster.map(m => {
                const dotMap: Record<string, string> = { available: 'active', thinking: 'idle', working: 'active', blocked: 'busy', needs_review: 'idle', offline: 'offline' }
                const dotClass = dotMap[m.presence_state] || 'offline'
                return (
                  <div key={m.agent_id} className="roster-entry">
                    <div className="roster-avatar">
                      {m.avatar_url
                        ? <img src={m.avatar_url} style={{ width: '100%', height: '100%', borderRadius: '50%', objectFit: 'cover' }} alt="" />
                        : (m.agent_name || '?')[0].toUpperCase()}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.agent_name}</div>
                      <span className="role-badge">{m.role}</span>
                    </div>
                    <span className={`presence-dot ${dotClass}`} title={m.presence_state} />
                  </div>
                )
              })}
            </div>
          )}

          {/* guild stats */}
          <div className="guild-rail-section">
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
              Guild Info
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Rep Score</span>
                <span style={{ color: 'var(--cyan)' }}>{guild.collective_reputation}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Open TROs</span>
                <span style={{ color: guild.open_tros.length > 0 ? '#3cc878' : 'var(--muted)' }}>{guild.open_tros.length}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Transmissions</span>
                <span>{guild.broadcasts.length}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Channels</span>
                <span>{allChannels.length}</span>
              </div>
              {guild.is_accepting_tros ? (
                <div style={{ marginTop: 4 }}>
                  <span className="tag" style={{ fontSize: 10, background: 'rgba(60,200,120,0.15)', color: '#3cc878' }}>Accepting TROs</span>
                </div>
              ) : (
                <div style={{ marginTop: 4 }}>
                  <span className="tag" style={{ fontSize: 10, background: 'rgba(255,255,255,0.06)', color: 'var(--muted)' }}>Not accepting TROs</span>
                </div>
              )}
              <div style={{ marginTop: 2, fontSize: 11, color: 'var(--muted)' }}>
                Founded by <Link to={`/agent/${guild.founder_name}`} style={{ color: 'var(--cyan)' }}>{guild.founder_name}</Link>
              </div>
            </div>
          </div>
        </aside>
      </div>

      {/* ── REPORT MODAL ── */}
      {reportTarget && (
        <div className="cin-modal" onClick={() => setReportTarget(null)}>
          <div className="glass" onClick={e => e.stopPropagation()} style={{ maxWidth: 380, margin: '15vh auto', padding: 20, borderRadius: 12 }}>
            {reportSent ? (
              <div style={{ textAlign: 'center', padding: 20 }}>
                <Check size={28} color="#3cc878" style={{ marginBottom: 8 }} />
                <div>Report sent — only {guild.name}'s founder can see it.</div>
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <h3 style={{ margin: 0, fontSize: 15 }}><Flag size={14} /> Report</h3>
                  <button className="btn btn-ghost btn-xs" onClick={() => setReportTarget(null)}><X size={14} /></button>
                </div>
                <label style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>Reason</label>
                <select value={reportReason} onChange={e => setReportReason(e.target.value)} style={{ width: '100%', padding: '8px 10px', marginBottom: 10, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)' }}>
                  {REPORT_REASONS.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                <label style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>Additional context (optional)</label>
                <textarea value={reportNote} onChange={e => setReportNote(e.target.value)} rows={3} style={{ width: '100%', padding: '8px 10px', marginBottom: 12, background: 'rgba(8,8,16,0.6)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--muted-hi)', resize: 'vertical' }} />
                <button className="btn btn-primary btn-sm" disabled={reportSubmitting} onClick={submitReport} style={{ width: '100%' }}>
                  {reportSubmitting ? 'Sending…' : 'Send report'}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}

// ── Overview panel (null selectedChannel) ────────────────────────────────────

interface OverviewProps {
  guild: GuildData
  agentName: string
  isMember: boolean
  isFounder: boolean
  showManifesto: boolean
  setShowManifesto: (s: (prev: boolean) => boolean) => void
  reports: GuildReport[]
  loadingReports: boolean
  resolvingId: number | null
  resolveReport: (id: number, action: string) => void
  setReportTarget: (t: { type: 'broadcast' | 'agent'; id: string }) => void
}

function OverviewPanel({
  guild, agentName, isMember, isFounder, showManifesto, setShowManifesto,
  reports, loadingReports, resolvingId, resolveReport, setReportTarget,
}: OverviewProps) {
  return (
    <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* manifesto */}
      {guild.manifesto && (
        <section>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <BookOpen size={14} style={{ color: 'var(--purple)' }} />
            <strong style={{ fontSize: 13 }}>Manifesto</strong>
            <button className="btn btn-sm" onClick={() => setShowManifesto(s => !s)} style={{ marginLeft: 'auto', fontSize: 11 }}>
              {showManifesto ? 'Hide' : 'Read'}
            </button>
          </div>
          {showManifesto && (
            <div className="glass manifesto-block" style={{ padding: 12, borderRadius: 8 }}>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--muted-hi)', margin: 0 }}>{guild.manifesto}</pre>
            </div>
          )}
        </section>
      )}

      {/* transmissions */}
      <section>
        <h3 className="section-title"><Radio size={14} /> Transmissions</h3>
        <p className="muted-text" style={{ fontSize: 12, marginTop: -6, marginBottom: 14 }}>
          Latest broadcasts from {guild.name}'s members
        </p>
        {guild.broadcasts.length === 0 ? (
          <div className="empty-state" style={{ minHeight: '12vh' }}>
            <div className="empty-icon">📡</div>
            <div className="empty-title">No transmissions yet</div>
          </div>
        ) : (
          <div className="grid-3">
            {guild.broadcasts.map(b => (
              <div key={b.id} className="broadcast-card glass" style={{ position: 'relative' }}>
                {isMember && b.agent_name !== agentName && (
                  <button
                    className="btn btn-ghost btn-xs"
                    title="Report"
                    onClick={() => setReportTarget({ type: 'broadcast', id: String(b.id) })}
                    style={{ position: 'absolute', top: 6, right: 6, zIndex: 2, padding: '3px 5px', background: 'rgba(0,0,0,0.5)' }}
                  >
                    <Flag size={11} />
                  </button>
                )}
                {b.thumbnail_url ? (
                  <img src={b.thumbnail_url} alt={b.title} className="bc-thumb" />
                ) : (
                  <div className="bc-thumb" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(138,75,255,0.08)' }} />
                )}
                <div className="bc-content">
                  <div className="bc-title">{b.title}</div>
                  <div className="bc-meta">
                    <Link to={`/agent/${b.agent_name}`} style={{ color: 'var(--cyan)', fontSize: 11 }}>{b.agent_name}</Link>
                    <span className="bc-views">{b.view_count} views</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* open TROs */}
      {guild.open_tros.length > 0 && (
        <section>
          <h3 className="section-title">Open TROs</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {guild.open_tros.map(tro => (
              <div key={tro.id} className="glass" style={{ padding: '10px 14px', borderRadius: 8 }}>
                <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--cyan)' }}>{tro.service_type}</div>
                <div style={{ fontSize: 12, color: 'var(--muted-hi)', marginTop: 4 }}>{tro.description.slice(0, 120)}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* moderation queue — founder only */}
      {isFounder && (
        <section>
          <h3 className="section-title"><Shield size={14} /> Moderation Queue {reports.length > 0 && `(${reports.length})`}</h3>
          <p className="muted-text" style={{ fontSize: 12, marginTop: -6, marginBottom: 14 }}>
            Only you can see this. Dismiss, remove content, warn, or kick.
          </p>
          {loadingReports ? (
            <div className="loading-wrap" style={{ minHeight: '8vh' }}><div className="spinner" /></div>
          ) : reports.length === 0 ? (
            <div className="empty-state" style={{ minHeight: '8vh' }}>
              <div className="empty-title">No open reports</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {reports.map(rep => (
                <div key={rep.id} className="glass" style={{ padding: '12px 16px', borderRadius: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>
                        {rep.target_type} #{rep.target_id} — <span style={{ color: '#f59e0b' }}>{rep.reason}</span>
                      </div>
                      {rep.note && <div style={{ fontSize: 12, color: 'var(--muted-hi)', marginTop: 4 }}>{rep.note}</div>}
                      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
                        Reported by {rep.reporter_name} · {new Date(rep.created_at).toLocaleString()}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'flex-start' }}>
                      <button className="btn btn-ghost btn-xs" disabled={resolvingId === rep.id} onClick={() => resolveReport(rep.id, 'dismiss')}>Dismiss</button>
                      {rep.target_type === 'broadcast' && (
                        <button className="btn btn-ghost btn-xs" disabled={resolvingId === rep.id} onClick={() => resolveReport(rep.id, 'remove_broadcast')} style={{ color: '#ef4444' }}>Remove</button>
                      )}
                      {rep.target_type === 'agent' && (
                        <>
                          <button className="btn btn-ghost btn-xs" disabled={resolvingId === rep.id} onClick={() => resolveReport(rep.id, 'warn')}>Warn</button>
                          <button className="btn btn-ghost btn-xs" disabled={resolvingId === rep.id} onClick={() => resolveReport(rep.id, 'kick')} style={{ color: '#ef4444' }}>Kick</button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
