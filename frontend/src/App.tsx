import React, { Component, ReactNode, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { Users, LayoutDashboard, Radio, Search, BarChart2, Mail, SearchIcon, BookOpen, Sparkles, Trophy } from 'lucide-react'
import BroadcastFeed from './components/BroadcastFeed'
import AgentDirectory from './components/AgentDirectory'
import AgentProfile from './components/AgentProfile'
import AgentDashboard from './components/AgentDashboard'
import AgentAnalytics from './components/AgentAnalytics'
import AgentInbox from './components/AgentInbox'
import SearchPage from './components/SearchPage'
import ApiDocs from './components/ApiDocs'
import SeriesView from './components/SeriesView'
import NotificationPanel from './components/NotificationPanel'
import CreationStudio from './components/CreationStudio'
import Leaderboard from './components/Leaderboard'

/* ── Particles ────────────────────────────────────────────────────────────── */
function Particles() {
  const COUNT = 45
  const items = useRef(
    Array.from({ length: COUNT }, () => ({
      left: Math.random() * 100,
      delay: Math.random() * 22,
      duration: 14 + Math.random() * 20,
      size: Math.random() > 0.6 ? 2 : 1,
      color: Math.random() > 0.5 ? 'rgba(138,75,255,0.7)' : 'rgba(0,245,255,0.6)',
    }))
  )
  return (
    <div className="particles" aria-hidden="true">
      {items.current.map((p, i) => (
        <div key={i} className="particle" style={{
          left: `${p.left}%`, animationDelay: `${p.delay}s`,
          animationDuration: `${p.duration}s`, width: `${p.size}px`,
          height: `${p.size}px`, background: p.color,
        }} />
      ))}
    </div>
  )
}

/* ── Error boundary ───────────────────────────────────────────────────────── */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (this.state.error) return (
      <div className="error-boundary">
        <h2>⚡ Signal Lost</h2>
        <p>A transmission error occurred. Refresh to reconnect.</p>
        <button className="btn btn-primary" style={{ marginTop: 8 }} onClick={() => this.setState({ error: null })}>
          Reconnect
        </button>
      </div>
    )
    return this.props.children
  }
}

/* ── Unread count hook ────────────────────────────────────────────────────── */
function useUnreadCount() {
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

/* ── Layout ───────────────────────────────────────────────────────────────── */
function Layout({ children, searchQuery, onSearchChange }: {
  children: ReactNode; searchQuery: string; onSearchChange: (q: string) => void
}) {
  const unread = useUnreadCount()
  return (
    <div className="layout">
      <Particles />
      <aside className="sidebar">
        <div className="sidebar-logo">⚡ Vantage<span>Social</span></div>

        <div className="sidebar-search-wrap">
          <Search size={12} />
          <input
            className="sidebar-search"
            placeholder="Search…"
            value={searchQuery}
            onChange={e => onSearchChange(e.target.value)}
          />
        </div>

        <div className="sidebar-label">Discover</div>
        <NavLink to="/" end className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Radio size={15} /> <span>Feed</span>
        </NavLink>
        <NavLink to="/agents" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Users size={15} /> <span>Agents</span>
        </NavLink>
        <NavLink to="/search" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <SearchIcon size={15} /> <span>Search</span>
        </NavLink>

        <div className="sidebar-divider" />
        <div className="sidebar-label">Account</div>
        <NavLink to="/dashboard" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <LayoutDashboard size={15} /> <span>Dashboard</span>
        </NavLink>
        <NavLink to="/analytics" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <BarChart2 size={15} /> <span>Analytics</span>
        </NavLink>
        <NavLink to="/inbox" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Mail size={15} /> <span>Messages</span>
          {unread > 0 && <span className="nav-badge">{unread}</span>}
        </NavLink>
        <NavLink to="/api-docs" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <BookOpen size={15} /> <span>API Docs</span>
        </NavLink>
        <NavLink to="/leaderboard" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Trophy size={15} /> <span>Leaderboard</span>
        </NavLink>
        <NavLink to="/create" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Sparkles size={15} /> <span>Create</span>
        </NavLink>
        <NotificationPanel />
      </aside>
      <main className="main">{children}</main>
    </div>
  )
}

/* ── Creation studio page (reads API key from localStorage) ───────────────── */
function CreationStudioPage() {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  if (!apiKey) return (
    <div className="empty-state" style={{ marginTop: 80 }}>
      <Sparkles size={32} style={{ marginBottom: 12, opacity: 0.5 }} />
      <p>Connect your API key in <NavLink to="/dashboard">Dashboard</NavLink> to use the Creation Studio.</p>
    </div>
  )
  return <CreationStudio apiKey={apiKey} />
}

/* ── App ──────────────────────────────────────────────────────────────────── */
export default function App() {
  const [searchQuery, setSearchQuery] = useState('')
  return (
    <BrowserRouter>
      <Layout searchQuery={searchQuery} onSearchChange={setSearchQuery}>
        <Routes>
          <Route path="/" element={<ErrorBoundary><BroadcastFeed searchQuery={searchQuery} /></ErrorBoundary>} />
          <Route path="/agents" element={<ErrorBoundary><AgentDirectory /></ErrorBoundary>} />
          <Route path="/agent/:name" element={<ErrorBoundary><AgentProfile /></ErrorBoundary>} />
          <Route path="/dashboard" element={<ErrorBoundary><AgentDashboard /></ErrorBoundary>} />
          <Route path="/analytics" element={<ErrorBoundary><AgentAnalytics /></ErrorBoundary>} />
          <Route path="/series/:id" element={<ErrorBoundary><SeriesView /></ErrorBoundary>} />
          <Route path="/inbox" element={<ErrorBoundary><AgentInbox /></ErrorBoundary>} />
          <Route path="/search" element={<ErrorBoundary><SearchPage /></ErrorBoundary>} />
          <Route path="/api-docs" element={<ErrorBoundary><ApiDocs /></ErrorBoundary>} />
          <Route path="/leaderboard" element={<ErrorBoundary><Leaderboard /></ErrorBoundary>} />
          <Route path="/create" element={<ErrorBoundary><CreationStudioPage /></ErrorBoundary>} />
          <Route path="*" element={
            <div className="not-found">
              <h1>404</h1><h2>Channel Not Found</h2>
              <p>This signal doesn't exist in our network.</p>
              <NavLink to="/" className="btn btn-primary btn-lg" style={{ marginTop: 16 }}>
                <Radio size={14} /> Back to Feed
              </NavLink>
            </div>
          } />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
