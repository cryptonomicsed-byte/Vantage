import React, { useState } from 'react'
import { Gauge, TrendingUp } from 'lucide-react'
import {
  AuthNotice, CalibrationData, Chip, Empty, GateCard, HistoryData, Loading, Panel,
  Toggle, compact, pct, useJSON,
} from './shared'

/**
 * Calibration — the gate, plus the series behind it.
 *
 * The chart is abstention-first on purpose. With zero scored directional calls
 * there is no Brier curve to draw, and drawing a flat 0.0 line would be the
 * original bug in pixels: a perfect-looking number over an empty set. Days with
 * no scored call render as gaps, and the caption says why.
 *
 * Bars are hand-rolled divs, matching AgentAnalytics and MarketIntel. recharts is
 * in package.json and imported by nothing, so adding it here would introduce a
 * second chart idiom for one screen.
 */
export default function Calibration() {
  const [window, setWindow] = useState<'7d' | '30d' | '90d'>('30d')
  const cal = useJSON<CalibrationData>('/api/trading/prediction/calibration', 60000)
  const hist = useJSON<HistoryData>(`/api/trading/prediction/calibration/history?window=${window}`, 60000)

  const series = hist.data?.series || []
  const peak = Math.max(1, ...series.map(d => d.recorded))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <AuthNotice error={cal.error || hist.error} onRetry={() => { cal.refresh(); hist.refresh() }} />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.3fr)', gap: 16 }}>
        <Panel title="Gate" icon={<Gauge size={13} />}>
          <GateCard cal={cal.data} />
        </Panel>

        <Panel title="Daily history" icon={<TrendingUp size={13} />} right={
          <div style={{ display: 'flex', gap: 6 }}>
            {(['7d', '30d', '90d'] as const).map(w => (
              <Toggle key={w} on={window === w} label={w} onClick={() => setWindow(w)} />
            ))}
          </div>
        }>
          {hist.loading && series.length === 0 && <Loading what="history" />}
          {hist.data && (
            <>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
                <div style={{ fontFamily: 'monospace', fontSize: 24, fontWeight: 700, color: hist.data.vacuous ? '#f59e0b' : '#fff' }}>
                  {hist.data.totals.brier == null ? '—' : hist.data.totals.brier.toFixed(3)}
                </div>
                <Chip label={hist.data.vacuous ? 'VACUOUS' : `${compact(hist.data.totals.scored)} scored`}
                  color={hist.data.vacuous ? '#f59e0b' : '#22c55e'} />
                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted)' }}>
                  abstention rate {pct(hist.data.totals.abstention_rate, 1)}
                </span>
              </div>

              {/* Two series per day: recorded (bar height) with abstentions layered
                  in amber and scored calls in green. A day that is entirely amber
                  is a day the engine declined everything, which is the truth. */}
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 92, marginBottom: 6 }}>
                {series.map(d => {
                  const h = Math.max(2, (d.recorded / peak) * 92)
                  const abstainShare = d.recorded ? d.abstained / d.recorded : 0
                  return (
                    <div key={d.date} title={`${d.date}: ${d.recorded} recorded, ${d.abstained} abstained, ${d.scored} scored${d.brier != null ? `, Brier ${d.brier.toFixed(3)}` : ''}`}
                      style={{ flex: 1, height: h, borderRadius: 1, overflow: 'hidden', background: 'rgba(34,197,94,0.45)', display: 'flex', flexDirection: 'column' }}>
                      <div style={{ height: `${abstainShare * 100}%`, background: 'rgba(245,158,11,0.55)' }} />
                    </div>
                  )
                })}
              </div>
              <div style={{ display: 'flex', gap: 12, fontSize: 9, color: 'var(--muted)' }}>
                <span><span style={{ color: '#f59e0b' }}>▮</span> abstained</span>
                <span><span style={{ color: '#22c55e' }}>▮</span> directional</span>
                <span style={{ marginLeft: 'auto' }}>{series.length} days · {compact(hist.data.totals.recorded)} recorded</span>
              </div>

              {hist.data.vacuous && (
                <div style={{ marginTop: 10, padding: 8, borderRadius: 8, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', fontSize: 10, color: '#f59e0b', lineHeight: 1.5 }}>
                  No scored directional calls in this window. The bars are real decisions; the Brier
                  number is not available, and deliberately not shown as 0.000.
                </div>
              )}

              {!hist.data.vacuous && (
                <div style={{ marginTop: 10 }}>
                  <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--muted)', marginBottom: 4 }}>days with a score</div>
                  {series.filter(d => d.scored > 0).slice(-8).map(d => (
                    <div key={d.date} style={{ display: 'flex', gap: 8, fontSize: 10, padding: '2px 0' }}>
                      <span style={{ color: 'var(--muted)', width: 78 }}>{d.date}</span>
                      <span style={{ fontFamily: 'monospace', width: 52 }}>{d.brier?.toFixed(3)}</span>
                      <span style={{ color: 'var(--muted)' }}>{d.scored} scored / {d.recorded} recorded</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </Panel>
      </div>
    </div>
  )
}
