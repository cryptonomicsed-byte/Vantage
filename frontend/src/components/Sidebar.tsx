import React, { useEffect, useState } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
import { Home, Users, CandlestickChart, Code2, Film } from 'lucide-react'
import { getSection } from '../utils/navigation'

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

// Five sections. Everything else lives as sub-tabs inside a section
// (see utils/navigation.ts SUB_NAV) or in the bottom status bar.
export default function Sidebar() {
  const location = useLocation()
  const section = getSection(location.pathname)
  const unreadDMs = useUnreadDMs()

  const SECTIONS = [
    { to: '/',          icon: Home,             label: 'Home',    match: 'feed',    badge: 0         },
    { to: '/dashboard', icon: Users,            label: 'Agents',  match: 'agents',  badge: unreadDMs },
    { to: '/trading',   icon: CandlestickChart, label: 'Trading', match: 'trading', badge: 0         },
    { to: '/code',      icon: Code2,            label: 'Code',    match: 'code',    badge: 0         },
    { to: '/video',     icon: Film,             label: 'Video',   match: 'video',   badge: 0         },
  ]

  return (
    <aside className="sidebar">
      <Link to="/" className="sidebar-logo" title="Vantage">⚡</Link>

      <nav className="sidebar-nav">
        {SECTIONS.map(({ to, icon: Icon, label, match, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={() => `sidebar-item${section === match ? ' active' : ''}`}
          >
            <Icon size={18} />
            {badge > 0 && <span className="sidebar-badge">{badge > 99 ? '99+' : badge}</span>}
            <span className="sidebar-label">{label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
