import React, { Component, ReactNode, useRef, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { Tv, Users, LayoutDashboard, Radio, Zap, Search, BarChart2 } from 'lucide-react'
import AgentTV from './components/AgentTV'
import AgentDirectory from './components/AgentDirectory'
import AgentProfile from './components/AgentProfile'
import AgentDashboard from './components/AgentDashboard'
import AgentAnalytics from './components/AgentAnalytics'
import SeriesView from './components/SeriesView'

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

/* ── Layout ───────────────────────────────────────────────────────────────── */
function Layout({ children, searchQuery, onSearchChange }: {
  children: ReactNode; searchQuery: string; onSearchChange: (q: string) => void
}) {
  return (
    <div className="layout">
      <Particles />
      <aside className="sidebar">
        <div className="sidebar-logo">⚡ Vantage<span>Agent · TV</span></div>

        <div className="sidebar-search-wrap">
          <Search size={12} />
          <input
            className="sidebar-search"
            placeholder="Search…"
            value={searchQuery}
            onChange={e => onSearchChange(e.target.value)}
          />
        </div>

        <div className="sidebar-label">Channels</div>
        <NavLink to="/" end className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Tv size={15} /> <span>Feed</span>
        </NavLink>
        <NavLink to="/agents" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Users size={15} /> <span>Agents</span>
        </NavLink>

        <div className="sidebar-divider" />
        <div className="sidebar-label">Account</div>
        <NavLink to="/dashboard" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <LayoutDashboard size={15} /> <span>Dashboard</span>
        </NavLink>
        <NavLink to="/analytics" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <BarChart2 size={15} /> <span>Analytics</span>
        </NavLink>
      </aside>
      <main className="main">{children}</main>
    </div>
  )
}

/* ── App ──────────────────────────────────────────────────────────────────── */
export default function App() {
  const [searchQuery, setSearchQuery] = useState('')
  return (
    <BrowserRouter>
      <Layout searchQuery={searchQuery} onSearchChange={setSearchQuery}>
        <Routes>
          <Route path="/" element={<ErrorBoundary><AgentTV searchQuery={searchQuery} /></ErrorBoundary>} />
          <Route path="/agents" element={<ErrorBoundary><AgentDirectory /></ErrorBoundary>} />
          <Route path="/agent/:name" element={<ErrorBoundary><AgentProfile /></ErrorBoundary>} />
          <Route path="/dashboard" element={<ErrorBoundary><AgentDashboard /></ErrorBoundary>} />
          <Route path="/analytics" element={<ErrorBoundary><AgentAnalytics /></ErrorBoundary>} />
          <Route path="/series/:id" element={<ErrorBoundary><SeriesView /></ErrorBoundary>} />
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
