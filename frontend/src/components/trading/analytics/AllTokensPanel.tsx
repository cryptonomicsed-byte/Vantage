import React, { useState } from 'react'
import { TokenLink } from '../EntityProfileCard'
import { useFeed, FeedBanner, fmtAge, ageSince, fmtPct, fmtUsd, useSort } from './feed'

// Two universes, both with search / sort / filter:
//  - DEX pools (GeckoTerminal trending/new, ranked in GeckoTerminal's order)
//  - Ranked market (top 100 by market cap, CoinPaprika via /api/intel/market)
export default function AllTokensPanel() {
  const [mode, setMode] = useState<'trending' | 'new' | 'ranked'>('trending')
  const [network, setNetwork] = useState('solana')
  const [search, setSearch] = useState('')
  const [minLiq, setMinLiq] = useState(0)
  const [minVol, setMinVol] = useState(0)
  const dex = useFeed<any>(mode !== 'ranked' ? `/api/intel/dex/pools?network=${network}&kind=${mode}&limit=40` : null, 60000)
  const mkt = useFeed<any>(mode === 'ranked' ? '/api/intel/market?limit=100' : null, 120000)
  const s = search.trim().toLowerCase()
  const match = (...xs: any[]) => !s || xs.some(x => String(x || '').toLowerCase().includes(s))

  const pools: any[] = (dex.data?.pools || []).filter((p: any) => match(p.pair, p.base_address, p.pool_address, p.dex)
    && (p.liquidity_usd || 0) >= minLiq && (p.volume_24h || 0) >= minVol)
  const toks: any[] = (mkt.data?.tokens || []).filter((t: any) => match(t.symbol, t.name) && (t.volume_24h || 0) >= minVol)
  const pt = useSort(pools, { key: 'rank', dir: 1 }, {
    rank: p => p.rank, liq: p => p.liquidity_usd, vol: p => p.volume_24h, chg: p => p.change_24h_pct, c1: p => p.change_1h_pct,
    fdv: p => p.fdv_usd, age: p => p.created_at ? -Date.parse(p.created_at) : null, flow: p => (p.buys_24h || 0) + (p.sells_24h || 0),
  })
  const mt = useSort(toks, { key: 'rank', dir: 1 }, {
    rank: t => t.market_cap_rank, price: t => t.price, chg: t => t.change_24h, vol: t => t.volume_24h, cap: t => t.market_cap,
  })
  const feed = mode === 'ranked' ? mkt : dex
  const src = mode === 'ranked' ? mkt.data?.market_snapshot?.data_source : dex.data?.source
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 12 }}>
        {(['trending', 'new', 'ranked'] as const).map(m => (
          <button key={m} className={`btn btn-sm ${mode === m ? 'btn-primary' : ''}`} onClick={() => setMode(m)}>
            {m === 'ranked' ? 'Ranked (top 100)' : m === 'new' ? 'New pools' : 'Trending pools'}</button>
        ))}
        {mode !== 'ranked' && (
          <select className="ares-input" value={network} onChange={e => setNetwork(e.target.value)} style={{ maxWidth: 130 }}>
            <option value="solana">Solana</option><option value="eth">Ethereum</option><option value="base">Base</option><option value="bsc">BSC</option>
          </select>
        )}
        <input className="ares-input" placeholder="Search symbol, name or address" value={search} onChange={e => setSearch(e.target.value)} style={{ maxWidth: 240 }} />
        {mode !== 'ranked' && (
          <select className="ares-input" value={minLiq} onChange={e => setMinLiq(Number(e.target.value))} style={{ maxWidth: 150 }}>
            {[0, 10000, 50000, 250000].map(v => <option key={v} value={v}>liquidity ≥ {fmtUsd(v, 0)}</option>)}
          </select>
        )}
        <select className="ares-input" value={minVol} onChange={e => setMinVol(Number(e.target.value))} style={{ maxWidth: 150 }}>
          {[0, 10000, 100000, 1000000].map(v => <option key={v} value={v}>volume ≥ {fmtUsd(v, 0)}</option>)}
        </select>
        <button className="btn btn-ghost btn-sm" onClick={feed.refresh}>Refresh</button>
      </div>
      <FeedBanner feeds={[feed]} intervalMs={mode === 'ranked' ? 120000 : 60000} source={src}
        reason={feed.data && !(mode === 'ranked' ? mkt.data?.tokens?.length : dex.data?.pools?.length) ? (feed.data.reason || feed.data.error || 'source returned nothing') : null} />
      {mode === 'ranked' ? (
        <div className="dash-panel" style={{ maxHeight: 600, overflowY: 'auto', padding: 0 }}>
          <table className="ares-table">
            <thead><tr>{mt.header('rank', '#')}<th>Token</th>{mt.header('price', 'Price')}{mt.header('chg', '24h')}{mt.header('vol', 'Volume 24h')}{mt.header('cap', 'Market cap')}<th>Source</th></tr></thead>
            <tbody>
              {!mt.sorted.length && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>{mkt.loading ? 'Loading…' : 'No tokens.'}</td></tr>}
              {mt.sorted.map(t => (
                <tr key={`${t.symbol}-${t.market_cap_rank}`}>
                  <td style={{ color: 'var(--muted)' }}>{t.market_cap_rank ?? '—'}</td>
                  <td style={{ fontWeight: 600 }}><TokenLink symbol={t.symbol} /></td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(t.price, t.price < 1 ? 5 : 2)}</td>
                  <td style={{ color: (t.change_24h || 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{fmtPct(t.change_24h)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(t.volume_24h)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(t.market_cap)}</td>
                  <td style={{ fontSize: 10, color: 'var(--muted)' }}>{mkt.data?.market_snapshot?.price_source || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="dash-panel" style={{ maxHeight: 600, overflowY: 'auto', padding: 0 }}>
          <table className="ares-table">
            <thead><tr>
              {pt.header('rank', '#', "GeckoTerminal's own order")}<th>Pair</th><th>DEX</th><th>Price</th>{pt.header('liq', 'Liquidity')}{pt.header('fdv', 'FDV')}
              {pt.header('vol', 'Vol 24h')}{pt.header('c1', '1h')}{pt.header('chg', '24h')}{pt.header('flow', 'Buys / sells')}{pt.header('age', 'Age')}<th>Source</th>
            </tr></thead>
            <tbody>
              {!pt.sorted.length && <tr><td colSpan={12} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>{dex.loading ? 'Loading…' : 'No pools.'}</td></tr>}
              {pt.sorted.map(p => (
                <tr key={p.pool_address || p.rank}>
                  <td style={{ color: 'var(--muted)' }}>{p.rank ?? '—'}</td>
                  <td style={{ fontWeight: 600 }} title={`token ${p.base_address}\npool ${p.pool_address}`}>
                    <TokenLink symbol={(p.pair || '').split('/')[0]} ca={p.base_address} chain={network} />/{(p.pair || '').split('/')[1]}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.dex || '—'}</td>
                  <td style={{ fontFamily: 'monospace' }}>{p.price_usd != null ? `$${Number(p.price_usd).toPrecision(4)}` : '—'}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(p.liquidity_usd)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(p.fdv_usd)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(p.volume_24h)}</td>
                  <td style={{ fontSize: 11, color: (p.change_1h_pct || 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{fmtPct(p.change_1h_pct, 1)}</td>
                  <td style={{ fontWeight: 700, color: (p.change_24h_pct || 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{fmtPct(p.change_24h_pct, 1)}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.buys_24h ?? '—'} / {p.sells_24h ?? '—'}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.created_at ? fmtAge(ageSince(p.created_at)) : '—'}</td>
                  <td style={{ fontSize: 10, color: 'var(--muted)' }}>{p.source || 'geckoterminal'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
