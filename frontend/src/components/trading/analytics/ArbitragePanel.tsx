import React, { useState } from 'react'
import { TokenLink } from '../EntityProfileCard'
import { useFeed, FeedBanner, fmtAge, fmtPct, useSort } from './feed'

// Cross-exchange spreads, executable: buy at the lowest ask, sell at the
// highest bid on another venue, minus a base-tier taker fee on BOTH legs.
// Read-only analytics -- nothing here places an order.
export default function ArbitragePanel() {
  const feed = useFeed<any>('/api/intel/arbitrage', 30000)
  const [minNet, setMinNet] = useState(-1)
  const [maxAge, setMaxAge] = useState(60)
  const [open, setOpen] = useState<string | null>(null)
  const all: any[] = feed.data?.opportunities || []
  const rows = all.filter(o => (o.net_spread_pct ?? -99) >= minNet && (o.max_quote_age_s ?? 0) <= maxAge)
  const { sorted, header } = useSort(rows, { key: 'net', dir: -1 }, {
    net: o => o.net_spread_pct, gross: o => o.spread_pct, fees: o => o.est_fees_pct,
    pair: o => o.pair, age: o => o.max_quote_age_s,
  })
  const missing = feed.data?.missing_venues || {}
  const profitable = all.filter(o => (o.net_spread_pct ?? 0) > 0).length
  return (
    <div>
      <FeedBanner feeds={[feed]} intervalMs={30000} source={feed.data?.source}
        reason={feed.data && !all.length ? (feed.data.reason || 'no venue pair answered') : null} />
      <div className="ares-section-title" style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span>{rows.length}/{all.length} routes</span>
        <span style={{ fontSize: 11, color: profitable ? 'var(--warning)' : 'var(--muted)' }}>
          {profitable} profitable after both taker legs
        </span>
      </div>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, marginBottom: 10 }}>
        <label>Min net spread&nbsp;
          <input className="ares-input" type="number" step="0.05" value={minNet} style={{ width: 80 }}
            onChange={e => setMinNet(Number(e.target.value))} />%
        </label>
        <label>Max quote age&nbsp;
          <select className="ares-input" value={maxAge} onChange={e => setMaxAge(Number(e.target.value))} style={{ width: 90 }}>
            {[5, 15, 30, 60, 300].map(s => <option key={s} value={s}>{s}s</option>)}
          </select>
        </label>
        {Object.keys(missing).length > 0 && (
          <span style={{ color: 'var(--muted)' }}>
            Venues not quoting: {Object.entries(missing).map(([v, why]) => `${v} (${why})`).join(', ')}
          </span>
        )}
      </div>
      <table className="ares-table">
        <thead><tr>
          {header('pair', 'Pair')}<th>Route</th>
          <th title="price paid on the buy venue (ask) and received on the sell venue (bid)">Buy → Sell</th>
          {header('gross', 'Gross', 'executable spread before fees')}
          {header('fees', 'Fees', 'base-tier taker fee, buy leg + sell leg')}
          {header('net', 'Net')}
          {header('age', 'Quote age', 'oldest of the two legs; exchange timestamp where published, else fetch time')}
          <th></th>
        </tr></thead>
        <tbody>
          {!sorted.length && <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>
            {all.length ? 'No route passes the filters.' : feed.loading ? 'Loading…' : 'No data.'}</td></tr>}
          {sorted.map(o => {
            const net = o.net_spread_pct ?? 0
            const key = o.pair
            return (
              <React.Fragment key={key}>
                <tr className={net > 0 ? 'threat-high' : ''}>
                  <td>{o.pair?.includes('/') ? <TokenLink symbol={o.pair.split('/')[0]} /> : o.pair}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{o.route}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>
                    {o.buy_leg ? <>${o.buy_leg.price} <span style={{ color: 'var(--muted)' }}>({o.buy_leg.taker_fee_pct}%)</span> → ${o.sell_leg.price} <span style={{ color: 'var(--muted)' }}>({o.sell_leg.taker_fee_pct}%)</span></> : `$${o.buy_price} → $${o.sell_price}`}
                    {o.spread_basis === 'last' && <span title="fewer than two venues published a book; last-trade prices" style={{ color: '#f59e0b' }}> last</span>}
                  </td>
                  <td style={{ color: 'var(--muted)' }}>{fmtPct(o.spread_pct, 3)}</td>
                  <td style={{ color: 'var(--muted)' }}>−{o.est_fees_pct?.toFixed(2)}%</td>
                  <td style={{ fontWeight: 700, color: net > 0 ? 'var(--warning)' : 'var(--muted)' }}>{fmtPct(net, 3)}</td>
                  <td style={{ fontSize: 11, color: (o.max_quote_age_s ?? 0) > 15 ? '#f59e0b' : 'var(--muted)' }}>
                    {o.buy_leg ? `${fmtAge(o.buy_leg.quote_age_s)} / ${fmtAge(o.sell_leg.quote_age_s)}` : '—'}
                  </td>
                  <td><button className="btn btn-ghost btn-sm" onClick={() => setOpen(open === key ? null : key)}>{open === key ? 'hide' : 'venues'}</button></td>
                </tr>
                {open === key && (
                  <tr><td colSpan={8} style={{ background: 'rgba(0,0,0,0.25)' }}>
                    <table className="ares-table" style={{ fontSize: 11 }}>
                      <thead><tr><th>Venue</th><th>Bid</th><th>Ask</th><th>Last</th><th>Book</th><th>Taker</th><th>Age</th><th>Timestamp</th></tr></thead>
                      <tbody>{(o.venue_quotes || []).map((q: any) => (
                        <tr key={q.venue}>
                          <td>{q.venue}</td><td>{q.bid ?? '—'}</td><td>{q.ask ?? '—'}</td><td>{q.last ?? '—'}</td>
                          <td>{q.quote_ccy}{q.quote_ccy === 'USDT' ? ` → USD @ ${o.usdt_usd}${o.usdt_usd_measured ? '' : ' (assumed)'}` : ''}</td>
                          <td>{q.taker_fee_pct}%</td><td>{fmtAge(q.quote_age_s)}</td>
                          <td style={{ color: 'var(--muted)' }}>{q.ts_source === 'exchange' ? 'exchange' : 'our fetch time'}</td>
                        </tr>))}</tbody>
                    </table>
                  </td></tr>
                )}
              </React.Fragment>
            )
          })}
        </tbody>
      </table>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8 }}>
        {feed.data?.fees_note || 'Fees are public base-tier taker rates; your tier may differ.'} Top-of-book only — depth, withdrawal fees and transfer time are not included.
      </div>
    </div>
  )
}
