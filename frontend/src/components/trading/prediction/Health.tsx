import React from 'react'
import { HeartPulse } from 'lucide-react'
import {
  AuthNotice, Chip, Empty, HealthRow, Loading, Panel, STATUS_COLOR,
  ago, compact, pct, useJSON,
} from './shared'

/**
 * Health — collector liveness with the failure named.
 *
 * Three states, each meaning something specific:
 *   ok      ingested within 2 poll intervals and has open inventory
 *   stale   ingesting, but nothing open — the archived-feed failure
 *   silent  nothing ingested for 2 intervals
 *
 * This exists because "the daemon is running" and "the feed is useful" are
 * different claims, and the second one was false for weeks while the first was
 * true.
 */
export default function Health() {
  const h = useJSON<{ sources: HealthRow[] }>('/api/trading/prediction/health', 30000)
  const rows = h.data?.sources || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <AuthNotice error={h.error} onRetry={h.refresh} />

      <Panel title="Collector health" icon={<HeartPulse size={13} />} right={
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          {rows.filter(r => r.status === 'ok').length}/{rows.length} ok
        </span>
      }>
        {h.loading && rows.length === 0 && <Loading what="health" />}
        {!h.loading && rows.length === 0 && <Empty>No collectors have written a signal yet.</Empty>}
        {rows.map(r => (
          <div key={r.source} style={{
            display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0',
            borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: 11,
          }}>
            <span style={{ width: 84, textTransform: 'uppercase', fontSize: 10, color: '#fff' }}>{r.source}</span>
            <Chip label={r.status} color={STATUS_COLOR[r.status] || '#6b7280'} />
            <span style={{ color: 'var(--muted)' }}>
              {compact(r.signals)} signals · {compact(r.open_markets)} open ({pct(r.open_ratio)})
            </span>
            <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 10 }}>
              last ingest {ago(r.last_ingest)}
              {r.expected_interval_seconds ? ` · expected every ${Math.round(r.expected_interval_seconds / 60)}m` : ''}
            </span>
          </div>
        ))}
      </Panel>

      <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
        A silent collector is a stopped daemon. A stale collector is a running daemon reading the wrong
        window — worse, because the process looks healthy and the data looks fresh.
      </div>
    </div>
  )
}
