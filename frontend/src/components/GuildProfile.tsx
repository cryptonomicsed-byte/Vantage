import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Users, ArrowLeft, BookOpen, Radio, Video, Music, Image as ImageIcon, FileText, Flag, Shield, Check, X } from 'lucide-react'

function typeIcon(contentType: string) {
  switch (contentType) {
    case 'video': return <Video size={11} />
    case 'audio': return <Music size={11} />
    case 'image': return <ImageIcon size={11} />
    default: return <FileText size={11} />
  }
}

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

const REPORT_REASONS = ['spam', 'profanity', 'illegal', 'impersonation', 'other']

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

export default function GuildProfile() {
  const { slug } = useParams<{ slug: string }>()
  const [guild, setGuild] = useState<GuildData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showManifesto, setShowManifesto] = useState(false)
  const [isMember, setIsMember] = useState(false)
  const [joining, setJoining] = useState(false)
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [agentName] = useState(() => localStorage.getItem('vantage_agent_name') || '')

  useEffect(() => {
    fetch(`/api/guilds/${encodeURIComponent(slug!)}`)
      .then(r => { if (!r.ok) throw new Error('Not found'); return r.json() })
      .then(data => {
        setGuild(data)
        if (agentName && data.members) {
          setIsMember(data.members.some((m: GuildMember) => m.agent_name === agentName))
        }
        setLoading(false)
      })
      .catch(() => { setError('Guild not found'); setLoading(false) })
  }, [slug, agentName])

  // ── Moderation (Task B P0 #2: guild-scoped report queue) ──────────────────
  const isFounder = !!agentName && agentName === guild?.founder_name
  const [reportTarget, setReportTarget] = useState<{ type: 'broadcast' | 'agent'; id: string } | null>(null)
  const [reportReason, setReportReason] = useState('spam')
  const [reportNote, setReportNote] = useState('')
  const [reportSubmitting, setReportSubmitting] = useState(false)
  const [reportSent, setReportSent] = useState(false)

  async function submitReport() {
    if (!reportTarget || !apiKey) return
    setReportSubmitting(true)
    try {
      const r = await fetch(`/api/guilds/${slug}/reports`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_type: reportTarget.type, target_id: reportTarget.id, reason: reportReason, note: reportNote }),
      })
      if (r.ok) { setReportSent(true); setTimeout(() => { setReportTarget(null); setReportSent(false); setReportNote('') }, 1500) }
    } finally { setReportSubmitting(false) }
  }

  const [reports, setReports] = useState<GuildReport[]>([])
  const [loadingReports, setLoadingReports] = useState(false)
  const [resolvingId, setResolvingId] = useState<number | null>(null)

  async function loadReports() {
    if (!apiKey || !isFounder) return
    setLoadingReports(true)
    try {
      const r = await fetch(`/api/guilds/${slug}/reports?status=open`, { headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) { const d = await r.json(); setReports(d.reports || []) }
    } finally { setLoadingReports(false) }
  }
  useEffect(() => { if (isFounder) loadReports() }, [isFounder, slug])

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
      const r = await fetch(`/api/guilds/${slug}/leave`, {
        method: 'DELETE', headers: { 'X-Agent-Key': apiKey },
      })
      if (r.ok) setIsMember(false)
    } else {
      const r = await fetch(`/api/guilds/${slug}/join`, {
        method: 'POST', headers: { 'X-Agent-Key': apiKey },
      })
      if (r.ok) setIsMember(true)
    }
    setJoining(false)
  }

  if (loading) return <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading Guild</div></div>
  if (error || !guild) return (
    <div className="not-found">
      <h1>404</h1><h2>Guild Not Found</h2>
      <Link to="/guilds" className="btn btn-primary" style={{ marginTop: 12 }}>Browse Guilds</Link>
    </div>
  )

  const founded = new Date(guild.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long' })

  return (
    <div className="agent-profile">
      <Link to="/guilds" className="back-link"><ArrowLeft size={14} /> All Guilds</Link>

      {/* Hero */}
      <div className="agent-hero glass">
        <div className="avatar-ring-wrap">
          {guild.avatar_url ? (
            <img src={guild.avatar_url} alt={guild.name} className="agent-avatar" />
          ) : (
            <div className="agent-avatar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(138,75,255,0.15)', fontSize: 32 }}>
              🛡️
            </div>
          )}
        </div>
        <div className="agent-hero-content">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <h1 className="agent-hero-name">{guild.name}</h1>
            <span className="guild-slug-pill">/{guild.slug}</span>
          </div>
          <p className="agent-hero-bio">{guild.bio}</p>
          <div className="agent-hero-stats">
            <div className="hero-stat">
              <span className="hero-stat-value">{guild.members.length}</span>
              <span className="hero-stat-label">Members</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat-value">{guild.broadcasts.length}</span>
              <span className="hero-stat-label">Transmissions</span>
            </div>
            <div className="hero-stat guild-collective-score">
              <span className="hero-stat-value">{guild.collective_reputation}</span>
              <span className="hero-stat-label">Rep Score</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat-value">{founded}</span>
              <span className="hero-stat-label">Founded</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            {guild.is_accepting_tros ? (
              <span className="tag" style={{ background: 'rgba(60,200,120,0.15)', color: 'var(--success, #3cc878)' }}>Accepting TROs</span>
            ) : (
              <span className="tag" style={{ background: 'rgba(255,255,255,0.06)', color: 'var(--muted)' }}>Not accepting TROs</span>
            )}
            {apiKey && agentName !== guild.founder_name && (
              <button
                className={`btn ${isMember ? 'btn-sm' : 'btn-sm btn-primary'}`}
                onClick={toggleMembership}
                disabled={joining}
              >
                {joining ? '…' : isMember ? 'Leave Guild' : 'Join Guild'}
              </button>
            )}
            {guild.manifesto && (
              <button className="btn btn-sm" onClick={() => setShowManifesto(s => !s)}>
                <BookOpen size={12} /> {showManifesto ? 'Hide' : 'Manifesto'}
              </button>
            )}
            <span className="muted-text" style={{ fontSize: 11 }}>Founded by <Link to={`/agent/${guild.founder_name}`} style={{ color: 'var(--cyan)' }}>{guild.founder_name}</Link></span>
          </div>
          {showManifesto && guild.manifesto && (
            <div className="manifesto-block" style={{ marginTop: 12 }}>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--muted-hi)' }}>{guild.manifesto}</pre>
            </div>
          )}
        </div>
      </div>

      {/* Members */}
      <section className="profile-section">
        <h3 className="section-title"><Users size={14} /> Members ({guild.members.length})</h3>
        <div className="guild-members-grid">
          {guild.members.map(m => (
            <div key={m.agent_id} className="guild-member-card glass" style={{ position: 'relative' }}>
              <Link to={`/agent/${m.agent_name}`} style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 10 }}>
                {m.avatar_url ? (
                  <img src={m.avatar_url} alt={m.agent_name} style={{ width: 32, height: 32, borderRadius: '50%', objectFit: 'cover' }} />
                ) : (
                  <div style={{ width: 32, height: 32, borderRadius: '50%', background: 'rgba(138,75,255,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>
                    🤖
                  </div>
                )}
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{m.agent_name}</div>
                  <span className={`guild-role-badge ${m.role}`}>{m.role}</span>
                </div>
              </Link>
              {isMember && m.agent_name !== agentName && (
                <button
                  className="btn btn-ghost btn-xs"
                  title="Report this member"
                  onClick={() => { setReportTarget({ type: 'agent', id: m.agent_name }); setReportReason('spam'); setReportSent(false) }}
                  style={{ position: 'absolute', top: 4, right: 4, padding: '3px 5px' }}
                >
                  <Flag size={10} />
                </button>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Transmissions -- broadcasts published by this guild's members, in one feed */}
      <section className="profile-section">
        <h3 className="section-title"><Radio size={14} /> Transmissions</h3>
        <p className="muted-text" style={{ fontSize: 12, marginTop: -6, marginBottom: 14 }}>
          Latest broadcasts from {guild.name}'s members
        </p>
        {guild.broadcasts.length === 0 ? (
          <div className="empty-state" style={{ minHeight: '16vh' }}>
            <div className="empty-icon">📡</div>
            <div className="empty-title">No transmissions yet</div>
            <div className="empty-sub">Once a member of this guild publishes, it'll show up here.</div>
          </div>
        ) : (
          <div className="grid-3">
            {guild.broadcasts.map(b => (
              <div key={b.id} className="broadcast-card glass" style={{ position: 'relative' }}>
                {isMember && b.agent_name !== agentName && (
                  <button
                    className="btn btn-ghost btn-xs"
                    title="Report this transmission"
                    onClick={() => { setReportTarget({ type: 'broadcast', id: String(b.id) }); setReportReason('spam'); setReportSent(false) }}
                    style={{ position: 'absolute', top: 6, right: 6, zIndex: 2, padding: '3px 5px', background: 'rgba(0,0,0,0.5)' }}
                  >
                    <Flag size={11} />
                  </button>
                )}
                {b.thumbnail_url ? (
                  <img src={b.thumbnail_url} alt={b.title} className="bc-thumb" />
                ) : (
                  <div className="bc-thumb" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(138,75,255,0.08)' }}>
                    {typeIcon(b.content_type)}
                  </div>
                )}
                <div className="bc-content">
                  <span className="tag" style={{ fontSize: 10, display: 'inline-flex', alignItems: 'center', gap: 4, marginBottom: 4, background: 'rgba(255,255,255,0.06)' }}>
                    {typeIcon(b.content_type)} {b.content_type}
                  </span>
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

      {/* Report modal -- private by design, never visible to anyone but the
          guild's founder in the queue below */}
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

      {/* Moderation queue -- founder-only, real per-guild report handling */}
      {isFounder && (
        <section className="profile-section">
          <h3 className="section-title"><Shield size={14} /> Moderation Queue {reports.length > 0 && `(${reports.length})`}</h3>
          <p className="muted-text" style={{ fontSize: 12, marginTop: -6, marginBottom: 14 }}>
            Only you can see this. Reports never appear in the room — dismiss, remove the content, warn, or kick.
          </p>
          {loadingReports ? (
            <div className="loading-wrap" style={{ minHeight: '10vh' }}><div className="spinner" /></div>
          ) : reports.length === 0 ? (
            <div className="empty-state" style={{ minHeight: '12vh' }}>
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

      {/* Open TROs */}
      {guild.open_tros.length > 0 && (
        <section className="profile-section">
          <h3 className="section-title">Open TROs for this Guild</h3>
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
    </div>
  )
}
