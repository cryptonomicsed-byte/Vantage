import { useEffect, useState } from 'react'

// Whales tab: largest CONFIRMED BTC transactions in recent blocks (backend
// btc_whales.py, Esplora block scan) + the newest mempool transactions.
// Real data only; when the block scan is off or still running it says so.

type Row = {
  txid?: string; txid_full?: string; value_btc: number; largest_output_btc?: number
  largest_output_address?: string; outputs?: number; inputs?: number; fee_sat?: number
  vsize?: number; size_vb?: number; block_height?: number; block_time?: number
}

function usePoll<T>(path: string, ms: number) {
  const [data, setData] = useState<T | null>(null)
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

const short = (s?: string, a = 10, b = 6) => (s ? (s.length > a + b + 1 ? `${s.slice(0, a)}…${s.slice(-b)}` : s) : '—')
const ago = (t?: number) => {
  if (!t) return '—'
  const s = Math.max(0, Math.round(Date.now() / 1000 - t))
  return s < 3600 ? `${Math.round(s / 60)}m ago` : `${(s / 3600).toFixed(1)}h ago`
}

export default function WhalesPanel() {
  const [minBtc, setMinBtc] = useState(0)
  const q = minBtc > 0 ? `?limit=25&min_value_btc=${minBtc}` : '?limit=25'
  const w = usePoll<any>(`/api/intel/whales${q}`, 30000)
  const intel = usePoll<any>('/api/intel', 60000)
  const btcPx = Number(intel.data?.anomalies?.fusion?.btc_consensus || 0)
  const conf = w.data?.confirmed
  const confirmed: Row[] = conf?.transactions || []
  const mempool: Row[] = w.data?.transactions || []
  const usd = (b: number) => (btcPx && b ? `$${Math.round(b * btcPx).toLocaleString()}` : '—')
  const link = (r: Row) => {
    const id = r.txid_full || (r.txid && !r.txid.endsWith('…') ? r.txid : '')
    return id
      ? <a href={`https://mempool.space/tx/${id}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)', fontFamily: 'monospace', fontSize: 11 }}>{short(id)}</a>
      : <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.txid || '—'}</span>
  }

  return (
    <div>
      <div className="ares-section-title" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span>Largest confirmed BTC transactions</span>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>(recent blocks · mempool.space / blockstream Esplora · coinbase excluded)</span>
        <label style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
          min BTC{' '}
          <input className="ares-input" type="number" min={0} step={10} value={minBtc}
            onChange={e => setMinBtc(Math.max(0, Number(e.target.value) || 0))} style={{ maxWidth: 90 }} />
        </label>
      </div>
      {w.err && <div style={{ color: 'var(--danger)', fontSize: 12 }}>Whales feed failed: {w.err}</div>}
      {conf && !conf.enabled && (
        <div style={{ color: 'var(--muted)', padding: 12, fontSize: 12 }}>Block scan is switched off on this server (WHALE_BLOCK_SCAN_ENABLED).</div>
      )}
      {conf?.enabled && (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
          {(conf.blocks || []).length
            ? `blocks ${conf.blocks.map((b: any) => b.height).join(', ')} · ${conf.blocks.reduce((a: number, b: any) => a + (b.tx_count || 0), 0).toLocaleString()} txs scanned`
            : 'first block scan in progress…'}
          {conf.scanning ? ' · scanning new block…' : ''}
          {conf.last_error ? ` · last error: ${conf.last_error}` : ''}
        </div>
      )}
      {confirmed.length > 0 && (
        <table className="ares-table" style={{ marginBottom: 20 }}>
          <thead><tr><th>Tx</th><th>Block</th><th>Value (BTC)</th><th>≈ USD</th><th>Largest output</th><th>In/Out</th><th>Fee (sat/vB)</th></tr></thead>
          <tbody>
            {confirmed.map((r, i) => (
              <tr key={(r.txid || '') + i} className={r.value_btc >= 100 ? 'threat-high' : ''}>
                <td>{link(r)}</td>
                <td>{r.block_height ?? '—'} <span style={{ color: 'var(--muted)', fontSize: 10 }}>{ago(r.block_time)}</span></td>
                <td style={{ fontFamily: 'monospace', fontWeight: 700 }}>{r.value_btc.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
                <td style={{ fontFamily: 'monospace' }}>{usd(r.value_btc)}</td>
                <td style={{ fontSize: 11 }}>
                  {r.largest_output_btc != null ? `${r.largest_output_btc.toLocaleString(undefined, { maximumFractionDigits: 4 })} → ` : ''}
                  {r.largest_output_address
                    ? <a href={`https://mempool.space/address/${r.largest_output_address}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)', fontFamily: 'monospace' }}>{short(r.largest_output_address, 8, 6)}</a>
                    : '—'}
                </td>
                <td style={{ color: 'var(--muted)' }}>{r.inputs ?? '—'}/{r.outputs ?? '—'}</td>
                <td style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>
                  {r.fee_sat?.toLocaleString() ?? '—'}{r.fee_sat && r.vsize ? ` (${(r.fee_sat / r.vsize).toFixed(1)})` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="ares-section-title">Newest mempool transactions <span style={{ fontSize: 11, color: 'var(--muted)' }}>(unconfirmed · the 10 newest, largest first — usually small)</span></div>
      <table className="ares-table">
        <thead><tr><th>Tx</th><th>Value (BTC)</th><th>≈ USD</th><th>Fee (sat/vB)</th></tr></thead>
        <tbody>
          {mempool.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No mempool data.</td></tr>}
          {mempool.map((r, i) => (
            <tr key={i}>
              <td>{link(r)}</td>
              <td style={{ fontFamily: 'monospace' }}>{r.value_btc}</td>
              <td style={{ fontFamily: 'monospace' }}>{usd(r.value_btc)}</td>
              <td style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{r.fee_sat?.toLocaleString()}{r.fee_sat && r.size_vb ? ` (${(r.fee_sat / r.size_vb).toFixed(1)})` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
