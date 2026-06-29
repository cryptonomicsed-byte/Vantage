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
]

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
    text: 'I\'m your Vantage Copilot. Ask me to check prices, place trades, view P&L, set alerts, or navigate anywhere.',
  }])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [showAlerts, setShowAlerts] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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
