import { useCallback, useEffect, useState } from 'react'

// Julia TradingOSQuant engine (ares-quant-julia) via GET /api/intel/quant/backtest/{symbol}.
// Candles come from free public OHLC (Kraken/Binance); the engine annualises
// Sharpe/Sortino with bars_per_year derived from the interval (365-day year).

const INTERVALS = ['15m', '1h', '4h', '1d', '1w']
const fmt = (v: unknown, d = 2, suffix = '') =>
  typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(d)}${suffix}` : '—'

type Trade = { side?: string; entry_bar?: number; exit_bar?: number; entry_price?: number;
  exit_price?: number; pnl?: number; pnl_pct?: number; bars?: number; exit_reason?: string }

function EquityChart({ points }: { points: number[] }) {
  if (points.length < 2) return null
  const w = 640, h = 120, min = Math.min(...points), max = Math.max(...points)
  const span = max - min || 1
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${(i / (points.length - 1) * w).toFixed(1)},${(h - (p - min) / span * h).toFixed(1)}`).join(' ')
  const up = points[points.length - 1] >= points[0]
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: h, background: '#000', borderRadius: 8, border: '1px solid var(--border)' }}
      role="img" aria-label="Equity curve">
      <path d={d} fill="none" stroke={up ? '#22c55e' : '#ef4444'} strokeWidth={1.5} />
    </svg>
  )
}

export default function QuantBacktestPanel() {
  const [symbol, setSymbol] = useState('BTC')
  const [interval, setInterval] = useState('1d')
  const [limit, setLimit] = useState(300)
  const [hma, setHma] = useState<string>('')
  const [sl, setSl] = useState<string>('')
  const [tp, setTp] = useState<string>('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const run = useCallback(async () => {
    setLoading(true); setErr(null)
    const q = new URLSearchParams({ interval, limit: String(limit) })
    if (hma) q.set('hma_len', hma)
    if (sl) q.set('sl_mult', sl)
    if (tp) q.set('tp_mult', tp)
    try {
      const r = await fetch(`/api/intel/quant/backtest/${encodeURIComponent(symbol)}?${q}`)
      const j = await r.json().catch(() => null)
      if (!r.ok) setErr((j && (j.detail || j.error)) ? String(j.detail || j.error) : `HTTP ${r.status}`)
      else setData(j)
    } catch (e) { setErr(String(e)) }
    setLoading(false)
  }, [symbol, interval, limit, hma, sl, tp])
  useEffect(() => { run() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const m = data?.metrics || {}
  const tile = (label: string, value: string, color?: string, title?: string) => (
    <div className="ares-stat-tile" title={title}>
      <div className="ares-stat-label">{label}</div>
      <div className="ares-stat-value" style={{ color }}>{value}</div>
    </div>
  )
  const trades: Trade[] = data?.trades || []
  const curve: number[] = (data?.equity_curve || []).filter((x: unknown) => typeof x === 'number')

  return (
    <div style={{ marginTop: 24 }}>
      <div className="ares-section-title">
        Julia quant engine <span style={{ fontSize: 11, color: 'var(--muted)' }}>(TradingOSQuant · HMA momentum strategy · free OHLC)</span>
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="ares-input" value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} style={{ maxWidth: 110 }} aria-label="Symbol" />
        <select className="ares-input" value={interval} onChange={e => setInterval(e.target.value)} style={{ maxWidth: 80 }} aria-label="Interval">
          {INTERVALS.map(i => <option key={i} value={i}>{i}</option>)}
        </select>
        <input className="ares-input" type="number" min={60} max={500} value={limit} onChange={e => setLimit(Number(e.target.value))} style={{ maxWidth: 80 }} title="bars (60–500)" aria-label="Bars" />
        <input className="ares-input" type="number" min={2} max={200} placeholder="HMA len" value={hma} onChange={e => setHma(e.target.value)} style={{ maxWidth: 90 }} />
        <input className="ares-input" type="number" step="0.1" min={0.1} max={20} placeholder="SL ×ATR" value={sl} onChange={e => setSl(e.target.value)} style={{ maxWidth: 90 }} />
        <input className="ares-input" type="number" step="0.1" min={0.1} max={50} placeholder="TP ×ATR" value={tp} onChange={e => setTp(e.target.value)} style={{ maxWidth: 90 }} />
        <button className="btn btn-primary btn-sm" onClick={run} disabled={loading}>{loading ? 'Running…' : 'Run Julia backtest'}</button>
      </div>

      {err && <div style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 8 }}>Engine request failed: {err}</div>}
      {data && data.enabled === false && (
        <div style={{ color: 'var(--muted)', padding: 12 }}>Julia engine is switched off on this server (QUANT_JULIA_ENABLED).</div>
      )}
      {data && data.enabled !== false && data.ok === false && (
        <div style={{ color: 'var(--muted)', padding: 12 }}>Unavailable: {data.error}{data.bars != null ? ` (${data.bars} bars)` : ''}</div>
      )}
      {data?.ok && (
        <>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
            {data.symbol} · {data.interval} · {data.bars} bars · annualised with {data.bars_per_year ? Math.round(data.bars_per_year).toLocaleString() : '?'} bars/yr{data.cached ? ' · cached' : ''}
          </div>
          <div className="ares-stat-grid" style={{ marginBottom: 12 }}>
            {tile('Net return', fmt(m.net_profit_pct, 2, '%'), (m.net_profit_pct ?? 0) >= 0 ? 'var(--green)' : 'var(--danger)')}
            {tile('Sharpe', fmt(m.sharpe_ratio), undefined, 'annualised, 365-day year')}
            {tile('Sortino', fmt(m.sortino_ratio))}
            {tile('Calmar', fmt(m.calmar_ratio))}
            {tile('Max drawdown', fmt(m.max_drawdown_pct, 2, '%'), 'var(--danger)')}
            {tile('Profit factor', fmt(m.profit_factor), undefined, '— when there are no losing trades')}
            {tile('Win rate', typeof m.win_rate === 'number' ? `${(m.win_rate * 100).toFixed(1)}%` : '—')}
            {tile('Trades', `${m.total_trades ?? 0} (${m.winning_trades ?? 0}W / ${m.losing_trades ?? 0}L)`)}
            {tile('Avg hold', fmt(m.avg_trade_duration_bars, 1, ' bars'))}
          </div>
          <EquityChart points={curve} />
          {trades.length > 0 && (
            <div style={{ maxHeight: 260, overflowY: 'auto', marginTop: 12 }}>
              <table className="ares-table">
                <thead><tr><th>#</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Bars</th><th>Exit reason</th></tr></thead>
                <tbody>
                  {trades.slice().reverse().map((t, i) => (
                    <tr key={i}>
                      <td>{trades.length - i}</td>
                      <td>{t.side || '—'}</td>
                      <td style={{ fontFamily: 'monospace' }}>{fmt(t.entry_price, 4)}</td>
                      <td style={{ fontFamily: 'monospace' }}>{fmt(t.exit_price, 4)}</td>
                      <td style={{ color: (t.pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--danger)', fontFamily: 'monospace' }}>
                        {typeof t.pnl_pct === 'number' ? fmt(t.pnl_pct, 2, '%') : fmt(t.pnl, 2)}
                      </td>
                      <td>{t.bars ?? (t.exit_bar != null && t.entry_bar != null ? t.exit_bar - t.entry_bar : '—')}</td>
                      <td style={{ color: 'var(--muted)' }}>{t.exit_reason || '—'}</td>
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
