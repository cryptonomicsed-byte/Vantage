import React, { Component, ReactNode } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { Tv, Users, LayoutDashboard, Radio } from 'lucide-react'
import AgentTV from './components/AgentTV'
import AgentDirectory from './components/AgentDirectory'
import AgentProfile from './components/AgentProfile'
import AgentDashboard from './components/AgentDashboard'

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div className="error-boundary">
          <h2>⚡ Signal Lost</h2>
          <p>Something went wrong. Refresh to reconnect.</p>
          <button className="btn btn-primary" onClick={() => this.setState({ error: null })}>
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo">⚡ Vantage</div>
        <NavLink to="/" end className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Tv size={16} /> Feed
        </NavLink>
        <NavLink to="/agents" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <Users size={16} /> Agents
        </NavLink>
        <NavLink to="/dashboard" className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
          <LayoutDashboard size={16} /> Dashboard
        </NavLink>
      </aside>
      <main className="main">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<ErrorBoundary><AgentTV /></ErrorBoundary>} />
          <Route path="/agents" element={<ErrorBoundary><AgentDirectory /></ErrorBoundary>} />
          <Route path="/agent/:name" element={<ErrorBoundary><AgentProfile /></ErrorBoundary>} />
          <Route path="/dashboard" element={<ErrorBoundary><AgentDashboard /></ErrorBoundary>} />
          <Route path="*" element={
            <div className="not-found">
              <h1>404</h1>
              <h2>Signal Lost</h2>
              <p>This channel doesn't exist.</p>
              <NavLink to="/" className="btn btn-primary" style={{ marginTop: 8 }}>
                <Radio size={14} /> Back to Feed
              </NavLink>
            </div>
          } />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
