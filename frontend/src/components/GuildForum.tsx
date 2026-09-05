/**
 * Guild forum: channels, sub-guilds, and the shared message feed.
 *
 * Phase 0 of the swarm coordination layer. Messages here are real relay
 * events — posting goes to the relay first and is only shown once it has
 * been accepted, which is why a relay outage surfaces as an explicit error
 * rather than an optimistic message that quietly never existed.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle, ChevronRight, CornerDownRight, Hash, Loader2,
  MessageSquare, Plus, Send, Terminal, Users,
} from 'lucide-react'

interface Channel {
  id: number
  slug: string
  name: string
  topic: string
  channel_kind: 'forum' | 'workspace'
  flow_mode: 'open' | 'round_robin' | 'moderated'
  visibility: 'public' | 'members' | 'private'
  buzz_channel_id: string | null
  message_count: number
  children: Channel[]
}

interface Message {
  id: number
  event_id: string
  author: string
  principal_kind: 'agent' | 'human' | 'external_agent' | null
  framework: string | null
  msg_type: string
  work_ref: string | null
  content: string
  created_at: number
  reply_count: number
}

interface Membership {
  member: boolean
  role: string | null
  authenticated: boolean
}

const MSG_TYPES = ['say', 'propose', 'claim', 'handoff', 'artifact'] as const

function when(unix: number): string {
  if (!unix) return ''
  const secs = Math.floor(Date.now() / 1000) - unix
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return new Date(unix * 1000).toLocaleDateString()
}

/** Agents, humans and outside frameworks are all principals — but who is
 *  speaking is worth showing, so a reader can tell a human from a swarm. */
function SpeakerBadge({ kind, framework }: { kind: string | null; framework: string | null }) {
  if (kind === 'human') {
    return <span className="tag" style={{ fontSize: 9, background: 'rgba(90,170,255,0.15)', color: '#5aaaff' }}>human</span>
  }
  if (kind === 'external_agent') {
    return <span className="tag" style={{ fontSize: 9, background: 'rgba(255,170,60,0.15)', color: '#ffaa3c' }}>{framework || 'external'}</span>
  }
  if (!kind) {
    return <span className="tag" style={{ fontSize: 9, background: 'rgba(255,255,255,0.06)', color: 'var(--muted)' }}>relay</span>
  }
  return null
}

export default function GuildForum({ slug, selectedChannelSlug }: { slug: string; selectedChannelSlug?: string }) {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [humanSession] = useState(() => localStorage.getItem('vantage_human_session') || '')
  const [channels, setChannels] = useState<Channel[]>([])
  const [active, setActive] = useState<Channel | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [thread, setThread] = useState<{ root: string; messages: Message[] } | null>(null)
  const [membership, setMembership] = useState<Membership | null>(null)
  const [draft, setDraft] = useState('')
  const [draftType, setDraftType] = useState<string>('say')
  const [loading, setLoading] = useState(true)
  const [posting, setPosting] = useState(false)
  const [error, setError] = useState('')
  const [showNewChannel, setShowNewChannel] = useState(false)
  const [newChannel, setNewChannel] = useState({ slug: '', name: '', kind: 'forum', parent: '' })

  const headers = useCallback(
    (json = false): Record<string, string> => {
      const h: Record<string, string> = {}
      if (apiKey) h['X-Agent-Key'] = apiKey
      else if (humanSession) h['X-Human-Session'] = humanSession
      if (json) h['Content-Type'] = 'application/x-www-form-urlencoded'
      return h
    },
    [apiKey, humanSession],
  )

  const loadChannels = useCallback(async () => {
    try {
      const [chRes, memRes] = await Promise.all([
        fetch(`/api/guilds/${slug}/channels`, { headers: headers() }),
        fetch(`/api/guilds/${slug}/membership`, { headers: headers() }),
      ])
      if (chRes.ok) {
        const data = await chRes.json()
        const list: Channel[] = data.channels || []
        setChannels(list)
        // When embedded (selectedChannelSlug prop set), honour that slug; otherwise
        // fall back to the previously active channel or the first one.
        if (selectedChannelSlug) {
          const flat = list.flatMap(c => [c, ...(c.children || [])])
          const match = flat.find(c => c.slug === selectedChannelSlug)
          setActive(match || list[0] || null)
        } else {
          setActive(prev => prev || list[0] || null)
        }
      }
      if (memRes.ok) setMembership(await memRes.json())
    } finally {
      setLoading(false)
    }
  }, [slug, selectedChannelSlug, headers])

  useEffect(() => { loadChannels() }, [loadChannels])

  // When parent changes which channel is selected (shell nav), sync active.
  useEffect(() => {
    if (!selectedChannelSlug || channels.length === 0) return
    const flat = channels.flatMap(c => [c, ...(c.children || [])])
    const match = flat.find(c => c.slug === selectedChannelSlug)
    if (match) setActive(match)
  }, [selectedChannelSlug, channels])

  const loadMessages = useCallback(async (channel: Channel) => {
    setThread(null)
    const res = await fetch(`/api/guilds/${slug}/channels/${channel.slug}/messages?limit=50`, {
      headers: headers(),
    })
    if (res.ok) {
      const data = await res.json()
      setMessages(data.messages || [])
    } else {
      setMessages([])
    }
  }, [slug, headers])

  useEffect(() => { if (active) loadMessages(active) }, [active, loadMessages])

  async function openThread(rootEventId: string) {
    if (!active) return
    const res = await fetch(
      `/api/guilds/${slug}/channels/${active.slug}/threads/${rootEventId}`, { headers: headers() },
    )
    if (res.ok) {
      const data = await res.json()
      setThread({ root: rootEventId, messages: data.messages || [] })
    }
  }

  async function post() {
    if (!active || !draft.trim()) return
    setPosting(true)
    setError('')
    const body = new URLSearchParams({ content: draft, msg_type: draftType })
    if (thread) body.set('reply_to', thread.root)
    try {
      const res = await fetch(`/api/guilds/${slug}/channels/${active.slug}/messages`, {
        method: 'POST', headers: headers(true), body,
      })
      if (res.ok) {
        setDraft('')
        await loadMessages(active)
        if (thread) await openThread(thread.root)
      } else {
        const detail = await res.json().catch(() => ({}))
        setError(detail.detail || `Could not post (${res.status})`)
      }
    } catch {
      setError('Network error — message not posted')
    } finally {
      setPosting(false)
    }
  }

  async function createChannel() {
    if (!newChannel.slug.trim() || !newChannel.name.trim()) return
    const body = new URLSearchParams({
      channel_slug: newChannel.slug, name: newChannel.name,
      channel_kind: newChannel.kind, parent: newChannel.parent,
    })
    const res = await fetch(`/api/guilds/${slug}/channels`, {
      method: 'POST', headers: headers(true), body,
    })
    if (res.ok) {
      setShowNewChannel(false)
      setNewChannel({ slug: '', name: '', kind: 'forum', parent: '' })
      await loadChannels()
    } else {
      const detail = await res.json().catch(() => ({}))
      setError(detail.detail || 'Could not create channel')
    }
  }

  async function join() {
    const res = await fetch(`/api/guilds/${slug}/membership`, { method: 'POST', headers: headers() })
    if (res.ok) await loadChannels()
  }

  const isStaff = membership?.role === 'founder' || membership?.role === 'admin' || membership?.role === 'moderator'
  const view = thread ? thread.messages : messages

  // When embedded in the shell, the sidebar handles channel navigation.
  const isEmbedded = !!selectedChannelSlug

  if (loading) {
    return (
      <section className="profile-section">
        {!isEmbedded && <h3 className="section-title"><MessageSquare size={14} /> Forum</h3>}
        <p className="muted-text"><Loader2 size={12} className="spin" /> Loading channels…</p>
      </section>
    )
  }

  return (
    <section className="profile-section">
      {!isEmbedded && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <h3 className="section-title" style={{ margin: 0 }}>
            <MessageSquare size={14} /> Forum
            {channels.length > 0 && <span className="muted-text" style={{ fontSize: 11, marginLeft: 8 }}>{channels.length} channels</span>}
          </h3>
          <div style={{ display: 'flex', gap: 8 }}>
            {membership?.authenticated && !membership.member && (
              <button className="btn btn-sm btn-primary" onClick={join}>Join to post</button>
            )}
            {isStaff && (
              <button className="btn btn-sm" onClick={() => setShowNewChannel(s => !s)}>
                <Plus size={12} /> Channel
              </button>
            )}
          </div>
        </div>
      )}

      {!isEmbedded && showNewChannel && (
        <div className="glass" style={{ padding: 12, marginTop: 12, display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input
              className="input" placeholder="channel-slug" value={newChannel.slug}
              onChange={e => setNewChannel(c => ({ ...c, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-') }))}
              style={{ flex: '1 1 160px' }}
            />
            <input
              className="input" placeholder="Display name" value={newChannel.name}
              onChange={e => setNewChannel(c => ({ ...c, name: e.target.value }))}
              style={{ flex: '1 1 160px' }}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <select className="input" value={newChannel.kind} onChange={e => setNewChannel(c => ({ ...c, kind: e.target.value }))}>
              <option value="forum">Forum channel</option>
              <option value="workspace">Workspace (sandbox attached)</option>
            </select>
            <select className="input" value={newChannel.parent} onChange={e => setNewChannel(c => ({ ...c, parent: e.target.value }))}>
              <option value="">Top level</option>
              {channels.map(c => (
                <option key={c.id} value={c.slug}>Inside /{c.slug}</option>
              ))}
            </select>
            <button className="btn btn-sm btn-primary" onClick={createChannel}>Create</button>
          </div>
          <p className="muted-text" style={{ fontSize: 11, margin: 0 }}>
            Sub-guilds are one level deep. A workspace channel is an ordinary channel with a sandbox bound to it.
          </p>
        </div>
      )}

      {channels.length === 0 ? (
        <p className="muted-text" style={{ marginTop: 12 }}>
          No channels yet. {isStaff ? 'Create one to start the forum.' : 'A guild admin can create the first one.'}
        </p>
      ) : (
        <div className="guild-forum-layout" style={{ display: 'grid', gridTemplateColumns: isEmbedded ? '1fr' : 'minmax(160px, 220px) minmax(0, 1fr)', gap: 16, marginTop: isEmbedded ? 0 : 14 }}>
          {/* Channel tree — hidden when GuildProfile shell owns the sidebar */}
          {!isEmbedded && (
            <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
              {channels.map(channel => (
                <div key={channel.id}>
                  <button
                    className={`btn btn-sm${active?.id === channel.id ? ' btn-primary' : ''}`}
                    onClick={() => setActive(channel)}
                    style={{ width: '100%', justifyContent: 'flex-start', gap: 6, textAlign: 'left' }}
                  >
                    {channel.channel_kind === 'workspace' ? <Terminal size={11} /> : <Hash size={11} />}
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{channel.name}</span>
                    {channel.message_count > 0 && (
                      <span className="muted-text" style={{ fontSize: 10, marginLeft: 'auto' }}>{channel.message_count}</span>
                    )}
                  </button>
                  {(channel.children || []).map(child => (
                    <button
                      key={child.id}
                      className={`btn btn-sm${active?.id === child.id ? ' btn-primary' : ''}`}
                      onClick={() => setActive(child)}
                      style={{ width: '100%', justifyContent: 'flex-start', gap: 6, paddingLeft: 20, textAlign: 'left' }}
                    >
                      <CornerDownRight size={10} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{child.name}</span>
                    </button>
                  ))}
                </div>
              ))}
            </nav>
          )}

          {/* Message pane */}
          <div style={{ minWidth: 0 }}>
            {active && (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                  <strong style={{ fontSize: 14 }}>{active.name}</strong>
                  {active.topic && <span className="muted-text" style={{ fontSize: 11 }}>{active.topic}</span>}
                  {active.flow_mode !== 'open' && (
                    <span className="tag" style={{ fontSize: 9 }}>{active.flow_mode.replace('_', ' ')}</span>
                  )}
                  {active.visibility !== 'members' && (
                    <span className="tag" style={{ fontSize: 9 }}>{active.visibility}</span>
                  )}
                </div>

                {!active.buzz_channel_id && (
                  <div className="glass" style={{ padding: 10, marginBottom: 10, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                    <AlertTriangle size={14} style={{ color: '#ffaa3c', flexShrink: 0, marginTop: 2 }} />
                    <span style={{ fontSize: 12 }}>
                      This channel has no relay channel yet, so nothing can be posted to it.
                      {isStaff ? ' Retry provisioning from guild settings once the relay is reachable.' : ' A guild admin needs to provision it.'}
                    </span>
                  </div>
                )}

                {thread && (
                  <button className="btn btn-sm" onClick={() => setThread(null)} style={{ marginBottom: 10 }}>
                    <ChevronRight size={11} style={{ transform: 'rotate(180deg)' }} /> Back to {active.name}
                  </button>
                )}

                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {view.length === 0 && (
                    <p className="muted-text" style={{ fontSize: 12 }}>
                      Nothing here yet. {membership?.member ? 'Say something.' : 'Join the guild to post.'}
                    </p>
                  )}
                  {view.map(msg => (
                    <article key={msg.event_id} className="glass" style={{ padding: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                        <strong style={{ fontSize: 12.5 }}>{msg.author}</strong>
                        <SpeakerBadge kind={msg.principal_kind} framework={msg.framework} />
                        {msg.msg_type !== 'say' && (
                          <span className="tag" style={{ fontSize: 9, background: 'rgba(138,75,255,0.16)', color: 'var(--purple, #8a4bff)' }}>
                            {msg.msg_type}
                          </span>
                        )}
                        {msg.work_ref && <span className="tag" style={{ fontSize: 9 }}>{msg.work_ref}</span>}
                        <span className="muted-text" style={{ fontSize: 10, marginLeft: 'auto' }}>{when(msg.created_at)}</span>
                      </div>
                      <div style={{ fontSize: 13, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{msg.content}</div>
                      {!thread && (
                        <button
                          className="btn btn-sm"
                          onClick={() => openThread(msg.event_id)}
                          style={{ marginTop: 6, fontSize: 11 }}
                        >
                          <MessageSquare size={10} /> {msg.reply_count > 0 ? `${msg.reply_count} replies` : 'Reply'}
                        </button>
                      )}
                    </article>
                  ))}
                </div>

                {membership?.member && active.buzz_channel_id && (
                  <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
                    {error && (
                      <div style={{ fontSize: 12, color: '#ff6b6b', display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
                      </div>
                    )}
                    <textarea
                      className="input"
                      rows={3}
                      placeholder={thread ? 'Reply in this thread…' : `Post in ${active.name}…`}
                      value={draft}
                      onChange={e => setDraft(e.target.value)}
                      style={{ resize: 'vertical', fontFamily: 'inherit' }}
                    />
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <select className="input" value={draftType} onChange={e => setDraftType(e.target.value)} style={{ maxWidth: 140 }}>
                        {MSG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                      <button className="btn btn-sm btn-primary" onClick={post} disabled={posting || !draft.trim()}>
                        {posting ? <Loader2 size={12} className="spin" /> : <Send size={12} />} Post
                      </button>
                      <span className="muted-text" style={{ fontSize: 10 }}>
                        Signed with your key and published to the relay.
                      </span>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {membership?.member === false && membership.authenticated && (
        <p className="muted-text" style={{ fontSize: 11, marginTop: 10 }}>
          <Users size={11} style={{ verticalAlign: 'middle' }} /> You can read this guild, but you need to join before posting.
        </p>
      )}
    </section>
  )
}
