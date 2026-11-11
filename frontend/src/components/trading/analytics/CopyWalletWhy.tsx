import React from 'react'
import { fmtAge } from './feed'

// "Why this wallet ranks": live role counts (vs what the stale score saw),
// win rate where an outcome source exists, and how recent its activity is.
export default function CopyWalletWhy({ why }: { why?: any }) {
  if (!why) return null
  const live: Record<string, number> = why.live_roles || {}
  const roles = Object.entries(live).sort((a, b) => b[1] - a[1])
  const h = (x?: number | null) => (x == null ? '—' : `${fmtAge(x * 3600)} ago`)
  return (
    <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <span title="token_wallet_roles, read live">
        roles now: {roles.length ? roles.map(([r, n]) => `${n} ${r.replace('_', ' ')}`).join(' · ') : 'none recorded'}
        {why.roles_changed_since_scoring && <span style={{ color: '#f59e0b' }} title={`score was computed on: ${Object.entries(why.stored_roles || {}).map(([r, n]) => `${n} ${r}`).join(', ')}`}> (changed since scored)</span>}
      </span>
      <span title={why.win_rate_source || why.win_rate_unavailable}>
        win rate: {why.win_rate != null ? `${why.win_rate}%${why.trades_observed != null ? ` of ${why.trades_observed}` : ''}` : <span title={why.win_rate_unavailable}>unavailable</span>}
      </span>
      <span>last role seen {h(why.roles_last_seen_age_h)}</span>
      <span>last trade {why.last_trade_at ? h(why.last_trade_age_h) : 'not observed'}</span>
      {why.score_age_h != null && why.score_age_h > 24 && <span style={{ color: '#f59e0b' }}>score {fmtAge(why.score_age_h * 3600)} old</span>}
    </div>
  )
}
