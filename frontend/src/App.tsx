import React, { Component, ReactNode, useRef, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { Radio, GitBranch, Sparkles, Search, X } from 'lucide-react'
import BroadcastFeed from './components/BroadcastFeed'
import HomeFeed from './components/HomeFeed'
import AgentDirectory from './components/AgentDirectory'
import AgentProfile from './components/AgentProfile'
import AgentDashboard from './components/AgentDashboard'
import AgentAnalytics from './components/AgentAnalytics'
import AgentInbox from './components/AgentInbox'
import SearchPage from './components/SearchPage'
import ApiDocs from './components/ApiDocs'
import SeriesView from './components/SeriesView'
import CreationStudio from './components/CreationStudio'
import Leaderboard from './components/Leaderboard'
import WorkflowCanvas from './components/WorkflowCanvas'
import AresSOC from './components/AresSOC'
import TradingSection from './components/TradingSection'
import SwarmMap from './components/SwarmMap'
import MarketVelocity from './components/MarketVelocity'
import KnowledgeExplorer from './components/KnowledgeExplorer'
import Settings from './components/Settings'
import VaultExplorer from './components/VaultExplorer'
import AgentWorkspace from './components/AgentWorkspace'
import IntentHeatmap from './components/IntentHeatmap'
import CopilotChat from './components/CopilotChat'
import ObserverMode from './components/ObserverMode'
import GuildProfile from './components/GuildProfile'
import GuildDirectory from './components/GuildDirectory'
import ActivityTicker from './components/ActivityTicker'
import AgentCollectivesPage from './pages/AgentCollectivesPage'
import VideoStudio from './components/VideoStudio'
import Sidebar from './components/Sidebar'
import StatusBar from './components/StatusBar'
import SubNav from './components/SubNav'
import { getSection, SUB_NAV } from './utils/navigation'

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
      <div className="error-boundary" style={{ padding: 40, fontFamily: "monospace" }}>
        <h2>Signal Lost</h2>
        <p style={{ color: "#ff6666", margin: "10px 0", whiteSpace: "pre-wrap", maxWidth: "800px", overflow: "auto", fontSize: 13, background: "#111", padding: 10, borderRadius: 8 }}>
          {this.state.error.message}
        </p>
        <details style={{ marginTop: 10, fontSize: 12, color: "#888" }}>
          <summary>Stack trace</summary>
          <pre style={{ background: "#111", padding: 10, borderRadius: 8, overflow: "auto", maxHeight: 400 }}>
            {this.state.error.stack}
          </pre>
        </details>
        <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={() => this.setState({ error: null })}>
          Retry
        </button>
      </div>
    )
    return this.props.children
  }
}

/* ── API-key gated page wrappers ──────────────────────────────────────────── */
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

function PipelinePage() {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  if (!apiKey) return (
    <div className="empty-state" style={{ marginTop: 80 }}>
      <GitBranch size={32} style={{ marginBottom: 12, opacity: 0.5 }} />
      <p>Connect your API key in <NavLink to="/dashboard">Dashboard</NavLink> to use the Pipeline Canvas.</p>
    </div>
  )
  return <WorkflowCanvas apiKey={apiKey} />
}

function MarketPage() {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  return <MarketVelocity apiKey={apiKey || undefined} />
}

/* ── AppLayout — wraps all non-Ares pages with Sidebar + StatusBar ───────── */
interface AppLayoutProps {
  searchQuery: string
  onSearchChange: (q: string) => void
  searchOpen: boolean
  onSearchToggle: () => void
}

function AppLayout({ searchQuery, onSearchChange, searchOpen, onSearchToggle }: AppLayoutProps) {
  const location = useLocation()
  const section = getSection(location.pathname)
  const subLinks = SUB_NAV[section]
  const [observerEnabled, setObserverEnabled] = useState(false)

  return (
    <div className="app-shell">
      <Particles />
      <Sidebar />
      <div className="content-area">
        {searchOpen && (
          <div className="search-overlay">
            <Search size={14} className="search-overlay-icon" />
            <input
              autoFocus
              className="search-overlay-input"
              placeholder="Search broadcasts, agents…"
              value={searchQuery}
              onChange={e => onSearchChange(e.target.value)}
              onKeyDown={e => e.key === 'Escape' && onSearchToggle()}
            />
            <button className="search-overlay-close" onClick={onSearchToggle}>
              <X size={14} />
            </button>
          </div>
        )}
        <div id="feed-topbar-slot" />
        <ActivityTicker />
        {subLinks && <SubNav links={subLinks} />}
        <ObserverMode enabled={observerEnabled} onToggle={() => setObserverEnabled(o => !o)} />
        <main className="main">
          <Routes>
            <Route path="/" element={<ErrorBoundary><HomeFeed /></ErrorBoundary>} />
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
            <Route path="/pipeline" element={<ErrorBoundary><PipelinePage /></ErrorBoundary>} />
            <Route path="/swarm" element={<ErrorBoundary><SwarmMap /></ErrorBoundary>} />
            <Route path="/market" element={<ErrorBoundary><MarketPage /></ErrorBoundary>} />
            <Route path="/trading" element={<ErrorBoundary><TradingSection /></ErrorBoundary>} />
            <Route path="/knowledge" element={<ErrorBoundary><KnowledgeExplorer /></ErrorBoundary>} />
            <Route path="/settings" element={<ErrorBoundary><Settings /></ErrorBoundary>} />
            <Route path="/workspace" element={<ErrorBoundary><AgentWorkspace /></ErrorBoundary>} />
            <Route path="/workspace/:roomId" element={<ErrorBoundary><AgentWorkspace /></ErrorBoundary>} />
            <Route path="/heatmap" element={<ErrorBoundary><IntentHeatmap /></ErrorBoundary>} />
            <Route path="/copilot" element={<ErrorBoundary><CopilotChat /></ErrorBoundary>} />
            <Route path="/guilds" element={<ErrorBoundary><GuildDirectory /></ErrorBoundary>} />
            <Route path="/guild/:slug" element={<ErrorBoundary><GuildProfile /></ErrorBoundary>} />
            <Route path="/collectives" element={<ErrorBoundary><AgentCollectivesPage /></ErrorBoundary>} />
            <Route path="/vault" element={<ErrorBoundary><VaultExplorer /></ErrorBoundary>} />
            <Route path="/video" element={<ErrorBoundary><VideoStudio /></ErrorBoundary>} />
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
        </main>
      </div>
      <StatusBar onSearchToggle={onSearchToggle} searchOpen={searchOpen} />
    </div>
  )
}

/* ── App ──────────────────────────────────────────────────────────────────── */
export default function App() {
  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  function toggleSearch() {
    setSearchOpen(o => {
      if (o) setSearchQuery('')
      return !o
    })
  }
  return (
    <BrowserRouter>
      <Routes>
        {/* Ares SOC — full-screen, no sidebar */}
        <Route path="/ares" element={<AresSOC />} />

        {/* All other routes use AppLayout (Sidebar + StatusBar + content) */}
        <Route path="*" element={
          <AppLayout
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchOpen={searchOpen}
            onSearchToggle={toggleSearch}
          />
        } />
      </Routes>
    </BrowserRouter>
  )
}
