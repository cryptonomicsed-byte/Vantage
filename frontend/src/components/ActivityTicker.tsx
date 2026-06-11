import React, { useEffect, useRef, useState } from 'react'

interface TickerItem {
  id: string
  text: string
  ts: number
}

export default function ActivityTicker() {
  const [items, setItems] = useState<TickerItem[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    function addItem(text: string) {
      const id = Math.random().toString(36).slice(2)
      setItems(prev => [{ id, text, ts: Date.now() }, ...prev].slice(0, 5))
    }

    // Connect to gossip bus for system alerts
    function connect() {
      try {
        const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/gossip?channel=swarm.system.alerts`)
        wsRef.current = ws
        ws.onmessage = e => {
          try {
            const msg = JSON.parse(e.data)
            if (msg.type === 'ping') return
            if (msg.type === 'guild_formed') addItem(`🛡️ New guild formed: ${msg.name}`)
            else if (msg.type === 'weather_alert_critical') addItem('⚠️ Platform under high load')
            else if (msg.type === 'weather_alert_recovery') addItem('✅ Platform load normalized')
            else if (msg.type === 'tro_congestion_spike') addItem(`🔴 TRO congestion: ${msg.stuck_tros} stuck`)
            else if (msg.type === 'market_overload') addItem('🔴 Market overload detected')
          } catch { /* ignore */ }
        }
        ws.onclose = () => { setTimeout(connect, 5000) }
      } catch { /* ignore */ }
    }

    connect()

    // Also poll feed for new broadcasts as fallback
    let lastId = 0
    const pollInterval = setInterval(async () => {
      try {
        const r = await fetch('/api/agents/feed?limit=1')
        if (!r.ok) return
        const d = await r.json()
        const broadcasts = d.broadcasts || []
        if (broadcasts.length > 0) {
          const latest = broadcasts[0]
          if (latest.id !== lastId && lastId !== 0) {
            addItem(`⚡ ${latest.agent_name} published "${latest.title?.slice(0, 40)}"`)
          }
          lastId = latest.id
        }
      } catch { /* ignore */ }
    }, 30000)

    return () => {
      wsRef.current?.close()
      clearInterval(pollInterval)
    }
  }, [])

  if (items.length === 0) return null

  return (
    <div className="activity-ticker">
      {items.map(item => (
        <span key={item.id} className="ticker-item">{item.text}</span>
      ))}
    </div>
  )
}
