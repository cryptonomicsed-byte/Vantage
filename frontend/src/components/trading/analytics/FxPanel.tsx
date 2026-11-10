import { useEffect, useMemo, useState } from 'react'

// FX tab: rates from /api/intel/fx (ExchangeRate-API, daily), a converter,
// a searchable full table, and BTC/ETH/SOL priced in local currencies using
// the live USD prices from /api/intel (anomalies.fusion, CoinPaprika
// fallback). Official/aggregator rates only -- informal street rates (e.g.
// Cuba's CUP) can differ a lot and are not modelled here.

const MAJORS = ['EUR', 'GBP', 'JPY', 'CNY', 'CAD', 'AUD', 'CHF', 'INR', 'BRL', 'MXN', 'KRW', 'SGD']
const LOCAL = ['CUP', 'NGN', 'PHP', 'VES', 'ARS', 'KES', 'GHS', 'ZAR', 'EGP', 'TRY', 'COP', 'DOP']

function useJson(path: string, ms: number) {
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const r = await fetch(path)
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const j = await r.json()
        if (alive) { setData(j); setErr(null) }
      } catch (e) { if (alive) setErr(String(e)) }
    }
    load()
    const id = setInterval(load, ms)
    return () => { alive = false; clearInterval(id) }
  }, [path, ms])
  return { data, err }
}

const num = (v: number, d = 4) => (Number.isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: d }) : '—')

export default function FxPanel() {
  const [base, setBase] = useState('USD')
  const [amount, setAmount] = useState(100)
  const [to, setTo] = useState('EUR')
  const [q, setQ] = useState('')
  const fx = useJson(`/api/intel/fx?base=${encodeURIComponent(base)}`, 300000)
  const usdFx = useJson('/api/intel/fx?base=USD', 300000)
  const intel = useJson('/api/intel', 60000)
  const rates: Record<string, number> = fx.data?.rates || {}
  const usdRates: Record<string, number> = usdFx.data?.rates || {}
  const fusion = intel.data?.anomalies?.fusion || {}
  const coins: [string, number][] = [['BTC', Number(fusion.btc_consensus || 0)], ['ETH', Number(fusion.eth || 0)], ['SOL', Number(fusion.sol || 0)]]
  const all = useMemo(() => Object.entries(rates).filter(([c]) => !q || c.includes(q.toUpperCase())).sort(([a], [b]) => a.localeCompare(b)), [rates, q])

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="ares-input" aria-label="Base currency" value={base} onChange={e => setBase(e.target.value.toUpperCase().slice(0, 3))} style={{ maxWidth: 90 }} />
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>
          ExchangeRate-API · {fx.data?.updated ? `updated ${fx.data.updated}` : 'loading…'} · daily official/aggregator rates
        </span>
      </div>
      {fx.err && <div style={{ color: 'var(--danger)', fontSize: 12 }}>FX feed failed: {fx.err}</div>}
      {fx.data && Object.keys(rates).length === 0 && <div style={{ color: 'var(--muted)', padding: 12 }}>No rates for “{base}” — check the currency code.</div>}

      {Object.keys(rates).length > 0 && (
        <>
          <div className="ares-section-title">Convert</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
            <input className="ares-input" type="number" value={amount} onChange={e => setAmount(Number(e.target.value))} style={{ maxWidth: 140 }} aria-label="Amount" />
            <span>{base} →</span>
            <select className="ares-input" value={to} onChange={e => setTo(e.target.value)} style={{ maxWidth: 100 }} aria-label="Target currency">
              {Object.keys(rates).sort().map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <b style={{ fontFamily: 'monospace' }}>{rates[to] != null ? `${num(amount * rates[to], 2)} ${to}` : '—'}</b>
          </div>

          <div className="ares-section-title">{base} majors</div>
          <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
            {MAJORS.filter(c => rates[c] != null && c !== base).map(c => (
              <div key={c} className="ares-stat-tile"><div className="ares-stat-label">{c}</div><div className="ares-stat-value">{num(rates[c])}</div></div>
            ))}
          </div>
        </>
      )}

      <div className="ares-section-title">Crypto in local currencies <span style={{ fontSize: 11, color: 'var(--muted)' }}>(USD price × official USD rate · source {fusion.price_source || '—'})</span></div>
      {coins.every(([, p]) => !p) ? (
        <div style={{ color: 'var(--muted)', padding: 12, fontSize: 12 }}>Crypto USD prices unavailable right now.</div>
      ) : (
        <table className="ares-table" style={{ marginBottom: 16 }}>
          <thead><tr><th>Currency</th><th>USD rate</th>{coins.map(([c]) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {LOCAL.filter(c => usdRates[c] != null).map(c => (
              <tr key={c}>
                <td>{c}{c === 'CUP' ? <span title="official rate; the informal market rate is typically far higher" style={{ color: 'var(--warning)' }}> *</span> : null}</td>
                <td style={{ fontFamily: 'monospace' }}>{num(usdRates[c], 2)}</td>
                {coins.map(([k, p]) => <td key={k} style={{ fontFamily: 'monospace' }}>{p ? num(p * usdRates[c], 0) : '—'}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {Object.keys(rates).length > 0 && (
        <>
          <div className="ares-section-title" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            All {Object.keys(rates).length} rates
            <input className="ares-input" placeholder="Filter (e.g. NG)" value={q} onChange={e => setQ(e.target.value)} style={{ maxWidth: 140 }} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 4, maxHeight: 300, overflowY: 'auto' }}>
            {all.map(([c, r]) => (
              <div key={c} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12 }}>
                <span>{c}</span><span style={{ fontFamily: 'monospace' }}>{num(r)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
