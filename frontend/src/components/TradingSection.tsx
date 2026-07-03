import React, { useState } from 'react'
import { LayoutDashboard, TrendingUp, Briefcase } from 'lucide-react'
import TradingDashboard from './trading/TradingDashboard'
import MarketIntel from './trading/MarketIntel'
import Portfolio from './trading/Portfolio'

// ══════════════════════════════════════════════════════════════════════════════
// Trading — a first-class main-app section (sidebar + status bar via AppLayout).
// Three groups:
//   • Dashboard — the default landing view: native chart (+ agent Pine overlays,
//     with a TradingView-engine toggle for pairs beyond native OHLC coverage),
//     the live cross-source signal feed, market vitals, and a portfolio snapshot,
//     all on one screen.
//   • Analytics — the deep-dive lenses (arbitrage, alpha, yields, DEX, whales,
//     sentiment, debate, chain health, sources, raw intel) for when one signal
//     needs more context than the Dashboard rail gives.
//   • Portfolio — the agent-scoped trading workspace on /api/trading, with an
//     honest-ledger / simulated-paper mode toggle.
// ══════════════════════════════════════════════════════════════════════════════

const GROUPS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'analytics', label: 'Analytics', icon: TrendingUp },
  { id: 'portfolio', label: 'Portfolio', icon: Briefcase },
]

export default function TradingSection() {
  const [group, setGroup] = useState<'dashboard' | 'analytics' | 'portfolio'>('dashboard')
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 20 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Trading</h1>
        <div className="top-nav-tabs" style={{ flex: 'initial' }}>
          {GROUPS.map(g => (
            <button key={g.id} type="button" className={`top-nav-tab ${group === g.id ? 'active' : ''}`} onClick={() => setGroup(g.id as 'dashboard' | 'analytics' | 'portfolio')}>
              <g.icon size={15} /> {g.label}
            </button>
          ))}
        </div>
      </div>

      {group === 'dashboard' && <TradingDashboard onOpenPortfolio={() => setGroup('portfolio')} />}
      {group === 'analytics' && <MarketIntel />}
      {group === 'portfolio' && <Portfolio />}
    </div>
  )
}
