import React, { useCallback, useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'

// ══════════════════════════════════════════════════════════════════════════════
// Shared primitives for the Prediction surface.
//
// Extracted from the original single-page tab so nine sub-tabs can share one
// vocabulary instead of nine copies of the same chip. The rules the original
// encoded still hold and are the reason these are here rather than inline:
//
//   1. A Brier of 0.000 over zero scored predictions is NOT "perfect". Every
//      gate render goes through `vacuous` and says so in words.
//   2. A 401 renders as "connect your key", never as an empty table. An empty
//      panel that is really an auth failure has cost this project days.
// ══════════════════════════════════════════════════════════════════════════════

export const apiKey = () => localStorage.getItem('vantage_api_key') || ''

// ── types ────────────────────────────────────────────────────────────────────

export interface CalStats {
  recorded: number; scored: number; pending: number
  abstentions_pending: number; abstentions_resolved: number; void: number
  brier_score: number; threshold: number; vacuous: boolean; gated: boolean
}
export interface OverviewData {
  generated_at: number
  signals: { total: number; last_24h: number; unprocessed: number; by_source: { source: string; signals: number }[] }
  decisions: { total: number; last_24h: number; verdicts: Record<string, number>; abstentions: number; directional: number; avg_confidence_24h: number }
  calibration: CalStats
  markets: { open_now: number; closing_next_24h: number; next_close: string | null }
}
export interface Market {
  id: string; source: string; market_id: string; question: string
  yes_price: number | null; no_price: number | null; volume_24h: number | null
  end_date: string | null; category: string | null; ingested_ts: number; processed: number
  verdict: string | null; confidence: number | null; direction: string | null
  conviction: string | null; should_trade: number | null; reasoning: string | null
  outcome: string | null; scored: number | null; abstained: number | null; predicted_ts: number | null
  implied_yes: number | null; implied_no: number | null; settled: boolean
}
export interface Decision {
  signal_id: string; verdict: string; confidence: number; abstained: number
  direction: string | null; conviction: string | null; should_trade: number | null
  reasoning: string | null; outcome: string | null; scored: number
  predicted_ts: number; resolved_ts: number | null
  question: string | null; source: string | null; market_id: string | null
  end_date: string | null; yes_price: number | null
}
export interface CalibrationData {
  stats: CalStats
  recent_resolutions: Decision[]
  abstention_audit: { settled_yes: number; settled_no: number; note: string }
  pending_resolution: number
}
export interface SourceRow {
  source: string; signals: number; unprocessed: number; last_ingest: number | null
  open_markets: number
  decisions: number; abstentions: number; resolved: number; pending: number; resolvable: boolean
}
export interface ContextSignal {
  source: string; market_id: string; signal_type: string
  observed_ts: number; confidence: number; direction: string
  payload: Record<string, unknown>
  match_score?: number
  matched_on?: string[]
}
export interface ContextFeed {
  count: number
  signals: ContextSignal[]
  by_source: { source: string; signal_type: string; n: number; last_seen: number }[]
  cadence_seconds: Record<string, number>
}
export interface HistoryDay {
  date: string; recorded: number; abstained: number; scored: number; brier: number | null
}
export interface HistoryData {
  window: string; days: number; series: HistoryDay[]
  totals: { recorded: number; abstained: number; scored: number; abstention_rate: number | null; brier: number | null }
  vacuous: boolean
}
export interface HealthRow extends SourceRow {
  archived_markets: number; age_seconds: number | null
  expected_interval_seconds: number | null; open_ratio: number | null
  status: 'ok' | 'stale' | 'silent' | 'untracked'
}
export interface MarketDetailData {
  market: Market
  decision: Decision | null
  context: ContextSignal[]
  venue_url: string | null
  raw?: Record<string, unknown>
  raw_bytes?: number
}

// ── data ─────────────────────────────────────────────────────────────────────

const CACHE_PREFIX = 'pm_cache:'

/**
 * Fetch + poll, seeded from localStorage so switching sub-tabs does not blank
 * the panel and re-show a spinner on every return. The cached value is shown
 * immediately and marked stale by `updatedAt`; a failed refresh clears the data
 * rather than silently serving an old number as current.
 */
export function useJSON<T>(path: string, intervalMs = 60000) {
  const [data, setData] = useState<T | null>(() => {
    try {
      const raw = localStorage.getItem(CACHE_PREFIX + path)
      return raw ? (JSON.parse(raw) as T) : null
    } catch { return null }
  })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(path, { headers: { 'X-Agent-Key': apiKey() } })
      if (r.status === 401) { setError('unauthorized'); setLoading(false); return }
      if (!r.ok) { setError(`http_${r.status}`); setLoading(false); return }
      const json = await r.json()
      setData(json)
      setError(null)
      setUpdatedAt(Date.now())
      try { localStorage.setItem(CACHE_PREFIX + path, JSON.stringify(json)) } catch { /* quota */ }
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

export const compact = (n: number | null | undefined) => {
  if (n == null || Number.isNaN(n)) return '—'
  const a = Math.abs(n)
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(Math.round(n))
}
export const pct = (v: number | null | undefined, digits = 0) =>
  v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`
export const money = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`
  return `$${v.toFixed(0)}`
}
export const ago = (ts: number | null | undefined) => {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export const until = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const s = Math.floor(t / 1000 - Date.now() / 1000)
  if (s <= 0) return 'closed'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

export const VERDICT_COLOR: Record<string, string> = {
  yes: '#22c55e', no: '#ef4444', skip: '#6b7280',
  resolved: '#22c55e', void: '#f59e0b', unresolved: '#6b7280',
}
export const DIRECTION_COLOR: Record<string, string> = {
  bullish: '#22c55e', bearish: '#ef4444', neutral: '#6b7280', catalyst: '#f59e0b',
}
export const STATUS_COLOR: Record<string, string> = {
  ok: '#22c55e', stale: '#f59e0b', silent: '#ef4444', untracked: '#6b7280',
}

/** Kalshi multi-leg collections arrive comma-joined; show the leading clause. */
export function label(m: { question?: string | null; market_id?: string; id?: string }): string {
  const q = (m.question || '').trim()
  if (!q) return m.market_id || m.id || '—'
  const first = q.split(',')[0].trim()
  return q.includes(',') ? `${first} …` : first
}

export function contextSummary(c: ContextSignal): string {
  const p = (c.payload || {}) as Record<string, unknown>
  const value = p.label ?? p.question ?? p.city ?? p.title ?? c.market_id
  return String(value)
}

// ── primitives ───────────────────────────────────────────────────────────────

export function Chip({ label: text, color, title }: { label: string; color: string; title?: string }) {
  return (
    <span title={title} style={{
      fontSize: 9, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase',
      color, border: `1px solid ${color}55`, background: `${color}18`,
      borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap',
    }}>{text}</span>
  )
}

export function StatTile({ label: text, value, sub, color }: { label: string; value: React.ReactNode; sub?: string; color?: string }) {
  return (
    <div className="ares-stat-tile">
      <div className="ares-stat-label">{text}</div>
      <div className="ares-stat-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

export function Bar({ v, color }: { v: number; color: string }) {
  return (
    <div style={{ width: 46, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.08)', flexShrink: 0 }}>
      <div style={{ width: `${Math.max(0, Math.min(100, v * 100))}%`, height: '100%', borderRadius: 2, background: color }} />
    </div>
  )
}

export function Toggle({ on, onClick, label: text, title }: { on: boolean; onClick: () => void; label: string; title?: string }) {
  return (
    <button type="button" onClick={onClick} title={title} style={{
      background: on ? 'rgba(139,92,246,0.18)' : 'rgba(255,255,255,0.04)',
      border: `1px solid ${on ? 'rgba(139,92,246,0.45)' : 'rgba(255,255,255,0.08)'}`,
      color: on ? '#c4b5fd' : 'var(--muted)', borderRadius: 7, padding: '6px 10px', fontSize: 11, cursor: 'pointer',
    }}>{text}</button>
  )
}

export function Panel({ title, icon, right, children, style }: {
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

export function AuthNotice({ error, onRetry }: { error: string | null; onRetry: () => void }) {
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
      <button type="button" onClick={onRetry} style={{
        marginLeft: 'auto', background: 'transparent', border: '1px solid currentColor', color: 'inherit',
        borderRadius: 6, padding: '3px 10px', fontSize: 11, cursor: 'pointer',
      }}>Retry</button>
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="td-empty">{children}</div>
}

export function Loading({ what }: { what: string }) {
  return <div className="td-empty">Loading {what}…</div>
}

// ── gate card (shared by Overview and Calibration) ───────────────────────────

export function GateCard({ cal }: { cal: CalibrationData | null }) {
  if (!cal) return <Loading what="calibration" />
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
