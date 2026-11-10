import React, { useState } from 'react'
import { useFeed, FeedBanner, fmtAge, fmtPct, fmtUsd, useSort } from './feed'

const CHAINS = ['', 'Ethereum', 'Solana', 'Base', 'Arbitrum', 'BSC', 'Polygon', 'Optimism', 'Avalanche', 'Sui', 'Hyperliquid L1']

// DefiLlama pools: organic (base) vs emission (reward) yield, TVL, IL risk,
// stablecoin flag and a link to the pool's own DefiLlama page.
export default function YieldsPanel() {
  const [chain, setChain] = useState('')
  const [stable, setStable] = useState(false)
  const [sort, setSort] = useState<'apy' | 'tvl' | 'apy_base'>('apy')
  const [minTvl, setMinTvl] = useState(1_000_000)
  const [organic, setOrganic] = useState(false)
  const qs = new URLSearchParams({ limit: '60', sort, min_tvl: String(minTvl), ...(chain ? { chain } : {}), ...(stable ? { stablecoin: 'true' } : {}), ...(organic ? { min_apy_base: '1' } : {}) })
  const feed = useFeed<any>(`/api/intel/yields?${qs}`, 120000)
  const pools: any[] = feed.data?.pools || []
  const t = useSort(pools, { key: sort === 'tvl' ? 'tvl' : sort === 'apy_base' ? 'base' : 'apy', dir: -1 }, {
    apy: p => p.apy, base: p => p.apy_base, reward: p => p.apy_reward, tvl: p => p.tvl_usd, m30: p => p.apy_mean_30d, d7: p => p.apy_pct_7d,
  })
  return (
    <div>
      <FeedBanner feeds={[feed]} intervalMs={120000} source={feed.data?.source} reason={feed.data && !pools.length ? feed.data.reason : null} />
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10, fontSize: 12 }}>
        <select className="ares-input" value={chain} onChange={e => setChain(e.target.value)} style={{ maxWidth: 160 }}>
          {CHAINS.map(c => <option key={c} value={c}>{c || 'All chains'}</option>)}
        </select>
        <select className="ares-input" value={sort} onChange={e => setSort(e.target.value as any)} style={{ maxWidth: 160 }}>
          <option value="apy">Rank by total APY</option><option value="apy_base">Rank by base APY</option><option value="tvl">Rank by TVL</option>
        </select>
        <select className="ares-input" value={minTvl} onChange={e => setMinTvl(Number(e.target.value))} style={{ maxWidth: 140 }}>
          {[1e6, 1e7, 1e8].map(v => <option key={v} value={v}>TVL ≥ {fmtUsd(v, 0)}</option>)}
        </select>
        <label><input type="checkbox" checked={stable} onChange={e => setStable(e.target.checked)} /> stablecoin only</label>
        <label title="hide pools whose yield is (almost) all reward-token emissions"><input type="checkbox" checked={organic} onChange={e => setOrganic(e.target.checked)} /> base APY ≥ 1%</label>
        {feed.data?.upstream_age_s != null && <span style={{ color: 'var(--muted)' }}>DefiLlama snapshot {fmtAge(feed.data.upstream_age_s)} old</span>}
      </div>
      <div className="dash-panel" style={{ maxHeight: 600, overflowY: 'auto', padding: 0 }}>
        <table className="ares-table">
          <thead><tr>
            <th>Pool</th><th>Project</th><th>Chain</th>{t.header('apy', 'APY')}{t.header('base', 'Base')}{t.header('reward', 'Reward', 'emission yield -- stops when the incentive does')}
            {t.header('m30', '30d mean')}{t.header('d7', '7d Δ', 'APY change over 7 days (percentage points)')}<th>IL</th>{t.header('tvl', 'TVL')}<th></th>
          </tr></thead>
          <tbody>
            {!t.sorted.length && <tr><td colSpan={11} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>{feed.loading ? 'Loading…' : 'No pools.'}</td></tr>}
            {t.sorted.map(p => (
              <tr key={p.pool_id || `${p.project}-${p.pool}-${p.chain}`}>
                <td style={{ fontWeight: 600 }}>{p.pool}{p.pool_meta ? <span style={{ fontSize: 10, color: 'var(--muted)' }}> {p.pool_meta}</span> : null}
                  {p.stablecoin && <span style={{ fontSize: 9, color: 'var(--cyan)', marginLeft: 4 }}>STABLE</span>}</td>
                <td style={{ fontSize: 11 }}>{p.project}</td>
                <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.chain}</td>
                <td style={{ color: 'var(--green)', fontWeight: 700 }}>{fmtPct(p.apy, 1, false)}</td>
                <td style={{ fontSize: 11 }}>{p.apy_base != null ? fmtPct(p.apy_base, 1, false) : '—'}</td>
                <td style={{ fontSize: 11, color: (p.reward_share_pct ?? 0) > 80 ? '#f59e0b' : 'var(--muted)' }} title={p.reward_share_pct != null ? `${p.reward_share_pct}% of APY is emissions` : ''}>
                  {p.apy_reward ? fmtPct(p.apy_reward, 1, false) : '—'}</td>
                <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.apy_mean_30d != null ? fmtPct(p.apy_mean_30d, 1, false) : '—'}</td>
                <td style={{ fontSize: 11, color: (p.apy_pct_7d ?? 0) < 0 ? 'var(--danger)' : 'var(--muted)' }}>{p.apy_pct_7d != null ? `${p.apy_pct_7d > 0 ? '+' : ''}${p.apy_pct_7d.toFixed(1)}pp` : '—'}</td>
                <td style={{ fontSize: 10, color: p.il_risk === 'yes' ? 'var(--warning)' : 'var(--muted)' }} title={p.exposure ? `exposure: ${p.exposure}` : ''}>
                  {p.il_risk === 'yes' ? '⚠ IL' : p.il_risk === 'no' ? 'none' : '—'}</td>
                <td style={{ fontFamily: 'monospace' }}>{fmtUsd(p.tvl_usd)}</td>
                <td>{p.url ? <a href={p.url} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: 'var(--accent)' }}>DefiLlama ↗</a> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6 }}>DefiLlama's own outlier-flagged pools are excluded. APY figures are DefiLlama's, not ours.</div>
    </div>
  )
}
