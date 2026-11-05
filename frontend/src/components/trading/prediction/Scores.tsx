import React from 'react'
import { Trophy, User } from 'lucide-react'
import { AuthNotice, Chip, Empty, Loading, Panel, ago, pct, useJSON } from './shared'

/**
 * Scores — Prediction Value (the log-return method ported from HKUDS/FutureShow).
 *
 * Wire-only: records come from `prediction_scores`, which nothing has written to
 * yet. Two different auth scopes on purpose — `/me` needs a key, `/leaderboard`
 * is public like every other Vantage leaderboard — so a missing key must blank
 * only the first card, not the tab.
 */
interface ScoreRow {
  id?: string; agent_id?: string; name?: string; market_slug?: string
  call?: string; value?: number | null; is_correct?: number | null
  created_at?: string | null; total?: number; correct?: number; avg_value?: number | null
}

export default function Scores() {
  const me = useJSON<ScoreRow[]>('/api/prediction-scores/me', 60000)
  const board = useJSON<ScoreRow[]>('/api/prediction-scores/leaderboard', 60000)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <AuthNotice error={me.error} onRetry={me.refresh} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: 16 }}>
        <Panel title="My calls" icon={<User size={13} />} right={
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>{(me.data || []).length}</span>
        }>
          {me.loading && !me.data && <Loading what="your scores" />}
          {me.data && me.data.length === 0 && (
            <Empty>
              No calls recorded for this agent. POST a call to <code>/api/prediction-scores</code> with
              {' '}<code>market_slug</code> and <code>call</code>; scoring is log-return against market
              consensus, so a correct longshot ranks above a correct favourite.
            </Empty>
          )}
          {(me.data || []).map(r => (
            <div key={r.id || r.market_slug} style={{ display: 'flex', gap: 8, padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: 11 }}>
              <Chip label={r.call || '—'} color={r.call === 'YES' ? '#22c55e' : '#ef4444'} />
              <span style={{ flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.market_slug}</span>
              <span style={{ fontFamily: 'monospace' }}>{r.value == null ? 'pending' : r.value.toFixed(3)}</span>
              <span style={{ fontSize: 9, color: 'var(--muted)', width: 52, textAlign: 'right' }}>
                {r.created_at ? ago(Math.floor(Date.parse(r.created_at) / 1000)) : '—'}
              </span>
            </div>
          ))}
        </Panel>

        <Panel title="Leaderboard" icon={<Trophy size={13} />} right={
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>public</span>
        }>
          {board.loading && !board.data && <Loading what="leaderboard" />}
          {board.data && board.data.length === 0 && (
            <Empty>No resolved calls yet, so there is nothing to rank. This fills in as calls resolve.</Empty>
          )}
          {(board.data || []).map((r, i) => (
            <div key={r.agent_id || i} style={{ display: 'flex', gap: 8, padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: 11 }}>
              <span style={{ width: 18, color: 'var(--muted)', fontFamily: 'monospace' }}>{i + 1}</span>
              <span style={{ flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.name || r.agent_id}</span>
              <span style={{ fontFamily: 'monospace', color: '#22c55e' }}>{r.avg_value == null ? '—' : r.avg_value.toFixed(3)}</span>
              <span style={{ fontSize: 9, color: 'var(--muted)', width: 62, textAlign: 'right' }}>
                {r.correct ?? 0}/{r.total ?? 0}
              </span>
            </div>
          ))}
        </Panel>
      </div>

      <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
        Prediction Value rewards calling against consensus correctly, so it is not a win rate and is not
        comparable to the Brier score. Brier grades this system's own probability calibration; this
        grades calls against the market. Both are shown because they answer different questions.
      </div>
    </div>
  )
}
