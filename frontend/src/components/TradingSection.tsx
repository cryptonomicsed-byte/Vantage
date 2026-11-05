import React, { useState } from 'react'
import { LayoutDashboard, TrendingUp, Briefcase, Zap, Flame, Terminal, Rocket, Users, Target } from 'lucide-react'
import TradingDashboard from './trading/TradingDashboard'
import TradingTerminal from './trading/TradingTerminal'
import MarketIntel from './trading/MarketIntel'
import DailyIntel from './trading/DailyIntel'
import DegenTrenches from './trading/DegenTrenches'
import LaunchTokenPanel from './trading/LaunchTokenPanel'
import Portfolio from './trading/Portfolio'
import TradingViewChart from './trading/TradingViewChart'
import { ProfileCardProvider } from './trading/EntityProfileCard'
import AggregateWinnerBanner from './trading/AggregateWinnerBanner'
import CopyTradingPanel from './trading/CopyTradingPanel'
import PredictionMarkets from './trading/PredictionMarkets'

const GROUPS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'analytics', label: 'Analytics', icon: TrendingUp },
  { id: 'prediction', label: 'Prediction', icon: Target },
  { id: 'daily-intel', label: 'Daily Intel', icon: Zap },
  { id: 'degen-trenches', label: 'Degen Trenches', icon: Flame },
  { id: 'launch', label: 'Launch Token', icon: Rocket },
  { id: 'copy', label: 'My Leaders', icon: Users },
  { id: 'portfolio', label: 'Portfolio', icon: Briefcase },
  { id: 'terminal', label: 'Terminal', icon: Terminal },
]

type Group = 'dashboard' | 'analytics' | 'prediction' | 'daily-intel' | 'degen-trenches' | 'launch' | 'copy' | 'portfolio' | 'terminal'

/** `?tab=<group>&market=<source>:<market_id>` — shareable links into the section. */
function parseMarketParam(): { source: string; market_id: string } | null {
  try {
    const qs = new URLSearchParams(window.location.search)
    const raw = qs.get('market')
    if (!raw) return null
    const idx = raw.indexOf(':')
    if (idx > 0) return { source: raw.slice(0, idx), market_id: raw.slice(idx + 1) }
    // A bare id is ambiguous across venues, so the caller has to say which.
    const source = qs.get('source')
    return source ? { source, market_id: raw } : null
  } catch { return null }
}

function initialGroup(): Group {
  try {
    const tab = new URLSearchParams(window.location.search).get('tab')
    return (GROUPS.some(g => g.id === tab) ? tab : 'dashboard') as Group
  } catch { return 'dashboard' }
}

export default function TradingSection() {
  const [group, setGroup] = useState<Group>(initialGroup)
  const [deepLinkMarket] = useState(parseMarketParam)

  const select = (id: Group) => {
    setGroup(id)
    try {
      const url = new URL(window.location.href)
      url.searchParams.set('tab', id)
      window.history.replaceState({}, '', url.toString())
    } catch { /* non-browser env */ }
  }

  return (
    <ProfileCardProvider>
      <div>
        <AggregateWinnerBanner />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 className="page-title" style={{ marginBottom: 0 }}>Trading</h1>
          </div>
          <div className="top-nav-tabs" style={{ flex: 'initial' }}>
            {GROUPS.map(g => (
              <button key={g.id} type="button" className={`top-nav-tab ${group === g.id ? 'active' : ''}`} onClick={() => select(g.id as Group)}>
                <g.icon size={15} /> {g.label}
              </button>
            ))}
          </div>
        </div>
        {group === 'dashboard' && <TradingDashboard onOpenPortfolio={() => select('portfolio')} />}
        {group === 'analytics' && <MarketIntel />}
        {group === 'prediction' && (
          <PredictionMarkets
            initialMarket={deepLinkMarket}
            initialTab={deepLinkMarket ? 'markets' : undefined}
          />
        )}
        {group === 'daily-intel' && <DailyIntel />}
        {group === 'degen-trenches' && <DegenTrenches />}
        {group === 'launch' && <LaunchTokenPanel />}
        {group === 'copy' && <CopyTradingPanel />}
        {group === 'portfolio' && <Portfolio />}
        {group === 'terminal' && <TradingTerminal />}
      </div>
    </ProfileCardProvider>
  )
}
