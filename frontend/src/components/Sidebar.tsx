import React, { useEffect, useState } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
import { Home, Compass, Zap, User, MessageSquare, Search, Settings, Shield } from 'lucide-react'
import NotificationPanel from './NotificationPanel'
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

interface SidebarProps { onSearchToggle: () => void }

export default function Sidebar({ onSearchToggle }: SidebarProps) {
  const location = useLocation()
  const section = getSection(location.pathname)
  const unreadDMs = useUnreadDMs()

  const NAV = [
    { to: '/',          icon: Home,          label: 'Feed',    sec: 'feed'    },
    { to: '/agents',    icon: Compass,        label: 'Explore', sec: 'explore' },
    { to: '/create',    icon: Zap,            label: 'Create',  sec: 'create'  },
    { to: '/dashboard', icon: User,           label: 'Me',      sec: 'me'      },
    { to: '/inbox',     icon: MessageSquare,  label: 'Inbox',   sec: 'me', badge: unreadDMs },
  ]

  return (
    <aside className="sidebar">
      <Link to="/" className="sidebar-logo" title="Vantage">⚡</Link>

      <nav className="sidebar-nav">
        {NAV.map(({ to, icon: Icon, label, sec, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={() => `sidebar-item${section === sec ? ' active' : ''}`}
          >
            <Icon size={20} />
            {badge != null && badge > 0 && (
              <span className="sidebar-badge">{badge > 99 ? '99+' : badge}</span>
            )}
            <span className="sidebar-label">{label}</span>
          </NavLink>
        ))}

        <div className="sidebar-divider" />

        <button className="sidebar-item" onClick={onSearchToggle} title="Search">
          <Search size={20} />
          <span className="sidebar-label">Search</span>
        </button>

        <NotificationPanel sidebarMode />
      </nav>

      <div className="sidebar-bottom">
        <NavLink
          to="/settings"
          className={() => `sidebar-item${section === 'settings' ? ' active' : ''}`}
        >
          <Settings size={18} />
          <span className="sidebar-label">Settings</span>
        </NavLink>
        <Link to="/ares" className="sidebar-item ares-item">
          <Shield size={18} />
          <span className="sidebar-label">Ares SOC</span>
        </Link>
      </div>
    </aside>
  )
}
