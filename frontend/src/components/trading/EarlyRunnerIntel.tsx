import React, { useCallback, useEffect, useState } from 'react'
import { Radio, RefreshCw, Rocket, ChevronDown, ChevronRight } from 'lucide-react'
import { TokenLink, WalletLink } from './EntityProfileCard'

// ══════════════════════════════════════════════════════════════════════════════
// EarlyRunnerIntel — the early-runner chain inside Degen Trenches (Phase 4).
//
//   curve feed (P4-01) → runners (P4-02) → backtrace (P4-03) → watchlist (P4-06)
//   → confluence pings (P4-09)
//
// Each section reads its own read-only endpoint and says plainly when its
// flag is off or it has nothing yet. Nothing here trades.
// ══════════════════════════════════════════════════════════════════════════════

interface Gap { id: number; start_ts: number; end_ts: number | null; backfill: string; backfilled_events: number; note: string | null }
interface Coverage {
  enabled: boolean
  source_pref: string
  state: { running: boolean; connected: boolean; source: string | null; events_session: number; reconnects: number; last_error: string | null; sol_usd: number | null }
  last_event_age_s?: number
  db: boolean
  trades: number
  trades_1h?: number
  mints?: number
  mints_24h?: number
  created_seen_24h?: number
  by_source?: Record<string, number>
  db_bytes?: number
  gaps: Gap[]
  keep_first: number
  recent_hours: number
}

export function ago(ts?: number | null): string {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function usePoll<T>(url: string, intervalMs = 30000) {
  const [data, setData] = useState<T | null>(null)
  const [status, setStatus] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const k = localStorage.getItem('vantage_api_key') || ''
      const r = await fetch(url, { headers: { 'X-Agent-Key': k } })
      setStatus(r.status)
      if (r.ok) setData(await r.json())
    } catch { setStatus(0) }
    setLoading(false)
  }, [url])
  useEffect(() => {
    load()
    const t = setInterval(load, intervalMs)
    return () => clearInterval(t)
  }, [load, intervalMs])
  return { data, status, loading, reload: load }
}

export const card: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: 8, padding: 10, minWidth: 0,
}
export const pill = (on: boolean): React.CSSProperties => ({
  fontSize: 10, fontWeight: 700, borderRadius: 4, padding: '1px 6px', border: '1px solid',
  color: on ? '#22c55e' : 'var(--muted)', borderColor: on ? 'rgba(34,197,94,0.4)' : 'rgba(107,114,128,0.35)',
})
export const muted: React.CSSProperties = { fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }

function notDeployed(status: number | null) {
  return status === 404 ? 'Not deployed on this server yet.' : status === 401 ? 'Connect your agent key.' : status === 0 ? 'Server unreachable.' : null
}

function CurveFeed() {
  const { data: c, status, loading, reload } = usePoll<Coverage>('/api/intel/curve/coverage', 20000)
  const err = !c ? notDeployed(status) : null
  const live = !!c?.state.connected && (c.last_event_age_s ?? 999) < 30
  const unbackfilled = (c?.gaps || []).filter(g => g.backfill !== 'done')
  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
        <Radio size={13} color={live ? '#22c55e' : '#6b7280'} />
        <span style={{ fontSize: 12, fontWeight: 600 }}>Curve feed</span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>every pump.fun trade, free Solana websocket</span>
        {c && <span style={pill(live)}>{!c.enabled ? 'OFF · CURVE_LISTENER_ENABLED' : live ? `LIVE · ${c.state.source}` : c.state.connected ? 'connected, quiet' : 'reconnecting'}</span>}
        <button onClick={reload} disabled={loading} title="Refresh" style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', padding: 2 }}>
          <RefreshCw size={11} />
        </button>
      </div>
      {err && <div style={muted}>{err}</div>}
      {c && !c.enabled && c.trades === 0 && (
        <div style={muted}>Off. When on, it records every bonding-curve trade (wallet, side, SOL, market cap) so runners can be traced back to their first buyers.</div>
      )}
      {c && (c.enabled || c.trades > 0) && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 6, marginBottom: 6 }}>
            {[
              ['Trades / 1h', (c.trades_1h ?? 0).toLocaleString()],
              ['Mints / 24h', (c.mints_24h ?? 0).toLocaleString()],
              ['Creates seen', (c.created_seen_24h ?? 0).toLocaleString()],
              ['Last trade', c.last_event_age_s != null ? `${Math.round(c.last_event_age_s)}s ago` : '—'],
            ].map(([k, v]) => (
              <div key={k}><div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>{k}</div><div style={{ fontSize: 13, fontWeight: 600, fontFamily: 'monospace' }}>{v}</div></div>
            ))}
          </div>
          <div style={muted}>
            {c.trades.toLocaleString()} trades stored{c.db_bytes ? ` (${(c.db_bytes / 1e6).toFixed(0)} MB)` : ''}
            {c.by_source && Object.keys(c.by_source).length > 1 ? ` · ${Object.entries(c.by_source).map(([k, v]) => `${k} ${v}`).join(', ')}` : ''}
            {c.state.sol_usd ? ` · SOL $${c.state.sol_usd.toFixed(2)}` : ''}
            {' · '}first {c.keep_first} trades per mint kept, the rest {c.recent_hours}h
          </div>
          {c.state.last_error && !live && <div style={{ ...muted, color: '#f59e0b' }}>last error: {c.state.last_error}</div>}
          {unbackfilled.length > 0 && (
            <div style={{ ...muted, color: '#f59e0b', marginTop: 4 }}>
              {unbackfilled.length} gap{unbackfilled.length > 1 ? 's' : ''} not fully backfilled — runners first seen inside {unbackfilled.length > 1 ? 'them' : 'it'} may be missing early buyers:
              {unbackfilled.slice(0, 3).map(g => (
                <div key={g.id}>· {ago(g.start_ts)} for {g.end_ts ? `${Math.round(g.end_ts - g.start_ts)}s` : 'ongoing'} — {g.backfill}{g.backfilled_events ? `, ${g.backfilled_events} recovered` : ''}{g.note ? ` (${g.note})` : ''}</div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}


interface Runner {
  mint: string; symbol: string | null; name: string | null; tier_usd: number; tier_weight: number
  peak_mcap_usd: number; peak_at: number; last_mcap_usd: number | null; tiers_crossed: Record<string, number>
  created_seen: number; detected_at: number; trace_status: string
}
interface EarlyBuyers {
  trace: { status: string; source: string | null; reached_origin: number | null; origin_slot: number | null; signatures_scanned: number | null; last_error: string | null; buyers: number } | null
  buyers: { wallet: string; rank: number; sol: number | null; is_major: number; funded_by: string | null; entry_ts: number | null }[]
}

export function usd(v?: number | null): string {
  if (v == null) return '—'
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

const TRACE_COLOR: Record<string, string> = { done: '#22c55e', partial: '#f59e0b', failed: '#ef4444', tracing: '#60a5fa', pending: '#6b7280' }

function RunnerBuyers({ mint }: { mint: string }) {
  const { data } = usePoll<EarlyBuyers>(`/api/intel/runners/${encodeURIComponent(mint)}/early-buyers`, 60000)
  if (!data) return <div style={muted}>Loading…</div>
  const t = data.trace
  return (
    <div style={{ padding: '4px 0 6px 18px' }}>
      <div style={{ ...muted, marginBottom: 4 }}>
        {!t ? 'Not traced yet.'
          : t.status === 'done' ? `Traced to the create${t.origin_slot ? ` (slot ${t.origin_slot.toLocaleString()})` : ''} via ${t.source === 'curve' ? 'the curve feed — no RPC' : `RPC, ${t.signatures_scanned?.toLocaleString() ?? '?'} signatures`}.`
          : t.status === 'partial' ? `Partial: ${t.last_error}. These are early buyers, not proven first.`
          : t.status === 'failed' ? `Trace failed: ${t.last_error}`
          : `Trace ${t.status}…`}
      </div>
      {data.buyers.slice(0, 12).map(b => (
        <div key={b.wallet} style={{ display: 'flex', justifyContent: 'space-between', gap: 6, fontSize: 11, padding: '1px 0' }}>
          <span><span style={{ color: 'var(--muted)', marginRight: 4 }}>#{b.rank}</span><WalletLink address={b.wallet} chain="solana" />
            {b.is_major ? <span title="Largest or earliest entry" style={{ marginLeft: 4, color: '#f59e0b' }}>★</span> : null}
            {b.funded_by ? <span style={{ color: 'var(--muted)', marginLeft: 4 }}>funded by <WalletLink address={b.funded_by} chain="solana" /></span> : null}
          </span>
          <span style={{ color: 'var(--muted)', fontFamily: 'monospace' }}>{b.sol != null ? `${b.sol.toFixed(2)} SOL` : ''}</span>
        </div>
      ))}
      {data.buyers.length > 12 && <div style={muted}>+{data.buyers.length - 12} more</div>}
    </div>
  )
}

function Runners() {
  const { data, status } = usePoll<{ enabled: boolean; tiers: { usd: number; weight: number }[]; runners: Runner[] }>('/api/intel/runners?limit=30', 60000)
  const [open, setOpen] = useState<string | null>(null)
  const err = !data ? notDeployed(status) : null
  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
        <Rocket size={13} color="#f59e0b" />
        <span style={{ fontSize: 12, fontWeight: 600 }}>Runners</span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>{data ? `tiers ${data.tiers.map(t => `${usd(t.usd)}×${t.weight}`).join(' · ')}` : ''}</span>
        {data && <span style={pill(data.enabled)}>{data.enabled ? 'DETECTING' : 'OFF · RUNNER_DETECT_ENABLED'}</span>}
      </div>
      {err && <div style={muted}>{err}</div>}
      {data && data.runners.length === 0 && (
        <div style={muted}>{data.enabled ? 'No runner detected yet. Graduated curve mints are checked every 5 minutes.' : 'Off. When on, graduated pump.fun tokens are checked against DexScreener every 5 minutes; a token that crosses a tier is a runner and gets traced back to its first buyers.'}</div>
      )}
      {data?.runners.map(r => (
        <div key={r.mint} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
          <div onClick={() => setOpen(open === r.mint ? null : r.mint)} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '4px 0', cursor: 'pointer' }}>
            {open === r.mint ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            <TokenLink ca={r.mint} symbol={r.symbol || r.mint.slice(0, 6)} />
            <span style={{ ...pill(true), color: '#f59e0b', borderColor: 'rgba(245,158,11,0.4)' }}>{usd(r.tier_usd)} ×{r.tier_weight}</span>
            <span style={{ color: 'var(--muted)' }} title="Highest market cap this poller has observed (not an all-time high)">peak {usd(r.peak_mcap_usd)}</span>
            <span style={{ color: 'var(--muted)' }}>now {usd(r.last_mcap_usd)}</span>
            <span style={{ marginLeft: 'auto', color: TRACE_COLOR[r.trace_status] || '#6b7280', fontSize: 10 }}>{r.trace_status}</span>
          </div>
          {open === r.mint && <RunnerBuyers mint={r.mint} />}
        </div>
      ))}
    </div>
  )
}

export default function EarlyRunnerIntel() {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Early-Runner Intel</span>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>who bought the runners first, and when they buy again</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 8 }}>
        <CurveFeed />
        <Runners />
      </div>
    </div>
  )
}
