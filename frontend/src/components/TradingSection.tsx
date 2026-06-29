import TradingViewChart from "./trading/TradingViewChart"
import React, { useState } from 'react'
import { TrendingUp, Briefcase, CandlestickChart } from 'lucide-react'
import MarketIntel from './trading/MarketIntel'
import Portfolio from './trading/Portfolio'

// ══════════════════════════════════════════════════════════════════════════════
// Trading — a first-class main-app section (sidebar + status bar via AppLayout).
// Two groups:
//   • Market Intel — public market-data dashboard relocated out of the admin
//     (ARES) console. Unauthenticated, same endpoints as before.
//   • Portfolio — the agent-scoped trading workspace on /api/trading, with an
//     honest-ledger / simulated-paper mode toggle.
// ══════════════════════════════════════════════════════════════════════════════

const GROUPS = [
  { id: 'intel',     label: 'Market Intel', icon: TrendingUp },
  { id: "portfolio", label: "Portfolio",    icon: Briefcase },
  { id: "chart",     label: "Chart",        icon: CandlestickChart },
]

export default function TradingSection() {
  const [group, setGroup] = useState<'intel' | 'portfolio' | 'chart'>('intel')
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 20 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Trading</h1>
        <div className="top-nav-tabs" style={{ flex: 'initial' }}>
          {GROUPS.map(g => (
            <button key={g.id} type="button" className={`top-nav-tab ${group === g.id ? 'active' : ''}`} onClick={() => setGroup(g.id as 'intel' | 'portfolio' | 'chart')}>
              <g.icon size={15} /> {g.label}
            </button>
          ))}
        </div>
      </div>

      {group === "intel" && <MarketIntel />}
      {group === "chart" && <TradingViewChart />}
      {group === "portfolio" && <Portfolio />}
    </div>
  )
}
