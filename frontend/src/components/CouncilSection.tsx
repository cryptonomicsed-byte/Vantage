import React, { useEffect, useState } from 'react'

// Ares Council — read-only dashboard tab. API auth handled by the global
// X-Agent-Key interceptor (src/utils/apiKeyInterceptor.ts).

interface Vote { persona: string; direction: string; confidence: number; weight: number; rationale: string }
interface Verdict {
  id: number; symbol: string; direction: string; conviction: number
  entry_price: number | null; outcome: string; paper: number; posted_at: string; votes: Vote[]
}
interface CalRow {
  persona: string; role: string; base_weight: number; veto: boolean
  correct: number; total: number; rate: number | null; multiplier: number; eff_weight: number
}
interface Trace { ts?: string; kind?: string; action?: string; target?: string }

const S = {
  page: { padding: '1.5em', maxWidth: 1100, margin: '0 auto', fontFamily: 'ui-monospace, Menlo, monospace', color: '#c9d1d9' } as React.CSSProperties,
  h1: { color: '#58a6ff', fontSize: '1.3em' },
  row: { display: 'flex', gap: '1.5em', flexWrap: 'wrap' as const, margin: '1em 0' },
  card: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: '12px 16px', minWidth: 150 },
  label: { color: '#8b949e', fontSize: '.75em' },
  val: { fontSize: '1.3em', fontWeight: 700 as const },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '.85em' },
  th: { color: '#8b949e', textAlign: 'left' as const, padding: '6px 8px', borderBottom: '1px solid #21262d' },
  td: { padding: '6px 8px', borderBottom: '1px solid #21262d' },
  buy: { color: '#3fb950' }, sell: { color: '#f85149' }, abstain: { color: '#8b949e' },
  win: { color: '#3fb950' }, loss: { color: '#f85149' }, pending: { color: '#d29922' },
  bar: { background: '#21262d', borderRadius: 4, height: 12, width: 140, display: 'inline-block', verticalAlign: 'middle', overflow: 'hidden' },
  veto: { color: '#f85149', fontWeight: 700 },
  badge: { padding: '2px 8px', borderRadius: 10, fontSize: '.72em', marginLeft: 8, background: '#238636', color: '#fff' },
  badgeWarn: { padding: '2px 8px', borderRadius: 10, fontSize: '.72em', marginLeft: 8, background: '#9e6a03', color: '#fff' },
  badgeDown: { padding: '2px 8px', borderRadius: 10, fontSize: '.72em', marginLeft: 8, background: '#da3633', color: '#fff' },
  muted: { color: '#8b949e', fontSize: '.78em' },
  summary: { color: '#8b949e', fontSize: '.8em', cursor: 'pointer' },
  voteRow: { fontSize: '.78em', padding: '2px 0 2px 14px' },
}

function dirCls(d: string): React.CSSProperties {
  const u = (d || '').toUpperCase()
  if (u === 'BUY') return S.buy
  if (u === 'SELL') return S.sell
  return S.abstain
}

export default function CouncilSection() {
  const [verdicts, setVerdicts] = useState<Verdict[]>([])
  const [cal, setCal] = useState<CalRow[]>([])
  const [traces, setTraces] = useState<Trace[]>([])
  const [ov, setOv] = useState<any>(null)
  const [err, setErr] = useState('')

  const load = async () => {
    try {
      const [o, v, c, s] = await Promise.all([
        fetch('/api/council/overview').then(r => r.json()),
        fetch('/api/council/verdicts?limit=25').then(r => r.json()),
        fetch('/api/council/calibration').then(r => r.json()),
        fetch('/api/council/substrate').then(r => r.json()),
      ])
      setOv(o); setVerdicts(Array.isArray(v) ? v : []); setCal(Array.isArray(c) ? c : [])
      setTraces((s && s.council_traces) || []); setErr('')
    } catch (e: any) { setErr(String(e)) }
  }

  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t) }, [])

  return (
    <div style={S.page}>
      <h1 style={S.h1}>
        ⚖️ Ares Council
        {ov && (ov.daemon_running
          ? <span style={S.badge}>daemon {ov.daemon_pid}</span>
          : <span style={S.badgeDown}>daemon DOWN</span>)}
      </h1>
      {err && <p style={S.loss as React.CSSProperties}>ERR {err}</p>}
      <div style={S.row}>
        <div style={S.card}><div style={S.label}>Verdicts</div><div style={S.val}>{ov?.verdict_count ?? '—'}</div></div>
        <div style={S.card}><div style={S.label}>Mycelium</div>
          <div style={{ ...S.val, fontSize: '1em' }}>{ov?.mycelium?.status ?? '—'} · {ov?.mycelium?.traces ?? '?'} traces</div></div>
        <div style={S.card}><div style={S.label}>Trace buffer pending</div><div style={S.val}>{ov?.trace_buffer_pending ?? '—'}</div></div>
      </div>

      <h2 style={{ color: '#7ee787', fontSize: '1em' }}>Verdicts</h2>
      <table style={S.table}>
        <thead><tr><th style={S.th}>#</th><th style={S.th}>Symbol</th><th style={S.th}>Dir</th><th style={S.th}>Conv</th>
          <th style={S.th}>Outcome</th><th style={S.th}>Mode</th><th style={S.th}>Time (UTC)</th><th style={S.th}>Votes</th></tr></thead>
        <tbody>
          {verdicts.map(v => (
            <tr key={v.id}>
              <td style={S.td}>{v.id}</td>
              <td style={{ ...S.td, fontWeight: 700 }}>{v.symbol}</td>
              <td style={{ ...S.td, ...dirCls(v.direction) }}>{v.direction || 'ABSTAIN'}</td>
              <td style={S.td}>{(v.conviction ?? 0).toFixed(2)}</td>
              <td style={{ ...S.td, ...(v.outcome === 'win' ? S.win : v.outcome === 'loss' ? S.loss : S.pending) }}>{v.outcome || 'pending'}</td>
              <td style={S.td}>{v.paper ? <span style={S.badgeWarn}>PAPER</span> : <span style={S.badge}>LIVE</span>}</td>
              <td style={{ ...S.td, ...S.muted }}>{(v.posted_at || '').slice(0, 16)}</td>
              <td style={S.td}>
                <details>
                  <summary style={S.summary}>{v.votes.length} members</summary>
                  {v.votes.map((x, i) => (
                    <div key={i} style={S.voteRow}>
                      <span style={dirCls(x.direction)}>{x.direction}</span> {x.persona}{' '}
                      <span style={S.muted}>conf {x.confidence.toFixed(2)} · w {x.weight.toFixed(2)}</span>
                      <br /><span style={S.muted}>{(x.rationale || '').slice(0, 160)}</span>
                    </div>
                  ))}
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ color: '#7ee787', fontSize: '1em', marginTop: '1.5em' }}>Calibration</h2>
      <table style={S.table}>
        <thead><tr><th style={S.th}>Persona</th><th style={S.th}>Role</th><th style={S.th}>Wins</th><th style={S.th}>Tracked</th>
          <th style={S.th}>Win rate</th><th style={S.th}>Weight mult</th><th style={S.th}>Eff. weight</th></tr></thead>
        <tbody>
          {cal.map(p => (
            <tr key={p.persona}>
              <td style={{ ...S.td, fontWeight: 700 }}>{p.persona}{p.veto ? <span style={S.veto}> VETO</span> : ''}</td>
              <td style={{ ...S.td, ...S.muted }}>{p.role}</td>
              <td style={S.td}>{p.correct}</td><td style={S.td}>{p.total}</td>
              <td style={S.td}>{p.rate == null ? '—' : p.rate + '%'}</td>
              <td style={S.td}>
                <span style={S.bar}><span style={{ display: 'block', height: '100%', width: `${Math.min(100, p.multiplier * 50)}%`, background: '#58a6ff' }} /></span>{' '}
                {p.multiplier.toFixed(2)}
              </td>
              <td style={S.td}>{p.eff_weight.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ color: '#7ee787', fontSize: '1em', marginTop: '1.5em' }}>Substrate (Mycelium council traces)</h2>
      {traces.length === 0 ? <p style={S.muted}>No traces yet.</p> : (
        <table style={S.table}>
          <thead><tr><th style={S.th}>ts</th><th style={S.th}>kind</th><th style={S.th}>action</th><th style={S.th}>target</th></tr></thead>
          <tbody>
            {traces.slice(0, 25).map((t, i) => (
              <tr key={i}>
                <td style={{ ...S.td, ...S.muted }}>{(t.ts || '').slice(0, 19)}</td>
                <td style={S.td}>{t.kind}</td><td style={S.td}>{t.action}</td><td style={S.td}>{t.target || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p style={{ ...S.muted, marginTop: '1.5em' }}>
        Council: 6 personas · 2-round debate · calibration-weighted votes · Risk veto · PAPER mode by default. Verdicts also post to the feed.
      </p>
    </div>
  )
}
