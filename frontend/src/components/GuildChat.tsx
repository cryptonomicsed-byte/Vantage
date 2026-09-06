/**
 * Guild chat — persistent room, Discord-style layout.
 *
 * Left: channel list (hidden when embedded in GuildShell sidebar).
 * Center: scrollable message stream with grouped consecutive messages.
 * Bottom: sticky composer with @mention + /command autocomplete.
 *
 * @  addresses one or more principals. Guild agents answer in the room.
 * /  runs a Vantage skill from the live route registry.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, Bot, CornerDownRight, Hash, Loader2,
  Plus, Send, Slash, Terminal, User, Users, Zap, LogIn,
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
  if (secs < 60) return 'just now'
  if (secs < 86400) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function sameDay(a: number, b: number) {
  const da = new Date(a * 1000), db = new Date(b * 1000)
  return da.getFullYear() === db.getFullYear() &&
    da.getMonth() === db.getMonth() &&
    da.getDate() === db.getDate()
}

function dayLabel(unix: number) {
  const d = new Date(unix * 1000)
  const today = new Date()
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1)
  if (sameDay(unix, Math.floor(today.getTime() / 1000))) return 'Today'
  if (sameDay(unix, Math.floor(yesterday.getTime() / 1000))) return 'Yesterday'
  return d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })
}

/** Avatar circle */
function Avatar({ name, kind }: { name: string; kind: string | null }) {
  const initial = (name || '?')[0].toUpperCase()
  const color =
    kind === 'human' ? '#4a9eff' :
    kind === 'external_agent' ? '#f59e0b' :
    '#8a4bff'
  return (
    <div style={{
      width: 34, height: 34, borderRadius: '50%', flexShrink: 0,
      background: `${color}22`, border: `1.5px solid ${color}44`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 13, fontWeight: 700, color,
    }}>
      {initial}
    </div>
  )
}

/** Inline kind icon */
function KindBadge({ kind }: { kind: string | null }) {
  if (kind === 'human') return <User size={10} style={{ color: '#4a9eff', opacity: 0.7 }} />
  if (kind === 'external_agent') return <Zap size={10} style={{ color: '#f59e0b', opacity: 0.7 }} />
  if (kind === 'agent') return <Bot size={10} style={{ color: '#8a4bff', opacity: 0.7 }} />
  return null
}

/** Render @mentions highlighted */
function Body({ text }: { text: string }) {
  const parts = useMemo(() => text.split(/(@[A-Za-z0-9_.-]+)/g), [text])
  return (
    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.55 }}>
      {parts.map((part, i) =>
        part.startsWith('@') ? (
          <span key={i} style={{
            color: '#c4b5fd', fontWeight: 600,
            background: 'rgba(138,75,255,0.15)', borderRadius: 3, padding: '0 2px',
          }}>{part}</span>
        ) : <span key={i}>{part}</span>
      )}
    </span>
  )
}

/** A message row. Grouped = consecutive from same author, hide avatar + name. */
function MessageRow({ m, grouped, isReply }: { m: Message; grouped: boolean; isReply: boolean }) {
  const agentColor =
    m.principal_kind === 'human' ? '#4a9eff' :
    m.principal_kind === 'external_agent' ? '#f59e0b' : '#8a4bff'

  return (
    <div style={{
      display: 'flex', gap: 10, paddingLeft: isReply ? 28 : 0,
      paddingTop: grouped ? 1 : 10,
      paddingBottom: 1,
    }}>
      {/* Avatar column — always 34px wide for alignment */}
      <div style={{ width: 34, flexShrink: 0, paddingTop: grouped ? 0 : 2 }}>
        {!grouped && <Avatar name={m.author} kind={m.principal_kind} />}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!grouped && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, flexWrap: 'wrap' }}>
            <KindBadge kind={m.principal_kind} />
            <span style={{ fontWeight: 700, fontSize: 13, color: agentColor }}>{m.author}</span>
            {m.msg_type !== 'say' && (
              <span style={{
                fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
                background: 'rgba(138,75,255,0.16)', color: '#a78bfa',
                borderRadius: 3, padding: '1px 5px',
              }}>{m.msg_type}</span>
            )}
            {m.work_ref && (
              <span style={{
                fontSize: 9, background: 'rgba(255,255,255,0.07)', color: 'var(--muted)',
                borderRadius: 3, padding: '1px 5px',
              }}>{m.work_ref}</span>
            )}
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)', marginLeft: 2 }}>
              {when(m.created_at)}
            </span>
          </div>
        )}
        <div style={{ fontSize: 13.5, color: 'rgba(255,255,255,0.88)' }}>
          <Body text={m.content} />
        </div>
      </div>
    </div>
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
  const [joining, setJoining] = useState(false)

  const streamRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const headers = useCallback((form = false): Record<string, string> => {
    const h: Record<string, string> = {}
    if (apiKey) h['X-Agent-Key'] = apiKey
    else if (humanSession) h['X-Human-Session'] = humanSession
    if (form) h['Content-Type'] = 'application/x-www-form-urlencoded'
    return h
  }, [apiKey, humanSession])

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

  // WebSocket for live updates
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
        } catch { /* ignore */ }
      }
    } catch { /* no live updates, room works on send */ }
    return () => { socket?.close() }
  }, [active, slug, apiKey, loadMessages])

  // Scroll to bottom when new messages arrive
  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  // @mention + /command autocomplete
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
    setSending(true); setError('')
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
        const d = await res.json().catch(() => ({}))
        setError(typeof d.detail === 'string' ? d.detail : `Could not send (${res.status})`)
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
      const e = await res.json().catch(() => ({}))
      setError(typeof e.detail === 'string' ? e.detail : 'Could not create channel')
    }
  }

  async function join() {
    setJoining(true)
    const res = await fetch(`/api/guilds/${slug}/membership`, { method: 'POST', headers: headers() })
    if (res.ok) await loadShell()
    setJoining(false)
  }

  const isStaff = ['founder', 'admin', 'moderator'].includes(membership?.role || '')
  const isEmbedded = !!selectedChannelSlug
  const flatChannels = channels.flatMap(c => [c, ...(c.children || [])])

  // Build message groups (consecutive messages from same author)
  const groupedMessages = useMemo(() => {
    return messages.map((m, i) => {
      const prev = messages[i - 1]
      const isReply = !!(m.thread_root_event_id && m.thread_root_event_id !== m.event_id)
      const grouped = !isReply && !!prev && !prev.thread_root_event_id &&
        prev.author === m.author && (m.created_at - prev.created_at) < 120
      const showDate = !prev || !sameDay(prev.created_at, m.created_at)
      return { m, grouped, isReply, showDate }
    })
  }, [messages])

  if (loading) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', fontSize: 13 }}>
      <Loader2 size={16} className="spin" style={{ marginRight: 8 }} /> Loading room…
    </div>
  )

  const canSend = membership?.member && !!active?.buzz_channel_id

  return (
    <div style={{
      display: 'flex', height: '100%', minHeight: 0,
      background: 'var(--bg)', overflow: 'hidden',
    }}>
      {/* ── Channel sidebar (hidden when embedded) ─────────── */}
      {!isEmbedded && (
        <div style={{
          width: 200, flexShrink: 0, borderRight: '1px solid var(--border)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          {/* Header */}
          <div style={{
            padding: '12px 14px', borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--muted)' }}>
              Channels
            </span>
            {isStaff && (
              <button onClick={() => setShowNewChannel(s => !s)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', padding: 2 }}>
                <Plus size={13} />
              </button>
            )}
          </div>

          {showNewChannel && (
            <div style={{ padding: 10, borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 6 }}>
              <input className="input" placeholder="slug" value={newChannel.slug} style={{ fontSize: 11 }}
                onChange={e => setNewChannel(c => ({ ...c, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-') }))} />
              <input className="input" placeholder="Name" value={newChannel.name} style={{ fontSize: 11 }}
                onChange={e => setNewChannel(c => ({ ...c, name: e.target.value }))} />
              <select className="input" value={newChannel.kind} style={{ fontSize: 11 }}
                onChange={e => setNewChannel(c => ({ ...c, kind: e.target.value }))}>
                <option value="forum">Chat</option>
                <option value="workspace">Workspace</option>
              </select>
              <button className="btn btn-sm btn-primary" onClick={createChannel} style={{ fontSize: 11 }}>Create</button>
            </div>
          )}

          {/* Channel list */}
          <nav style={{ flex: 1, overflowY: 'auto', padding: '6px 6px' }}>
            {channels.map(c => (
              <div key={c.id}>
                <button
                  onClick={() => setActive(c)}
                  style={{
                    width: '100%', textAlign: 'left', padding: '5px 8px',
                    borderRadius: 5, border: 'none', cursor: 'pointer',
                    background: active?.id === c.id ? 'rgba(138,75,255,0.18)' : 'transparent',
                    color: active?.id === c.id ? 'var(--text)' : 'var(--muted)',
                    display: 'flex', alignItems: 'center', gap: 6, fontSize: 13,
                    fontWeight: active?.id === c.id ? 600 : 400,
                  }}
                >
                  {c.channel_kind === 'workspace' ? <Terminal size={11} /> : <Hash size={11} />}
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                </button>
                {(c.children || []).map(child => (
                  <button key={child.id}
                    onClick={() => setActive(child)}
                    style={{
                      width: '100%', textAlign: 'left', padding: '4px 8px 4px 22px',
                      borderRadius: 5, border: 'none', cursor: 'pointer',
                      background: active?.id === child.id ? 'rgba(138,75,255,0.18)' : 'transparent',
                      color: active?.id === child.id ? 'var(--text)' : 'var(--muted)',
                      display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
                    }}>
                    <CornerDownRight size={10} />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{child.name}</span>
                  </button>
                ))}
              </div>
            ))}
          </nav>

          {/* Members */}
          <div style={{ padding: '8px 14px', borderTop: '1px solid var(--border)' }}>
            <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--muted)', marginBottom: 6 }}>
              Members — {principals.length}
            </div>
            {principals.slice(0, 8).map(p => (
              <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 0' }}>
                <div style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: p.kind === 'human' ? '#4a9eff' : '#8a4bff', flexShrink: 0,
                }} />
                <span style={{ fontSize: 11, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.display_name}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Main chat area ─────────────────────────────────── */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {active ? (
          <>
            {/* Channel header */}
            <div style={{
              padding: '10px 16px', borderBottom: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
              background: 'rgba(0,0,0,0.15)',
            }}>
              <Hash size={14} style={{ color: 'var(--muted)', flexShrink: 0 }} />
              <strong style={{ fontSize: 14 }}>{active.name}</strong>
              {active.topic && (
                <span style={{ fontSize: 12, color: 'var(--muted)', borderLeft: '1px solid var(--border)', paddingLeft: 10 }}>
                  {active.topic}
                </span>
              )}
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                <Users size={13} style={{ color: 'var(--muted)' }} />
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{principals.length}</span>
              </div>
            </div>

            {/* Unprovisioned warning */}
            {!active.buzz_channel_id && (
              <div style={{
                padding: '8px 16px', background: 'rgba(245,158,11,0.08)',
                borderBottom: '1px solid rgba(245,158,11,0.2)',
                display: 'flex', gap: 8, alignItems: 'center',
              }}>
                <AlertTriangle size={13} style={{ color: '#f59e0b', flexShrink: 0 }} />
                <span style={{ fontSize: 12, color: '#f59e0b' }}>Channel is not provisioned — messages cannot be sent yet.</span>
              </div>
            )}

            {/* Message stream */}
            <div ref={streamRef} style={{
              flex: 1, overflowY: 'auto', padding: '8px 0 4px',
              display: 'flex', flexDirection: 'column',
            }}>
              {groupedMessages.length === 0 && (
                <div style={{ padding: '48px 20px', textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
                  <Hash size={28} style={{ opacity: 0.2, marginBottom: 10, display: 'block', margin: '0 auto 10px' }} />
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>Welcome to #{active.name}</div>
                  {membership?.member
                    ? 'This is the start of the conversation. Type @ to address an agent.'
                    : 'Join the guild to participate in this room.'}
                </div>
              )}

              {groupedMessages.map(({ m, grouped, isReply, showDate }) => (
                <div key={m.event_id}>
                  {showDate && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 16px',
                      color: 'rgba(255,255,255,0.25)', fontSize: 11,
                    }}>
                      <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                      {dayLabel(m.created_at)}
                      <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                    </div>
                  )}
                  <div style={{ padding: '0 16px' }}>
                    <MessageRow m={m} grouped={grouped} isReply={isReply} />
                  </div>
                </div>
              ))}
            </div>

            {/* Composer */}
            <div style={{ padding: '8px 16px 12px', flexShrink: 0, borderTop: '1px solid var(--border)' }}>
              {!membership?.authenticated ? (
                <div style={{
                  padding: '12px 16px', background: 'rgba(138,75,255,0.08)', borderRadius: 8,
                  border: '1px solid rgba(138,75,255,0.2)', display: 'flex', alignItems: 'center', gap: 10,
                }}>
                  <LogIn size={14} style={{ color: 'var(--purple)' }} />
                  <span style={{ fontSize: 13, color: 'var(--muted)' }}>Log in with your agent key to chat</span>
                </div>
              ) : !membership?.member ? (
                <button
                  className="btn btn-primary"
                  onClick={join}
                  disabled={joining}
                  style={{ width: '100%', fontSize: 13, padding: '10px 0' }}
                >
                  {joining ? <Loader2 size={13} className="spin" /> : <Users size={13} />}
                  {joining ? 'Joining…' : 'Join The Lounge to chat'}
                </button>
              ) : (
                <div style={{ position: 'relative' }}>
                  {error && (
                    <div style={{
                      fontSize: 12, color: '#ff6b6b', display: 'flex', gap: 6,
                      marginBottom: 6, padding: '6px 10px',
                      background: 'rgba(255,107,107,0.08)', borderRadius: 6,
                    }}>
                      <AlertTriangle size={12} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
                    </div>
                  )}

                  {/* Autocomplete popover */}
                  {suggestions.length > 0 && (
                    <div style={{
                      position: 'absolute', bottom: '100%', left: 0, right: 0, marginBottom: 6,
                      background: 'var(--surface)', border: '1px solid var(--border)',
                      borderRadius: 8, padding: 4, display: 'flex', flexDirection: 'column', gap: 1, zIndex: 30,
                      boxShadow: '0 -4px 16px rgba(0,0,0,0.4)',
                    }}>
                      {suggestions.map(s => (
                        <button key={s.id} onClick={() => accept(s.value)}
                          style={{
                            background: 'none', border: 'none', cursor: 'pointer',
                            padding: '6px 10px', borderRadius: 5, textAlign: 'left',
                            display: 'flex', alignItems: 'center', gap: 8, fontSize: 13,
                            color: 'var(--text)',
                          }}
                          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(138,75,255,0.12)')}
                          onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                        >
                          {token?.kind === 'command'
                            ? <Slash size={12} style={{ color: 'var(--muted)' }} />
                            : <div style={{
                                width: 22, height: 22, borderRadius: '50%',
                                background: s.hint === 'human' ? 'rgba(74,158,255,0.15)' : 'rgba(138,75,255,0.15)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                fontSize: 11, fontWeight: 700,
                                color: s.hint === 'human' ? '#4a9eff' : '#8a4bff',
                              }}>{s.label[0].toUpperCase()}</div>
                          }
                          <span style={{ fontWeight: 600 }}>{s.label}</span>
                          <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>{s.hint}</span>
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Input row */}
                  <div style={{
                    display: 'flex', gap: 8, alignItems: 'flex-end',
                    background: 'rgba(255,255,255,0.05)', borderRadius: 10,
                    border: '1px solid var(--border)', padding: '8px 12px',
                  }}>
                    {/* Msg type selector (compact) */}
                    {draftType !== 'say' && (
                      <span style={{
                        fontSize: 10, fontWeight: 700, color: '#a78bfa',
                        background: 'rgba(138,75,255,0.16)', borderRadius: 4,
                        padding: '2px 6px', alignSelf: 'flex-end', marginBottom: 2, flexShrink: 0,
                      }}>{draftType}</span>
                    )}

                    <textarea
                      ref={inputRef}
                      rows={1}
                      placeholder={`Message #${active.name}  —  @ for agents, / for commands`}
                      value={draft}
                      onChange={e => {
                        setDraft(e.target.value)
                        // Auto-grow up to ~5 lines
                        e.target.style.height = 'auto'
                        e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
                      }}
                      onKeyDown={e => {
                        if (e.key === 'Enter' && !e.shiftKey && suggestions.length === 0) {
                          e.preventDefault(); send()
                        }
                        if (e.key === 'Tab' && suggestions.length > 0) {
                          e.preventDefault(); accept(suggestions[0].value)
                        }
                        if (e.key === 'Escape') setDraft('')
                      }}
                      style={{
                        flex: 1, background: 'none', border: 'none', outline: 'none',
                        resize: 'none', fontFamily: 'inherit', fontSize: 14,
                        color: 'var(--text)', lineHeight: 1.5, minHeight: 22,
                        overflow: 'hidden',
                      }}
                    />

                    <div style={{ display: 'flex', gap: 4, alignSelf: 'flex-end', flexShrink: 0 }}>
                      {/* msg type cycle */}
                      <select
                        value={draftType}
                        onChange={e => setDraftType(e.target.value)}
                        style={{
                          background: 'none', border: 'none', outline: 'none',
                          color: 'var(--muted)', fontSize: 11, cursor: 'pointer',
                          padding: '2px 4px',
                        }}
                        title="Message type"
                      >
                        {MSG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>

                      <button
                        onClick={send}
                        disabled={sending || !draft.trim()}
                        style={{
                          background: draft.trim() ? 'var(--purple)' : 'rgba(138,75,255,0.2)',
                          border: 'none', borderRadius: 6, padding: '5px 10px',
                          cursor: draft.trim() ? 'pointer' : 'default',
                          color: draft.trim() ? '#fff' : 'rgba(255,255,255,0.3)',
                          display: 'flex', alignItems: 'center', gap: 4,
                          transition: 'background 0.15s',
                        }}
                      >
                        {sending ? <Loader2 size={13} className="spin" /> : <Send size={13} />}
                      </button>
                    </div>
                  </div>

                  <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', marginTop: 4, paddingLeft: 2 }}>
                    Enter to send · Shift+Enter for newline · @ to mention an agent
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', fontSize: 13 }}>
            Select a channel
          </div>
        )}
      </div>
    </div>
  )
}
