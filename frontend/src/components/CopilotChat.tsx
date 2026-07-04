import React, { useState, useRef, useEffect } from 'react'
import { Send, Bot, MessageSquare, TrendingUp, DollarSign, Bell, Navigation, AlertTriangle, X, Loader } from 'lucide-react'

type Message = {
  role: 'user' | 'assistant'
  text: string
  intent?: CopilotIntent | null
}

type CopilotIntent = {
  action: string
  target: string
  data: Record<string, any>
  confidence: number
}

type AlertItem = {
  id: number
  symbol: string
  condition_text: string
  target_price: number | null
  direction: string
  active: number
  created_at: string
}

const SUGGESTIONS = [
  'Show me the price of BTC',
  'Check my PnL',
  'Set alert when ETH hits 5000',
  'Go to trading',
  '/help',
]

const HELP_TEXT = [
  'Available commands:',
  '/help — show this list',
  '/create <prompt> — start a creation job (aliases: /video, /image, /article — same pipeline today, it doesn\'t yet branch by media type)',
  '/pipeline — list your recent creation jobs',
  '/trade <SYMBOL> <buy|sell> <QTY> — place a market order (no leverage support yet)',
  '/code <description> — post a claimable code job for the swarm (not instant generation)',
  '/audit <owner>/<repo> — run a Strix security scan on a Gitea repo',
  '/swarm invite @agent1 @agent2 [room name] — create a workspace room and invite named agents',
  'Anything else is sent to the regular Copilot assistant.',
].join('\n')

/** Slash commands hit real REST endpoints directly — simpler and more
 * predictable than routing new command types through /api/copilot/chat's
 * regex intent parser. Free text (no leading "/") is unaffected. */
async function runSlashCommand(query: string, apiKey: string): Promise<string> {
  const cmd = (query.slice(1).split(/\s+/)[0] || '').toLowerCase()
  const rest = query.slice(1 + cmd.length).trim()

  if (cmd === 'help') return HELP_TEXT
  if (!apiKey) return 'Connect your API key in Dashboard to use commands.'

  if (['create', 'video', 'image', 'article'].includes(cmd)) {
    if (!rest) return `Usage: /${cmd} <prompt>`
    const fd = new FormData()
    fd.append('prompt', rest)
    try {
      const r = await fetch('/api/agents/create', { method: 'POST', headers: { 'X-Agent-Key': apiKey }, body: fd })
      const data = await r.json()
      if (!r.ok) return `Failed to start creation job: ${data.detail || r.statusText}`
      return `Creation job #${data.job_id} started. Use /pipeline to check status.`
    } catch { return 'Network error starting creation job.' }
  }

  if (cmd === 'pipeline') {
    try {
      const r = await fetch('/api/agents/me/creation-jobs', { headers: { 'X-Agent-Key': apiKey } })
      if (!r.ok) return `Failed to load jobs: ${r.statusText}`
      const data = await r.json()
      const jobs = Array.isArray(data) ? data : (data.jobs || [])
      if (jobs.length === 0) return 'No creation jobs yet. Try /create <prompt>.'
      return jobs.slice(0, 10).map((j: any) => `#${j.id} — ${j.status} — ${(j.prompt || '').slice(0, 60)}`).join('\n')
    } catch { return 'Network error loading pipeline.' }
  }

  if (cmd === 'trade') {
    const parts = rest.split(/\s+/).filter(Boolean)
    const [symbol, side, qtyStr] = parts
    const quantity = Number(qtyStr)
    if (parts.length < 3 || !['buy', 'sell'].includes((side || '').toLowerCase()) || Number.isNaN(quantity)) {
      return 'Usage: /trade <SYMBOL> <buy|sell> <QTY>'
    }
    try {
      const r = await fetch('/api/trading/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({ symbol, side: side.toLowerCase(), quantity, chain: 'solana', order_type: 'market' }),
      })
      const data = await r.json()
      if (!r.ok) return `Order failed: ${data.detail || r.statusText}`
      return `Order placed: ${side.toLowerCase()} ${quantity} ${symbol} (order #${data.id ?? '?'}).`
    } catch { return 'Network error placing order.' }
  }

  if (cmd === 'code') {
    if (!rest) return 'Usage: /code <description>'
    const title = rest.slice(0, 80)
    try {
      const r = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({
          title, description: rest, job_type: 'code',
          tasks: [{ title, required_capability: 'implementer' }],
        }),
      })
      const data = await r.json()
      if (!r.ok) return `Failed to post job: ${data.detail || r.statusText}`
      return `Posted job #${data.id} (job_type=code) for the swarm to claim — this posts an open task, it doesn't generate code instantly.`
    } catch { return 'Network error posting job.' }
  }

  if (cmd === 'audit') {
    const m = rest.trim().match(/^([\w.-]+)\/([\w.-]+)$/)
    if (!m) return 'Usage: /audit <owner>/<repo>'
    const [, owner, name] = m
    try {
      const r = await fetch(`/api/code/repo/${owner}/${name}/scan?engine=strix`, { method: 'POST', headers: { 'X-Agent-Key': apiKey } })
      const data = await r.json()
      if (!r.ok) return `Scan failed: ${data.detail || r.statusText}`
      return `Strix scan ${data.status} (scan_id ${data.scan_id}) on ${owner}/${name}.`
    } catch { return 'Network error triggering scan.' }
  }

  if (cmd === 'swarm') {
    const inviteMatch = rest.match(/^invite\s+(.+)$/i)
    if (!inviteMatch) return 'Usage: /swarm invite @agent1 @agent2 [room name]'
    const body = inviteMatch[1].trim()
    const handles = Array.from(body.matchAll(/@([\w.-]+)/g)).map(m2 => m2[1])
    if (handles.length === 0) return 'Usage: /swarm invite @agent1 @agent2 [room name]'
    const roomName = body.replace(/@[\w.-]+/g, '').trim() || `collab-${Date.now()}`
    try {
      const r = await fetch('/api/agents/rooms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({ name: roomName }),
      })
      const room = await r.json()
      if (!r.ok) return `Failed to create room: ${room.detail || r.statusText}`
      const roomId = room.room_id
      await Promise.all(handles.map(h => {
        const fd = new FormData()
        fd.append('content', `You're invited to workspace room "${roomName}": /workspace/${roomId}`)
        fd.append('subject', 'Workspace invite')
        return fetch(`/api/agents/messages/send/${encodeURIComponent(h)}`, {
          method: 'POST', headers: { 'X-Agent-Key': apiKey }, body: fd,
        }).catch(() => {})
      }))
      return `Created room "${roomName}" (#${roomId}) and invited ${handles.map(h => '@' + h).join(', ')}.`
    } catch { return 'Network error creating room.' }
  }

  return `Unknown command /${cmd}. Try /help.`
}

function IntentBadge({ action, confidence }: { action: string; confidence: number }) {
  const color = confidence > 0.7 ? 'var(--cyan)' : confidence > 0.4 ? 'var(--warning)' : 'var(--muted)'
  return (
    <span style={{ fontSize: 10, color, border: `1px solid ${color}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.3 }}>
      {action} {(confidence * 100).toFixed(0)}%
    </span>
  )
}

function IntentResult({ intent }: { intent: CopilotIntent }) {
  if (!intent) return null
  const { action, target, data } = intent

  if (action === 'navigate' && data.path) {
    return (
      <div>
        <p style={{ marginBottom: 8 }}>Navigating to <strong>{target}</strong></p>
        <a href={data.path} className="btn btn-primary btn-sm" style={{ textDecoration: 'none', display: 'inline-flex' }}>
          <Navigation size={13} /> Go to {target}
        </a>
      </div>
    )
  }

  if (action === 'show_price') {
    const price = data.price != null ? `$${Number(data.price).toLocaleString()}` : '—'
    const change = data.change_24h != null ? Number(data.change_24h).toFixed(2) : null
    return (
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <DollarSign size={16} style={{ color: 'var(--cyan)' }} />
          <strong>{target}</strong>
          <span style={{ fontSize: 18, fontWeight: 700 }}>{price}</span>
          {change != null && (
            <span style={{ color: parseFloat(change) >= 0 ? 'var(--green)' : 'var(--danger)', fontSize: 13 }}>
              {change}%
            </span>
          )}
        </div>
      </div>
    )
  }

  if (action === 'check_pnl') {
    return (
      <div>
        <p>Your P&L data is available on the <a href="/trading" style={{ color: 'var(--cyan)' }}>Trading</a> page.</p>
        <a href="/trading" className="btn btn-cyan btn-sm" style={{ marginTop: 6, textDecoration: 'none', display: 'inline-flex' }}>
          <TrendingUp size={13} /> View Portfolio
        </a>
      </div>
    )
  }

  if (action === 'place_trade') {
    return (
      <div>
        <p style={{ marginBottom: 6 }}>Ready to <strong>{data.side}</strong> <strong>{target}</strong></p>
        {data.price != null && <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>Current price: ${Number(data.price).toLocaleString()}</p>}
        <a href="/trading" className="btn btn-cyan btn-sm" style={{ textDecoration: 'none', display: 'inline-flex' }}>
          <TrendingUp size={13} /> Open Trading
        </a>
      </div>
    )
  }

  if (action === 'set_alert') {
    return (
      <div>
        <p>Alert condition: <strong>{data.condition || target}</strong></p>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>Alert will be created when you confirm.</p>
      </div>
    )
  }

  if (action === 'unknown') {
    return <p style={{ color: 'var(--muted)' }}>I didn't understand that. Try one of the suggestions below.</p>
  }

  return <p style={{ color: 'var(--muted)' }}>Parsed: {action} → {target}</p>
}

export default function CopilotChat() {
  const [messages, setMessages] = useState<Message[]>([{
    role: 'assistant',
    text: 'This Copilot acts as the agent you\'re connected as — it drives Vantage on your behalf. Ask it to check prices, run a backtest, scan yields/arbitrage, place a paper trade, or view your live P&L.',
  }])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [showAlerts, setShowAlerts] = useState(false)
  const [agentName, setAgentName] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // The Copilot is the connected agent — surface its identity.
  useEffect(() => {
    if (!apiKey) return
    fetch('/api/copilot/whoami', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setAgentName(d.agent || ''))
      .catch(() => {})
  }, [apiKey])

  async function loadAlerts() {
    if (!apiKey) return
    try {
      const r = await fetch('/api/copilot/alerts', { headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) setAlerts(await r.json())
    } catch {}
  }

  async function send(text: string) {
    if (!text.trim() || loading) return
    const query = text.trim()
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: query }])
    setLoading(true)

    if (query.startsWith('/')) {
      const reply = await runSlashCommand(query, apiKey)
      setMessages(prev => [...prev, { role: 'assistant', text: reply, intent: null }])
      setLoading(false)
      return
    }

    try {
      const r = await fetch('/api/copilot/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
        body: JSON.stringify({ text: query }),
      })
      if (!r.ok) {
        setMessages(prev => [...prev, { role: 'assistant', text: `Error: ${r.status} ${r.statusText}`, intent: null }])
        return
      }
      const data = await r.json()
      const intent: CopilotIntent = data.intent
      let reply = ''
      if (intent.action === 'unknown') {
        reply = 'I didn\'t catch that. Try something like "show BTC price" or "go to trading".'
      } else if (intent.action === 'navigate') {
        reply = `Navigating to ${intent.target}…`
      } else if (intent.action === 'show_price') {
        const price = intent.data?.price
        reply = price != null ? `${intent.target} is at $${Number(price).toLocaleString()}` : `Couldn't fetch price for ${intent.target}`
      } else if (intent.action === 'check_pnl') {
        reply = 'Here\'s a link to your portfolio.'
      } else if (intent.action === 'place_trade') {
        reply = `Ready to ${intent.data?.side || 'trade'} ${intent.target}.`
      } else if (intent.action === 'set_alert') {
        reply = 'I\'ll set that alert for you.'
        if (apiKey) {
          try {
            await fetch('/api/copilot/alerts', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey },
              body: JSON.stringify({ condition: intent.data?.condition || query, symbol: intent.target }),
            })
            reply = 'Alert created!'
            loadAlerts()
          } catch { reply = 'Failed to create alert.' }
        }
      } else {
        reply = `Parsed as ${intent.action} → ${intent.target}`
      }
      setMessages(prev => [...prev, { role: 'assistant', text: reply, intent }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', text: 'Network error. Is the backend running?', intent: null }])
    }
    setLoading(false)
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }

  function deleteAlert(id: number) {
    if (!apiKey) return
    fetch(`/api/copilot/alerts/${id}`, { method: 'DELETE', headers: { 'X-Agent-Key': apiKey } })
      .then(r => { if (r.ok) loadAlerts() })
      .catch(() => {})
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <h1 className="page-title" style={{ marginBottom: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Bot size={22} /> Copilot
          {agentName && <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--muted)', fontFamily: 'Inter, sans-serif' }}>· acting as <strong style={{ color: 'var(--purple-bright)' }}>{agentName}</strong></span>}
        </h1>
        {apiKey && (
          <button
            className={`btn btn-ghost btn-sm`}
            onClick={() => { loadAlerts(); setShowAlerts(s => !s) }}
          >
            <Bell size={13} /> {showAlerts ? 'Hide' : 'Alerts'} {alerts.length > 0 && `(${alerts.length})`}
          </button>
        )}
      </div>

      {showAlerts && (
        <div className="glass" style={{ marginBottom: 16, padding: 12, maxHeight: 200, overflowY: 'auto' }}>
          <h3 style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-hi)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Active Alerts</h3>
          {alerts.length === 0 && <p style={{ fontSize: 12, color: 'var(--muted)' }}>No alerts set.</p>}
          {alerts.map(a => (
            <div key={a.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
              <div>
                {a.symbol && <strong style={{ color: 'var(--cyan)' }}>{a.symbol}</strong>}
                {a.condition_text && <span style={{ color: 'var(--muted-hi)', marginLeft: 6 }}>{a.condition_text}</span>}
                {a.target_price != null && <span style={{ color: 'var(--muted)' }}> at ${a.target_price}</span>}
              </div>
              <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px', fontSize: 10 }} onClick={() => deleteAlert(a.id)}>
                <X size={10} />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="glass" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {messages.map((m, i) => (
            <div key={i} style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: m.role === 'user' ? 'flex-end' : 'flex-start',
            }}>
              <div style={{
                maxWidth: '80%',
                background: m.role === 'user' ? 'rgba(138,75,255,0.15)' : 'rgba(255,255,255,0.03)',
                border: `1px solid ${m.role === 'user' ? 'rgba(138,75,255,0.2)' : 'var(--border)'}`,
                borderRadius: 12,
                padding: '10px 14px',
                fontSize: 13,
                lineHeight: 1.6,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: m.intent ? 8 : 0 }}>
                  {m.role === 'assistant' ? <Bot size={14} style={{ color: 'var(--purple-bright)', flexShrink: 0 }} /> : <MessageSquare size={14} style={{ color: 'var(--cyan)', flexShrink: 0 }} />}
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>{m.role === 'user' ? 'You' : 'Copilot'}</span>
                </div>
                <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{m.text}</p>
                {m.intent && (
                  <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                    <div style={{ marginBottom: 6 }}>
                      <IntentBadge action={m.intent.action} confidence={m.intent.confidence} />
                    </div>
                    <IntentResult intent={m.intent} />
                  </div>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--muted)', fontSize: 13, padding: '0 4px' }}>
              <Loader size={14} className="spin" /> Thinking…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {messages.length === 1 && (
          <div style={{ padding: '0 16px 12px' }}>
            <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>Suggestions</p>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {SUGGESTIONS.map(s => (
                <button key={s} className="btn btn-ghost btn-sm" onClick={() => send(s)} style={{ fontSize: 11 }}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <input
          style={{ flex: 1 }}
          placeholder="Ask me anything…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          disabled={loading}
        />
        <button className="btn btn-primary" onClick={() => send(input)} disabled={loading || !input.trim()}>
          <Send size={14} />
        </button>
      </div>
    </div>
  )
}
