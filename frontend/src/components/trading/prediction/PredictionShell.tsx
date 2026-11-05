import React, { useState } from 'react'
import {
  Activity, Gauge, HeartPulse, Layers, ListOrdered, RefreshCw, Sparkles, Target, Trophy,
} from 'lucide-react'
import Overview from './Overview'
import Markets from './Markets'
import Decisions from './Decisions'
import Calibration from './Calibration'
import ContextTab from './ContextTab'
import Venues from './Venues'
import Scores from './Scores'
import Health from './Health'
import Forecast from './Forecast'

/**
 * PredictionShell — nine lenses over one read-only pipeline.
 *
 * The single page this replaces was a dense scroll: the feed, decisions, gate,
 * context and venues all fought for the same column, and the only way to read
 * the calibration was to scroll past 80 market rows. Trading → Analytics solved
 * that with sub-tabs, so this copies the pattern rather than inventing one.
 *
 * Nothing here writes. Prediction is telemetry: the venue settles, the gate
 * grades, and no path from this surface reaches an order (enforced by
 * backend/tests/test_no_prediction_orders.py).
 */
const TABS = [
  { id: 'overview', label: 'Overview', icon: Target, Component: Overview },
  { id: 'markets', label: 'Markets', icon: Layers, Component: Markets },
  { id: 'decisions', label: 'Decisions', icon: ListOrdered, Component: Decisions },
  { id: 'calibration', label: 'Calibration', icon: Gauge, Component: Calibration },
  { id: 'context', label: 'Context', icon: Activity, Component: ContextTab },
  { id: 'venues', label: 'Venues', icon: Layers, Component: Venues },
  { id: 'scores', label: 'Scores', icon: Trophy, Component: Scores },
  { id: 'health', label: 'Health', icon: HeartPulse, Component: Health },
  { id: 'forecast', label: 'Forecast', icon: Sparkles, Component: Forecast },
] as const

type TabId = typeof TABS[number]['id']

export interface PredictionShellProps {
  /** Deep link: ?tab=markets&market=kalshi:KX-CPI-26 preselects the detail. */
  initialMarket?: { source: string; market_id: string } | null
  initialTab?: string
}

export default function PredictionShell({ initialMarket = null, initialTab }: PredictionShellProps = {}) {
  const startTab = (TABS.some(t => t.id === initialTab) ? initialTab : 'overview') as TabId
  const [tab, setTab] = useState<TabId>(startTab)
  const [nonce, setNonce] = useState(0)
  const active = TABS.find(t => t.id === tab) || TABS[0]
  const Active = active.Component as React.ComponentType<{ initialMarket?: { source: string; market_id: string } | null }>

  return (
    <div>
      <div className="top-nav-tabs" style={{
        flexWrap: 'wrap', borderBottom: '1px solid var(--border)',
        paddingBottom: 8, marginBottom: 16, alignItems: 'center',
      }}>
        {TABS.map(t => (
          <button
            key={t.id}
            type="button"
            className={`top-nav-tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            <t.icon size={14} /> {t.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setNonce(n => n + 1)}
          title="Force every panel on this tab to refetch"
          style={{
            marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 5,
            background: 'transparent', border: '1px solid rgba(255,255,255,0.08)',
            color: 'var(--muted)', borderRadius: 7, padding: '5px 9px', fontSize: 10, cursor: 'pointer',
          }}
        >
          <RefreshCw size={11} /> refresh
        </button>
      </div>

      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6, lineHeight: 1.5 }}>
        Read-only telemetry: venue prices and settlements, this system's decisions (abstentions
        included), and the outside evidence behind them. No control on this surface can place an order.
      </div>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 12, lineHeight: 1.5 }}>
        Three different gates live in this app and they do not grade the same thing: the hermetic /
        odu / REM gates govern <b>orders and memory</b>, while the Brier gate on the Calibration tab
        grades <b>settled venue outcomes</b>. A prediction can be well calibrated and still never be
        traded — nothing here feeds the trading engine.
      </div>

      {/* Remounting on tab change keeps each panel's poll cadence scoped to the
          tab that is actually visible; the nonce forces a full refetch. */}
      <div key={`${tab}-${nonce}`}><Active initialMarket={initialMarket} /></div>
    </div>
  )
}
