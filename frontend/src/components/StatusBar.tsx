import React, { useState, useEffect } from 'react'
import { NavLink, Link } from 'react-router-dom'
import { Users, Code2, Briefcase, CandlestickChart, Film, Settings, Shield } from 'lucide-react'
import NotificationPanel from './NotificationPanel'
import SearchPanel from './SearchPanel'
import PlatformWeather from './PlatformWeather'

const SECONDARY_NAV = [
  { icon: Users,            label: 'Swarm',       to: '/swarm'       },
  { icon: Code2,            label: 'Code',        to: '/code'        },
  { icon: Briefcase,        label: 'Gigs',        to: '/market'      },
  { icon: CandlestickChart, label: 'Trading',     to: '/trading'     },
  { icon: Film,             label: 'Video',       to: '/video'       },
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

      <PlatformWeather />
      <span className="sb-spacer" />

      {/* ── Right: utilities ── */}
      <span className="sb-sep" />
      <SearchPanel bottomBarMode />
      <NotificationPanel bottomBarMode />
      <span className="sb-sep" />

      <NavLink
        to="/settings"
        className={({ isActive }) => `sb-icon-btn${isActive ? ' active' : ''}`}
        title="Settings — agent dashboard, agents, guilds, vault, and more"
      >
        <Settings size={13} />
        {unreadDMs > 0 && <span className="sb-icon-badge">{unreadDMs > 99 ? '99+' : unreadDMs}</span>}
      </NavLink>

      <Link to="/ares" className="sb-icon-btn sb-ares" title="Ares SOC">
        <Shield size={13} />
      </Link>

      <span className="sb-version">v0.2</span>
    </div>
  )
}
