import React, { useMemo, useState } from 'react'
import { ExternalLink, Filter, Search } from 'lucide-react'
import {
  AuthNotice, Chip, DIRECTION_COLOR, Empty, Loading, Market, MarketDetailData, Panel,
  Toggle, VERDICT_COLOR, ago, contextSummary, label, money, pct, until, useJSON,
} from './shared'

/**
 * Markets — the feed, with the detail panel the flat tab never had room for.
 *
 * Selecting a row fetches /markets/{source}/{market_id} rather than reusing the
 * list row: the detail needs the decision *and* the outside evidence that
 * matched the question, and neither is in the feed payload. Composite key
 * because market ids are not unique across venues.
 */
export default function Markets({ initialMarket }: { initialMarket?: { source: string; market_id: string } | null } = {}) {
  const [source, setSource] = useState<string>('')
  const [openOnly, setOpenOnly] = useState(true)
  const [decidedOnly, setDecidedOnly] = useState(false)
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState<{ source: string; market_id: string } | null>(initialMarket || null)

  const path = useMemo(() => {
    const p = new URLSearchParams()
    p.set('limit', '80')
    if (source) p.set('source', source)
    if (openOnly) p.set('open_only', 'true')
    if (decidedOnly) p.set('decided_only', 'true')
    if (q.trim()) p.set('q', q.trim())
    return `/api/trading/prediction/markets?${p.toString()}`
  }, [source, openOnly, decidedOnly, q])

  const feed = useJSON<{ markets: Market[]; count: number }>(path, 60000)
  const rows = feed.data?.markets || []

  const detailPath = selected
    ? `/api/trading/prediction/markets/${encodeURIComponent(selected.source)}/${encodeURIComponent(selected.market_id)}`
    : null
  const detail = useJSON<MarketDetailData>(detailPath || '/api/trading/prediction/overview', 60000)

  // Outcome labels come from the venue payload: "Up / Down", two team names, or
  // Yes / No. Rendering "yes" over a football team would be a claim about the
  // data that the data does not make.
  const sideLabels = (() => {
    const raw = (detail.data?.raw || {}) as Record<string, unknown>
    let outcomes: unknown = raw.outcomes
    if (typeof outcomes === 'string') {
      try { outcomes = JSON.parse(outcomes) } catch { return null }
    }
    if (!Array.isArray(outcomes) || outcomes.length !== 2) return null
    const a = String(outcomes[0]).trim()
    const b = String(outcomes[1]).trim()
    if (/^(yes|no)$/i.test(a) && /^(yes|no)$/i.test(b)) return null
    return [a, b] as [string, string]
  })()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <AuthNotice error={feed.error} onRetry={feed.refresh} />

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <Filter size={13} style={{ color: 'var(--muted)' }} />
        <div style={{ display: 'flex', gap: 6 }}>
          {['', 'kalshi', 'polymarket'].map(s => (
            <Toggle key={s || 'all'} on={source === s} label={s || 'all venues'} onClick={() => setSource(s)} />
          ))}
        </div>
        <Toggle on={openOnly} label="open only" onClick={() => setOpenOnly(v => !v)}
          title="Default on. Newest ingest is often archived market data and makes the feed look dead." />
        <Toggle on={decidedOnly} label="decided" onClick={() => setDecidedOnly(v => !v)} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' }}>
          <Search size={12} style={{ color: 'var(--muted)' }} />
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="question contains…"
            style={{
              background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 7, padding: '6px 9px', fontSize: 11, color: '#fff', width: 200,
            }}
          />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: selected ? 'minmax(0,1.4fr) minmax(0,1fr)' : '1fr', gap: 14 }}>
        <Panel title="Market feed" icon={<Filter size={13} />} right={
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>{feed.data?.count ?? rows.length} rows</span>
        }>
          {feed.loading && rows.length === 0 && <Loading what="markets" />}
          {!feed.loading && rows.length === 0 && (
            <Empty>No markets match these filters. Open-only is on by default; if the collector is
              ingesting archived inventory there is nothing open to show.</Empty>
          )}
          {rows.map(m => {
            const key = `${m.source}:${m.market_id}`
            const isSel = selected?.source === m.source && selected?.market_id === m.market_id
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSelected(isSel ? null : { source: m.source, market_id: m.market_id })}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left',
                  padding: '6px 4px', borderBottom: '1px solid rgba(255,255,255,0.04)',
                  background: isSel ? 'rgba(139,92,246,0.10)' : 'transparent',
                  border: 'none', cursor: 'pointer', color: 'inherit',
                }}
              >
                <span style={{ fontSize: 9, color: 'var(--muted)', width: 58, textTransform: 'uppercase' }}>{m.source}</span>
                <span style={{ flex: 1, minWidth: 0, fontSize: 11, color: '#ffffffe6', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={m.question}>
                  {label(m)}
                </span>
                <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#fff', width: 42, textAlign: 'right' }}>
                  {pct(m.implied_yes ?? m.yes_price)}
                </span>
                <span style={{ fontSize: 9, color: 'var(--muted)', width: 44, textAlign: 'right' }}>{until(m.end_date)}</span>
                {m.verdict && <Chip label={m.verdict} color={VERDICT_COLOR[m.verdict] || '#6b7280'} />}
              </button>
            )
          })}
        </Panel>

        {selected && (
          <Panel title="Market detail" icon={<ExternalLink size={13} />} right={
            detail.data?.venue_url ? (
              <a href={detail.data.venue_url} target="_blank" rel="noreferrer"
                 style={{ fontSize: 10, color: '#c4b5fd', display: 'flex', alignItems: 'center', gap: 4 }}>
                venue <ExternalLink size={10} />
              </a>
            ) : null
          }>
            {detail.loading && !detail.data && <Loading what="market" />}
            {detail.error && <Empty>Could not load this market ({detail.error}).</Empty>}
            {detail.data && (
              <div style={{ fontSize: 11, lineHeight: 1.5 }}>
                <div style={{ color: '#fff', marginBottom: 6 }}>{detail.data.market.question}</div>
                <div style={{ color: 'var(--muted)', marginBottom: 10 }}>
                  {detail.data.market.source} · {detail.data.market.market_id} · volume {money(detail.data.market.volume_24h)} · closes {until(detail.data.market.end_date)}
                </div>

                <div style={{ display: 'flex', gap: 14, marginBottom: 10 }}>
                  <span>{sideLabels?.[0] ?? 'yes'} <b style={{ color: '#22c55e' }}>{pct(detail.data.market.implied_yes ?? detail.data.market.yes_price)}</b></span>
                  <span>{sideLabels?.[1] ?? 'no'} <b style={{ color: '#ef4444' }}>{pct(detail.data.market.implied_no ?? detail.data.market.no_price)}</b></span>
                </div>

                <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--muted)', marginBottom: 4 }}>decision</div>
                {detail.data.decision ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <Chip label={detail.data.decision.verdict} color={VERDICT_COLOR[detail.data.decision.verdict] || '#6b7280'} />
                    <span style={{ fontFamily: 'monospace' }}>conf {pct(detail.data.decision.confidence)}</span>
                    {detail.data.decision.abstained === 1 && <Chip label="abstained" color="#f59e0b" />}
                    {detail.data.decision.outcome && <Chip label={`settled ${detail.data.decision.outcome}`} color={VERDICT_COLOR[detail.data.decision.outcome]} />}
                  </div>
                ) : (
                  <div style={{ color: 'var(--muted)', marginBottom: 6 }}>No decision recorded for this market yet.</div>
                )}

                <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--muted)', margin: '10px 0 4px' }}>matched evidence</div>
                {detail.data.context.length === 0 ? (
                  <div style={{ color: 'var(--muted)' }}>
                    No outside evidence matched this question. That is a real answer, not a failure —
                    the matcher compares significant words and would rather miss than invent a link.
                  </div>
                ) : detail.data.context.map(c => (
                  <div key={`${c.source}:${c.market_id}`} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 0' }}>
                    <Chip label={c.source} color="#00f5ff" />
                    <span style={{ flex: 1, minWidth: 0, color: '#ffffffd9', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {contextSummary(c)}
                    </span>
                    <Chip label={c.direction} color={DIRECTION_COLOR[c.direction] || '#6b7280'} />
                    <span style={{ fontFamily: 'monospace', fontSize: 10 }}>{pct(c.confidence)}</span>
                  </div>
                ))}

                {detail.data.decision?.reasoning && (
                  <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid rgba(255,255,255,0.06)', color: 'var(--muted)', fontSize: 10 }}>
                    {detail.data.decision.reasoning}
                  </div>
                )}

                {/* The venue's own payload, collapsed. It is the only way to
                    audit a normalized field (a price, a close date) back to what
                    the venue actually said, and it is huge — so it opens on
                    demand rather than bloating the panel. */}
                {detail.data.raw && (
                  <details style={{ marginTop: 10 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 10, color: 'var(--muted)' }}>
                      raw venue payload ({Math.max(1, Math.round((detail.data.raw_bytes || 0) / 1024))} KB)
                    </summary>
                    <pre style={{
                      maxHeight: 220, overflow: 'auto', fontSize: 9, lineHeight: 1.4,
                      background: 'rgba(0,0,0,0.25)', padding: 8, borderRadius: 6,
                      border: '1px solid rgba(255,255,255,0.06)', marginTop: 6,
                    }}>
                      {JSON.stringify(detail.data.raw, null, 2)}
                    </pre>
                  </details>
                )}

                <div style={{ marginTop: 8, fontSize: 9, color: 'var(--muted)' }}>
                  ingested {ago(detail.data.market.ingested_ts)}
                </div>
              </div>
            )}
          </Panel>
        )}
      </div>
    </div>
  )
}
