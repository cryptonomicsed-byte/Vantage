import React, { useState } from 'react'
import { LayoutDashboard, TrendingUp, Briefcase, Zap, Flame } from 'lucide-react'
import TradingDashboard from './trading/TradingDashboard'
import MarketIntel from './trading/MarketIntel'
import DailyIntel from './trading/DailyIntel'
import DegenTrenches from './trading/DegenTrenches'
import Portfolio from './trading/Portfolio'

const GROUPS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'analytics', label: 'Analytics', icon: TrendingUp },
  { id: 'daily-intel', label: 'Daily Intel', icon: Zap },
  { id: 'degen-trenches', label: 'Degen Trenches', icon: Flame },
  { id: 'portfolio', label: 'Portfolio', icon: Briefcase },
]

export default function TradingSection() {
  const [group, setGroup] = useState<'dashboard' | 'analytics' | 'daily-intel' | 'degen-trenches' | 'portfolio'>('dashboard')
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 20 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Trading</h1>
        <div className="top-nav-tabs" style={{ flex: 'initial' }}>
          {GROUPS.map(g => (
            <button key={g.id} type="button" className={`top-nav-tab ${group === g.id ? 'active' : ''}`} onClick={() => setGroup(g.id as any)}>
              <g.icon size={15} /> {g.label}
            </button>
          ))}
        </div>
      </div>
      {group === 'dashboard' && <TradingDashboard onOpenPortfolio={() => setGroup('portfolio')} />}
      {group === 'analytics' && <MarketIntel />}
      {group === 'daily-intel' && <DailyIntel />}
      {group === 'degen-trenches' && <DegenTrenches />}
      {group === 'portfolio' && <Portfolio />}
    </div>
  )
}
