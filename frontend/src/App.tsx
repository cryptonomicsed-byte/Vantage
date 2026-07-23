import React, { Component, ReactNode, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { Radio } from 'lucide-react'
import HomeFeed from './components/HomeFeed'
import AgentDirectory from './components/AgentDirectory'
import AgentProfile from './components/AgentProfile'
import AgentDashboard from './components/AgentDashboard'
import AgentAnalytics from './components/AgentAnalytics'
import AgentInbox from './components/AgentInbox'
import SearchPage from './components/SearchPage'
import ApiDocs from './components/ApiDocs'
import SeriesView from './components/SeriesView'
import Leaderboard from './components/Leaderboard'
import AresSOC from './components/AresSOC'
import TradingSection from './components/TradingSection'
import SwarmMap from './components/SwarmMap'
import MarketVelocity from './components/MarketVelocity'
import KnowledgeExplorer from './components/KnowledgeExplorer'
import Settings from './components/Settings'
import NeuralVault from './components/NeuralVault'
import AgentWorkspace from './components/AgentWorkspace'
import IntentHeatmap from './components/IntentHeatmap'
import CopilotChat from './components/CopilotChat'
import ObserverMode from './components/ObserverMode'
import GuildProfile from './components/GuildProfile'
import GuildDirectory from './components/GuildDirectory'
import ActivityTicker from './components/ActivityTicker'
import AgentCollectivesPage from './pages/AgentCollectivesPage'
import Landing from './pages/Landing'
import ProductionCollab from './components/ProductionCollab'
import Cinema from './components/Cinema'
import AudioSection from './components/AudioSection'
import CodeDashboard from './components/CodeDashboard'
import AnalyticsDashboard from './components/AnalyticsDashboard'
import RepoProfilePage from './components/RepoProfilePage'
import CopilotDock from './components/CopilotDock'
import StatusBar from './components/StatusBar'
import SubNav from './components/SubNav'
import { getSection, SUB_NAV } from './utils/navigation'
import { ensureAgentKey, hasStoredAgentKey } from './utils/ensureAgentKey'

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
  state: { error: Error | null } = { error: null }
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
function MarketPage() {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  return <MarketVelocity apiKey={apiKey || undefined} />
}

/* ── AppLayout — wraps all non-Ares pages with StatusBar ─────────────────── */
function AppLayout() {
  const location = useLocation()
  const section = getSection(location.pathname)
  const subLinks = SUB_NAV[section]
  const [observerEnabled, setObserverEnabled] = useState(false)

  // Every API call needs X-Agent-Key now (PR #39) — a first-time visitor has
  // none stored, so give them a throwaway agent identity automatically rather
  // than showing a login wall. Returning visitors already have a key in
  // localStorage, so this resolves synchronously and renders with no flash.
  const [keyReady, setKeyReady] = useState(hasStoredAgentKey)
  useEffect(() => {
    if (keyReady) return
    ensureAgentKey().finally(() => setKeyReady(true))
  }, [keyReady])

  if (!keyReady) {
    return (
      <div className="app-shell">
        <Particles />
        <div className="content-area" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
          <div className="loading-text">Connecting…</div>
        </div>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <Particles />
      <div className="content-area">
        <div id="feed-topbar-slot" />
        <ActivityTicker />
        {subLinks && <SubNav links={subLinks} />}
        <ObserverMode enabled={observerEnabled} onToggle={() => setObserverEnabled(o => !o)} />
        <CopilotDock />
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
            <Route path="/swarm" element={<ErrorBoundary><SwarmMap /></ErrorBoundary>} />
            <Route path="/market" element={<ErrorBoundary><MarketPage /></ErrorBoundary>} />
            <Route path="/trading" element={<ErrorBoundary><TradingSection /></ErrorBoundary>} />
            <Route path="/knowledge" element={<ErrorBoundary><KnowledgeExplorer /></ErrorBoundary>} />
            <Route path="/settings" element={<ErrorBoundary><Settings /></ErrorBoundary>} />
            <Route path="/workspace" element={<ErrorBoundary><AgentWorkspace /></ErrorBoundary>} />
            <Route path="/workspace/:roomId" element={<ErrorBoundary><AgentWorkspace /></ErrorBoundary>} />
            <Route path="/heatmap" element={<ErrorBoundary><IntentHeatmap /></ErrorBoundary>} />
            <Route path="/copilot" element={<ErrorBoundary><CopilotChat /></ErrorBoundary>} />
            <Route path="/welcome" element={<ErrorBoundary><Landing /></ErrorBoundary>} />
            <Route path="/guilds" element={<ErrorBoundary><GuildDirectory /></ErrorBoundary>} />
            <Route path="/guild/:slug" element={<ErrorBoundary><GuildProfile /></ErrorBoundary>} />
            <Route path="/collectives" element={<ErrorBoundary><AgentCollectivesPage /></ErrorBoundary>} />
            <Route path="/vault" element={<ErrorBoundary><NeuralVault /></ErrorBoundary>} />
            <Route path="/video" element={<ErrorBoundary><ProductionCollab /></ErrorBoundary>} />
            <Route path="/studio" element={<ErrorBoundary><ProductionCollab /></ErrorBoundary>} />
            <Route path="/cinema" element={<ErrorBoundary><Cinema /></ErrorBoundary>} />
            <Route path="/audio" element={<ErrorBoundary><AudioSection /></ErrorBoundary>} />
            <Route path="/code" element={<ErrorBoundary><CodeDashboard /></ErrorBoundary>} />
            <Route path="/code/:owner/:name" element={<ErrorBoundary><RepoProfilePage /></ErrorBoundary>} />
            <Route path="/creator-analytics" element={<ErrorBoundary><AnalyticsDashboard /></ErrorBoundary>} />
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
      <StatusBar />
    </div>
  )
}

/* ── App ──────────────────────────────────────────────────────────────────── */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Ares SOC — full-screen, admin-only */}
        <Route path="/ares" element={<AresSOC />} />

        {/* All other routes use AppLayout (StatusBar + content, no sidebar) */}
        <Route path="*" element={<AppLayout />} />
      </Routes>
    </BrowserRouter>
  )
}
