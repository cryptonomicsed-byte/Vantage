import React, { useState } from 'react'
import { TokenLink } from '../EntityProfileCard'
import { useFeed, FeedBanner, fmtAge, fmtPct, fmtUsd, useSort } from './feed'

// Momentum signals with the inputs behind each one and how old they are.
// These are CEX-listed top-100 tokens (no mint), shown for research only.
export default function AlphaPanel() {
  const feed = useFeed<any>('/api/alpha', 30000)
  const [open, setOpen] = useState<string | null>(null)
  const items: any[] = feed.data?.items || []
  const { sorted, header } = useSort(items, { key: 'conv', dir: -1 }, {
    conv: i => i.conviction, chg: i => i.change_24h, vol: i => i.volume_24h, rank: i => i.rank, age: i => i.quote_age_s,
  })
  return (
    <div>
      <FeedBanner feeds={[feed]} intervalMs={30000} source={feed.data?.source} reason={feed.data && !items.length ? feed.data.reason : null} />
      <div className="ares-section-title">{items.length} momentum signals <span style={{ fontSize: 11, color: 'var(--muted)' }}>{feed.data?.summary}</span></div>
      <table className="ares-table">
        <thead><tr>
          <th>Token</th>{header('rank', 'Rank')}{header('conv', 'Conviction', 'min(5, |24h change| / 5)')}<th>Signal</th>
          {header('chg', '24h')}<th>Price</th>{header('vol', 'Volume 24h')}<th>Vol / cap</th>{header('age', 'Quote age', 'time since the source last updated this quote')}<th></th>
        </tr></thead>
        <tbody>
          {!sorted.length && <tr><td colSpan={10} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>{feed.loading ? 'Loading…' : 'No signals.'}</td></tr>}
          {sorted.map(i => {
            const key = i.source_id || i.symbol
            const turnover = i.market_cap ? (i.volume_24h || 0) / i.market_cap * 100 : null
            return (
              <React.Fragment key={key}>
                <tr>
                  <td style={{ fontWeight: 600 }}><TokenLink symbol={i.symbol} />{i.name && <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 4 }}>{i.name}</span>}
                    {(i.flags || []).map((f: string) => <span key={f} title={f} style={{ marginLeft: 4, fontSize: 9, color: '#f59e0b', border: '1px solid #f59e0b55', borderRadius: 3, padding: '0 3px' }}>⚠ {f}</span>)}
                  </td>
                  <td style={{ color: 'var(--muted)' }}>{i.rank ?? '—'}</td>
                  <td style={{ fontWeight: 700, color: i.conviction > 3 ? 'var(--danger)' : 'var(--warning)' }}>{(i.conviction ?? 0).toFixed(2)}</td>
                  <td style={{ color: i.signal === 'breakdown' ? 'var(--danger)' : i.signal === 'breakout' ? 'var(--green)' : 'var(--muted)' }}>{i.signal}</td>
                  <td style={{ fontFamily: 'monospace', color: i.direction === 'down' ? 'var(--danger)' : 'var(--green)' }}>{fmtPct(i.change_24h)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(i.price, i.price < 1 ? 6 : 2)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(i.volume_24h)}</td>
                  <td style={{ fontFamily: 'monospace', color: turnover != null && turnover < 0.1 ? '#f59e0b' : 'var(--muted)' }}>{turnover != null ? `${turnover.toFixed(2)}%` : '—'}</td>
                  <td style={{ fontSize: 11, color: (i.quote_age_s ?? 0) > 900 ? '#f59e0b' : 'var(--muted)' }}>{fmtAge(i.quote_age_s)}</td>
                  <td><button className="btn btn-ghost btn-sm" onClick={() => setOpen(open === key ? null : key)}>{open === key ? 'hide' : 'inputs'}</button></td>
                </tr>
                {open === key && (
                  <tr><td colSpan={10} style={{ background: 'rgba(0,0,0,0.25)', fontSize: 11, color: 'var(--muted)' }}>
                    <div>Inputs: 24h change {fmtPct(i.inputs?.change_24h)} · volume {fmtUsd(i.inputs?.volume_24h)} · rank {i.inputs?.rank ?? '—'} · market cap {fmtUsd(i.market_cap)}</div>
                    <div>Conviction = {i.inputs?.conviction_formula}; signal: {i.inputs?.signal_rule}</div>
                    <div>Universe: {i.inputs?.universe}; source id <code>{i.source_id || '—'}</code>; quote updated {i.quote_updated_at || 'unknown'}; our copy is {fmtAge(i.fetched_age_s)} old</div>
                  </td></tr>
                )}
              </React.Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
