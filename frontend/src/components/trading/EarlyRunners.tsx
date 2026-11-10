import React, { useMemo, useState } from 'react'
import { Crosshair, Activity, ListOrdered, Users, Zap, Radio, Search, AlertTriangle } from 'lucide-react'
import { usePoll, ago, usd, LabelChips, CurveFeed, Watchlist, Confluence } from './EarlyRunnerIntel'
import { TokenLink, WalletLink } from './EntityProfileCard'

/* Early Runners: the P4 early-runner chain as its own section (moved out of
   Degen Trenches). Everything here is read-only intel over
   /api/intel/early-runners/* and the existing runner endpoints. Two rules the
   backend applies, both found on the live tables and explained in the UI:
   - a runner only counts when pool liquidity backs its market cap
     (111 of 114 "runners" in a day were wash pools quoting up to $750M on ~$2k)
   - buyer rows are classified buy / dust / liquidity; ranks count real buys. */

type Profile = {
  real_buyers: number; dust: number; liquidity_events: number; dust_share: number | null
  first_real_delay_s: number | null; sol_first_n: number; bundled_share: number | null
  label_mix: Record<string, number>; first_row: { wallet: string; sol: number | null; kind: string; dev: boolean } | null
}
type RunnerRow = {
  mint: string; symbol: string | null; tier_usd: number; peak_mcap_usd: number; last_mcap_usd: number | null
  detected_at: number; first_seen: number | null; trace_status: string; backed: number | null; liq_usd: number | null
  age_to_tier_s: number | null; profile: Profile | null
}
type Overview = {
  generated_at: number
  liquidity_rule: { min_usd: number; min_frac_of_cap: number }
  runner_validity: Record<string, number>
  trace_queue: Record<string, { n: number; buyers: number }>
  runners_by_tier: Record<string, number>
  runners_24h: number
  labels: Record<string, number>
  watchlist_entities: number
  confluence_24h?: number
  buyer_rows: { total: number; buy?: number; dust?: number; liquidity?: number }
  curve: { enabled: boolean; last_event_age_s: number | null; trades_1h: number; mints_24h: number; created_seen_24h: number; db_bytes: number; gaps: number } | null
  runners: RunnerRow[]
}
type BuyerRow = {
  wallet: string; rank: number; real_rank: number | null; kind: 'buy' | 'dust' | 'liquidity'
  delay_s: number | null; sol: number | null; tokens: number | null; funded_by: string | null
  labels: string[]; funder_labels: string[]; is_major: number; signature: string | null
}
type ProfileResp = {
  mint: string
  trace: { status: string; source: string | null; reached_origin: number | null; signatures_scanned: number | null; last_error: string | null; attempts: number } | null
  profile: Profile & { bundles: { size: number; sol: number; wallets: string[] }[]; shared_funders: Record<string, number>; median_sol_first_n: number | null; first_n: number }
  buyers: BuyerRow[]
}

const SUBTABS = [
  { id: 'overview', label: 'Overview', icon: Activity },
  { id: 'runners', label: 'Runners', icon: ListOrdered },
  { id: 'watchlist', label: 'Watchlist', icon: Users },
  { id: 'confluence', label: 'Confluence', icon: Zap },
  { id: 'feed', label: 'Curve Feed', icon: Radio },
  { id: 'wallet', label: 'Wallet Lookup', icon: Search },
] as const
type Sub = typeof SUBTABS[number]['id']

const card: React.CSSProperties = { background: 'var(--card-bg, rgba(255,255,255,0.03))', border: '1px solid var(--border, rgba(255,255,255,0.08))', borderRadius: 8, padding: 12 }
const muted: React.CSSProperties = { color: 'var(--muted)', fontSize: 11 }
const KIND_COLOR: Record<string, string> = { buy: '#22c55e', dust: '#6b7280', liquidity: '#a78bfa' }
const TRACE_COLOR: Record<string, string> = { done: '#22c55e', partial: '#f59e0b', failed: '#ef4444', tracing: '#60a5fa', walking: '#60a5fa', pending: '#6b7280' }

function secs(s?: number | null): string {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`
  return `${(s / 86400).toFixed(1)}d`
}
function pct(x?: number | null) { return x == null ? '—' : `${Math.round(x * 100)}%` }
function sol(x?: number | null) { return x == null ? '—' : x >= 100 ? x.toFixed(0) : x >= 1 ? x.toFixed(2) : x.toFixed(4) }

function Stat({ label, value, sub, warn }: { label: string; value: React.ReactNode; sub?: React.ReactNode; warn?: boolean }) {
  return (
    <div style={{ ...card, minWidth: 140, flex: '1 1 140px', borderColor: warn ? '#f59e0b66' : undefined }}>
      <div style={{ ...muted, textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: warn ? '#f59e0b' : undefined }}>{value}</div>
      {sub && <div style={{ ...muted, marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function OverviewTab({ o, onOpen }: { o: Overview; onOpen: (m: string) => void }) {
  const v = o.runner_validity || {}
  const unbacked = (v.unbacked || 0) + (v.unverified || 0)
  const q = o.trace_queue || {}
  const br = o.buyer_rows || { total: 0 }
  const feedStale = o.curve && (o.curve.last_event_age_s == null || o.curve.last_event_age_s > 120)
  const traced = o.runners.filter(r => r.profile)
  const fastest = [...traced].filter(r => r.age_to_tier_s != null).sort((a, b) => (a.age_to_tier_s! - b.age_to_tier_s!)).slice(0, 5)
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <Stat label="Real runners" value={v.backed || 0} sub={`${o.runners_24h} new in 24h`} />
        <Stat label="Rejected (wash / unbacked)" value={unbacked} warn={unbacked > 0}
          sub={`liquidity < max($${(o.liquidity_rule.min_usd / 1000).toFixed(0)}k, ${o.liquidity_rule.min_frac_of_cap * 100}% of cap)`} />
        <Stat label="Trace queue" value={`${q.done?.n || 0} done`} sub={`${q.pending?.n || 0} pending · ${(q.tracing?.n || 0) + (q.walking?.n || 0)} walking · ${q.failed?.n || 0} failed`} />
        <Stat label="Buyer rows" value={br.buy || 0} sub={`real buys · ${br.dust || 0} dust · ${br.liquidity || 0} liquidity`} />
        <Stat label="Watchlist" value={o.watchlist_entities} sub="entities (2+ real runners)" warn={o.watchlist_entities === 0} />
        <Stat label="Confluence 24h" value={o.confluence_24h ?? '—'} sub="pings posted (≤ 0.69 conviction)" />
        <Stat label="Curve feed" value={o.curve ? (feedStale ? 'stale' : 'live') : 'off'} warn={!!feedStale || !o.curve}
          sub={o.curve ? `last event ${secs(o.curve.last_event_age_s)} ago · ${o.curve.trades_1h.toLocaleString()} trades/h · ${o.curve.gaps} gaps` : 'listener not reporting'} />
      </div>

      {unbacked > 0 && (
        <div style={{ ...card, borderColor: '#f59e0b55', marginBottom: 14, display: 'flex', gap: 10 }}>
          <AlertTriangle size={16} color="#f59e0b" style={{ flexShrink: 0, marginTop: 2 }} />
          <div style={{ fontSize: 12 }}>
            <b>{unbacked} detected tokens are not counted as runners.</b> Their quoted market cap isn't backed by pool liquidity —
            typically a launch bot buys the whole curve in the create transaction, then wash-trades a tiny pool that quotes
            hundreds of millions on a few thousand dollars. They are kept for the record but excluded from tracing, the
            watchlist and confluence. <span style={muted}>Toggle "show rejected" on the Runners tab to see them.</span>
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
        <div style={card}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Runners by tier</div>
          {Object.keys(o.runners_by_tier).length === 0 && <div style={muted}>No liquidity-backed runners yet.</div>}
          {Object.entries(o.runners_by_tier).sort((a, b) => +a[0] - +b[0]).map(([t, n]) => (
            <div key={t} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0' }}>
              <span>{usd(+t)}+</span><b>{n}</b>
            </div>
          ))}
        </div>
        <div style={card}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Fastest to tier (traced)</div>
          {fastest.length === 0 && <div style={muted}>No traced runners yet — the tracer walks each one from its create tx on the free RPC.</div>}
          {fastest.map(r => (
            <div key={r.mint} onClick={() => onOpen(r.mint)} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0', cursor: 'pointer' }}>
              <span>{r.symbol || r.mint.slice(0, 6)} <span style={muted}>{usd(r.tier_usd)}</span></span>
              <span>{secs(r.age_to_tier_s)}</span>
            </div>
          ))}
        </div>
        <div style={card}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Wallet labels</div>
          {Object.entries(o.labels).sort((a, b) => b[1] - a[1]).map(([l, n]) => (
            <div key={l} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, padding: '3px 0' }}>
              <LabelChips labels={[l]} /><b>{n.toLocaleString()}</b>
            </div>
          ))}
        </div>
      </div>
      <div style={{ ...muted, marginTop: 10 }}>Updated {ago(o.generated_at)}</div>
    </div>
  )
}

function RunnerDetail({ mint }: { mint: string }) {
  const { data, reload } = usePoll<ProfileResp>(`/api/intel/early-runners/${encodeURIComponent(mint)}/profile`, 60000)
  const [show, setShow] = useState<'buy' | 'all'>('buy')
  const [queued, setQueued] = useState('')
  if (!data) return <div style={{ ...muted, padding: 10 }}>Loading profile…</div>
  const p = data.profile, t = data.trace
  const rows = show === 'buy' ? data.buyers.filter(b => b.kind === 'buy') : data.buyers
  async function traceNow() {
    try {
      const r = await fetch(`/api/intel/runners/${encodeURIComponent(mint)}/trace`, { method: 'POST', headers: { 'X-Agent-Key': localStorage.getItem('vantage_api_key') || '' } })
      setQueued(r.ok ? 'queued' : `failed (${r.status})`); reload?.()
    } catch { setQueued('failed') }
  }
  return (
    <div style={{ padding: '10px 4px 14px' }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <Stat label="Trace" value={<span style={{ color: TRACE_COLOR[t?.status || 'pending'] }}>{t?.status || 'not queued'}</span>}
          sub={t ? `${t.source || '—'} · ${t.reached_origin ? 'reached create tx' : 'origin not reached'} · ${(t.signatures_scanned || 0).toLocaleString()} sigs` : undefined} />
        <Stat label="Real buyers" value={p.real_buyers} sub={`${p.dust} dust · ${p.liquidity_events} liquidity`} />
        <Stat label="First real buy" value={secs(p.first_real_delay_s)} sub="after create" />
        <Stat label={`SOL, first ${p.first_n}`} value={sol(p.sol_first_n)} sub={`median ${sol(p.median_sol_first_n)}`} />
        <Stat label="Bundled" value={pct(p.bundled_share)} sub={`${p.bundles.length} groups landing within 1s`} warn={(p.bundled_share || 0) >= 0.3} />
      </div>
      {p.first_row?.dev && p.real_buyers <= 1 && (
        <div style={{ fontSize: 12, color: '#f59e0b', marginBottom: 8 }}>
          Only the creator bought on the curve ({sol(p.first_row.sol)} SOL in the create tx). A dev buy that size completes the curve
          instantly, so all later trading happened on the AMM — run the trace to walk those buys over RPC.
        </div>
      )}
      {Object.keys(p.shared_funders).length > 0 && (
        <div style={{ fontSize: 12, marginBottom: 8 }}>
          <b>Shared funders</b> (one wallet funding several early buyers):{' '}
          {Object.entries(p.shared_funders).map(([f, n]) => <span key={f} style={{ marginRight: 10 }}><WalletLink address={f} chain="solana" /> ×{n}</span>)}
        </div>
      )}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
        <button className={`btn btn-sm ${show === 'buy' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setShow('buy')}>Real buys</button>
        <button className={`btn btn-sm ${show === 'all' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setShow('all')}>All rows ({data.buyers.length})</button>
        <button className="btn btn-sm btn-ghost" onClick={traceNow} style={{ marginLeft: 'auto' }}>Trace now</button>
        {queued && <span style={muted}>{queued}</span>}
      </div>
      {rows.length === 0 && <div style={muted}>No {show === 'buy' ? 'real buys' : 'rows'} recorded yet.</div>}
      {rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead><tr style={{ ...muted, textAlign: 'left' }}>
              <th>#</th><th>Wallet</th><th>Kind</th><th>After create</th><th style={{ textAlign: 'right' }}>SOL</th><th>Labels</th><th>Funded by</th>
            </tr></thead>
            <tbody>
              {rows.slice(0, 100).map(b => (
                <tr key={b.wallet + b.rank} style={{ borderTop: '1px solid rgba(255,255,255,0.05)', opacity: b.kind === 'buy' ? 1 : 0.55 }}>
                  <td>{b.real_rank ?? <span style={muted}>({b.rank})</span>}</td>
                  <td><WalletLink address={b.wallet} chain="solana" />{b.is_major ? <span title="Largest or earliest entry" style={{ marginLeft: 4, color: '#ffaa00' }}>★</span> : null}</td>
                  <td style={{ color: KIND_COLOR[b.kind] }}>{b.kind}</td>
                  <td>{secs(b.delay_s)}</td>
                  <td style={{ textAlign: 'right' }}>{sol(b.sol)}</td>
                  <td><LabelChips labels={b.labels} /></td>
                  <td>{b.funded_by ? <><WalletLink address={b.funded_by} chain="solana" /> <LabelChips labels={b.funder_labels} /></> : <span style={muted}>—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ ...muted, marginTop: 6 }}>
        Kinds: <b style={{ color: KIND_COLOR.buy }}>buy</b> ≥ 0.01 SOL · <b style={{ color: KIND_COLOR.dust }}>dust</b> under that (bots trading side pools at
        prices no real buyer pays) · <b style={{ color: KIND_COLOR.liquidity }}>liquidity</b> a pool being seeded (≥ 5% of supply for ≥ 100 SOL). Ranks count real buys only.
      </div>
    </div>
  )
}

function RunnersTab({ open, setOpen }: { open: string | null; setOpen: (m: string | null) => void }) {
  const [rejected, setRejected] = useState(false)
  const [sort, setSort] = useState<'recent' | 'fastest' | 'liquidity' | 'peak'>('recent')
  const { data } = usePoll<Overview>(`/api/intel/early-runners/overview?limit=200${rejected ? '&include_unbacked=true' : ''}`, 60000)
  const list = useMemo(() => {
    const rs = [...(data?.runners || [])]
    if (sort === 'fastest') rs.sort((a, b) => (a.age_to_tier_s ?? 9e9) - (b.age_to_tier_s ?? 9e9))
    if (sort === 'liquidity') rs.sort((a, b) => (b.liq_usd || 0) - (a.liq_usd || 0))
    if (sort === 'peak') rs.sort((a, b) => b.peak_mcap_usd - a.peak_mcap_usd)
    return rs
  }, [data, sort])
  if (!data) return <div style={muted}>Loading runners…</div>
  return (
    <div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        {(['recent', 'fastest', 'liquidity', 'peak'] as const).map(s => (
          <button key={s} className={`btn btn-sm ${sort === s ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setSort(s)}>{s}</button>
        ))}
        <label style={{ ...muted, marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input type="checkbox" checked={rejected} onChange={e => setRejected(e.target.checked)} /> show rejected (wash / unbacked)
        </label>
      </div>
      {list.length === 0 && <div style={muted}>No liquidity-backed runners in the window. Real ones are rare — most "$10M" pump.fun tokens are wash pools.</div>}
      {list.map(r => {
        const p = r.profile
        const bad = r.backed !== 1
        return (
          <div key={r.mint} style={{ ...card, marginBottom: 8, opacity: bad ? 0.6 : 1 }}>
            <div onClick={() => setOpen(open === r.mint ? null : r.mint)} style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', cursor: 'pointer' }}>
              <div style={{ minWidth: 150 }}>
                <TokenLink ca={r.mint} symbol={r.symbol || r.mint.slice(0, 6)} />
                <div style={muted}>{usd(r.tier_usd)} tier · {ago(r.detected_at)}</div>
              </div>
              <div style={{ fontSize: 12 }}>peak {usd(r.peak_mcap_usd)}<div style={muted}>now {usd(r.last_mcap_usd)}</div></div>
              <div style={{ fontSize: 12, color: bad ? '#f59e0b' : undefined }}>liq {usd(r.liq_usd)}<div style={muted}>{bad ? (r.backed === 0 ? 'unbacked' : 'unverified') : 'backed'}</div></div>
              <div style={{ fontSize: 12 }}>{secs(r.age_to_tier_s)}<div style={muted}>to tier</div></div>
              <div style={{ fontSize: 12 }}>
                {p ? <>{p.real_buyers} real · {p.dust} dust</> : <span style={muted}>not traced</span>}
                <div style={muted}>{p ? `first buy ${secs(p.first_real_delay_s)} · ${pct(p.bundled_share)} bundled` : ''}</div>
              </div>
              <div style={{ fontSize: 12, color: TRACE_COLOR[r.trace_status] || 'var(--muted)' }}>{r.trace_status}</div>
              <div style={{ marginLeft: 'auto' }}>{p && <LabelChips labels={Object.keys(p.label_mix).slice(0, 4)} />}</div>
            </div>
            {open === r.mint && <RunnerDetail mint={r.mint} />}
          </div>
        )
      })}
    </div>
  )
}

function WalletTab() {
  const [w, setW] = useState('')
  const [res, setRes] = useState<any>(null)
  const [err, setErr] = useState('')
  async function look() {
    setErr(''); setRes(null)
    try {
      const r = await fetch(`/api/intel/wallets/${encodeURIComponent(w.trim())}/labels`, { headers: { 'X-Agent-Key': localStorage.getItem('vantage_api_key') || '' } })
      const d = await r.json(); if (r.ok) setRes(d); else setErr(d.detail || `error ${r.status}`)
    } catch (e: any) { setErr(e?.message || 'request failed') }
  }
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <input className="ares-input" placeholder="Solana wallet address" value={w} onChange={e => setW(e.target.value)} onKeyDown={e => e.key === 'Enter' && look()} style={{ flex: 1 }} />
        <button className="btn btn-primary btn-sm" onClick={look} disabled={!w.trim()}>Look up</button>
      </div>
      {err && <div style={{ color: '#ef4444', fontSize: 12 }}>{err}</div>}
      {res && (
        <div style={card}>
          <div style={{ marginBottom: 8 }}><WalletLink address={res.wallet || w} chain="solana" /></div>
          {(res.labels || []).length === 0 && <div style={muted}>No labels — this wallet hasn't shown up as an early buyer, funder, dev or KOL in what's been traced.</div>}
          {(res.labels || []).map((l: any, i: number) => (
            <div key={i} style={{ fontSize: 12, padding: '4px 0', borderTop: i ? '1px solid rgba(255,255,255,0.05)' : undefined }}>
              <LabelChips labels={[l.label || l]} /> <span style={muted}>{l.updated_at ? `updated ${ago(l.updated_at)}` : ''}</span>
              {l.evidence && <pre style={{ ...muted, whiteSpace: 'pre-wrap', margin: '4px 0 0' }}>{typeof l.evidence === 'string' ? l.evidence : JSON.stringify(l.evidence, null, 1)}</pre>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function EarlyRunners() {
  const [sub, setSub] = useState<Sub>(() => {
    try { const s = new URLSearchParams(window.location.search).get('er'); return (SUBTABS.some(t => t.id === s) ? s : 'overview') as Sub } catch { return 'overview' }
  })
  const [open, setOpen] = useState<string | null>(null)
  const { data: o, status } = usePoll<Overview>('/api/intel/early-runners/overview?limit=100', 60000)
  const pick = (s: Sub) => {
    setSub(s)
    try { const u = new URL(window.location.href); u.searchParams.set('er', s); window.history.replaceState({}, '', u.toString()) } catch { /* */ }
  }
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <Crosshair size={20} color="#22c55e" />
        <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Early Runners</h2>
        <span style={muted}>Who got in first on the tokens that actually ran — traced from the create transaction</span>
      </div>
      <div className="top-nav-tabs" style={{ marginBottom: 14, flexWrap: 'wrap' }}>
        {SUBTABS.map(t => (
          <button key={t.id} type="button" className={`top-nav-tab ${sub === t.id ? 'active' : ''}`} onClick={() => pick(t.id)}>
            <t.icon size={13} style={{ marginRight: 4 }} />{t.label}
          </button>
        ))}
      </div>
      {sub === 'overview' && (o ? <OverviewTab o={o} onOpen={m => { setOpen(m); pick('runners') }} />
        : <div style={muted}>{status && status >= 400 ? `Overview unavailable (${status}).` : 'Loading…'}</div>)}
      {sub === 'runners' && <RunnersTab open={open} setOpen={setOpen} />}
      {sub === 'watchlist' && <Watchlist />}
      {sub === 'confluence' && <Confluence />}
      {sub === 'feed' && <CurveFeed />}
      {sub === 'wallet' && <WalletTab />}
    </div>
  )
}
