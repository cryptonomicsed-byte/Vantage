import React, { useState, useEffect } from 'react'
import { NavLink, Link } from 'react-router-dom'
import { Users, Code2, CandlestickChart, Clapperboard, Settings, Shield, Radio, Globe } from 'lucide-react'
import NotificationPanel from './NotificationPanel'
import SearchPanel from './SearchPanel'
import PlatformWeather from './PlatformWeather'

// 2026-07-27: Cinema/Audio folded into Studio (one bottom tab, three
// SubNav rows inside -- Collab/Cinema/Audio, see utils/navigation.ts),
// Gigs removed as its own tab (Marketplace/Rankings moved into Swarm's
// SubNav instead, since they're swarm-wide concerns).
const SECONDARY_NAV = [
  { icon: Users,            label: 'Swarm',       to: '/swarm'       },
  { icon: Code2,            label: 'Code',        to: '/code'        },
  { icon: CandlestickChart, label: 'Trading',     to: '/trading'     },
  { icon: Clapperboard,     label: 'Studio',      to: '/video'       },
  { icon: Radio,            label: 'Buzz',        to: '/buzz'        },
]

function useUnreadDMs(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    const apiKey = localStorage.getItem('vantage_api_key')
    if (!apiKey) return
    function poll() {
      fetch('/api/agents/messages/unread-count', { headers: { 'X-Agent-Key': apiKey! } })
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setCount(d.unread))
        .catch(() => {})
    }
    poll()
    const t = setInterval(poll, 60000)
    return () => clearInterval(t)
  }, [])
  return count
}

export default function StatusBar() {
  const [agentName, setAgentName] = useState(() => localStorage.getItem('vantage_agent_name') || '')
  const [connected, setConnected] = useState(false)
  const unreadDMs = useUnreadDMs()

  useEffect(() => {
    function sync() {
      setConnected(!!localStorage.getItem('vantage_api_key'))
      setAgentName(localStorage.getItem('vantage_agent_name') || '')
    }
    sync()
    window.addEventListener('storage', sync)
    const t = setInterval(sync, 5000)
    return () => { window.removeEventListener('storage', sync); clearInterval(t) }
  }, [])

  return (
    <div className="status-bar">
      {/* ── Left: agent identity — routes to the home feed, not the dashboard ── */}
      <Link to="/" className="sb-agent-pill">
        <span className={`sb-dot${connected ? ' on' : ''}`} />
        <span className="sb-agent-name">
          {connected ? (agentName || 'agent') : 'offline'}
        </span>
      </Link>

      <span className="sb-sep" />

      {/* ── Center: secondary nav ── */}
      {SECONDARY_NAV.map(({ icon: Icon, label, to }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) => `sb-nav-btn${isActive ? ' active' : ''}`}
        >
          <Icon size={11} />
          <span>{label}</span>
        </NavLink>
      ))}

      <span className="sb-spacer" />

      {/* ── Right: utilities ── */}
      <span className="sb-sep" />
      <SearchPanel bottomBarMode />
      <NotificationPanel bottomBarMode />
      <PlatformWeather />
      <span className="sb-sep" />

      <NavLink
        to="/settings"
        className={({ isActive }) => `sb-icon-btn${isActive ? ' active' : ''}`}
        title="Settings — agent dashboard, agents, guilds, vault, and more"
      >
        <Settings size={13} />
        {unreadDMs > 0 && <span className="sb-icon-badge">{unreadDMs > 99 ? '99+' : unreadDMs}</span>}
      </NavLink>

      <NavLink
        to="/federation"
        className={({ isActive }) => `sb-icon-btn${isActive ? ' active' : ''}`}
        title="Federation — Nostr · Freenet · Sui · Arweave"
      >
        <Globe size={13} />
      </NavLink>

      <Link to="/ares" className="sb-icon-btn sb-ares" title="Ares SOC">
        <Shield size={13} />
      </Link>

      <span className="sb-version">v0.2</span>
    </div>
  )
}
