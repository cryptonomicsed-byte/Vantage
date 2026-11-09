import React, { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'

// ══════════════════════════════════════════════════════════════════════════════
// LearningPanel — the learning loop in one drawer tab (Phase 1-2).
//
//   signal ledger (P1-03/04) -> mirror to Mycelium (P1-08) -> resolver marks
//   +1h/+6h/+24h (P2-03) -> source hit rates (P2-04) -> shadow rank (P2-05)
//
// Read-only: it calls GET /api/pipeline/learning, which never writes. Each
// section shows its own flag, so a section that is off says "off" instead of
// looking empty-but-broken. Nothing here places or ranks a trade -- the shadow
// rank is logged, never fed back into the live rank.
// ══════════════════════════════════════════════════════════════════════════════

interface LedgerRow {
  id: string
  door: string
  source: string
  symbol?: string
  chain?: string
  direction?: string
  conviction?: number
  entry_price_usd?: number | null
  ingested_at: number
}

interface HitRate {
  source: string
  window: string
  n: number
  hits: number
  n_eff?: number | null
  hits_eff?: number | null
  hit_rate: number | null
  brier: number | null
  min_samples_met: boolean
}

interface ShadowRow {
  source: string
  symbol: string
  direction: string
  live_rank: number
  shadow_rank: number
  conviction: number
  reliability: number
  hits: number
  n: number
  moved: number
}

interface LearningData {
  flags: Record<string, boolean>
  ledger: { total: number; recent: LedgerRow[]; error?: string }
  hit_rates: HitRate[]
  mycelium_reachable: boolean
  shadow: { ts: number | null; rows: ShadowRow[]; batches: number }
}

const FLAG_LABELS: Record<string, string> = {
  SIGNAL_RECORD_V1: 'signal_record/1',
  SIGNAL_LEDGER_ENABLED: 'Ledger',
  LEDGER_MIRROR_ENABLED: 'Mirror → Mycelium',
  SHADOW_RANK_ENABLED: 'Shadow rank',
}

function ago(ts?: number | null): string {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`
}

function Section({ title, sub, flagOn, flagName, children }: {
  title: string; sub: string; flagOn?: boolean; flagName?: string; children: React.ReactNode
}) {
  return (
    <div style={s.section}>
      <div style={s.sectionHead}>
        <span style={s.sectionTitle}>{title}</span>
        <span style={s.sectionSub}>{sub}</span>
        {flagName !== undefined && (
          <span style={{ ...s.pill, ...(flagOn ? s.pillOn : s.pillOff) }}>
            {flagOn ? 'ON' : `OFF · ${flagName}`}
          </span>
        )}
      </div>
      {children}
    </div>
  )
}

// P4-15: TradingOS challengers on live calls -- shadow only
interface ChallengerData {
  enabled: boolean; configured: boolean; total: number; would_veto: number; failed: number
  rows: { call_key: string; source: string; symbol: string; direction: string; conviction: number | null; live_rank: number | null; status: string; max_severity: string | null; would_veto: number | null; challenged_at: number }[]
}

function ChallengerSection() {
  const [d, setD] = useState<ChallengerData | null>(null)
  useEffect(() => {
    const load = () => fetch('/api/pipeline/tradingos-challenger?limit=10').then(r => r.ok ? r.json() : null).then(x => x && setD(x)).catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [])
  if (!d) return null
  const sev: Record<string, string> = { low: '#6b7280', medium: '#ffaa00', high: '#ff2d4a', critical: '#ff2d4a' }
  return (
    <Section title="TradingOS challenger" sub={`shadow only · ${d.total} challenged · ${d.would_veto} would have vetoed`}
             flagOn={d.enabled} flagName="TRADINGOS_CHALLENGER_ENABLED">
      {d.rows.length === 0 ? (
        <div style={s.note}>
          {!d.configured ? 'TradingOS is not running here (TRADINGOS_INTEL_URL unset). When set, its challengers argue against each live call and the verdict is logged next to the live rank — it never blocks or changes a trade.'
            : d.enabled ? 'Waiting for the first challenge cycle (every 5 minutes).' : 'Off.'}
        </div>
      ) : (
        <table style={s.table}>
          <thead><tr><th style={s.th}>Live</th><th style={s.th}>Call</th><th style={s.th}>Severity</th><th style={s.th}>Would veto</th><th style={s.th}>When</th></tr></thead>
          <tbody>
            {d.rows.map(r => (
              <tr key={r.call_key} style={s.tr}>
                <td style={s.tdMono}>#{r.live_rank ?? '–'}</td>
                <td style={s.td}><b>{r.symbol}</b> <span style={s.dim}>{r.direction} · {r.source}</span></td>
                <td style={{ ...s.tdMono, color: sev[r.max_severity || ''] || '#6b7280' }}>{r.status === 'failed' ? 'failed' : (r.max_severity || '–')}</td>
                <td style={s.tdMono}>{r.would_veto == null ? '–' : r.would_veto ? 'yes' : 'no'}</td>
                <td style={s.dim}>{ago(r.challenged_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  )
}

export default function LearningPanel() {
  const [data, setData] = useState<LearningData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const key = localStorage.getItem('vantage_api_key') || ''
      const r = await fetch('/api/pipeline/learning', { headers: { 'X-Agent-Key': key } })
      if (r.status === 401) { setError('Connect your agent key to see the learning loop.'); setData(null) }
      else if (r.status === 404) { setError('The learning endpoint is not deployed on this server yet.'); setData(null) }
      else if (!r.ok) { setError(`Learning status failed (${r.status})`) }
      else { setData(await r.json()); setError('') }
    } catch {
      setError('Could not reach the server.')
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [load])

  if (error && !data) return <div style={s.empty}>{error}</div>
  if (!data) return <div style={s.empty}>Loading learning loop…</div>

  const f = data.flags || {}
  const measured = data.hit_rates.filter(h => h.min_samples_met)

  return (
    <div style={{ padding: '0 16px' }}>
      <div style={s.header}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#e0e0e0' }}>Learning Loop</div>
        <div style={{ fontSize: 11, color: '#6b7280' }}>
          every signal → marked against price → per-source reliability → shadow rank (never traded on)
        </div>
        <button style={s.refresh} onClick={load} disabled={loading} title="Refresh">
          <RefreshCw size={12} style={loading ? { animation: 'spin 1s linear infinite' } : undefined} />
        </button>
      </div>

      <div style={s.flagRow}>
        {Object.keys(FLAG_LABELS).map(k => (
          <span key={k} style={{ ...s.pill, ...(f[k] ? s.pillOn : s.pillOff) }} title={k}>
            {FLAG_LABELS[k]} {f[k] ? 'on' : 'off'}
          </span>
        ))}
        <span style={{ ...s.pill, ...(data.mycelium_reachable ? s.pillOn : s.pillOff) }}>
          Mycelium {data.mycelium_reachable ? 'reachable' : 'unreachable'}
        </span>
      </div>

      <div style={s.grid}>
        <Section title="Signal ledger" sub={`${data.ledger.total.toLocaleString()} recorded, never trimmed`}
                 flagOn={f.SIGNAL_LEDGER_ENABLED} flagName="SIGNAL_LEDGER_ENABLED">
          {data.ledger.recent.length === 0 ? (
            <div style={s.note}>
              {f.SIGNAL_LEDGER_ENABLED ? 'No signals recorded yet.' : 'Ledger is off — signals still flow to the pool, but nothing is recorded for scoring.'}
            </div>
          ) : (
            <table style={s.table}>
              <thead><tr>
                <th style={s.th}>Source</th><th style={s.th}>Token</th><th style={s.th}>Dir</th>
                <th style={s.th}>Conv</th><th style={s.th}>Entry</th><th style={s.th}>Door</th><th style={s.th}>When</th>
              </tr></thead>
              <tbody>
                {data.ledger.recent.map(r => (
                  <tr key={r.id} style={s.tr}>
                    <td style={s.td}>{r.source}</td>
                    <td style={{ ...s.td, fontWeight: 600 }}>{r.symbol || '—'}<span style={s.dim}> {r.chain || ''}</span></td>
                    <td style={{ ...s.td, color: r.direction === 'long' ? '#39ff14' : r.direction === 'short' ? '#ff2d4a' : '#6b7280' }}>{r.direction || '—'}</td>
                    <td style={s.td}>{r.conviction !== undefined ? r.conviction.toFixed(2) : '—'}</td>
                    <td style={s.tdMono}>{r.entry_price_usd ? `$${Number(r.entry_price_usd).toPrecision(4)}` : <span style={s.dim} title="No price at ingest: this signal cannot be scored">unpriced</span>}</td>
                    <td style={s.dim}>{r.door}</td>
                    <td style={s.dim}>{ago(r.ingested_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <Section title="Source reliability" sub={`${measured.length} measured · ${data.hit_rates.length - measured.length} still collecting`}>
          {data.hit_rates.length === 0 ? (
            <div style={s.note}>
              {data.mycelium_reachable
                ? 'No source has a marked outcome yet. Outcomes appear after the resolver marks signals at +1h/+6h/+24h (MYCELIUM_RESOLVER_ENABLED).'
                : 'Mycelium is unreachable, so hit rates cannot be read.'}
            </div>
          ) : (
            <table style={s.table}>
              <thead><tr>
                <th style={s.th}>Source</th><th style={s.th}>Window</th><th style={s.th}>Hits / n</th>
                <th style={s.th}>Hit rate</th><th style={s.th}>Brier</th>
              </tr></thead>
              <tbody>
                {data.hit_rates.slice(0, 20).map(h => (
                  <tr key={`${h.source}-${h.window}`} style={s.tr}>
                    <td style={{ ...s.td, fontWeight: 600 }}>{h.source}</td>
                    <td style={s.dim}>{h.window}</td>
                    <td style={s.tdMono} title={h.n_eff != null ? `${h.n} signals collapse to ${h.n_eff} distinct calls (same token, direction and hour count once); the shadow rank uses the distinct count` : ''}>
                      {h.hits} / {h.n}{h.n_eff != null && h.n_eff < h.n ? <span style={s.dim}> · {Number(h.hits_eff ?? 0).toFixed(1)} / {h.n_eff} eff</span> : null}
                    </td>
                    <td style={{ ...s.tdMono, color: h.hit_rate === null ? '#6b7280' : h.hit_rate >= 0.5 ? '#39ff14' : '#ffaa00' }}
                        title={h.min_samples_met ? '' : 'Withheld below 30 scored samples'}>
                      {h.min_samples_met ? pct(h.hit_rate) : `need ${30 - h.n} more`}
                    </td>
                    <td style={s.tdMono}>{h.brier !== null ? h.brier.toFixed(3) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <Section title="Shadow rank" sub={data.shadow.ts ? `last ranked ${ago(data.shadow.ts)} · ${data.shadow.batches} batches` : 'conviction × (hits+15)/(n+30)'}
                 flagOn={f.SHADOW_RANK_ENABLED} flagName="SHADOW_RANK_ENABLED">
          {data.shadow.rows.length === 0 ? (
            <div style={s.note}>
              {f.SHADOW_RANK_ENABLED ? 'Waiting for the first ranking cycle (every 5 minutes).' : 'Shadow rank is off. When on, it re-ranks the live calls by measured source reliability and logs the difference — it never changes what is traded.'}
            </div>
          ) : (
            <table style={s.table}>
              <thead><tr>
                <th style={s.th}>Shadow</th><th style={s.th}>Live</th><th style={s.th}>Move</th>
                <th style={s.th}>Call</th><th style={s.th}>Conv</th><th style={s.th}>Reliability</th>
              </tr></thead>
              <tbody>
                {data.shadow.rows.map(r => (
                  <tr key={`${r.source}-${r.symbol}-${r.direction}-${r.shadow_rank}`} style={s.tr}>
                    <td style={{ ...s.tdMono, fontWeight: 700 }}>#{r.shadow_rank}</td>
                    <td style={s.tdMono}>#{r.live_rank}</td>
                    <td style={{ ...s.tdMono, color: r.moved > 0 ? '#39ff14' : r.moved < 0 ? '#ff2d4a' : '#6b7280' }}>
                      {r.moved > 0 ? `▲${r.moved}` : r.moved < 0 ? `▼${-r.moved}` : '–'}
                    </td>
                    <td style={s.td}><b>{r.symbol}</b> <span style={s.dim}>{r.direction} · {r.source}</span></td>
                    <td style={s.tdMono}>{r.conviction?.toFixed(2)}</td>
                    <td style={s.tdMono} title={`${Number(r.hits).toFixed(1)} hits / ${r.n} distinct calls (prior 15/30)`}>{pct(r.reliability)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <ChallengerSection />
      </div>
      {error && <div style={{ ...s.note, color: '#ffaa00' }}>{error}</div>}
    </div>
  )
}

const s: Record<string, React.CSSProperties> = {
  empty: { padding: 20, color: '#6b7280', textAlign: 'center', fontSize: 12 },
  header: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8, flexWrap: 'wrap' },
  refresh: { marginLeft: 'auto', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 5, color: '#9ca3af', cursor: 'pointer', padding: 4, display: 'flex' },
  flagRow: { display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 },
  pill: { fontSize: 10, fontWeight: 700, borderRadius: 4, padding: '2px 7px', border: '1px solid', letterSpacing: 0.3, whiteSpace: 'nowrap' },
  pillOn: { color: '#39ff14', borderColor: 'rgba(57,255,20,0.35)', background: 'rgba(57,255,20,0.06)' },
  pillOff: { color: '#6b7280', borderColor: 'rgba(107,114,128,0.35)', background: 'transparent' },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 12 },
  section: { background: 'rgba(12,12,22,0.9)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: 12, minWidth: 0 },
  sectionHead: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' },
  sectionTitle: { fontSize: 12, fontWeight: 700, color: '#e0e0e0' },
  sectionSub: { fontSize: 10, color: '#6b7280' },
  note: { fontSize: 11, color: '#6b7280', lineHeight: 1.5 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 11 },
  th: { textAlign: 'left', padding: '4px 6px', color: '#6b7280', fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 },
  tr: { borderTop: '1px solid rgba(255,255,255,0.04)' },
  td: { padding: '4px 6px', color: '#e0e0e0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 },
  tdMono: { padding: '4px 6px', color: '#e0e0e0', fontFamily: 'monospace', whiteSpace: 'nowrap' },
  dim: { color: '#6b7280', fontSize: 10 },
}
