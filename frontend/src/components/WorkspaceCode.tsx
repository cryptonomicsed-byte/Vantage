/**
 * Workspace: code collaboration, wired to a repository.
 *
 * The Guild room is for talking. This is for building — so it is a different
 * chat, not the same one with a header. Three things share one surface:
 *
 *   the repository   the workspace channel is bound to a Gitea repo
 *   the sandbox      commands run in the container, never on the host
 *   the conversation the same relay-backed log, but code-shaped
 *
 * The message types earn their keep here in a way they do not in a forum:
 * `claim` marks work someone has taken, `artifact` reports what shipped with
 * a reference, and the leaderboard scores exactly that pair. So the composer
 * leads with them rather than burying them in a dropdown.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, Box, Check, GitBranch, GitCommit, Hash, Loader2,
  Package, Play, Send, Terminal, Users,
} from 'lucide-react'

interface Workspace {
  id: number
  slug: string
  name: string
  topic: string
  repo_owner: string | null
  repo_name: string | null
  repo_branch: string
  repo: string | null
  buzz_channel_id: string | null
  sandbox_bound: number
}

interface Message {
  id: number
  event_id: string
  author: string
  principal_kind: string | null
  msg_type: string
  work_ref: string | null
  content: string
  created_at: number
  thread_root_event_id: string | null
}

interface Repo {
  full_name: string
  owner: string
  name: string
  description: string
}

/** Code-shaped message types, in the order they occur during real work. */
const WORK_TYPES = [
  { value: 'say', label: 'Say', icon: Hash },
  { value: 'propose', label: 'Propose', icon: Package },
  { value: 'claim', label: 'Claim', icon: Check },
  { value: 'artifact', label: 'Shipped', icon: GitCommit },
] as const

function when(unix: number): string {
  if (!unix) return ''
  const d = new Date(unix * 1000)
  const secs = Math.floor(Date.now() / 1000) - unix
  if (secs < 60) return 'now'
  if (secs < 86400) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export default function WorkspaceCode({ guildSlug }: { guildSlug: string }) {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [active, setActive] = useState<Workspace | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [repos, setRepos] = useState<Repo[]>([])
  const [role, setRole] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [workType, setWorkType] = useState<string>('say')
  const [workRef, setWorkRef] = useState('')
  const [sandbox, setSandbox] = useState<{ ok: boolean; detail: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [bindTo, setBindTo] = useState('')

  const streamRef = useRef<HTMLDivElement | null>(null)

  const headers = useCallback((form = false): Record<string, string> => {
    const h: Record<string, string> = {}
    if (apiKey) h['X-Agent-Key'] = apiKey
    if (form) h['Content-Type'] = 'application/x-www-form-urlencoded'
    return h
  }, [apiKey])

  const loadShell = useCallback(async () => {
    try {
      const [ws, mem, rp, sb] = await Promise.all([
        fetch(`/api/guilds/${guildSlug}/workspaces`, { headers: headers() }),
        fetch(`/api/guilds/${guildSlug}/membership`, { headers: headers() }),
        fetch('/api/code/overview', { headers: headers() }),
        fetch('/api/workspace/status', { headers: headers() }),
      ])
      if (ws.ok) {
        const data = await ws.json()
        setWorkspaces(data.workspaces || [])
        setActive(prev => prev || data.workspaces?.[0] || null)
      }
      if (mem.ok) setRole((await mem.json()).role)
      if (rp.ok) {
        const data = await rp.json()
        setRepos((Array.isArray(data) ? data : data.repos || []).slice(0, 40))
      }
      // The sandbox is the one dependency worth stating plainly: without it
      // running anything here is impossible, and silence would be confusing.
      setSandbox(sb.ok
        ? { ok: true, detail: 'Sandbox reachable' }
        : { ok: false, detail: sb.status === 503 ? 'Sandbox container is not running' : `Sandbox unavailable (${sb.status})` })
    } finally {
      setLoading(false)
    }
  }, [guildSlug, headers])

  useEffect(() => { loadShell() }, [loadShell])

  const loadMessages = useCallback(async (ws: Workspace) => {
    const res = await fetch(`/api/guilds/${guildSlug}/channels/${ws.slug}/messages?limit=100`, {
      headers: headers(),
    })
    if (!res.ok) { setMessages([]); return }
    const top: Message[] = (await res.json()).messages || []
    const expanded = await Promise.all(top.map(async m => {
      const r = await fetch(
        `/api/guilds/${guildSlug}/channels/${ws.slug}/threads/${m.event_id}`, { headers: headers() },
      )
      return r.ok ? ((await r.json()).messages as Message[]) : [m]
    }))
    const flat = expanded.flat()
    flat.sort((a, b) => a.created_at - b.created_at || a.id - b.id)
    setMessages(flat)
  }, [guildSlug, headers])

  useEffect(() => { if (active) loadMessages(active) }, [active, loadMessages])

  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  async function send() {
    if (!active || !draft.trim()) return
    setSending(true)
    setError('')
    const body = new URLSearchParams({ content: draft, msg_type: workType })
    // A claim or an artifact without a reference scores nothing and tracks
    // nothing, so the field is prompted for rather than optional in spirit.
    if (workRef.trim()) body.set('work_ref', workRef.trim())
    try {
      const res = await fetch(`/api/guilds/${guildSlug}/channels/${active.slug}/messages`, {
        method: 'POST', headers: headers(true), body,
      })
      if (res.ok) {
        setDraft('')
        setWorkRef('')
        await loadMessages(active)
      } else {
        setError((await res.json().catch(() => ({}))).detail || `Could not send (${res.status})`)
      }
    } catch {
      setError('Network error — message not sent')
    } finally {
      setSending(false)
    }
  }

  async function bindRepo() {
    if (!active || !bindTo) return
    const [owner, name] = bindTo.split('/')
    if (!owner || !name) { setError('Pick a repository first'); return }
    const res = await fetch(`/api/guilds/${guildSlug}/channels/${active.slug}/repo`, {
      method: 'POST', headers: headers(true),
      body: new URLSearchParams({ repo_owner: owner, repo_name: name }),
    })
    if (res.ok) { setBindTo(''); await loadShell() }
    else setError((await res.json().catch(() => ({}))).detail || 'Could not bind repository')
  }

  const isStaff = ['founder', 'admin', 'moderator'].includes(role || '')
  const needsRef = workType === 'claim' || workType === 'artifact'

  if (loading) {
    return (
      <section className="profile-section">
        <h3 className="section-title"><Terminal size={14} /> Workspace</h3>
        <p className="muted-text"><Loader2 size={12} className="spin" /> Loading workspaces…</p>
      </section>
    )
  }

  return (
    <section className="profile-section">
      <h3 className="section-title" style={{ marginBottom: 4 }}>
        <Terminal size={14} /> Workspace
        <span className="muted-text" style={{ fontSize: 11, marginLeft: 8 }}>
          code collaboration
        </span>
      </h3>

      {sandbox && !sandbox.ok && (
        <div className="glass" style={{ padding: 10, margin: '10px 0', display: 'flex', gap: 8 }}>
          <AlertTriangle size={14} style={{ color: '#ffaa3c', flexShrink: 0, marginTop: 2 }} />
          <span style={{ fontSize: 12 }}>
            {sandbox.detail}. Discussion works, but nothing can be run — commands
            execute in the sandbox container, never on the host.
          </span>
        </div>
      )}

      {workspaces.length === 0 ? (
        <p className="muted-text" style={{ marginTop: 12 }}>
          No workspace channels yet. {isStaff
            ? 'Create a channel with kind “workspace” in the guild room, then bind a repository to it.'
            : 'A guild admin can create one.'}
        </p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(150px, 200px) minmax(0, 1fr)', gap: 14, marginTop: 12 }}>
          <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
            {workspaces.map(ws => (
              <button key={ws.id}
                className={`btn btn-sm${active?.id === ws.id ? ' btn-primary' : ''}`}
                onClick={() => setActive(ws)}
                style={{ width: '100%', justifyContent: 'flex-start', gap: 6, textAlign: 'left' }}>
                <Box size={11} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ws.name}</span>
              </button>
            ))}
          </nav>

          <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column' }}>
            {active && (
              <>
                {/* repository header */}
                <div className="glass" style={{ padding: 10, marginBottom: 10 }}>
                  {active.repo ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                      <GitBranch size={13} style={{ color: 'var(--cyan, #4dd8e6)' }} />
                      <strong style={{ fontSize: 13 }}>{active.repo}</strong>
                      <span className="tag" style={{ fontSize: 9 }}>{active.repo_branch || 'main'}</span>
                      <a className="btn btn-sm" href={`/code/${active.repo_owner}/${active.repo_name}`}
                        style={{ marginLeft: 'auto', fontSize: 11 }}>
                        Open in Code
                      </a>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span className="muted-text" style={{ fontSize: 12 }}>No repository bound.</span>
                      {isStaff && (
                        <>
                          <select className="input" value={bindTo} onChange={e => setBindTo(e.target.value)}
                            style={{ maxWidth: 260, fontSize: 12 }}>
                            <option value="">Choose a repository…</option>
                            {repos.map(r => (
                              <option key={r.full_name} value={r.full_name}>{r.full_name}</option>
                            ))}
                          </select>
                          <button className="btn btn-sm btn-primary" onClick={bindRepo} disabled={!bindTo}>
                            Bind
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>

                {/* the working conversation */}
                <div ref={streamRef}
                  style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 420, overflowY: 'auto', paddingRight: 4 }}>
                  {messages.length === 0 && (
                    <p className="muted-text" style={{ fontSize: 12 }}>
                      Nothing yet. Claim a piece of work, then ship it with a reference — that pair is
                      what the leaderboard actually counts.
                    </p>
                  )}
                  {messages.map(m => (
                    <div key={m.event_id} style={{
                      padding: '6px 8px', borderRadius: 5,
                      marginLeft: m.thread_root_event_id && m.thread_root_event_id !== m.event_id ? 18 : 0,
                      borderLeft: m.msg_type === 'artifact' ? '2px solid var(--success, #3cc878)'
                        : m.msg_type === 'claim' ? '2px solid var(--cyan, #4dd8e6)' : '2px solid transparent',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <strong style={{ fontSize: 12.5 }}>{m.author}</strong>
                        {m.msg_type !== 'say' && (
                          <span className="tag" style={{ fontSize: 9 }}>{m.msg_type}</span>
                        )}
                        {m.work_ref && (
                          <span className="tag" style={{ fontSize: 9, background: 'rgba(77,216,230,0.15)', color: 'var(--cyan, #4dd8e6)' }}>
                            {m.work_ref}
                          </span>
                        )}
                        <span className="muted-text" style={{ fontSize: 10, marginLeft: 'auto' }}>{when(m.created_at)}</span>
                      </div>
                      <div style={{ fontSize: 13, whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginTop: 2 }}>
                        {m.content}
                      </div>
                    </div>
                  ))}
                </div>

                {/* composer */}
                {active.buzz_channel_id ? (
                  <div style={{ marginTop: 10 }}>
                    {error && (
                      <div style={{ fontSize: 12, color: '#ff6b6b', display: 'flex', gap: 6, marginBottom: 6 }}>
                        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 4, marginBottom: 6, flexWrap: 'wrap' }}>
                      {WORK_TYPES.map(t => {
                        const Icon = t.icon
                        return (
                          <button key={t.value}
                            className={`btn btn-sm${workType === t.value ? ' btn-primary' : ''}`}
                            onClick={() => setWorkType(t.value)} style={{ fontSize: 11, gap: 5 }}>
                            <Icon size={11} /> {t.label}
                          </button>
                        )
                      })}
                    </div>
                    {needsRef && (
                      <input className="input" placeholder="Work reference — e.g. tro:12, issue:47, commit:abc1234"
                        value={workRef} onChange={e => setWorkRef(e.target.value)}
                        style={{ width: '100%', marginBottom: 6, fontSize: 12 }} />
                    )}
                    <textarea className="input" rows={2} value={draft}
                      placeholder={`Message ${active.name} — @ to address someone`}
                      onChange={e => setDraft(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
                      }}
                      style={{ resize: 'vertical', fontFamily: 'inherit', width: '100%' }} />
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
                      <button className="btn btn-sm btn-primary" onClick={send} disabled={sending || !draft.trim()}>
                        {sending ? <Loader2 size={12} className="spin" /> : <Send size={12} />} Send
                      </button>
                      {needsRef && !workRef.trim() && (
                        <span className="muted-text" style={{ fontSize: 10 }}>
                          A {workType} without a reference isn't counted — add one.
                        </span>
                      )}
                      {sandbox?.ok && active.sandbox_bound ? (
                        <span className="muted-text" style={{ fontSize: 10, display: 'flex', alignItems: 'center', gap: 4 }}>
                          <Play size={10} /> sandbox attached
                        </span>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <p className="muted-text" style={{ fontSize: 12, marginTop: 10 }}>
                    This workspace has no relay channel yet, so nothing can be posted to it.
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      )}

      <p className="muted-text" style={{ fontSize: 10.5, marginTop: 12, display: 'flex', gap: 5, alignItems: 'center' }}>
        <Users size={11} /> Commands run in the sandbox container — never on the Vantage host.
      </p>
    </section>
  )
}
