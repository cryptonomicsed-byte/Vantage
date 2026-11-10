import React from 'react'
import { TokenLink } from '../EntityProfileCard'
import { useFeed, FeedBanner, fmtAge, fmtPct, fmtUsd } from './feed'

const card: React.CSSProperties = { background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }
const healthColor = (h?: string) => h === 'healthy' ? '#39ff14' : h === 'degraded' ? '#f59e0b' : '#ff2d4a'

// Market overview: every number says where it came from, and a section
// whose source failed says so instead of rendering as zero.
export default function OverviewPanel() {
  const feed = useFeed<any>('/api/intel', 60000)
  const i = feed.data
  const sections: Record<string, any> = i?.sections || {}
  const fusion = i?.anomalies?.fusion || {}
  const chains: Record<string, any> = i?.chain_details && Object.keys(i.chain_details).length ? i.chain_details : (i?.health?.chains || {})
  const arb: any[] = i?.arbitrage?.opportunities || []
  const sent = i?.sentiment?.sentiment || {}
  const snap = i?.market_snapshot || {}
  const prices: any[] = i?.prices || []
  const down = Object.entries(sections).filter(([, s]) => !s.ok)
  const builtAgo = i?.as_of ? Date.now() / 1000 - i.as_of : null
  return (
    <div>
      <FeedBanner feeds={[feed]} intervalMs={60000} source={snap.data_source} />
      {down.length > 0 && (
        <div style={{ ...card, borderColor: '#f59e0b55', marginBottom: 12, fontSize: 12 }}>
          {down.map(([k, s]) => <div key={k}><b style={{ color: '#f59e0b' }}>{k.replace('_', ' ')}</b> unavailable — {s.error}</div>)}
        </div>
      )}
      <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
        <div className="ares-stat-tile"><div className="ares-stat-label">BTC</div><div className="ares-stat-value">{fusion.btc_consensus ? fmtUsd(fusion.btc_consensus, 0) : '—'}</div><div style={{ fontSize: 10, color: 'var(--muted)' }}>{fusion.price_source || 'unavailable'}{i?.pyth_refused ? ' · Pyth needs a key' : ''}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">ETH / SOL</div><div className="ares-stat-value" style={{ fontSize: 16 }}>{fusion.eth ? fmtUsd(fusion.eth, 0) : '—'} / {fusion.sol ? fmtUsd(fusion.sol) : '—'}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Sentiment</div><div className="ares-stat-value" style={{ textTransform: 'capitalize' }}>{sent.overall || '—'}</div><div style={{ fontSize: 10, color: 'var(--muted)' }}>F&amp;G {sent.fear_greed ?? '—'} · BTC dom {sent.btc_dominance != null ? `${Number(sent.btc_dominance).toFixed(1)}%` : '—'}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Top-100 green</div><div className="ares-stat-value">{snap.tokens_tracked ? `${snap.gainers_24h}/${snap.tokens_tracked}` : '—'}</div><div style={{ fontSize: 10, color: 'var(--muted)' }}>avg {fmtPct(sent.avg_change_24h)}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Arb after fees</div><div className="ares-stat-value">{arb.filter(o => o.net_spread_pct > 0).length}/{arb.length}</div><div style={{ fontSize: 10, color: 'var(--muted)' }}>best net {arb[0] ? fmtPct(arb[0].net_spread_pct, 3) : '—'}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Built</div><div className="ares-stat-value" style={{ fontSize: 14 }}>{builtAgo != null ? `${fmtAge(builtAgo)} ago` : '—'}</div><div style={{ fontSize: 10, color: 'var(--muted)' }}>{Object.entries(sections).map(([k, s]) => `${k} ${s.ms}ms`).join(' · ')}</div></div>
      </div>

      <div className="ares-section-title">Chain health ({Object.keys(chains).length})</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 8, marginBottom: 16 }}>
        {Object.entries(chains).map(([k, v]) => (
          <div key={k} style={card}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><b style={{ textTransform: 'capitalize', fontSize: 12 }}>{k}</b><span style={{ color: healthColor(v.health), fontSize: 11 }}>{v.health}</span></div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              height {v.block_height?.toLocaleString() ?? '—'}{v.latency_ms != null ? ` · ${v.latency_ms} ms` : ''}{v.gas_gwei != null ? ` · ${v.gas_gwei} gwei` : ''}
            </div>
            {v.error && <div style={{ fontSize: 10, color: '#f59e0b' }}>{v.error}</div>}
          </div>
        ))}
      </div>

      {(i?.anomalies?.anomalies || []).length > 0 && <>
        <div className="ares-section-title">Anomalies</div>
        {(i.anomalies.anomalies as any[]).map((a, k) => (
          <div key={k} style={{ ...card, marginBottom: 4, fontSize: 12 }}><b>{a.symbol}</b> <span style={{ color: 'var(--muted)' }}>{String(a.type || '').replace(/_/g, ' ')}</span> · {a.detail}</div>
        ))}
      </>}

      <div className="ares-section-title">Top 20 by market cap <span style={{ fontSize: 11, color: 'var(--muted)' }}>({snap.price_source || '—'})</span></div>
      <table className="ares-table">
        <thead><tr><th>#</th><th>Token</th><th>Price</th><th>24h</th><th>Volume 24h</th><th>Market cap</th></tr></thead>
        <tbody>
          {!prices.length && <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--muted)', padding: 16 }}>{sections.market && !sections.market.ok ? `Unavailable: ${sections.market.error}` : 'No data.'}</td></tr>}
          {prices.map(t => (
            <tr key={t.symbol}>
              <td style={{ color: 'var(--muted)' }}>{t.market_cap_rank ?? '—'}</td>
              <td><TokenLink symbol={t.symbol} /></td>
              <td style={{ fontFamily: 'monospace' }}>{fmtUsd(t.price, t.price < 1 ? 4 : 2)}</td>
              <td style={{ color: (t.change_24h || 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{fmtPct(t.change_24h)}</td>
              <td style={{ fontFamily: 'monospace' }}>{fmtUsd(t.volume_24h)}</td>
              <td style={{ fontFamily: 'monospace' }}>{fmtUsd(t.market_cap)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
