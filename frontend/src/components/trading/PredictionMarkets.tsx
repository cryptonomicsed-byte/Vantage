import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Gauge, Layers, RefreshCw, Target } from 'lucide-react'

// ══════════════════════════════════════════════════════════════════════════════
// PredictionMarkets — the dedicated surface for everything prediction-market.
//
// Before this tab the data existed twice and was visible nowhere: 55k+ venue
// signals sat in `prediction_signals`, and the decision stack wrote
// `brier_predictions` every five minutes, but the Trading section only ever
// showed token signals from /api/intel/signals. This screen is the read side
// of that pipeline: the feed, the decisions (abstentions included, because a
// skip is a recorded decision), the calibration gate, and the settlements that
// turn predictions into a score.
//
// Two rules the rendering follows:
//   1. A Brier of 0.000 over zero scored predictions is NOT "perfect". The
//      overview reports `vacuous`, and the gate card says so in words rather
//      than painting a green 0.
//   2. A 401 renders as "connect your key", never as an empty table. An empty
//      panel that is really an auth failure has cost this project days.
// ══════════════════════════════════════════════════════════════════════════════

const apiKey = () => localStorage.getItem('vantage_api_key') || ''

interface SourceCount { source: string; signals: number }
interface CalStats {
  recorded: number; scored: number; pending: number
  abstentions_pending: number; abstentions_resolved: number; void: number
  brier_score: number; threshold: number; vacuous: boolean; gated: boolean
}
interface Overview {
  generated_at: number
  signals: { total: number; last_24h: number; unprocessed: number; by_source: SourceCount[] }
  decisions: { total: number; last_24h: number; verdicts: Record<string, number>; abstentions: number; directional: number; avg_confidence_24h: number }
  calibration: CalStats
  markets: { open_now: number; closing_next_24h: number; next_close: string | null }
}
interface Market {
  id: string; source: string; market_id: string; question: string
  yes_price: number | null; no_price: number | null; volume_24h: number | null
  end_date: string | null; category: string | null; ingested_ts: number; processed: number
  verdict: string | null; confidence: number | null; direction: string | null
  conviction: string | null; should_trade: number | null; reasoning: string | null
  outcome: string | null; scored: number | null; abstained: number | null; predicted_ts: number | null
  implied_yes: number | null; implied_no: number | null; settled: boolean
}
interface Decision {
  signal_id: string; verdict: string; confidence: number; abstained: number
  direction: string | null; conviction: string | null; should_trade: number | null
  reasoning: string | null; outcome: string | null; scored: number
  predicted_ts: number; resolved_ts: number | null
  question: string | null; source: string | null; market_id: string | null
  end_date: string | null; yes_price: number | null
}
interface Calibration {
  stats: CalStats
  recent_resolutions: Decision[]
  abstention_audit: { settled_yes: number; settled_no: number; note: string }
  pending_resolution: number
}
interface SourceRow {
  source: string; signals: number; unprocessed: number; last_ingest: number | null
  open_markets: number
  decisions: number; abstentions: number; resolved: number; pending: number; resolvable: boolean
}
interface ContextSignal {
  source: string; market_id: string; signal_type: string
  observed_ts: number; confidence: number; direction: string
  payload: Record<string, unknown>
}
interface ContextFeed {
  count: number
  signals: ContextSignal[]
  by_source: { source: string; signal_type: string; n: number; last_seen: number }[]
  cadence_seconds: Record<string, number>
}

function useJSON<T>(path: string, intervalMs = 60000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(path, { headers: { 'X-Agent-Key': apiKey() } })
      if (r.status === 401) { setError('unauthorized'); setLoading(false); return }
      if (!r.ok) { setError(`http_${r.status}`); setLoading(false); return }
      setData(await r.json())
      setError(null)
      setUpdatedAt(Date.now())
    } catch {
      setError('network')
    }
    setLoading(false)
  }, [path])

  useEffect(() => {
    load()
    const t = setInterval(load, intervalMs)
    return () => clearInterval(t)
  }, [load, intervalMs])

  return { data, error, loading, updatedAt, refresh: load }
}

// ── formatting ───────────────────────────────────────────────────────────────

const compact = (n: number | null | undefined) => {
  if (n == null || Number.isNaN(n)) return '—'
  const a = Math.abs(n)
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(Math.round(n))
}
const pct = (v: number | null | undefined, digits = 0) =>
  v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`
const money = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`
  return `$${v.toFixed(0)}`
}
const ago = (ts: number | null | undefined) => {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
const until = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const s = Math.floor(t / 1000 - Date.now() / 1000)
  if (s <= 0) return 'closed'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

const VERDICT_COLOR: Record<string, string> = {
  yes: '#22c55e', no: '#ef4444', skip: '#6b7280',
  resolved: '#22c55e', void: '#f59e0b', unresolved: '#6b7280',
}

// Context-signal direction: what the evidence argues for, not what the venue
// price says. "catalyst" means "something is due" with no direction.
const DIRECTION_COLOR: Record<string, string> = {
  bullish: '#22c55e', bearish: '#ef4444', neutral: '#6b7280', catalyst: '#f59e0b',
}

function contextSummary(c: ContextSignal): string {
  const p = (c.payload || {}) as Record<string, unknown>
  const value = p.label ?? p.question ?? p.city ?? p.title ?? c.market_id
  return String(value)
}

// Kalshi multi-leg collections arrive as one comma-joined title ("yes $79,800
// or above,yes $2,405 or above,…"). The row shows the leading clause and the
// full text on hover; the detail panel always shows all of it.
function label(m: Market): string {
  const q = (m.question || '').trim()
  if (!q) return m.market_id || m.id
  const first = q.split(',')[0].trim()
  return q.includes(',') ? `${first} …` : first
}

function Chip({ label, color, title }: { label: string; color: string; title?: string }) {
  return (
    <span
      title={title}
      style={{
        fontSize: 9, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase',
        color, border: `1px solid ${color}55`, background: `${color}18`,
        borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap',
      }}
    >{label}</span>
  )
}

function StatTile({ label, value, sub, color }: { label: string; value: React.ReactNode; sub?: string; color?: string }) {
  return (
    <div className="ares-stat-tile">
      <div className="ares-stat-label">{label}</div>
      <div className="ares-stat-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function Bar({ v, color }: { v: number; color: string }) {
  return (
    <div style={{ width: 46, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.08)', flexShrink: 0 }}>
      <div style={{ width: `${Math.max(0, Math.min(100, v * 100))}%`, height: '100%', borderRadius: 2, background: color }} />
    </div>
  )
}

function Toggle({ on, onClick, label, title }: { on: boolean; onClick: () => void; label: string; title?: string }) {
  return (
    <button
      type="button" onClick={onClick} title={title}
      style={{
        background: on ? 'rgba(139,92,246,0.18)' : 'rgba(255,255,255,0.04)',
        border: `1px solid ${on ? 'rgba(139,92,246,0.45)' : 'rgba(255,255,255,0.08)'}`,
        color: on ? '#c4b5fd' : 'var(--muted)', borderRadius: 7, padding: '6px 10px', fontSize: 11, cursor: 'pointer',
      }}
    >{label}</button>
  )
}

function Panel({ title, icon, right, children, style }: {
  title: string; icon?: React.ReactNode; right?: React.ReactNode
  children: React.ReactNode; style?: React.CSSProperties
}) {
  return (
    <div className="td-card" style={style}>
      <div className="td-card-header">
        {icon}
        <span>{title}</span>
        {right && <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>{right}</div>}
      </div>
      {children}
    </div>
  )
}

function AuthNotice({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  if (!error) return null
  const unauthorized = error === 'unauthorized'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderRadius: 10,
      border: `1px solid ${unauthorized ? 'rgba(245,158,11,.35)' : 'rgba(239,68,68,.35)'}`,
      background: unauthorized ? 'rgba(245,158,11,.08)' : 'rgba(239,68,68,.08)',
      color: unauthorized ? '#f59e0b' : '#ef4444', fontSize: 12,
    }}>
      <AlertTriangle size={15} />
      {unauthorized
        ? 'Not authorized — this tab needs the API key from the dashboard connect form (stored as vantage_api_key). An empty panel here is an auth failure, not an empty feed.'
        : `Could not load prediction data (${error}).`}
      <button
        type="button" onClick={onRetry}
        style={{ marginLeft: 'auto', background: 'transparent', border: '1px solid currentColor', color: 'inherit', borderRadius: 6, padding: '3px 10px', fontSize: 11, cursor: 'pointer' }}
      >Retry</button>
    </div>
  )
}

// ── gate card ────────────────────────────────────────────────────────────────

function GateCard({ cal }: { cal: Calibration | null }) {
  if (!cal) return <div className="td-empty">Loading calibration…</div>
  const s = cal.stats
  const state = s.vacuous ? 'VACUOUS' : (s.gated ? 'CLOSED' : 'OPEN')
  const color = s.vacuous ? '#f59e0b' : (s.gated ? '#ef4444' : '#22c55e')

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ fontFamily: 'monospace', fontSize: 30, fontWeight: 700, color }}>
          {s.vacuous ? '—' : s.brier_score.toFixed(3)}
        </div>
        <Chip label={state} color={color} />
        <div style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto' }}>
          closes above {s.threshold.toFixed(2)}
        </div>
      </div>

      <div style={{ fontSize: 11, color: 'var(--muted)', margin: '6px 0 12px', lineHeight: 1.45 }}>
        {s.vacuous
          ? `No scored directional predictions yet (recorded=${s.recorded}, pending=${s.pending}). A 0.000 over an empty window is not calibration — the gate has nothing to grade and stays open by default.`
          : s.gated
            ? `Rolling Brier ${s.brier_score.toFixed(3)} is above the ${s.threshold.toFixed(2)} threshold: emission is gated until accuracy recovers.`
            : `${s.scored} scored prediction${s.scored === 1 ? '' : 's'} in the rolling window; accuracy is inside the ${s.threshold.toFixed(2)} threshold.`}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: '6px 14px', fontSize: 11 }}>
        {([
          ['recorded', s.recorded], ['scored', s.scored], ['pending settlement', s.pending],
          ['abstentions (open)', s.abstentions_pending], ['abstentions (settled)', s.abstentions_resolved],
          ['void', s.void],
        ] as [string, number][]).map(([k, v]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--muted)' }}>{k}</span>
            <span style={{ fontFamily: 'monospace', color: '#fff' }}>{compact(v)}</span>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
        Abstentions are recorded and resolved but excluded from the score — scoring a call that was never made inverts the number.
        Settled skips: <span style={{ color: '#fff' }}>{cal.abstention_audit.settled_yes}</span> resolved yes,{' '}
        <span style={{ color: '#fff' }}>{cal.abstention_audit.settled_no}</span> resolved no.
      </div>

      {cal.recent_resolutions.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--muted)', marginBottom: 6 }}>Recent settlements</div>
          {cal.recent_resolutions.slice(0, 6).map(r => (
            <div key={r.signal_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
              <Chip label={r.outcome || '—'} color={VERDICT_COLOR[r.outcome || 'unresolved']} />
              <span style={{ flex: 1, minWidth: 0, fontSize: 11, color: '#ffffffb3', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.question || r.signal_id}</span>
              <Chip label={r.verdict} color={VERDICT_COLOR[r.verdict] || '#6b7280'} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── market detail ────────────────────────────────────────────────────────────

function MarketDetail({ m }: { m: Market }) {
  const vColor = VERDICT_COLOR[m.verdict || 'skip'] || '#6b7280'
  return (
    <div>
      <div style={{ fontSize: 13, color: '#fff', lineHeight: 1.45, marginBottom: 10 }}>{m.question || '(no question)'}</div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        <Chip label={m.source} color="#8b5cf6" />
        {m.verdict ? <Chip label={m.verdict} color={vColor} /> : <Chip label="undecided" color="#6b7280" />}
        {m.settled ? <Chip label={`settled ${m.outcome}`} color={VERDICT_COLOR[m.outcome || 'unresolved']} /> : <Chip label="unsettled" color="#6b7280" />}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: '6px 14px', fontSize: 11, marginBottom: 12 }}>
        {([
          ['yes price', m.implied_yes == null ? '—' : pct(m.implied_yes, 1)],
          ['no price', m.implied_no == null ? '—' : pct(m.implied_no, 1)],
          ['24h volume', money(m.volume_24h)],
          ['closes in', until(m.end_date)],
          ['ingested', ago(m.ingested_ts)],
          ['decision made', m.predicted_ts ? ago(m.predicted_ts) : '—'],
        ] as [string, string][]).map(([k, v]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--muted)' }}>{k}</span>
            <span style={{ fontFamily: 'monospace', color: '#fff' }}>{v}</span>
          </div>
        ))}
      </div>

      {m.verdict && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>decision</span>
            <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#fff' }}>conf {pct(m.confidence, 0)}</span>
            <Bar v={m.confidence || 0} color={vColor} />
            {m.direction && <Chip label={`dir ${m.direction}`} color="#00f5ff" />}
            {m.conviction && <Chip label={`conv ${m.conviction}`} color="#f59e0b" />}
            {m.abstained ? <Chip label="abstained" color="#6b7280" title="recorded as a non-prediction; excluded from Brier" /> : null}
          </div>
          <div style={{ fontSize: 10, color: '#ffffff80', fontFamily: 'monospace', lineHeight: 1.5, wordBreak: 'break-word' }}>
            {m.reasoning || '(no reasoning recorded)'}
          </div>
        </div>
      )}

      <div style={{ marginTop: 10, fontSize: 9, color: 'var(--muted)', fontFamily: 'monospace', wordBreak: 'break-all' }}>
        {m.market_id}
      </div>
    </div>
  )
}

// ── main ─────────────────────────────────────────────────────────────────────

export default function PredictionMarkets() {
  const [source, setSource] = useState('')
  // Default to open markets: the feed's newest ingest is often archived
  // markets re-collected from the venue, and a tab that opens on 2024
  // nomination markets answered by 0% is a tab nobody reads twice.
  const [openOnly, setOpenOnly] = useState(true)
  const [decidedOnly, setDecidedOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)

  const overview = useJSON<Overview>('/api/trading/prediction/overview', 60000)
  const markets = useJSON<{ markets: Market[]; count: number }>(
    `/api/trading/prediction/markets?limit=60${source ? `&source=${encodeURIComponent(source)}` : ''}${openOnly ? '&open_only=true' : ''}${decidedOnly ? '&decided_only=true' : ''}${search ? `&q=${encodeURIComponent(search)}` : ''}`,
    60000,
  )
  const decisions = useJSON<{ decisions: Decision[] }>('/api/trading/prediction/decisions?limit=40', 60000)
  const calibration = useJSON<Calibration>('/api/trading/prediction/calibration', 60000)
  const sources = useJSON<{ sources: SourceRow[] }>('/api/trading/prediction/sources', 60000)
  const context = useJSON<ContextFeed>('/api/trading/prediction/context?limit=14', 60000)

  const o = overview.data
  const cal = o?.calibration
  const rows = markets.data?.markets || []
  const selectedMarket = useMemo(() => rows.find(r => r.id === selected) || null, [rows, selected])

  const refreshAll = () => { overview.refresh(); markets.refresh(); decisions.refresh(); calibration.refresh(); sources.refresh(); context.refresh() }
  const anyError = overview.error || markets.error || calibration.error

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Target size={17} style={{ color: '#8b5cf6' }} />
        <div style={{ fontSize: 13, color: '#fff', fontFamily: 'Orbitron, sans-serif', letterSpacing: '0.03em' }}>
          Prediction Markets
        </div>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          venue signals → Jev decision → Brier gate → settlement
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {overview.updatedAt && (
            <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>
              updated {new Date(overview.updatedAt).toLocaleTimeString()}
            </span>
          )}
          <button type="button" className="td-refresh-btn" onClick={refreshAll} title="Refresh">
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      <AuthNotice error={anyError} onRetry={refreshAll} />

      {/* tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
        <StatTile label="Signals ingested" value={compact(o?.signals.total)} sub={`${compact(o?.signals.last_24h)} in 24h`} />
        <StatTile label="Decisions" value={compact(o?.decisions.total)} sub={`${compact(o?.decisions.last_24h)} in 24h`} />
        <StatTile
          label="Abstention rate"
          value={o && o.decisions.total > 0
            ? pct(o.decisions.abstentions / o.decisions.total,
                  o.decisions.abstentions / o.decisions.total > 0.99 ? 1 : 0)
            : '—'}
          sub={`${compact(o?.decisions.abstentions)} skips recorded`}
          color={o && o.decisions.total > 0 && o.decisions.abstentions / o.decisions.total > 0.9 ? '#f59e0b' : undefined}
        />
        <StatTile
          label="Brier (rolling)"
          value={cal ? (cal.vacuous ? '—' : cal.brier_score.toFixed(3)) : '—'}
          sub={cal ? (cal.vacuous ? 'no scored predictions' : `threshold ${cal.threshold.toFixed(2)}`) : ''}
          color={cal ? (cal.vacuous ? '#f59e0b' : cal.gated ? '#ef4444' : '#22c55e') : undefined}
        />
        <StatTile label="Open markets" value={compact(o?.markets.open_now)} sub={`${compact(o?.markets.closing_next_24h)} close <24h`} />
        <StatTile label="Pending settlement" value={compact(o?.calibration.pending)} sub={`${compact(o?.calibration.scored)} scored`} />
      </div>

      {/* main grid */}
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* feed */}
        <div style={{ flex: '3 1 560px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Panel
            title="Market feed"
            icon={<Layers size={13} />}
            right={<span className="td-badge">{rows.length} shown</span>}
          >
            <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="search question…"
                style={{ flex: '1 1 180px', minWidth: 140, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 7, padding: '6px 10px', color: '#fff', fontSize: 11, outline: 'none' }}
              />
              <select
                value={source}
                onChange={e => setSource(e.target.value)}
                style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 7, padding: '6px 8px', color: '#fff', fontSize: 11 }}
              >
                <option value="">all venues</option>
                {(o?.signals.by_source || []).map(s => <option key={s.source} value={s.source}>{s.source}</option>)}
              </select>
              <Toggle on={openOnly} onClick={() => setOpenOnly(v => !v)} label="open only"
                title="only markets whose end_date is in the future" />
              <Toggle on={decidedOnly} onClick={() => setDecidedOnly(v => !v)} label="decided only"
                title="only markets the decision stack has already graded" />
            </div>

            <div style={{ display: 'flex', gap: 8, padding: '0 4px 6px', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--muted)' }}>
              <div style={{ flex: 1, minWidth: 0 }}>market</div>
              <div style={{ width: 54, textAlign: 'right' }}>yes</div>
              <div style={{ width: 66, textAlign: 'right' }}>vol 24h</div>
              <div style={{ width: 54, textAlign: 'right' }}>closes</div>
              <div style={{ width: 104 }}>decision</div>
            </div>

            {markets.loading && rows.length === 0 && <div className="td-empty">Loading feed…</div>}
            {!markets.loading && rows.length === 0 && !markets.error && (
              <div className="td-empty">
                No markets match this filter. The collectors ingest every few minutes; an empty feed with a healthy
                overview means the filter, not the pipeline.
              </div>
            )}

            <div style={{ maxHeight: 520, overflowY: 'auto' }}>
              {rows.map(m => {
                const vColor = VERDICT_COLOR[m.verdict || 'skip'] || '#6b7280'
                return (
                  <div
                    key={m.id}
                    onClick={() => setSelected(m.id === selected ? null : m.id)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '7px 4px', cursor: 'pointer',
                      borderBottom: '1px solid rgba(255,255,255,0.04)',
                      background: m.id === selected ? 'rgba(139,92,246,0.09)' : 'transparent',
                    }}
                    onMouseEnter={e => { if (m.id !== selected) e.currentTarget.style.background = 'rgba(255,255,255,0.02)' }}
                    onMouseLeave={e => { if (m.id !== selected) e.currentTarget.style.background = 'transparent' }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div title={m.question || m.market_id} style={{ fontSize: 11, color: '#ffffffe6', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {label(m)}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 2 }}>
                        <Chip label={m.source} color="#8b5cf6" />
                        {m.settled && <Chip label={m.outcome || ''} color={VERDICT_COLOR[m.outcome || 'unresolved']} />}
                        <span style={{ fontSize: 9, color: 'var(--muted)' }}>{ago(m.ingested_ts)}</span>
                      </div>
                    </div>
                    <div style={{ width: 54, textAlign: 'right', fontFamily: 'monospace', fontSize: 11, color: '#fff' }}>
                      {m.implied_yes == null ? '—' : pct(m.implied_yes, 0)}
                    </div>
                    <div style={{ width: 66, textAlign: 'right', fontFamily: 'monospace', fontSize: 10, color: 'var(--muted)' }}>
                      {money(m.volume_24h)}
                    </div>
                    <div style={{ width: 54, textAlign: 'right', fontFamily: 'monospace', fontSize: 10, color: 'var(--muted)' }}>
                      {until(m.end_date)}
                    </div>
                    <div style={{ width: 104, display: 'flex', alignItems: 'center', gap: 6 }}>
                      {m.verdict
                        ? <>
                            <Chip label={m.verdict} color={vColor} />
                            <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#ffffff99' }}>{pct(m.confidence, 0)}</span>
                          </>
                        : <span style={{ fontSize: 10, color: 'var(--muted)' }}>undecided</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          </Panel>

          <Panel title="Recent decisions" icon={<Activity size={13} />} right={<span className="td-badge">{decisions.data?.decisions.length || 0}</span>}>
            {(decisions.data?.decisions || []).length === 0 && <div className="td-empty">No decisions recorded yet.</div>}
            <div style={{ maxHeight: 320, overflowY: 'auto' }}>
              {(decisions.data?.decisions || []).slice(0, 24).map(d => {
                const vColor = VERDICT_COLOR[d.verdict] || '#6b7280'
                return (
                  <div key={d.signal_id} style={{ display: 'flex', gap: 8, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <div style={{ width: 62, display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'flex-start' }}>
                      <Chip label={d.verdict} color={vColor} />
                      <span style={{ fontFamily: 'monospace', fontSize: 9, color: '#ffffff80' }}>{pct(d.confidence, 0)}</span>
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 11, color: '#ffffffe6', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {d.question || d.signal_id}
                      </div>
                      <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontFamily: 'monospace' }}>
                        {d.reasoning || '—'}
                      </div>
                    </div>
                    <div style={{ width: 70, textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
                      <span style={{ fontSize: 9, color: 'var(--muted)' }}>{ago(d.predicted_ts)}</span>
                      {d.outcome
                        ? <Chip label={d.outcome} color={VERDICT_COLOR[d.outcome] || '#6b7280'} />
                        : <span style={{ fontSize: 9, color: '#ffffff40' }}>pending</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          </Panel>
        </div>

        {/* side column */}
        <div style={{ flex: '1 1 300px', minWidth: 280, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {selectedMarket && (
            <Panel title="Market detail" icon={<Target size={13} />} right={
              <button type="button" onClick={() => setSelected(null)}
                style={{ background: 'transparent', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 11 }}>close</button>
            }>
              <MarketDetail m={selectedMarket} />
            </Panel>
          )}

          <Panel title="Calibration gate" icon={<Gauge size={13} />}>
            <GateCard cal={calibration.data} />
          </Panel>

          <Panel title="Context feeds" icon={<Activity size={13} />} right={
            <span className="td-badge">{context.data?.count ?? 0}</span>
          }>
            {(context.data?.signals || []).length === 0 && (
              <div className="td-empty">
                No outside evidence stored yet. The context daemon polls FRED (6h), Open-Meteo (30m)
                and Manifold (15m); a source that is down simply leaves the others running.
              </div>
            )}
            {(context.data?.signals || []).map(c => {
              const color = DIRECTION_COLOR[c.direction] || '#6b7280'
              return (
                <div key={`${c.source}:${c.market_id}`} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <Chip label={c.source} color="#00f5ff" />
                  <span style={{ flex: 1, minWidth: 0, fontSize: 11, color: '#ffffffe6', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {contextSummary(c)}
                  </span>
                  <Chip label={c.direction} color={color} />
                  <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#ffffff99', width: 34, textAlign: 'right' }}>
                    {Math.round(c.confidence * 100)}%
                  </span>
                  <span style={{ fontSize: 9, color: 'var(--muted)', width: 44, textAlign: 'right' }}>
                    {ago(c.observed_ts)}
                  </span>
                </div>
              )
            })}
            {(context.data?.signals || []).length > 0 && (
              <div style={{ marginTop: 8, fontSize: 9, color: 'var(--muted)', lineHeight: 1.5 }}>
                Evidence only — these feeds never settle a market. The venue price stays the source of record
                and the Brier gate only ever scores venue settlements.
              </div>
            )}
          </Panel>

          <Panel title="Venues" icon={<Layers size={13} />}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(sources.data?.sources || []).map(s => (
                <div key={s.source} style={{ padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Chip label={s.source} color="#8b5cf6" />
                    <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#fff' }}>{compact(s.signals)}</span>
                    <span style={{ fontSize: 9, color: 'var(--muted)' }}>signals</span>
                    <span style={{ marginLeft: 'auto', fontFamily: 'monospace', fontSize: 11, color: s.open_markets ? '#22c55e' : '#f59e0b' }}>
                      {compact(s.open_markets)}
                    </span>
                    <span style={{ fontSize: 9, color: 'var(--muted)' }}>open</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 4, fontSize: 9, color: 'var(--muted)' }}>
                    <span style={{ fontFamily: 'monospace', color: '#ffffffb3' }}>{compact(s.decisions)}</span> decisions
                    <span>·</span>
                    <span style={{ fontFamily: 'monospace', color: '#ffffffb3' }}>{compact(s.resolved)}</span> resolved
                    <span>·</span>
                    <span>{ago(s.last_ingest)}</span>
                    {!s.resolvable && <Chip label="no reader" color="#f59e0b" title="no settlement reader for this venue" />}
                    {s.signals > 0 && s.open_markets === 0 && (
                      <Chip label="stale feed" color="#ef4444" title="still ingesting, but no market it ingested is open — the collector is pulling archived markets" />
                    )}
                  </div>
                </div>
              ))}
              {(sources.data?.sources || []).length === 0 && <div className="td-empty">No venues reporting.</div>}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
