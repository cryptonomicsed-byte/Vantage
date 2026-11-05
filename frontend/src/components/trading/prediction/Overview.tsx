import React from 'react'
import { Gauge, Target } from 'lucide-react'
import {
  AuthNotice, CalibrationData, Empty, GateCard, Market, OverviewData, Panel, StatTile,
  ago, compact, label, pct, until, useJSON,
} from './shared'

/**
 * Overview — the one screen that answers "is this pipeline alive, and what does
 * it think". Four counts, the gate, and the markets closing soonest.
 *
 * The counts are deliberately paired with their 24h delta: a total of 98,500
 * signals tells you nothing until you know how many arrived today, and that
 * pairing is what exposed the archived-feed bug.
 */
export default function Overview() {
  const o = useJSON<OverviewData>('/api/trading/prediction/overview', 60000)
  const cal = useJSON<CalibrationData>('/api/trading/prediction/calibration', 60000)
  const next = useJSON<{ markets: Market[] }>(
    '/api/trading/prediction/markets?open_only=true&limit=8', 60000)

  const d = o.data
  const markets = next.data?.markets || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <AuthNotice error={o.error || cal.error} onRetry={() => { o.refresh(); cal.refresh() }} />

      <div className="ares-stat-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 12 }}>
        <StatTile label="Signals" value={compact(d?.signals.total)} sub={d ? `+${compact(d.signals.last_24h)} today` : undefined} />
        <StatTile label="Decisions" value={compact(d?.decisions.total)} sub={d ? `${compact(d.decisions.abstentions)} abstentions` : undefined} />
        <StatTile
          label="Open markets"
          value={compact(d?.markets.open_now)}
          sub={d ? `${compact(d.markets.closing_next_24h)} close within 24h` : undefined}
        />
        <StatTile
          label="Brier"
          value={d?.calibration.vacuous ? '—' : (d?.calibration.brier_score ?? 0).toFixed(3)}
          color={d?.calibration.vacuous ? '#f59e0b' : undefined}
          sub={d?.calibration.vacuous ? 'vacuous — nothing scored' : `${compact(d?.calibration.scored)} scored`}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.2fr) minmax(0,1fr)', gap: 16 }}>
        <Panel title="Calibration gate" icon={<Gauge size={13} />}>
          <GateCard cal={cal.data} />
        </Panel>

        <Panel title="Closing soonest" icon={<Target size={13} />} right={
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>{markets.length} open</span>
        }>
          {markets.length === 0 && (
            <Empty>
              No open markets. If the feed is ingesting but nothing here is open, the collector is
              reading archived inventory — check the Health tab before trusting the count above.
            </Empty>
          )}
          {markets.map(m => (
            <div key={`${m.source}:${m.market_id}`} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0',
              borderBottom: '1px solid rgba(255,255,255,0.04)',
            }}>
              <span style={{ fontSize: 9, color: 'var(--muted)', width: 60, textTransform: 'uppercase' }}>{m.source}</span>
              <span style={{ flex: 1, minWidth: 0, fontSize: 11, color: '#ffffffe6', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={m.question}>
                {label(m)}
              </span>
              <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#fff' }}>{pct(m.implied_yes ?? m.yes_price)}</span>
              <span style={{ fontSize: 10, color: 'var(--muted)', width: 46, textAlign: 'right' }}>{until(m.end_date)}</span>
            </div>
          ))}
          {d?.markets.next_close && (
            <div style={{ marginTop: 8, fontSize: 10, color: 'var(--muted)' }}>
              next close {until(d.markets.next_close)} · updated {ago(o.updatedAt ? Math.floor(o.updatedAt / 1000) : null)}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}
