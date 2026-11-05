import React, { useState } from 'react'
import { Activity } from 'lucide-react'
import {
  AuthNotice, Chip, ContextFeed, DIRECTION_COLOR, Empty, Loading, Panel, Toggle,
  ago, contextSummary, pct, useJSON,
} from './shared'

/**
 * Context — the outside evidence: macro prints, forecasts, crowd probabilities.
 *
 * These never settle a market; the venue does. They are what the decision layer
 * reads before it speaks, shown here so a decision's evidence is inspectable
 * rather than implied. The cadence column is the useful one: FRED moves monthly,
 * a forecast hourly, a play-money crowd in minutes, and a source that has gone
 * quiet is visible before it starts skewing decisions.
 */
export default function ContextTab() {
  const [source, setSource] = useState('')
  const path = `/api/trading/prediction/context?limit=60${source ? `&source=${source}` : ''}`
  const feed = useJSON<ContextFeed>(path, 60000)
  const rows = feed.data?.signals || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <AuthNotice error={feed.error} onRetry={feed.refresh} />

      <div style={{ display: 'flex', gap: 6 }}>
        {['', 'fred', 'open-meteo', 'manifold'].map(s => (
          <Toggle key={s || 'all'} on={source === s} label={s || 'all sources'} onClick={() => setSource(s)} />
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted)', alignSelf: 'center' }}>
          {feed.data?.count ?? rows.length} signals
        </span>
      </div>

      <Panel title="Evidence feed" icon={<Activity size={13} />}>
        {feed.loading && rows.length === 0 && <Loading what="context" />}
        {!feed.loading && rows.length === 0 && (
          <Empty>
            No outside evidence stored yet. The context daemon polls FRED (6h), Open-Meteo (30m) and
            Manifold (15m); a source that is down leaves the others running.
          </Empty>
        )}
        {rows.map(c => (
          <div key={`${c.source}:${c.market_id}`} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
            borderBottom: '1px solid rgba(255,255,255,0.04)',
          }}>
            <Chip label={c.source} color="#00f5ff" />
            <span style={{ flex: 1, minWidth: 0, fontSize: 11, color: '#ffffffe6', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {contextSummary(c)}
            </span>
            <Chip label={c.direction} color={DIRECTION_COLOR[c.direction] || '#6b7280'} />
            <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#ffffff99', width: 36, textAlign: 'right' }}>{pct(c.confidence)}</span>
            <span style={{ fontSize: 9, color: 'var(--muted)', width: 50, textAlign: 'right' }}>{ago(c.observed_ts)}</span>
          </div>
        ))}
      </Panel>

      {feed.data && feed.data.by_source.length > 0 && (
        <Panel title="Source health">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 8, fontSize: 10 }}>
            {feed.data.by_source.map(b => (
              <div key={`${b.source}:${b.signal_type}`} style={{ padding: 8, borderRadius: 8, background: 'rgba(255,255,255,0.03)' }}>
                <div style={{ color: '#fff', fontWeight: 600 }}>{b.source}</div>
                <div style={{ color: 'var(--muted)' }}>{b.signal_type} · {b.n} rows</div>
                <div style={{ color: 'var(--muted)' }}>
                  every {Math.round((feed.data?.cadence_seconds?.[b.source] || 0) / 60)}m · seen {ago(b.last_seen)}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
        Evidence only. The venue price stays the source of record and the Brier gate only ever scores
        venue settlements — nothing here can move a settlement.
      </div>
    </div>
  )
}
