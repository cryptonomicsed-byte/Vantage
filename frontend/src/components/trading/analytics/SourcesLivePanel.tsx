import { useEffect, useState } from 'react'

// Measured per-host health (GET /api/intel/sources-live): every external host
// Vantage has actually called since start, with real ok/error counts,
// rate limits, last error and latency. The registry below this panel is a
// static list; this is what is really answering.

type Host = { host: string; calls: number; ok: number; errors: number; rate_limited: number
  last_status: number | null; last_error: string | null; last_ok_at: number | null
  last_call_at: number | null; avg_ms: number | null; ok_rate: number | null }

const ago = (t: number | null) => {
  if (!t) return '—'
  const s = Math.max(0, Math.round(Date.now() / 1000 - t))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`
}

export default function SourcesLivePanel() {
  const [data, setData] = useState<{ since: number; hosts: Host[] } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [onlyBad, setOnlyBad] = useState(false)
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const r = await fetch('/api/intel/sources-live')
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const j = await r.json()
        if (alive) { setData(j); setErr(null) }
      } catch (e) { if (alive) setErr(String(e)) }
    }
    load()
    const id = setInterval(load, 30000)
    return () => { alive = false; clearInterval(id) }
  }, [])
  const hosts = (data?.hosts || []).filter(h => !onlyBad || (h.ok_rate ?? 1) < 0.9)
  const color = (h: Host) => (h.ok_rate == null ? undefined : h.ok_rate >= 0.9 ? 'var(--green)' : h.ok_rate >= 0.5 ? 'var(--warning)' : 'var(--danger)')
  return (
    <div style={{ marginBottom: 20 }}>
      <div className="ares-section-title" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span>Live source health</span>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>(measured calls since {data ? new Date(data.since * 1000).toLocaleString() : '…'})</span>
        <label style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
          <input type="checkbox" checked={onlyBad} onChange={e => setOnlyBad(e.target.checked)} /> only failing
        </label>
      </div>
      {err && <div style={{ color: 'var(--danger)', fontSize: 12 }}>Failed to load: {err}</div>}
      {data && hosts.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 12, padding: 8 }}>{onlyBad ? 'No failing hosts.' : 'No calls recorded yet.'}</div>}
      {hosts.length > 0 && (
        <table className="ares-table">
          <thead><tr><th>Host</th><th>OK rate</th><th>Calls</th><th>Errors</th><th>429s</th><th>Avg ms</th><th>Last OK</th><th>Last status / error</th></tr></thead>
          <tbody>
            {hosts.map(h => (
              <tr key={h.host}>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{h.host}</td>
                <td style={{ color: color(h), fontWeight: 700 }}>{h.ok_rate == null ? '—' : `${Math.round(h.ok_rate * 100)}%`}</td>
                <td>{h.calls}</td>
                <td>{h.errors}</td>
                <td>{h.rate_limited}</td>
                <td>{h.avg_ms ?? '—'}</td>
                <td>{ago(h.last_ok_at)}</td>
                <td style={{ fontSize: 11, color: h.last_status === 200 ? 'var(--muted)' : 'var(--danger)', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  title={h.last_error || ''}>
                  {h.last_status ?? 'no response'}{h.last_status !== 200 && h.last_error ? ` · ${h.last_error}` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
