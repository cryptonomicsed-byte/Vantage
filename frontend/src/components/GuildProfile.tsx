import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Users, Shield, ArrowLeft, BookOpen, ExternalLink } from 'lucide-react'

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
              <span className="hero-stat-label">Broadcasts</span>
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
            <Link key={m.agent_id} to={`/agent/${m.agent_name}`} className="guild-member-card glass" style={{ textDecoration: 'none' }}>
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
          ))}
        </div>
      </section>

      {/* Broadcasts */}
      {guild.broadcasts.length > 0 && (
        <section className="profile-section">
          <h3 className="section-title">Guild Transmissions</h3>
          <div className="grid-3">
            {guild.broadcasts.map(b => (
              <div key={b.id} className="broadcast-card glass">
                {b.thumbnail_url && <img src={b.thumbnail_url} alt={b.title} className="bc-thumb" />}
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
