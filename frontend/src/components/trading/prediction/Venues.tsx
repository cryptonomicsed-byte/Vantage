import React from 'react'
import { AlertTriangle, Layers } from 'lucide-react'
import {
  AuthNotice, Chip, Empty, Loading, Panel, SourceRow, ago, compact, useJSON,
} from './shared'

/**
 * Venues — per-source ingest, decisions and open inventory.
 *
 * The alert that matters: a collector can ingest for weeks while every row is an
 * archived market. Totals look healthy, the tab is full of questions that
 * already resolved, and nothing on screen says so. `signals > 0 && open == 0` is
 * that failure and it is rendered as an error, not a table cell.
 */
export default function Venues() {
  const src = useJSON<{ sources: SourceRow[]; resolvable_venues: string[] }>(
    '/api/trading/prediction/sources', 60000)
  const rows = src.data?.sources || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <AuthNotice error={src.error} onRetry={src.refresh} />

      {rows.filter(r => r.signals > 0 && r.open_markets === 0).map(r => (
        <div key={r.source} style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderRadius: 10,
          border: '1px solid rgba(239,68,68,.35)', background: 'rgba(239,68,68,.08)',
          color: '#ef4444', fontSize: 12,
        }}>
          <AlertTriangle size={15} />
          <b>{r.source}</b> has {compact(r.signals)} signals and <b>0 open markets</b> — the collector is
          reading archived inventory. Every row on this venue is a question that already resolved.
        </div>
      ))}

      <Panel title="Venues" icon={<Layers size={13} />} right={
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          resolvable: {(src.data?.resolvable_venues || []).join(', ') || '—'}
        </span>
      }>
        {src.loading && rows.length === 0 && <Loading what="venues" />}
        {!src.loading && rows.length === 0 && <Empty>No venue has ingested anything yet.</Empty>}
        {rows.length > 0 && (
          <div style={{ fontSize: 11 }}>
            <div style={{ display: 'flex', gap: 8, padding: '4px 0', color: 'var(--muted)', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              <span style={{ width: 80 }}>source</span>
              <span style={{ flex: 1, textAlign: 'right' }}>signals</span>
              <span style={{ flex: 1, textAlign: 'right' }}>open</span>
              <span style={{ flex: 1, textAlign: 'right' }}>decisions</span>
              <span style={{ flex: 1, textAlign: 'right' }}>abstained</span>
              <span style={{ flex: 1, textAlign: 'right' }}>resolved</span>
              <span style={{ width: 70, textAlign: 'right' }}>last ingest</span>
            </div>
            {rows.map(r => (
              <div key={r.source} style={{ display: 'flex', gap: 8, padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)', alignItems: 'center' }}>
                <span style={{ width: 80, textTransform: 'uppercase', fontSize: 10, color: '#fff' }}>{r.source}</span>
                <span style={{ flex: 1, textAlign: 'right', fontFamily: 'monospace' }}>{compact(r.signals)}</span>
                <span style={{ flex: 1, textAlign: 'right', fontFamily: 'monospace', color: r.open_markets === 0 ? '#ef4444' : '#22c55e' }}>{compact(r.open_markets)}</span>
                <span style={{ flex: 1, textAlign: 'right', fontFamily: 'monospace' }}>{compact(r.decisions)}</span>
                <span style={{ flex: 1, textAlign: 'right', fontFamily: 'monospace', color: '#f59e0b' }}>{compact(r.abstentions)}</span>
                <span style={{ flex: 1, textAlign: 'right', fontFamily: 'monospace' }}>{compact(r.resolved)}</span>
                <span style={{ width: 70, textAlign: 'right', fontSize: 9, color: 'var(--muted)' }}>{ago(r.last_ingest)}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
        `resolvable` lists the venues the settlement readers can actually answer for. A venue outside
        that list can still be decided on, but its predictions will stay pending forever —
        which is why the Health tab distinguishes silent from stale.
      </div>
    </div>
  )
}
