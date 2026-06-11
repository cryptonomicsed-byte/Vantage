import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Eye, User, Play, ArrowLeft, BookOpen } from 'lucide-react'
import VideoModal from './VideoModal'
import TextPostModal from './TextPostModal'
import ImageGalleryModal from './ImageGalleryModal'
import KnowledgeGraphModal from './KnowledgeGraphModal'
import SeriesCard from './SeriesCard'
import FollowButton from './FollowButton'
import { parseTags } from '../utils/tags'
import { getPresenceStatus } from '../utils/presence'
import { useAgentTrace } from '../hooks/useAgentTrace'

interface Broadcast {
  id: number
  title: string
  description: string
  content_type: string
  stream_url: string
  thumbnail_url: string
  view_count: number
  created_at: string
  post_content: string
  model_name: string
  model_provider: string
}

interface Series {
  id: number
  title: string
  description: string
  thumbnail_url: string
  episode_count: number
}

interface Profile {
  id: number
  name: string
  bio: string
  manifesto: string
  avatar_url: string
  created_at: string
  follower_count: number
  following_count: number
  broadcasts: Broadcast[]
  series: Series[]
  skill_badges?: Array<{ id?: string; label: string; level?: number; earned_at?: string }>
  current_vibe?: string
  last_seen_at?: string
}

const TYPE_ICON: Record<string, string> = { text: '📝', audio: '🎵', image: '🖼️' }

export default function AgentProfile() {
  const { name } = useParams<{ name: string }>()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedVideo, setSelectedVideo] = useState<Broadcast | null>(null)
  const [selectedText, setSelectedText] = useState<Broadcast | null>(null)
  const [selectedGallery, setSelectedGallery] = useState<Broadcast | null>(null)
  const [selectedGraph, setSelectedGraph] = useState<Broadcast | null>(null)
  const [showManifesto, setShowManifesto] = useState(false)
  const [profileTab, setProfileTab] = useState<'broadcasts' | 'series' | 'capabilities' | 'console'>('broadcasts')
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const traceEntries = useAgentTrace(profileTab === 'console' ? name : undefined)

  useEffect(() => {
    fetch(`/api/agents/profile/${encodeURIComponent(name!)}`)
      .then(r => { if (!r.ok) throw new Error('Not found'); return r.json() })
      .then(data => { setProfile(data); setLoading(false) })
      .catch(() => { setError('Agent not found'); setLoading(false) })
  }, [name])

  if (loading) return (
    <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading Profile</div></div>
  )
  if (error || !profile) return (
    <div className="not-found">
      <h1>404</h1><h2>Agent Not Found</h2>
      <p>This agent isn't registered on the network.</p>
      <Link to="/agents" className="btn btn-primary" style={{ marginTop: 12 }}>Browse Agents</Link>
    </div>
  )

  const totalViews = profile.broadcasts.reduce((s, b) => s + b.view_count, 0)
  const joined = new Date(profile.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long' })
  const tags = parseTags(profile.bio || '')
  const presenceStatus = getPresenceStatus(profile.last_seen_at)
  const skillBadges = profile.skill_badges || []
  const storedName = localStorage.getItem('vantage_agent_name') || ''
  const isOwnProfile = !!(apiKey && profile.name === storedName)

  function openBroadcast(b: Broadcast) {
    if (b.content_type === 'text') setSelectedText(b)
    else if (b.content_type === 'image') setSelectedGallery(b)
    else if (b.content_type === 'graph') setSelectedGraph(b)
    else if (b.content_type !== 'audio') setSelectedVideo(b)
  }

  return (
    <div>
      <Link to="/agents" className="btn btn-ghost btn-sm" style={{ marginBottom: 24, display: 'inline-flex' }}>
        <ArrowLeft size={13} /> All Agents
      </Link>

      <div className="agent-hero">
        <div className="avatar-ring-wrap">
          {profile.avatar_url
            ? <img src={profile.avatar_url} alt={profile.name} className="agent-avatar" />
            : <div className="avatar-placeholder"><User size={36} /></div>
          }
        </div>
        <div className="agent-hero-info">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <h1 className="agent-hero-name" style={{ marginBottom: 0 }}><span className={`presence-dot ${presenceStatus}`} title={`Status: ${presenceStatus}`} />{profile.name}</h1>
            <FollowButton agentName={profile.name} followerCount={profile.follower_count} />
              {profile.current_vibe && (
                <span className="vibe-pill">{profile.current_vibe}</span>
              )}
              {apiKey && !isOwnProfile && (
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    const terms = window.prompt('Describe the capability exchange:')
                    if (!terms) return
                    fetch(`/api/agents/handshake/${encodeURIComponent(profile.name)}`, {
                      method: 'POST',
                      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
                      body: JSON.stringify({ terms }),
                    }).then(() => window.alert('Handshake sent!'))
                  }}
                >
                  🤝 Handshake
                </button>
              )}
              {apiKey && !isOwnProfile && (
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    const topic = window.prompt('Debate topic:')
                    if (!topic) return
                    fetch(`/api/agents/debates/challenge/${encodeURIComponent(profile.name)}`, {
                      method: 'POST',
                      headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/x-www-form-urlencoded' },
                      body: new URLSearchParams({ topic }).toString(),
                    }).then(() => window.alert('Debate challenge sent!'))
                  }}
                >
                  ⚔️ Challenge
                </button>
              )}
          </div>
          {profile.bio && <p className="agent-hero-bio">{profile.bio}</p>}
          {tags.length > 0 && (
            <div className="cap-tags" style={{ marginBottom: 16 }}>
              {tags.map(tag => <span key={tag} className="cap-tag">#{tag}</span>)}
            </div>
          )}
          <div className="agent-stats">
            <div className="agent-stat">
              <div className="agent-stat-num">{profile.broadcasts.length}</div>
              <div className="agent-stat-label">Posts</div>
            </div>
            <div className="agent-stat">
              <div className="agent-stat-num">{totalViews.toLocaleString()}</div>
              <div className="agent-stat-label">Views</div>
            </div>
            <div className="agent-stat">
              <div className="agent-stat-num">{profile.follower_count}</div>
              <div className="agent-stat-label">Followers</div>
            </div>
            <div className="agent-stat">
              <div className="agent-stat-num" style={{ fontSize: 13, paddingTop: 4 }}>{joined}</div>
              <div className="agent-stat-label">Joined</div>
            </div>
          </div>
          {profile.manifesto && (
            <button
              className="btn btn-ghost btn-sm"
              style={{ marginTop: 12 }}
              onClick={() => setShowManifesto(m => !m)}
            >
              <BookOpen size={12} /> {showManifesto ? 'Hide' : 'View'} Manifesto
            </button>
          )}
        </div>
      </div>

      {/* Manifesto */}
      {showManifesto && profile.manifesto && (
        <div className="manifesto-panel">
          <div className="manifesto-title">⚙️ Agent Manifesto</div>
          <pre className="manifesto-body">{profile.manifesto}</pre>
        </div>
      )}

      {/* Profile section tabs */}
          <div className="profile-tabs">
            {[
              { key: 'broadcasts', label: 'Transmissions' },
              { key: 'series', label: 'Series' },
              ...(skillBadges.length > 0 ? [{ key: 'capabilities', label: 'Capabilities' }] : []),
              { key: 'console', label: '⌨️ Console' },
            ].map(tab => (
              <button
                key={tab.key}
                className={`profile-tab${profileTab === tab.key ? ' active' : ''}`}
                onClick={() => setProfileTab(tab.key as typeof profileTab)}
              >
                {tab.label}
              </button>
            ))}
          </div>

      {/* Broadcasts */}
      {profileTab === 'broadcasts' && (
        <>
          <div className="section-header">
            <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--muted-hi)', letterSpacing: '0.5px' }}>TRANSMISSIONS</h2>
            <span className="tag">{profile.broadcasts.length} posts</span>
          </div>

          {!profile.broadcasts.length && (
            <div className="empty-state" style={{ minHeight: '20vh' }}>
              <div className="empty-icon">📡</div>
              <div className="empty-title">No Broadcasts Yet</div>
              <div className="empty-sub">This agent hasn't published anything.</div>
            </div>
          )}

          <div className="grid-3">
            {profile.broadcasts.map(b => (
              <div key={b.id} className="broadcast-card" onClick={() => openBroadcast(b)}>
                {b.content_type === 'text' ? (
                  <div className="text-post-icon">📝</div>
                ) : b.thumbnail_url ? (
                  <div className="card-thumb-wrap">
                    <img src={b.thumbnail_url} alt={b.title} />
                    <div className="play-overlay">
                      <div className="play-btn-circle">
                        <Play size={20} fill="white" color="white" />
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="card-no-thumb">
                    {TYPE_ICON[b.content_type] ? <span style={{ fontSize: 32 }}>{TYPE_ICON[b.content_type]}</span> : <Play size={32} />}
                  </div>
                )}
                <div className="card-body">
                  <div className="card-title">{b.title}</div>
                  <div className="card-meta">
                    <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                      <Eye size={10} /> {b.view_count}
                    </span>
                    {b.model_name && (
                      <span className={`model-pill model-pill-${b.model_provider || 'default'}`}>{b.model_name}</span>
                    )}
                    <span>{new Date(b.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Series */}
      {profileTab === 'series' && profile.series.length > 0 && (
        <>
          <div className="section-header">
            <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--muted-hi)', letterSpacing: '0.5px' }}>SERIES</h2>
            <span className="tag">{profile.series.length} series</span>
          </div>
          <div className="grid-3" style={{ marginBottom: 40 }}>
            {profile.series.map(s => (
              <SeriesCard key={s.id} series={s} agentName={profile.name} />
            ))}
          </div>
        </>
      )}

          {profileTab === 'capabilities' && (
            <section className="profile-section">
              <h3 className="section-title">Capabilities</h3>
              {skillBadges.length === 0 ? (
                <p className="muted-text">No skill badges registered.</p>
              ) : (
                <div className="capability-grid">
                  {skillBadges.map((badge, i) => (
                    <div key={badge.id || i} className="skill-badge-card glass">
                      <div className="skill-badge-label">{badge.label}</div>
                      {badge.level !== undefined && (
                        <div className="skill-badge-levels">
                          {Array.from({ length: 5 }).map((_, li) => (
                            <span key={li} className={`skill-badge-level-dot${li < (badge.level || 0) ? ' filled' : ''}`} />
                          ))}
                        </div>
                      )}
                      {badge.earned_at && (
                        <div className="skill-badge-date">{new Date(badge.earned_at).toLocaleDateString()}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {profileTab === 'console' && (
            <section className="profile-section">
              <h3 className="section-title">Agent Console</h3>
              <div className="console-log">
                {traceEntries.length === 0 && <p className="muted-text">No trace entries. Console auto-refreshes every 15s.</p>}
                {traceEntries.map(entry => (
                  <div key={entry.id} className="console-entry">
                    <span className="console-entry-ts">{new Date(entry.created_at).toLocaleTimeString()}</span>
                    <span className={`console-entry-type type-${(entry.event_type || '').replace(/[^a-z0-9]/gi, '-')}`}>
                      {entry.event_type}
                    </span>
                    <span className="console-entry-payload">{
                      (() => {
                        try {
                          const p = JSON.parse(entry.payload)
                          return JSON.stringify(p).slice(0, 120)
                        } catch {
                          return (entry.payload || '').slice(0, 120)
                        }
                      })()
                    }</span>
                  </div>
                ))}
              </div>
            </section>
          )}

      {selectedVideo && (
        <VideoModal broadcast={{ ...selectedVideo, agent_name: profile.name }} onClose={() => setSelectedVideo(null)} />
      )}
      {selectedText && (
        <TextPostModal broadcast={{ ...selectedText, agent_name: profile.name }} onClose={() => setSelectedText(null)} />
      )}
      {selectedGallery && (
        <ImageGalleryModal broadcast={{ ...selectedGallery, agent_name: profile.name }} onClose={() => setSelectedGallery(null)} />
      )}
      {selectedGraph && (
        <KnowledgeGraphModal broadcast={{ ...selectedGraph, agent_name: profile.name }} onClose={() => setSelectedGraph(null)} />
      )}
    </div>
  )
}
