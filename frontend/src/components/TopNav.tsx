import React, { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Search, Shield, X } from 'lucide-react'
import NotificationPanel from './NotificationPanel'
import { getSection } from '../utils/navigation'

function useUnreadDMs(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    const apiKey = localStorage.getItem('vantage_api_key')
    if (!apiKey) return
    function fetch_() {
      fetch('/api/agents/messages/unread-count', { headers: { 'X-Agent-Key': apiKey! } })
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setCount(d.unread))
        .catch(() => {})
    }
    fetch_()
    const t = setInterval(fetch_, 60000)
    return () => clearInterval(t)
  }, [])
  return count
}

interface TopNavProps {
  searchQuery: string
  onSearchChange: (q: string) => void
}

const TABS: Array<{ section: string; label: string; to: string }> = [
  { section: 'feed',     label: 'Feed',     to: '/' },
  { section: 'explore',  label: 'Explore',  to: '/agents' },
  { section: 'create',   label: 'Create',   to: '/create' },
  { section: 'me',       label: 'Me',       to: '/dashboard' },
  { section: 'settings', label: 'Settings', to: '/settings' },
]

export default function TopNav({ searchQuery, onSearchChange }: TopNavProps) {
  const location = useLocation()
  const section = getSection(location.pathname)
  const unreadDMs = useUnreadDMs()
  const [searchOpen, setSearchOpen] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (searchOpen) {
      searchRef.current?.focus()
    } else {
      onSearchChange('')
    }
  }, [searchOpen])

  useEffect(() => {
    setSearchOpen(false)
  }, [location.pathname])

  return (
    <nav className="top-nav">
      <Link to="/" className="top-nav-logo">
        ⚡ Vantage<span>Social</span>
      </Link>

      <div className="top-nav-tabs">
        {TABS.map(tab => (
          <Link
            key={tab.section}
            to={tab.to}
            className={'top-nav-tab' + (section === tab.section ? ' active' : '')}
          >
            {tab.label}
            {tab.section === 'me' && unreadDMs > 0 && (
              <span className="nav-badge" style={{ marginLeft: 6 }}>
                {unreadDMs > 99 ? '99+' : unreadDMs}
              </span>
            )}
          </Link>
        ))}
      </div>

      <div className="top-nav-utils">
        {searchOpen ? (
          <div className="top-nav-search-wrap">
            <Search size={13} className="top-nav-search-icon" />
            <input
              ref={searchRef}
              className="top-nav-search"
              placeholder="Search broadcasts, agents…"
              value={searchQuery}
              onChange={e => onSearchChange(e.target.value)}
              onKeyDown={e => e.key === 'Escape' && setSearchOpen(false)}
            />
            <button
              className="top-nav-search-close"
              onClick={() => setSearchOpen(false)}
              aria-label="Close search"
            >
              <X size={13} />
            </button>
          </div>
        ) : (
          <button
            className="top-nav-icon-btn"
            onClick={() => setSearchOpen(true)}
            aria-label="Open search"
          >
            <Search size={16} />
          </button>
        )}

        <NotificationPanel />

        <Link
          to="/ares"
          className="top-nav-icon-btn ares-btn"
          aria-label="Ares SOC"
          title="Ares Security Operations Center"
        >
          <Shield size={16} />
        </Link>
      </div>
    </nav>
  )
}
