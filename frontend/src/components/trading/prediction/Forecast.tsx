import React from 'react'
import { CloudOff, Sparkles } from 'lucide-react'
import { AuthNotice, Chip, Empty, Loading, Panel, ago, pct, useJSON } from './shared'

/**
 * Forecast — what the substrate expects, read from Mycelium's `predictions`.
 *
 * Deliberately not TimesFM and not `vantage_predictor`:
 *   - `daemons/timesfm_forecast_bridge.py` forecasts Mycelium trace buckets, is
 *     not installed as a unit, and is not running.
 *   - `daemons/vantage_predictor.py` is a crypto TA engine whose output is
 *     `signals/ingest` at conviction > 0.7 — an order path, not a forecast.
 *
 * Mycelium's own table is the real thing: question, probability, resolution
 * date/criterion, and a grading loop. It is empty today, and this tab says so
 * rather than inventing a number.
 */
interface PredictionRow {
  id?: string; created_ts?: string; source?: string; question?: string
  symbol?: string; probability?: number; resolution_date?: string
  resolution_criterion?: string; status?: string; outcome?: string | null
}
interface ForecastData {
  available: boolean; count: number; predictions: PredictionRow[]
  reason?: string; gateway?: string; note?: string
}

export default function Forecast() {
  const f = useJSON<ForecastData>('/api/trading/prediction/forecast', 60000)
  const rows = f.data?.predictions || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <AuthNotice error={f.error} onRetry={f.refresh} />

      {f.data && !f.data.available && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderRadius: 10,
          border: '1px solid rgba(245,158,11,.35)', background: 'rgba(245,158,11,.08)',
          color: '#f59e0b', fontSize: 12,
        }}>
          <CloudOff size={15} />
          Substrate unreachable — {f.data.reason}. Forecasts live in Mycelium; this tab does not
          generate its own.
        </div>
      )}

      <Panel title="Recorded forecasts" icon={<Sparkles size={13} />} right={
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          {f.data?.available ? `${f.data.count} rows` : 'offline'}
        </span>
      }>
        {f.loading && !f.data && <Loading what="forecasts" />}
        {f.data?.available && rows.length === 0 && (
          <Empty>
            The substrate is reachable and holds no forecasts yet. Mycelium's `predictions` table is
            where they will appear — written by its own forecasting loop, graded against
            `resolution_criterion` when the date passes. Nothing here is stubbed to look busy.
          </Empty>
        )}
        {rows.map(r => (
          <div key={r.id || r.question} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0',
            borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: 11,
          }}>
            <Chip label={r.status || 'open'} color={r.status === 'resolved' ? '#22c55e' : '#6b7280'} />
            <span style={{ flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {r.question || r.symbol || r.id}
            </span>
            <span style={{ fontFamily: 'monospace', color: '#fff', width: 44, textAlign: 'right' }}>
              {r.probability == null ? '—' : pct(r.probability)}
            </span>
            <span style={{ fontSize: 9, color: 'var(--muted)', width: 64, textAlign: 'right' }}>
              {r.resolution_date || (r.created_ts ? ago(Math.floor(Date.parse(r.created_ts) / 1000)) : '—')}
            </span>
            {r.outcome && <Chip label={r.outcome} color={r.outcome === 'yes' ? '#22c55e' : '#ef4444'} />}
          </div>
        ))}
      </Panel>

      {f.data?.note && (
        <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
          {f.data.note} Gateway: {f.data.gateway}.
        </div>
      )}
    </div>
  )
}
