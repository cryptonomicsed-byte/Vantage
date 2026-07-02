import React, { useEffect, useState } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
import { Home, Compass, Zap, User, MessageSquare, Network, TrendingUp, BookOpen, CandlestickChart, Bot, Film, Code2, BrainCircuit } from 'lucide-react'
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

const SECONDARY_PATHS = ['/swarm', '/market', '/knowledge', '/trading']

export default function Sidebar() {
  const location = useLocation()
  const section = getSection(location.pathname)
  const unreadDMs = useUnreadDMs()

  function active(to: string): boolean {
    if (to === '/') return location.pathname === '/'
    if (to === '/inbox') return location.pathname === '/inbox'
    if (to === '/dashboard') return section === 'me' && location.pathname !== '/inbox'
    if (to === '/agents') return section === 'explore' && !SECONDARY_PATHS.includes(location.pathname)
    return location.pathname === to
  }

  const PRIMARY = [
    { to: '/',          icon: Home,          label: 'Feed',    badge: 0         },
    { to: '/agents',    icon: Compass,       label: 'Explore', badge: 0         },
    { to: '/create',    icon: Zap,           label: 'Create',  badge: 0         },
    { to: '/dashboard', icon: User,          label: 'Me',      badge: 0         },
    { to: '/inbox',     icon: MessageSquare, label: 'Inbox',   badge: unreadDMs },
  ]

  const SECONDARY = [
    { to: '/copilot',   icon: Bot,              label: 'Copilot'   },
    { to: '/swarm',     icon: Network,          label: 'Swarm'     },
    { to: '/market',    icon: TrendingUp,       label: 'Market'    },
    { to: '/knowledge', icon: BookOpen,         label: 'Knowledge' },
    { to: '/trading',   icon: CandlestickChart, label: 'Trading'   },
    { to: '/code',      icon: Code2,            label: 'Code'      },
    { to: '/vault',     icon: BrainCircuit,     label: 'Vault'     },
    { to: '/video',     icon: Film,              label: 'Video'     },
  ]

  return (
    <aside className="sidebar">
      <Link to="/" className="sidebar-logo" title="Vantage">⚡</Link>

      <nav className="sidebar-nav">
        {PRIMARY.map(({ to, icon: Icon, label, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={() => `sidebar-item${active(to) ? ' active' : ''}`}
          >
            <Icon size={18} />
            {badge > 0 && <span className="sidebar-badge">{badge > 99 ? '99+' : badge}</span>}
            <span className="sidebar-label">{label}</span>
          </NavLink>
        ))}

        <div className="sidebar-divider" />

        {SECONDARY.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={() => `sidebar-item${active(to) ? ' active' : ''}`}
          >
            <Icon size={16} />
            <span className="sidebar-label">{label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
