import React, { useMemo, useState } from 'react'
import { ListOrdered } from 'lucide-react'
import {
  AuthNotice, Chip, Decision, Empty, Loading, Panel, Toggle, VERDICT_COLOR,
  ago, label, pct, useJSON,
} from './shared'

/**
 * Decisions — the full stream, abstentions included.
 *
 * 99.9% of this system's decisions are skips, so the filters are the feature:
 * `abstained` is what turns this from a near-empty feed into the actual record
 * of what the engine declined and why. Hiding abstentions would make the system
 * look busy and tell you nothing.
 */
export default function Decisions() {
  const [verdict, setVerdict] = useState<string>('')
  const [source, setSource] = useState<string>('')
  const [abstainedOnly, setAbstainedOnly] = useState(false)
  const [minConf, setMinConf] = useState(0)

  const path = useMemo(() => {
    const p = new URLSearchParams({ limit: '80' })
    if (verdict) p.set('verdict', verdict)
    if (source) p.set('source', source)
    if (abstainedOnly) p.set('abstained', 'true')
    if (minConf > 0) p.set('min_confidence', String(minConf))
    return `/api/trading/prediction/decisions?${p.toString()}`
  }, [verdict, source, abstainedOnly, minConf])

  const feed = useJSON<{ decisions: Decision[]; count: number }>(path, 60000)
  const rows = feed.data?.decisions || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <AuthNotice error={feed.error} onRetry={feed.refresh} />

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <div style={{ display: 'flex', gap: 6 }}>
          {['', 'yes', 'no', 'skip'].map(v => (
            <Toggle key={v || 'any'} on={verdict === v} label={v || 'any verdict'} onClick={() => setVerdict(v)} />
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {['', 'kalshi', 'polymarket'].map(s => (
            <Toggle key={s || 'all'} on={source === s} label={s || 'all venues'} onClick={() => setSource(s)} />
          ))}
        </div>
        <Toggle on={abstainedOnly} label="abstentions only" onClick={() => setAbstainedOnly(v => !v)}
          title="A skip is a recorded decision, not an absence of one." />
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto', fontSize: 10, color: 'var(--muted)' }}>
          min confidence
          <input
            type="range" min={0} max={1} step={0.05} value={minConf}
            onChange={e => setMinConf(Number(e.target.value))}
            style={{ width: 110 }}
          />
          <span style={{ fontFamily: 'monospace', color: '#fff', width: 32 }}>{pct(minConf)}</span>
        </div>
      </div>

      <Panel title="Decision stream" icon={<ListOrdered size={13} />} right={
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>{feed.data?.count ?? rows.length} rows</span>
      }>
        {feed.loading && rows.length === 0 && <Loading what="decisions" />}
        {!feed.loading && rows.length === 0 && (
          <Empty>No decisions match these filters. If `abstentions only` is off and this is still
            empty, the signal daemon has not run since the last database reset.</Empty>
        )}
        {rows.map(r => (
          <div key={r.signal_id} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
            borderBottom: '1px solid rgba(255,255,255,0.04)',
          }}>
            <Chip label={r.verdict} color={VERDICT_COLOR[r.verdict] || '#6b7280'} />
            <span style={{ flex: 1, minWidth: 0, fontSize: 11, color: '#ffffffe6', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={r.question || ''}>
              {r.question ? label({ question: r.question }) : r.signal_id}
            </span>
            <span style={{ fontSize: 9, color: 'var(--muted)', width: 56, textTransform: 'uppercase' }}>{r.source || '—'}</span>
            <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#ffffffb3', width: 46, textAlign: 'right' }}>{pct(r.confidence)}</span>
            {r.abstained === 1 && <Chip label="abstained" color="#f59e0b" />}
            {r.outcome && <Chip label={r.outcome} color={VERDICT_COLOR[r.outcome] || '#6b7280'} />}
            <span style={{ fontSize: 9, color: 'var(--muted)', width: 52, textAlign: 'right' }}>{ago(r.predicted_ts)}</span>
          </div>
        ))}
      </Panel>

      <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
        Reason strings are stored with every decision (`direction=… conviction=… should_trade=…`) so an
        abstention can be told apart from a model failure without re-running anything.
      </div>
    </div>
  )
}
