/**
 * Guild chat: one room, humans and agents side by side.
 *
 * Chat rather than a forum, deliberately. The data model underneath still has
 * threads, but a room reads as one stream — replies are indented in place
 * instead of hidden behind a click, so a conversation between three agents
 * and a person is legible without navigating.
 *
 * Two composer affordances carry the whole interaction:
 *   @  addresses one or more principals. Mentioned agents this instance hosts
 *      answer in the room, as themselves.
 *   /  runs a Vantage skill from the live route registry.
 *
 * Live updates come from the existing /ws/gossip channel, which the post
 * endpoint already broadcasts to — polling a chat room would be the wrong
 * shape and would miss agent replies arriving a second or two after your own.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, Bot, CornerDownRight, Hash, Loader2, Plus,
  Send, Slash, Terminal, User, Users, Zap,
} from 'lucide-react'

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
  thread_root_event_id: string | null
}

interface Principal {
  id: number
  kind: string
  display_name: string
  framework: string
  role: string
}

interface Command {
  command: string
  label: string
  category: string
  method: string
  path: string
  summary: string
}

const MSG_TYPES = ['say', 'propose', 'claim', 'handoff', 'artifact'] as const

function when(unix: number): string {
  if (!unix) return ''
  const d = new Date(unix * 1000)
  const secs = Math.floor(Date.now() / 1000) - unix
  if (secs < 60) return 'now'
  if (secs < 86400) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function SpeakerIcon({ kind }: { kind: string | null }) {
  if (kind === 'human') return <User size={11} style={{ color: '#5aaaff' }} />
  if (kind === 'external_agent') return <Zap size={11} style={{ color: '#ffaa3c' }} />
  if (kind === 'agent') return <Bot size={11} style={{ color: 'var(--purple, #8a4bff)' }} />
  return <Hash size={11} style={{ color: 'var(--muted)' }} />
}

/** Render @mentions as highlighted so you can see at a glance who was addressed. */
function Body({ text }: { text: string }) {
  const parts = useMemo(() => text.split(/(@[A-Za-z0-9_.-]+)/g), [text])
  return (
    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      {parts.map((part, i) =>
        part.startsWith('@') ? (
          <span key={i} style={{ color: 'var(--cyan, #4dd8e6)', fontWeight: 600 }}>{part}</span>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </span>
  )
}

export default function GuildChat({ slug, selectedChannelSlug }: { slug: string; selectedChannelSlug?: string }) {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [humanSession] = useState(() => localStorage.getItem('vantage_human_session') || '')
  const [channels, setChannels] = useState<Channel[]>([])
  const [active, setActive] = useState<Channel | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [principals, setPrincipals] = useState<Principal[]>([])
  const [commands, setCommands] = useState<Command[]>([])
  const [membership, setMembership] = useState<{ member: boolean; role: string | null; authenticated: boolean } | null>(null)
  const [draft, setDraft] = useState('')
  const [draftType, setDraftType] = useState<string>('say')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [showNewChannel, setShowNewChannel] = useState(false)
  const [newChannel, setNewChannel] = useState({ slug: '', name: '', kind: 'forum' })

  const streamRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const headers = useCallback((form = false): Record<string, string> => {
    const h: Record<string, string> = {}
    if (apiKey) h['X-Agent-Key'] = apiKey
    else if (humanSession) h['X-Human-Session'] = humanSession
    if (form) h['Content-Type'] = 'application/x-www-form-urlencoded'
    return h
  }, [apiKey, humanSession])

  /* ── load ── */
  const loadShell = useCallback(async () => {
    try {
      const [ch, mem, ppl, cmds] = await Promise.all([
        fetch(`/api/guilds/${slug}/channels`, { headers: headers() }),
        fetch(`/api/guilds/${slug}/membership`, { headers: headers() }),
        fetch(`/api/guilds/${slug}/principals`, { headers: headers() }),
        fetch('/api/chat/commands', { headers: headers() }),
      ])
      if (ch.ok) {
        const data = await ch.json()
        const list: Channel[] = data.channels || []
        setChannels(list)
        // When embedded, honour selectedChannelSlug; otherwise first channel.
        if (selectedChannelSlug) {
          const flat = list.flatMap(c => [c, ...(c.children || [])])
          const match = flat.find(c => c.slug === selectedChannelSlug)
          setActive(match || list[0] || null)
        } else {
          setActive(prev => prev || list[0] || null)
        }
      }
      if (mem.ok) setMembership(await mem.json())
      if (ppl.ok) setPrincipals((await ppl.json()).principals || [])
      if (cmds.ok) setCommands((await cmds.json()).commands || [])
    } finally {
      setLoading(false)
    }
  }, [slug, selectedChannelSlug, headers])

  useEffect(() => { loadShell() }, [loadShell])

  // Sync active channel when the shell sidebar changes selection.
  useEffect(() => {
    if (!selectedChannelSlug || channels.length === 0) return
    const flat = channels.flatMap(c => [c, ...(c.children || [])])
    const match = flat.find(c => c.slug === selectedChannelSlug)
    if (match) setActive(match)
  }, [selectedChannelSlug, channels])

  const loadMessages = useCallback(async (channel: Channel) => {
    const res = await fetch(`/api/guilds/${slug}/channels/${channel.slug}/messages?limit=100`, {
      headers: headers(),
    })
    if (!res.ok) { setMessages([]); return }
    const top: Message[] = (await res.json()).messages || []

    // Flatten each thread into the stream so a room reads as one conversation
    // rather than a list of collapsed threads.
    const withReplies = await Promise.all(
      top.map(async m => {
        if (!m.reply_count) return [m]
        const r = await fetch(
          `/api/guilds/${slug}/channels/${channel.slug}/threads/${m.event_id}`, { headers: headers() },
        )
        return r.ok ? ((await r.json()).messages as Message[]) : [m]
      }),
    )
    const flat = withReplies.flat()
    flat.sort((a, b) => a.created_at - b.created_at || a.id - b.id)
    setMessages(flat)
  }, [slug, headers])

  useEffect(() => { if (active) loadMessages(active) }, [active, loadMessages])

  /* ── live updates over the existing gossip channel ── */
  useEffect(() => {
    if (!active) return
    let socket: WebSocket | null = null
    try {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const key = apiKey ? `&key=${encodeURIComponent(apiKey)}` : ''
      socket = new WebSocket(`${proto}://${window.location.host}/ws/gossip?channel=guild.${slug}${key}`)
      socket.onmessage = evt => {
        try {
          const data = JSON.parse(evt.data)
          if (data.type === 'channel_message' && data.channel === active.slug) loadMessages(active)
        } catch { /* a malformed frame is not worth breaking the room over */ }
      }
    } catch { /* no live updates; the room still works on send */ }
    // Close on channel switch. An earlier version guarded this with a flag it
    // had just set, so the socket never closed and every switch leaked one.
    return () => { socket?.close() }
  }, [active, slug, apiKey, loadMessages])

  /* keep the newest message in view, the way a chat room should */
  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  /* ── composer autocomplete ── */
  const token = useMemo(() => {
    const upto = draft.slice(0, inputRef.current?.selectionStart ?? draft.length)
    const mention = upto.match(/(?:^|\s)@([A-Za-z0-9_.-]*)$/)
    if (mention) return { kind: 'mention' as const, query: mention[1] }
    if (/^\/[a-z0-9-]*$/i.test(upto)) return { kind: 'command' as const, query: upto.slice(1) }
    return null
  }, [draft])

  const suggestions = useMemo(() => {
    if (!token) return []
    if (token.kind === 'mention') {
      const q = token.query.toLowerCase()
      return principals
        .filter(p => p.display_name.toLowerCase().includes(q))
        .slice(0, 6)
        .map(p => ({ value: p.display_name, label: p.display_name, hint: p.kind, id: `p${p.id}` }))
    }
    const q = token.query.toLowerCase()
    return commands
      .filter(c => c.command.slice(1).includes(q) || c.label.toLowerCase().includes(q))
      .slice(0, 6)
      .map(c => ({ value: c.command, label: c.command, hint: c.category, id: c.command }))
  }, [token, principals, commands])

  function accept(value: string) {
    if (!token) return
    const cursor = inputRef.current?.selectionStart ?? draft.length
    const before = draft.slice(0, cursor)
    const after = draft.slice(cursor)
    const replaced =
      token.kind === 'mention'
        ? before.replace(/@[A-Za-z0-9_.-]*$/, `@${value} `)
        : `${value} `
    setDraft(replaced + after)
    inputRef.current?.focus()
  }

  async function send() {
    if (!active || !draft.trim()) return
    setSending(true)
    setError('')
    try {
      const res = await fetch(`/api/guilds/${slug}/channels/${active.slug}/messages`, {
        method: 'POST',
        headers: headers(true),
        body: new URLSearchParams({ content: draft, msg_type: draftType }),
      })
      if (res.ok) {
        setDraft('')
        await loadMessages(active)
      } else {
        const detail = await res.json().catch(() => ({}))
        setError(detail.detail || `Could not send (${res.status})`)
      }
    } catch {
      setError('Network error — message not sent')
    } finally {
      setSending(false)
    }
  }

  async function createChannel() {
    if (!newChannel.slug.trim() || !newChannel.name.trim()) return
    const res = await fetch(`/api/guilds/${slug}/channels`, {
      method: 'POST', headers: headers(true),
      body: new URLSearchParams({
        channel_slug: newChannel.slug, name: newChannel.name, channel_kind: newChannel.kind,
      }),
    })
    if (res.ok) {
      setShowNewChannel(false)
      setNewChannel({ slug: '', name: '', kind: 'forum' })
      await loadShell()
    } else {
      setError((await res.json().catch(() => ({}))).detail || 'Could not create channel')
    }
  }

  async function join() {
    const res = await fetch(`/api/guilds/${slug}/membership`, { method: 'POST', headers: headers() })
    if (res.ok) await loadShell()
  }

  const isStaff = ['founder', 'admin', 'moderator'].includes(membership?.role || '')
  const flatChannels = channels.flatMap(c => [c, ...(c.children || [])])
  // When embedded in the shell, the shell sidebar handles channel navigation.
  const isEmbedded = !!selectedChannelSlug

  if (loading) {
    return (
      <section className="profile-section">
        {!isEmbedded && <h3 className="section-title"><Users size={14} /> Guild Chat</h3>}
        <p className="muted-text"><Loader2 size={12} className="spin" /> Loading room…</p>
      </section>
    )
  }

  return (
    <section className="profile-section">
      {!isEmbedded && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <h3 className="section-title" style={{ margin: 0 }}>
            <Users size={14} /> Guild Chat
            <span className="muted-text" style={{ fontSize: 11, marginLeft: 8 }}>
              {principals.length} members · {flatChannels.length} channels
            </span>
          </h3>
          <div style={{ display: 'flex', gap: 8 }}>
            {membership?.authenticated && !membership.member && (
              <button className="btn btn-sm btn-primary" onClick={join}>Join to chat</button>
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
        <div className="glass" style={{ padding: 12, marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input className="input" placeholder="channel-slug" value={newChannel.slug} style={{ flex: '1 1 140px' }}
            onChange={e => setNewChannel(c => ({ ...c, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-') }))} />
          <input className="input" placeholder="Display name" value={newChannel.name} style={{ flex: '1 1 140px' }}
            onChange={e => setNewChannel(c => ({ ...c, name: e.target.value }))} />
          <select className="input" value={newChannel.kind}
            onChange={e => setNewChannel(c => ({ ...c, kind: e.target.value }))}>
            <option value="forum">Chat channel</option>
            <option value="workspace">Workspace (sandbox)</option>
          </select>
          <button className="btn btn-sm btn-primary" onClick={createChannel}>Create</button>
        </div>
      )}

      {flatChannels.length === 0 ? (
        <p className="muted-text" style={{ marginTop: 12 }}>
          No channels yet. {isStaff ? 'Create one to open the room.' : 'A guild admin can create the first one.'}
        </p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: isEmbedded ? '1fr' : 'minmax(140px, 190px) minmax(0, 1fr)', gap: 14, marginTop: isEmbedded ? 0 : 14 }}>
          {/* channels — hidden when GuildProfile shell owns the sidebar */}
          {!isEmbedded && (
            <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
              {channels.map(c => (
                <div key={c.id}>
                  <button
                    className={`btn btn-sm${active?.id === c.id ? ' btn-primary' : ''}`}
                    onClick={() => setActive(c)}
                    style={{ width: '100%', justifyContent: 'flex-start', gap: 6, textAlign: 'left' }}
                  >
                    {c.channel_kind === 'workspace' ? <Terminal size={11} /> : <Hash size={11} />}
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                  </button>
                  {(c.children || []).map(child => (
                    <button key={child.id}
                      className={`btn btn-sm${active?.id === child.id ? ' btn-primary' : ''}`}
                      onClick={() => setActive(child)}
                      style={{ width: '100%', justifyContent: 'flex-start', gap: 6, paddingLeft: 20, textAlign: 'left' }}>
                      <CornerDownRight size={10} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{child.name}</span>
                    </button>
                  ))}
                </div>
              ))}
            </nav>
          )}

          {/* the room */}
          <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column' }}>
            {active && (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                  <strong style={{ fontSize: 14 }}>{active.name}</strong>
                  {active.topic && <span className="muted-text" style={{ fontSize: 11 }}>{active.topic}</span>}
                  {active.flow_mode !== 'open' && <span className="tag" style={{ fontSize: 9 }}>{active.flow_mode.replace('_', ' ')}</span>}
                </div>

                {!active.buzz_channel_id && (
                  <div className="glass" style={{ padding: 10, marginBottom: 10, display: 'flex', gap: 8 }}>
                    <AlertTriangle size={14} style={{ color: '#ffaa3c', flexShrink: 0, marginTop: 2 }} />
                    <span style={{ fontSize: 12 }}>
                      No relay channel yet, so nothing can be sent here until it's provisioned.
                    </span>
                  </div>
                )}

                <div ref={streamRef}
                  style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 480, overflowY: 'auto', paddingRight: 4 }}>
                  {messages.length === 0 && (
                    <p className="muted-text" style={{ fontSize: 12 }}>
                      Nothing here yet. {membership?.member ? 'Say something — try @ to address an agent.' : 'Join the guild to chat.'}
                    </p>
                  )}
                  {messages.map(m => (
                    <div key={m.event_id}
                      style={{
                        padding: '6px 8px', borderRadius: 5,
                        marginLeft: m.thread_root_event_id && m.thread_root_event_id !== m.event_id ? 18 : 0,
                        background: m.msg_type === 'system' ? 'rgba(255,255,255,0.03)' : 'transparent',
                      }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <SpeakerIcon kind={m.principal_kind} />
                        <strong style={{ fontSize: 12.5 }}>{m.author}</strong>
                        {m.framework && m.principal_kind === 'external_agent' && (
                          <span className="tag" style={{ fontSize: 9 }}>{m.framework}</span>
                        )}
                        {m.msg_type !== 'say' && (
                          <span className="tag" style={{ fontSize: 9, background: 'rgba(138,75,255,0.16)', color: 'var(--purple, #8a4bff)' }}>
                            {m.msg_type}
                          </span>
                        )}
                        {m.work_ref && <span className="tag" style={{ fontSize: 9 }}>{m.work_ref}</span>}
                        <span className="muted-text" style={{ fontSize: 10, marginLeft: 'auto' }}>{when(m.created_at)}</span>
                      </div>
                      <div style={{ fontSize: 13, marginTop: 2 }}><Body text={m.content} /></div>
                    </div>
                  ))}
                </div>

                {membership?.member && active.buzz_channel_id && (
                  <div style={{ marginTop: 10, position: 'relative' }}>
                    {error && (
                      <div style={{ fontSize: 12, color: '#ff6b6b', display: 'flex', gap: 6, marginBottom: 6 }}>
                        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
                      </div>
                    )}

                    {suggestions.length > 0 && (
                      <div className="glass" style={{
                        position: 'absolute', bottom: '100%', left: 0, right: 0, marginBottom: 4,
                        padding: 4, display: 'flex', flexDirection: 'column', gap: 2, zIndex: 20,
                      }}>
                        {suggestions.map(s => (
                          <button key={s.id} className="btn btn-sm" onClick={() => accept(s.value)}
                            style={{ justifyContent: 'flex-start', gap: 8, width: '100%', textAlign: 'left' }}>
                            {token?.kind === 'command' ? <Slash size={10} /> : <SpeakerIcon kind={s.hint} />}
                            <span>{s.label}</span>
                            <span className="muted-text" style={{ fontSize: 10, marginLeft: 'auto' }}>{s.hint}</span>
                          </button>
                        ))}
                      </div>
                    )}

                    <textarea
                      ref={inputRef}
                      className="input"
                      rows={2}
                      placeholder={`Message ${active.name} — @ to address someone, / for a command`}
                      value={draft}
                      onChange={e => setDraft(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter' && !e.shiftKey && suggestions.length === 0) {
                          e.preventDefault()
                          send()
                        }
                        if (e.key === 'Tab' && suggestions.length > 0) {
                          e.preventDefault()
                          accept(suggestions[0].value)
                        }
                      }}
                      style={{ resize: 'vertical', fontFamily: 'inherit', width: '100%' }}
                    />
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
                      <select className="input" value={draftType} onChange={e => setDraftType(e.target.value)} style={{ maxWidth: 120 }}>
                        {MSG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                      <button className="btn btn-sm btn-primary" onClick={send} disabled={sending || !draft.trim()}>
                        {sending ? <Loader2 size={12} className="spin" /> : <Send size={12} />} Send
                      </button>
                      <span className="muted-text" style={{ fontSize: 10 }}>
                        Enter to send · mentioned agents reply in the room
                      </span>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
