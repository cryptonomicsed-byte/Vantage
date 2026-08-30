import React, { useEffect, useState } from 'react'

// Shadow Account — read-only dashboard tab, same pattern as CouncilSection.
// Retrospective self-behavior mining: what does this agent's OWN history of
// profitable trades have in common (backend/backtest/shadow_account.py), and
// how much did its actual (real) trades cost relative to that distilled
// "shadow" profile. Every number here traces back to a real filled order --
// no simulation, no LLM, pure arithmetic over TradeRecord fields. API auth
// handled by the global X-Agent-Key interceptor (src/utils/apiKeyInterceptor.ts).

interface ShadowRule {
  rule_id: string
  human_text: string
  entry_hour_range: [number, number]
  holding_hours_range: [number, number]
  support_count: number
  coverage_rate: number
}
interface Profile {
  agent_id: number
  profitable_trades: number
  total_trades: number
  rules: ShadowRule[]
  typical_holding_hours: [number, number]
}
interface CounterfactualTrade {
  symbol: string
  entry_time: string
  exit_time: string
  holding_hours: number
  pnl: number
  impact: number
  reason: string
}
interface Attribution {
  shadow_pnl: number
  real_pnl: number
  delta_pnl: number
  noise_trades_pnl: number
  early_exit_pnl: number
  late_exit_pnl: number
  overtrading_pnl: number
  missed_signals_pnl: number
  counterfactual_trades: CounterfactualTrade[]
}
interface Report { profile: Profile; attribution: Attribution }

const S = {
  page: { padding: '1.5em', maxWidth: 1100, margin: '0 auto', fontFamily: 'ui-monospace, Menlo, monospace', color: '#c9d1d9' } as React.CSSProperties,
  h1: { color: '#58a6ff', fontSize: '1.3em' },
  sub: { color: '#8b949e', fontSize: '.8em', marginTop: 4, marginBottom: '1em' },
  row: { display: 'flex', gap: '1.5em', flexWrap: 'wrap' as const, margin: '1em 0' },
  card: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: '12px 16px', minWidth: 150 },
  label: { color: '#8b949e', fontSize: '.75em' },
  val: { fontSize: '1.3em', fontWeight: 700 as const },
  section: { marginTop: '1.5em' },
  h2: { color: '#7ee787', fontSize: '1em', marginBottom: 8 },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '.85em' },
  th: { color: '#8b949e', textAlign: 'left' as const, padding: '6px 8px', borderBottom: '1px solid #21262d' },
  td: { padding: '6px 8px', borderBottom: '1px solid #21262d' },
  pos: { color: '#3fb950' },
  neg: { color: '#f85149' },
  muted: { color: '#8b949e', fontSize: '.78em' },
  ruleCard: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: '10px 14px', marginBottom: 8 },
  empty: { color: '#8b949e', fontSize: '.85em', padding: '2em 0', textAlign: 'center' as const },
}

function pnlCls(v: number): React.CSSProperties {
  return v >= 0 ? S.pos : S.neg
}
function fmtPnl(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2)
}
function fmtHourRange(r: [number, number]): string {
  return `${r[0]}:00–${r[1]}:00`
}

export default function ShadowAccountSection() {
  const [report, setReport] = useState<Report | null>(null)
  const [notEnoughData, setNotEnoughData] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      setNotEnoughData(null)
      try {
        const r = await fetch('/api/trading/shadow-account')
        if (r.status === 400) {
          const d = await r.json().catch(() => ({}))
          if (!cancelled) setNotEnoughData(d.detail || 'Not enough profitable trade history yet.')
          return
        }
        if (!r.ok) {
          if (!cancelled) setError(`Failed to load (${r.status})`)
          return
        }
        const d = await r.json()
        if (!cancelled) setReport(d)
      } catch {
        if (!cancelled) setError('Shadow Account is unavailable right now.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  return (
    <div style={S.page}>
      <h1 style={S.h1}>Shadow Account</h1>
      <div style={S.sub}>
        Retrospective self-behavior mining: rules distilled from YOUR own profitable trades, and exactly how much your
        real trading history cost relative to following them. Every number traces to a real filled order — no
        simulation, no LLM.
      </div>

      {loading && <div style={S.empty}>Loading…</div>}

      {!loading && notEnoughData && (
        <div style={S.empty}>
          {notEnoughData}
          <div style={{ ...S.muted, marginTop: 8 }}>
            Needs at least a handful of real profitable trades before a shadow profile can be distilled honestly —
            this never fabricates a rule from insufficient evidence.
          </div>
        </div>
      )}

      {!loading && error && <div style={{ ...S.empty, color: '#f85149' }}>{error}</div>}

      {!loading && report && (
        <>
          <div style={S.row}>
            <div style={S.card}>
              <div style={S.label}>Profitable / Total Trades</div>
              <div style={S.val}>{report.profile.profitable_trades} / {report.profile.total_trades}</div>
            </div>
            <div style={S.card}>
              <div style={S.label}>Typical Holding (median / p75)</div>
              <div style={S.val}>{report.profile.typical_holding_hours[0].toFixed(1)}h / {report.profile.typical_holding_hours[1].toFixed(1)}h</div>
            </div>
            <div style={S.card}>
              <div style={S.label}>Shadow vs Real PnL</div>
              <div style={S.val}>
                <span style={pnlCls(report.attribution.shadow_pnl)}>{fmtPnl(report.attribution.shadow_pnl)}</span>
                {' / '}
                <span style={pnlCls(report.attribution.real_pnl)}>{fmtPnl(report.attribution.real_pnl)}</span>
              </div>
            </div>
            <div style={S.card}>
              <div style={S.label}>Delta (what following your own rules would have added)</div>
              <div style={{ ...S.val, ...pnlCls(report.attribution.delta_pnl) }}>{fmtPnl(report.attribution.delta_pnl)}</div>
            </div>
          </div>

          <div style={S.section}>
            <h2 style={S.h2}>Your Distilled Rules</h2>
            {report.profile.rules.length === 0 && <div style={S.muted}>No stable rules found yet across your profitable trades.</div>}
            {report.profile.rules.map((r) => (
              <div key={r.rule_id} style={S.ruleCard}>
                <div style={{ fontWeight: 700 }}>{r.human_text}</div>
                <div style={S.muted}>
                  Entry window {fmtHourRange(r.entry_hour_range)} · hold {r.holding_hours_range[0].toFixed(1)}–{r.holding_hours_range[1].toFixed(1)}h
                  {' · '}{r.support_count} trades ({(r.coverage_rate * 100).toFixed(0)}% coverage)
                </div>
              </div>
            ))}
          </div>

          <div style={S.section}>
            <h2 style={S.h2}>PnL Attribution — where the delta comes from</h2>
            <table style={S.table}>
              <thead>
                <tr>
                  <th style={S.th}>Bucket</th>
                  <th style={S.th}>PnL Impact</th>
                  <th style={S.th}>Meaning</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td style={S.td}>Rule violations (noise trades)</td>
                  <td style={{ ...S.td, ...pnlCls(-report.attribution.noise_trades_pnl) }}>{fmtPnl(-report.attribution.noise_trades_pnl)}</td>
                  <td style={S.td}>Trades outside your own distilled rules</td>
                </tr>
                <tr>
                  <td style={S.td}>Early exits</td>
                  <td style={{ ...S.td, ...pnlCls(-report.attribution.early_exit_pnl) }}>{fmtPnl(-report.attribution.early_exit_pnl)}</td>
                  <td style={S.td}>Winners closed before your typical holding window</td>
                </tr>
                <tr>
                  <td style={S.td}>Late exits</td>
                  <td style={{ ...S.td, ...pnlCls(-report.attribution.late_exit_pnl) }}>{fmtPnl(-report.attribution.late_exit_pnl)}</td>
                  <td style={S.td}>Losers held past your typical holding window</td>
                </tr>
                <tr>
                  <td style={S.td}>Overtrading</td>
                  <td style={{ ...S.td, ...pnlCls(-report.attribution.overtrading_pnl) }}>{fmtPnl(-report.attribution.overtrading_pnl)}</td>
                  <td style={S.td}>Trade frequency beyond your own expected budget</td>
                </tr>
                <tr>
                  <td style={S.td}>Missed signals (unexplained residual)</td>
                  <td style={{ ...S.td, ...pnlCls(report.attribution.missed_signals_pnl) }}>{fmtPnl(report.attribution.missed_signals_pnl)}</td>
                  <td style={S.td}>Delta not explained by the buckets above — reported honestly, never absorbed elsewhere</td>
                </tr>
              </tbody>
            </table>
          </div>

          {report.attribution.counterfactual_trades.length > 0 && (
            <div style={S.section}>
              <h2 style={S.h2}>Highest-Impact Real Trades</h2>
              <table style={S.table}>
                <thead>
                  <tr>
                    <th style={S.th}>Symbol</th>
                    <th style={S.th}>Held</th>
                    <th style={S.th}>PnL</th>
                    <th style={S.th}>Impact</th>
                    <th style={S.th}>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {report.attribution.counterfactual_trades.map((c, i) => (
                    <tr key={i}>
                      <td style={S.td}>{c.symbol}</td>
                      <td style={S.td}>{c.holding_hours.toFixed(1)}h</td>
                      <td style={{ ...S.td, ...pnlCls(c.pnl) }}>{fmtPnl(c.pnl)}</td>
                      <td style={{ ...S.td, ...pnlCls(-c.impact) }}>{fmtPnl(-c.impact)}</td>
                      <td style={S.td}>{c.reason.replace('_', ' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
