import React, { useMemo, useState } from 'react'
import { TokenLink } from '../EntityProfileCard'
import { useFeed, FeedBanner, fmtAge, ageSince, fmtPct, fmtUsd, useSort, authHeaders } from './feed'

const short = (a?: string) => (a ? `${a.slice(0, 4)}…${a.slice(-4)}` : '—')

// DexScreener pair search. Rows are keyed and re-priced by PAIR ADDRESS --
// tickers collide constantly (a search for WIF returns several unrelated
// tokens called WIF), so the token address and chain are always shown and
// a symbol shared by several different tokens is flagged.
export default function DexPanel() {
  const [q, setQ] = useState('SOL')
  const [query, setQuery] = useState('SOL')
  const [chain, setChain] = useState('')
  const [minLiq, setMinLiq] = useState(10000)
  const [live, setLive] = useState<Record<string, any>>({})
  const feed = useFeed<any>(query ? `/api/intel/dex?q=${encodeURIComponent(query)}&limit=50` : null, 60000)
  const pairs: any[] = feed.data?.pairs || []
  const chains = useMemo(() => [...new Set(pairs.map(p => p.chain).filter(Boolean))].sort(), [pairs])
  const tokensPerSymbol = useMemo(() => {
    const m: Record<string, Set<string>> = {}
    pairs.forEach(p => { const s = (p.pair || '').split('/')[0]; (m[s] ||= new Set()).add(`${p.chain}:${p.base_address}`) })
    return m
  }, [pairs])
  const rows = pairs.filter(p => (!chain || p.chain === chain) && (p.liquidity_usd || 0) >= minLiq)
  const t = useSort(rows, { key: 'vol', dir: -1 }, {
    liq: p => p.liquidity_usd, vol: p => p.volume_24h, chg: p => p.change_24h, age: p => p.pair_created_at ? -p.pair_created_at : null,
    flow: p => (p.buys_24h || 0) + (p.sells_24h || 0),
  })
  async function reprice(p: any) {
    const k = `${p.chain}:${p.pair_address}`
    setLive(s => ({ ...s, [k]: { loading: true } }))
    try {
      const r = await fetch(`/api/intel/dex/pair/${encodeURIComponent(p.chain)}/${encodeURIComponent(p.pair_address)}`, { headers: authHeaders() })
      const j = await r.json()
      setLive(s => ({ ...s, [k]: r.ok ? { ...j.pair, at: Date.now() } : { error: j?.detail || `HTTP ${r.status}` } }))
    } catch (e: any) { setLive(s => ({ ...s, [k]: { error: e?.message || 'failed' } })) }
  }
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center', fontSize: 12 }}>
        <input className="ares-input" placeholder="Token symbol, name or address" value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && setQuery(q.trim())} style={{ maxWidth: 280 }} />
        <button className="btn btn-primary btn-sm" onClick={() => setQuery(q.trim())}>Search</button>
        <select className="ares-input" value={chain} onChange={e => setChain(e.target.value)} style={{ maxWidth: 140 }}>
          <option value="">All chains</option>{chains.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="ares-input" value={minLiq} onChange={e => setMinLiq(Number(e.target.value))} style={{ maxWidth: 150 }}>
          {[0, 10000, 100000, 1000000].map(v => <option key={v} value={v}>liquidity ≥ {fmtUsd(v, 0)}</option>)}
        </select>
      </div>
      <FeedBanner feeds={[feed]} intervalMs={60000} source={feed.data?.source} />
      <div className="ares-section-title">{rows.length}/{pairs.length} pairs for “{query}”</div>
      <div className="dash-panel" style={{ maxHeight: 600, overflowY: 'auto', padding: 0 }}>
        <table className="ares-table">
          <thead><tr>
            <th>Pair</th><th>Chain / DEX</th><th>Token · pair address</th><th>Price</th>
            {t.header('liq', 'Liquidity')}{t.header('vol', 'Vol 24h')}{t.header('chg', '24h')}{t.header('flow', 'Buys / sells 24h')}{t.header('age', 'Pair age')}<th></th>
          </tr></thead>
          <tbody>
            {!t.sorted.length && <tr><td colSpan={10} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>{feed.loading ? 'Loading…' : pairs.length ? 'No pair passes the filters.' : 'No pairs.'}</td></tr>}
            {t.sorted.map(p => {
              const k = `${p.chain}:${p.pair_address}`
              const lv = live[k]
              const sym = (p.pair || '').split('/')[0]
              const collide = (tokensPerSymbol[sym]?.size || 0) > 1
              const buyShare = (p.buys_24h || 0) + (p.sells_24h || 0) ? (p.buys_24h || 0) / ((p.buys_24h || 0) + (p.sells_24h || 0)) * 100 : null
              return (
                <tr key={k}>
                  <td style={{ fontWeight: 600 }}><TokenLink symbol={sym} ca={p.base_address} chain={p.chain} />/{(p.pair || '').split('/')[1]}
                    {collide && <span title={`${tokensPerSymbol[sym].size} different tokens use the ticker ${sym} in these results`} style={{ marginLeft: 4, fontSize: 9, color: '#f59e0b' }}>⚠ ticker shared</span>}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.chain} · {p.dex}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 10 }} title={`token ${p.base_address}\npair ${p.pair_address}`}>{short(p.base_address)} · {p.url ? <a href={p.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>{short(p.pair_address)}</a> : short(p.pair_address)}</td>
                  <td style={{ fontFamily: 'monospace' }}>
                    {lv?.price_usd != null ? <span title={`re-priced by pair address ${fmtAge((Date.now() - lv.at) / 1000)} ago`} style={{ color: '#39ff14' }}>${Number(lv.price_usd).toPrecision(4)}</span>
                      : p.price_usd != null ? `$${Number(p.price_usd).toPrecision(4)}` : '—'}
                    {lv?.error && <span style={{ fontSize: 9, color: '#f59e0b' }}> {lv.error}</span>}
                  </td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(p.liquidity_usd)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmtUsd(p.volume_24h)}</td>
                  <td style={{ color: (p.change_24h || 0) >= 0 ? 'var(--green)' : 'var(--danger)', fontWeight: 700 }}>{fmtPct(p.change_24h, 1)}</td>
                  <td style={{ fontSize: 11 }}>{p.buys_24h ?? '—'} / {p.sells_24h ?? '—'}{buyShare != null && <span style={{ color: 'var(--muted)' }}> ({buyShare.toFixed(0)}% buys)</span>}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.pair_created_at ? fmtAge(ageSince(p.pair_created_at)) : '—'}</td>
                  <td>{p.pair_address && <button className="btn btn-ghost btn-sm" disabled={lv?.loading} onClick={() => reprice(p)} title="live price for this exact pair address">↻</button>}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
