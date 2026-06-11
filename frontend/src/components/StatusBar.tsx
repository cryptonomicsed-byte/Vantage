import React, { useState, useEffect } from 'react'
import { NavLink, Link } from 'react-router-dom'
import { Search, Trophy, BarChart2, GitBranch, FileText, Settings, Shield } from 'lucide-react'
import NotificationPanel from './NotificationPanel'
import PlatformWeather from './PlatformWeather'

interface Props {
  onSearchToggle: () => void
  searchOpen: boolean
}

const SECONDARY_NAV = [
  { icon: Trophy,    label: 'Leaderboard', to: '/leaderboard' },
  { icon: BarChart2, label: 'Analytics',   to: '/analytics'   },
  { icon: GitBranch, label: 'Pipeline',    to: '/pipeline'    },
  { icon: FileText,  label: 'API Docs',    to: '/api-docs'    },
]

export default function StatusBar({ onSearchToggle, searchOpen }: Props) {
  const [agentName, setAgentName] = useState(() => localStorage.getItem('vantage_agent_name') || '')
  const [connected, setConnected] = useState(false)

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
      {/* ── Left: agent identity ── */}
      <Link to="/dashboard" className="sb-agent-pill">
        <span className={`sb-dot${connected ? ' on' : ''}`} />
        <span className="sb-agent-name">
          {connected ? (agentName || 'agent') : 'offline'}
        </span>
      </Link>

      <span className="sb-sep" />

      {/* ── Center: secondary nav ── */}
      <button
        className={`sb-nav-btn${searchOpen ? ' active' : ''}`}
        onClick={onSearchToggle}
        title="Search (Ctrl+K)"
      >
        <Search size={11} />
        <span>Search</span>
      </button>

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
      <NotificationPanel bottomBarMode />
      <span className="sb-sep" />

      <NavLink
        to="/settings"
        className={({ isActive }) => `sb-icon-btn${isActive ? ' active' : ''}`}
        title="Settings"
      >
        <Settings size={13} />
      </NavLink>

      <Link to="/ares" className="sb-icon-btn sb-ares" title="Ares SOC">
        <Shield size={13} />
      </Link>

      <span className="sb-version">v0.2</span>
    </div>
  )
}
