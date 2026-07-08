import React, { useState, useEffect, useCallback } from 'react'
import { Wallet, ListOrdered, Layers, BookOpen, PieChart, RefreshCw, Plus, XCircle, Zap, ShieldCheck, Coins, Pencil, Check, Landmark } from 'lucide-react'

// ══════════════════════════════════════════════════════════════════════════════
// Portfolio — the agent-scoped trading workspace built on the existing
// /api/trading backend (wallets, orders, strategies, PnL snapshots, journal, risk).
//
// Two clearly-distinguished modes, toggleable and persisted in localStorage:
//   • Honest ledger (default): records order INTENT + reasoning. Nothing fills
//     orders here; execution is handled externally. Orders stay pending/cancelled.
//   • Simulated (paper): an explicit, labeled mode. Pending orders can be
//     "paper-filled" at the live market quote (POST /orders/{id}/paper-fill);
//     such fills are badged SIMULATED everywhere so they're never mistaken for
//     real settlement.
//
// Auth mirrors AgentDashboard: localStorage 'vantage_api_key' → X-Agent-Key.
// ══════════════════════════════════════════════════════════════════════════════

function agentKey(): string {
  return localStorage.getItem('vantage_api_key') || ''
}

async function tradingApi(path: string, opts: RequestInit = {}): Promise<Response> {
  return fetch(`/api/trading${path}`, {
    ...opts,
    headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
}

function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(Number(n))) return '—'
  const v = Number(n)
  if (Math.abs(v) >= 1_000_000) return '$' + (v / 1_000_000).toFixed(2) + 'M'
  if (Math.abs(v) >= 1_000) return '$' + (v / 1_000).toFixed(2) + 'K'
  return '$' + v.toFixed(2)
}

function fmtSol(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(Number(n))) return '—'
  const v = Number(n)
  return (v >= 0 ? '+' : '') + v.toFixed(4) + ' SOL'
}

function shortMint(m: string | null | undefined): string {
  if (!m) return '—'
  return m.length > 12 ? `${m.slice(0, 4)}…${m.slice(-4)}` : m
}

function timeAgo(iso: string): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z').getTime()
  const m = Math.floor(diff / 60000)
  if (isNaN(m)) return iso
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// Order status pill. Paper fills (tx_hash starts with "paper:") get a distinct
// SIMULATED badge so they are visually separated from any real settlement.
function OrderStatus({ order }: { order: any }) {
  const isPaper = typeof order.tx_hash === 'string' && order.tx_hash.startsWith('paper:')
  if (isPaper) {
    return <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--purple)', border: '1px solid var(--purple)', borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>simulated</span>
  }
  const map: Record<string, string> = { pending: '#ffaa00', open: '#00f5ff', filled: '#39ff14', cancelled: '#6b7280', error: '#ff2d4a' }
  const c = map[String(order.status).toLowerCase()] || '#6b7280'
  return <span style={{ fontSize: 10, fontWeight: 700, color: c, border: `1px solid ${c}`, borderRadius: 4, padding: '1px 6px', textTransform: 'uppercase', letterSpacing: 0.5 }}>{order.status}</span>
}

// ── Inline SVG equity/PnL chart (no external chart lib needed) ──────────────────
function PnlChart({ points }: { points: { date: string; value: number }[] }) {
  if (!points.length) {
    return <div className="ares-stat-tile" style={{ textAlign: 'center', color: 'var(--muted)', padding: 28 }}>No PnL snapshots yet — record one from the Overview tab to build your equity curve.</div>
  }
  const W = 640, H = 180, pad = 8
  const vals = points.map(p => p.value)
  const min = Math.min(...vals), max = Math.max(...vals)
  const range = max - min || 1
  const xs = (i: number) => pad + (i / Math.max(points.length - 1, 1)) * (W - pad * 2)
  const ys = (v: number) => H - pad - ((v - min) / range) * (H - pad * 2)
  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xs(i).toFixed(1)},${ys(p.value).toFixed(1)}`).join(' ')
  const area = `${line} L${xs(points.length - 1).toFixed(1)},${H - pad} L${xs(0).toFixed(1)},${H - pad} Z`
  const up = points[points.length - 1].value >= points[0].value
  const stroke = up ? '#39ff14' : '#ff2d4a'
  return (
    <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
        <defs>
          <linearGradient id="pnlfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#pnlfill)" />
        <path d={line} fill="none" stroke={stroke} strokeWidth="2" />
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', marginTop: 6 }}>
        <span>{points[0].date}</span>
        <span style={{ color: stroke, fontWeight: 700 }}>{fmtUsd(points[points.length - 1].value)}</span>
        <span>{points[points.length - 1].date}</span>
      </div>
    </div>
  )
}

// ── Generic loader hook (agent-scoped) ──────────────────────────────────────────
function useTrading<T = any>(path: string, deps: any[] = []): { data: T | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const reload = useCallback(async () => {
    setLoading(true)
    try { const r = await tradingApi(path); if (r.ok) setData(await r.json()) } catch {}
    setLoading(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps])
  useEffect(() => { reload() }, [reload])
  return { data, loading, reload }
}

// ══════════════════════════════════════════════════════════════════════════════
// OVERVIEW — performance + risk stat tiles + equity chart + snapshot form
// ══════════════════════════════════════════════════════════════════════════════

function Overview() {
  const perf = useTrading<any>('/performance')
  const risk = useTrading<any>('/risk')
  const daily = useTrading<any[]>('/performance/daily?days=30')
  const holdings = useTrading<any>('/holdings')
  const networth = useTrading<any>('/networth')
  const [snap, setSnap] = useState({ portfolio_value_usd: '', daily_pnl_usd: '', daily_pnl_pct: '', notes: '' })
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const points = (daily.data || []).slice().reverse().map((d: any) => ({
    date: String(d.snapshot_date || '').slice(5),
    value: Number(d.portfolio_value_usd) || 0,
  }))

  async function saveSnapshot() {
    if (!snap.portfolio_value_usd) { setMsg('Portfolio value required'); return }
    setSaving(true); setMsg('')
    try {
      const today = new Date().toISOString().slice(0, 10)
      const r = await tradingApi('/performance/snapshot', {
        method: 'POST',
        body: JSON.stringify({
          snapshot_date: today,
          portfolio_value_usd: Number(snap.portfolio_value_usd),
          daily_pnl_usd: Number(snap.daily_pnl_usd) || 0,
          daily_pnl_pct: Number(snap.daily_pnl_pct) || 0,
          notes: snap.notes,
        }),
      })
      if (r.ok) { setMsg('Snapshot saved'); setSnap({ portfolio_value_usd: '', daily_pnl_usd: '', daily_pnl_pct: '', notes: '' }); daily.reload(); perf.reload() }
      else { const e = await r.json().catch(() => ({})); setMsg(e.detail || 'Save failed (one snapshot per day)') }
    } catch { setMsg('Save failed') }
    setSaving(false)
  }

  async function autoSnapshot() {
    setSaving(true); setMsg('')
    try {
      const r = await tradingApi('/networth/snapshot', { method: 'POST', body: '{}' })
      if (r.ok) { const d = await r.json(); setMsg(`Equity synced: ${fmtUsd(d.net_worth_usd)}`); daily.reload(); networth.reload(); holdings.reload() }
      else setMsg('Snapshot failed')
    } catch { setMsg('Snapshot failed') }
    setSaving(false)
  }

  const p = perf.data, r = risk.data
  const netWorth = networth.data?.net_worth_usd ?? holdings.data?.total_value_usd
  const realized = holdings.data?.realized_pnl_usd
  return (
    <div>
      <div className="ares-section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <PieChart size={15} /> Performance (linked wallets)
        <button className="btn btn-ghost btn-sm" onClick={() => { perf.reload(); risk.reload(); daily.reload(); holdings.reload(); networth.reload() }}><RefreshCw size={12} /></button>
      </div>
      <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
        <div className="ares-stat-tile"><div className="ares-stat-label">Net Worth</div><div className="ares-stat-value">{fmtUsd(netWorth)}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Realized P&L</div><div className="ares-stat-value" style={{ color: (realized ?? 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{fmtUsd(realized)}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Open Tokens</div><div className="ares-stat-value">{holdings.data?.open_tokens ?? 0}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">On-chain Trades</div><div className="ares-stat-value">{p?.total_trades ?? holdings.data?.holdings?.length ?? 0}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Exposure</div><div className="ares-stat-value" style={{ color: 'var(--warning)' }}>{fmtUsd(r?.total_exposure_usd)}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Active Strategies</div><div className="ares-stat-value">{r?.active_strategies ?? 0}</div></div>
      </div>

      <div className="ares-section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        Equity Curve (30d)
        <button className="btn btn-ghost btn-sm" onClick={autoSnapshot} disabled={saving} title="Value your linked wallets now and record today's equity point">
          <Plus size={12} /> Snapshot from linked wallets
        </button>
      </div>
      <div style={{ marginBottom: 20 }}><PnlChart points={points} /></div>

      <div className="ares-section-title">Record PnL Snapshot</div>
      <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10, marginBottom: 10 }}>
          <input className="ares-input" type="number" placeholder="Portfolio value (USD)" value={snap.portfolio_value_usd} onChange={e => setSnap({ ...snap, portfolio_value_usd: e.target.value })} />
          <input className="ares-input" type="number" placeholder="Daily PnL (USD)" value={snap.daily_pnl_usd} onChange={e => setSnap({ ...snap, daily_pnl_usd: e.target.value })} />
          <input className="ares-input" type="number" placeholder="Daily PnL (%)" value={snap.daily_pnl_pct} onChange={e => setSnap({ ...snap, daily_pnl_pct: e.target.value })} />
          <input className="ares-input" type="text" placeholder="Notes" value={snap.notes} onChange={e => setSnap({ ...snap, notes: e.target.value })} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button className="btn btn-primary btn-sm" onClick={saveSnapshot} disabled={saving}><Plus size={12} /> {saving ? 'Saving…' : 'Save Snapshot'}</button>
          {msg && <span style={{ fontSize: 12, color: 'var(--muted)' }}>{msg}</span>}
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// ORDERS — list + "log order" form. Paper-fill button only in simulated mode.
// ══════════════════════════════════════════════════════════════════════════════

function Orders({ simulated }: { simulated: boolean }) {
  const orders = useTrading<any[]>('/orders')
  const [form, setForm] = useState({ symbol: '', side: 'buy', chain: 'solana', quantity: '', price: '', trigger_reason: '' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [livePrice, setLivePrice] = useState<number | null>(null)

  // Live quote for the typed symbol (debounced) — Pyth → CoinGecko via the backend.
  useEffect(() => {
    const s = form.symbol.trim().toUpperCase()
    if (!s) { setLivePrice(null); return }
    let active = true
    const t = setTimeout(async () => {
      try {
        const r = await tradingApi(`/markets/${s}/price`)
        if (r.ok && active) { const d = await r.json(); setLivePrice(d.price || null) }
      } catch {}
    }, 400)
    return () => { active = false; clearTimeout(t) }
  }, [form.symbol])

  async function logOrder() {
    if (!form.symbol || !form.quantity) { setMsg('Symbol and quantity are required'); return }
    setBusy(true); setMsg('')
    try {
      const r = await tradingApi('/orders', {
        method: 'POST',
        body: JSON.stringify({
          symbol: form.symbol.toUpperCase(),
          side: form.side,
          chain: form.chain,
          quantity: Number(form.quantity),
          price: form.price ? Number(form.price) : null,
          order_type: form.price ? 'limit' : 'market',
          trigger_reason: form.trigger_reason || 'manual',
        }),
      })
      if (r.ok) { setMsg('Order logged'); setForm({ symbol: '', side: 'buy', chain: 'solana', quantity: '', price: '', trigger_reason: '' }); orders.reload() }
      else setMsg('Failed to log order')
    } catch { setMsg('Failed to log order') }
    setBusy(false)
  }

  async function cancel(id: number) {
    await tradingApi(`/orders/${id}/cancel`, { method: 'POST', body: '{}' })
    orders.reload()
  }

  async function paperFill(id: number) {
    setBusy(true)
    try {
      const r = await tradingApi(`/orders/${id}/paper-fill`, { method: 'POST', body: '{}' })
      if (!r.ok) { const e = await r.json().catch(() => ({})); setMsg(e.detail || 'Paper-fill failed') }
    } catch { setMsg('Paper-fill failed') }
    setBusy(false)
    orders.reload()
  }

  const rows = orders.data || []
  return (
    <div>
      <div className="ares-section-title"><ListOrdered size={15} style={{ verticalAlign: 'middle', marginRight: 6 }} /> Log Order Intent</div>
      <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10, marginBottom: 10 }}>
          <input className="ares-input" placeholder="Symbol (e.g. SOL)" value={form.symbol} onChange={e => setForm({ ...form, symbol: e.target.value })} />
          <select className="ares-input" value={form.side} onChange={e => setForm({ ...form, side: e.target.value })}>
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
          <input className="ares-input" placeholder="Chain" value={form.chain} onChange={e => setForm({ ...form, chain: e.target.value })} />
          <input className="ares-input" type="number" placeholder="Quantity" value={form.quantity} onChange={e => setForm({ ...form, quantity: e.target.value })} />
          <input className="ares-input" type="number" placeholder="Limit price (optional)" value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} />
          <input className="ares-input" placeholder="Reason / thesis" value={form.trigger_reason} onChange={e => setForm({ ...form, trigger_reason: e.target.value })} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <button className="btn btn-primary btn-sm" onClick={logOrder} disabled={busy}><Plus size={12} /> {busy ? 'Working…' : 'Log Order'}</button>
          {form.symbol && livePrice != null && (
            <span style={{ fontSize: 12, color: 'var(--green)', fontFamily: 'monospace' }}>
              live {form.symbol.toUpperCase()}: {fmtUsd(livePrice)}
              <button className="btn btn-ghost btn-sm" style={{ marginLeft: 6 }} onClick={() => setForm({ ...form, price: String(livePrice) })}>use</button>
            </span>
          )}
          {msg && <span style={{ fontSize: 12, color: 'var(--muted)' }}>{msg}</span>}
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>
          {simulated
            ? 'Simulated mode: pending orders can be paper-filled at the live market quote. Paper fills are labeled SIMULATED and are not real settlement.'
            : 'Orders are recorded here as intent; execution is handled externally. Nothing on this screen moves real funds.'}
        </div>
      </div>

      <div className="ares-section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        Orders ({rows.length})
        <button className="btn btn-ghost btn-sm" onClick={orders.reload}><RefreshCw size={12} /></button>
      </div>
      <table className="ares-table">
        <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>When</th><th></th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No orders logged yet.</td></tr>}
          {rows.map((o: any) => (
            <tr key={o.id}>
              <td style={{ fontWeight: 600 }}>{o.symbol}</td>
              <td style={{ color: String(o.side).toUpperCase() === 'BUY' ? 'var(--green)' : 'var(--danger)', fontWeight: 700 }}>{o.side}</td>
              <td style={{ fontFamily: 'monospace' }}>{o.filled_quantity || o.quantity}</td>
              <td style={{ fontFamily: 'monospace' }}>{o.avg_fill_price ? fmtUsd(o.avg_fill_price) : (o.price ? fmtUsd(o.price) : 'mkt')}</td>
              <td><OrderStatus order={o} /></td>
              <td style={{ fontSize: 11, color: 'var(--muted)' }}>{timeAgo(o.created_at)}</td>
              <td style={{ textAlign: 'right' }}>
                {String(o.status).toLowerCase() === 'pending' && simulated && (
                  <button className="btn btn-ghost btn-sm" onClick={() => paperFill(o.id)} disabled={busy} title="Simulate a fill at the live quote"><Zap size={12} /> Paper-fill</button>
                )}
                {['pending', 'open'].includes(String(o.status).toLowerCase()) && (
                  <button className="btn btn-ghost btn-sm" onClick={() => cancel(o.id)} title="Cancel"><XCircle size={12} /></button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// WALLETS
// ══════════════════════════════════════════════════════════════════════════════

function Wallets() {
  const wallets = useTrading<any[]>('/wallets')
  const [form, setForm] = useState({ label: '', chain: 'solana', address: '', exchange: '' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [syncingId, setSyncingId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState({ label: '', exchange: '' })

  async function add() {
    if (!form.label || !form.address) { setMsg('Label and address required'); return }
    setBusy(true); setMsg('')
    try {
      const r = await tradingApi('/wallets', { method: 'POST', body: JSON.stringify({ ...form, encrypted_key: '' }) })
      if (r.ok) { setForm({ label: '', chain: 'solana', address: '', exchange: '' }); wallets.reload() }
      else { const e = await r.json().catch(() => ({})); setMsg(e.detail || 'Failed') }
    } catch { setMsg('Failed') }
    setBusy(false)
  }

  async function remove(id: number) {
    await tradingApi(`/wallets/${id}`, { method: 'DELETE' })
    wallets.reload()
  }

  async function sync(id: number) {
    setSyncingId(id)
    try { await tradingApi(`/wallets/${id}/sync`, { method: 'POST' }); wallets.reload() } catch {}
    setSyncingId(null)
  }

  function startEdit(w: any) {
    setEditingId(w.id)
    setEditDraft({ label: w.label || '', exchange: w.exchange || '' })
  }

  async function saveEdit(id: number) {
    try {
      const r = await tradingApi(`/wallets/${id}`, { method: 'PATCH', body: JSON.stringify(editDraft) })
      if (r.ok) { setEditingId(null); wallets.reload() }
    } catch { /* ignore */ }
  }

  const rows = wallets.data || []
  return (
    <div>
      <div className="ares-section-title"><Wallet size={15} style={{ verticalAlign: 'middle', marginRight: 6 }} /> Wallets ({rows.length})</div>
      <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10, marginBottom: 10 }}>
          <input className="ares-input" placeholder="Label" value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} />
          <input className="ares-input" placeholder="Chain" value={form.chain} onChange={e => setForm({ ...form, chain: e.target.value })} />
          <input className="ares-input" placeholder="Address (public)" value={form.address} onChange={e => setForm({ ...form, address: e.target.value })} />
          <input className="ares-input" placeholder="Exchange (optional — e.g. Coinbase)" value={form.exchange} onChange={e => setForm({ ...form, exchange: e.target.value })} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button className="btn btn-primary btn-sm" onClick={add} disabled={busy}><Plus size={12} /> Add Wallet</button>
          {msg && <span style={{ fontSize: 12, color: 'var(--muted)' }}>{msg}</span>}
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>Track-only: store public addresses for bookkeeping, no private keys used. Sync fetches a real balance for bitcoin/solana addresses (mempool.space / Solana RPC) — other chains aren't live-synced yet. Leave Exchange blank for a self-custody wallet, or name the exchange it's held on (Coinbase, Binance, …) to organize which wallets are custodial.</div>
      </div>
      <table className="ares-table">
        <thead><tr><th>Label</th><th>Chain</th><th>Address</th><th>Held On</th><th>Balance</th><th>Synced</th><th></th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No wallets yet.</td></tr>}
          {rows.map((w: any) => {
            const editing = editingId === w.id
            return (
              <tr key={w.id}>
                <td style={{ fontWeight: 600 }}>
                  {editing
                    ? <input className="ares-input" value={editDraft.label} onChange={e => setEditDraft(d => ({ ...d, label: e.target.value }))} style={{ fontSize: 12, padding: '2px 6px', width: 100 }} />
                    : w.label}
                </td>
                <td>{w.chain}</td>
                <td style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--muted)' }}>{w.address}</td>
                <td>
                  {editing ? (
                    <input className="ares-input" placeholder="e.g. Coinbase" value={editDraft.exchange} onChange={e => setEditDraft(d => ({ ...d, exchange: e.target.value }))} style={{ fontSize: 12, padding: '2px 6px', width: 110 }} />
                  ) : w.exchange ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--warning)', border: '1px solid var(--warning)', borderRadius: 4, padding: '1px 6px' }}>
                      <Landmark size={11} /> {w.exchange}
                    </span>
                  ) : (
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>Self-custody</span>
                  )}
                </td>
                <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                  {(w.balances || []).length === 0 ? <span style={{ color: 'var(--muted)' }}>—</span> : w.balances.map((b: any) => `${b.balance} ${b.token}`).join(', ')}
                </td>
                <td style={{ fontSize: 11, color: 'var(--muted)' }}>{w.last_synced_at ? timeAgo(w.last_synced_at) : '—'}</td>
                <td style={{ textAlign: 'right', display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                  {editing ? (
                    <>
                      <button className="btn btn-ghost btn-sm" onClick={() => saveEdit(w.id)} title="Save"><Check size={12} /></button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditingId(null)} title="Cancel"><XCircle size={12} /></button>
                    </>
                  ) : (
                    <>
                      <button className="btn btn-ghost btn-sm" onClick={() => sync(w.id)} disabled={syncingId === w.id} title="Refresh balance from chain">
                        <RefreshCw size={12} className={syncingId === w.id ? 'spin' : ''} />
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => startEdit(w)} title="Edit"><Pencil size={12} /></button>
                      <button className="btn btn-ghost btn-sm" onClick={() => remove(w.id)}><XCircle size={12} /></button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// STRATEGIES
// ══════════════════════════════════════════════════════════════════════════════

function Strategies() {
  const strategies = useTrading<any[]>('/strategies')
  const [form, setForm] = useState({ name: '', strategy_type: 'manual', description: '' })
  const [busy, setBusy] = useState(false)

  async function add() {
    if (!form.name) return
    setBusy(true)
    try {
      const r = await tradingApi('/strategies', { method: 'POST', body: JSON.stringify(form) })
      if (r.ok) { setForm({ name: '', strategy_type: 'manual', description: '' }); strategies.reload() }
    } catch {}
    setBusy(false)
  }

  async function toggle(id: number) {
    await tradingApi(`/strategies/${id}/toggle`, { method: 'POST', body: '{}' })
    strategies.reload()
  }

  const rows = strategies.data || []
  return (
    <div>
      <div className="ares-section-title"><Layers size={15} style={{ verticalAlign: 'middle', marginRight: 6 }} /> Strategies ({rows.length})</div>
      <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10, marginBottom: 10 }}>
          <input className="ares-input" placeholder="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input className="ares-input" placeholder="Type" value={form.strategy_type} onChange={e => setForm({ ...form, strategy_type: e.target.value })} />
          <input className="ares-input" placeholder="Description" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
        </div>
        <button className="btn btn-primary btn-sm" onClick={add} disabled={busy}><Plus size={12} /> Add Strategy</button>
      </div>
      <table className="ares-table">
        <thead><tr><th>Name</th><th>Type</th><th>Enabled</th><th></th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No strategies yet.</td></tr>}
          {rows.map((s: any) => (
            <tr key={s.id}>
              <td style={{ fontWeight: 600 }}>{s.name}</td>
              <td>{s.strategy_type}</td>
              <td>{s.enabled ? <span style={{ color: 'var(--green)' }}>on</span> : <span style={{ color: 'var(--muted)' }}>off</span>}</td>
              <td style={{ textAlign: 'right' }}><button className="btn btn-ghost btn-sm" onClick={() => toggle(s.id)}>{s.enabled ? 'Disable' : 'Enable'}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// JOURNAL
// ══════════════════════════════════════════════════════════════════════════════

function Journal() {
  const act = useTrading<any>('/activity')
  const d = act.data
  const rows = d?.trades || []
  const realizedUsd = d?.realized_pnl_usd
  return (
    <div>
      <div className="ares-section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <BookOpen size={15} /> Wallet Activity ({d?.count ?? rows.length})
        {realizedUsd != null && (
          <span style={{ fontSize: 12, fontWeight: 700, color: realizedUsd >= 0 ? 'var(--green)' : 'var(--danger)' }}>
            realized {fmtUsd(realizedUsd)}{d?.realized_pnl_sol != null ? ` (${fmtSol(d.realized_pnl_sol)})` : ''}
          </span>
        )}
        <button className="btn btn-ghost btn-sm" onClick={act.reload}><RefreshCw size={12} /></button>
      </div>
      <table className="ares-table">
        <thead><tr><th>When</th><th>Type</th><th>Token</th><th>Δ Token</th><th>Δ SOL</th><th>Realized P&L</th><th></th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No on-chain trades yet — the wallet tracker ingests trades from your linked wallets automatically.</td></tr>}
          {rows.map((t: any) => {
            const rp = t.realized_pnl_sol || 0
            const isTrade = t.realized_pnl_sol !== 0
            return (
              <tr key={t.signature}>
                <td style={{ fontSize: 11, color: 'var(--muted)' }}>{t.ts_iso ? timeAgo(t.ts_iso) : '—'}</td>
                <td style={{ fontWeight: 600 }}>{t.type}</td>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }} title={t.description || t.token_mint || ''}>{shortMint(t.token_mint)}</td>
                <td style={{ fontFamily: 'monospace', color: (t.token_amount || 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{t.token_amount != null ? Number(t.token_amount).toLocaleString() : '—'}</td>
                <td style={{ fontFamily: 'monospace', color: (t.sol_change || 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{fmtSol(t.sol_change)}</td>
                <td style={{ fontFamily: 'monospace', fontWeight: 700, color: !isTrade ? 'var(--muted)' : rp >= 0 ? 'var(--green)' : 'var(--danger)' }}>
                  {isTrade ? `${fmtUsd(t.realized_pnl_usd)} (${fmtSol(rp)})` : '—'}
                </td>
                <td style={{ textAlign: 'right' }}>
                  {t.signature && <a className="btn btn-ghost btn-sm" href={`https://solscan.io/tx/${t.signature}`} target="_blank" rel="noreferrer" title="View on Solscan">↗</a>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>Every real on-chain trade from your linked wallets, newest first, with per-trade realized P&L (average-cost, SOL-denominated; USD at the live SOL price). Buys show no realized P&L until the position is sold.</div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// PORTFOLIO SHELL — sub-tabs + honest/simulated mode toggle
// ══════════════════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════════════════
// POSITIONS — net positions from filled orders, valued at the live quote
// ══════════════════════════════════════════════════════════════════════════════

function Positions() {
  const h = useTrading<any>('/holdings')
  const d = h.data
  const rows = d?.holdings || []
  const realized = d?.realized_pnl_usd
  return (
    <div>
      <div className="ares-section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Coins size={15} /> Linked-Wallet Holdings ({rows.length})
        <button className="btn btn-ghost btn-sm" onClick={h.reload}><RefreshCw size={12} /></button>
      </div>
      <div className="ares-stat-grid" style={{ marginBottom: 16 }}>
        <div className="ares-stat-tile"><div className="ares-stat-label">Net Value</div><div className="ares-stat-value">{fmtUsd(d?.total_value_usd)}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Realized P&L</div><div className="ares-stat-value" style={{ color: (realized ?? 0) >= 0 ? 'var(--green)' : 'var(--danger)' }}>{fmtUsd(realized)}</div></div>
        <div className="ares-stat-tile"><div className="ares-stat-label">Open Tokens</div><div className="ares-stat-value">{d?.open_tokens ?? rows.length}</div></div>
      </div>
      <table className="ares-table">
        <thead><tr><th>Token</th><th>Balance</th><th>Value</th><th>Wallets</th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--muted)', padding: 20 }}>No holdings yet — add a wallet and sync it (or let the balance daemon run) to populate this from real on-chain balances.</td></tr>}
          {rows.map((p: any) => (
            <tr key={p.token}>
              <td style={{ fontWeight: 600 }}>{p.token}</td>
              <td style={{ fontFamily: 'monospace' }}>{p.balance}</td>
              <td style={{ fontFamily: 'monospace' }}>{p.value_usd != null ? fmtUsd(p.value_usd) : '—'}</td>
              <td style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{p.wallet_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>Holdings are aggregated live from your linked wallets' real on-chain balances (auto-synced by the balance daemon and on manual wallet sync). Realized P&L is computed from actual on-chain swaps (average-cost, SOL-denominated) — see Journal for per-trade P&L.</div>
    </div>
  )
}

const PORTFOLIO_TABS = [
  { id: 'overview',   label: 'Overview',   icon: PieChart },
  { id: 'positions',  label: 'Positions',  icon: Coins },
  { id: 'orders',     label: 'Orders',     icon: ListOrdered },
  { id: 'wallets',    label: 'Wallets',    icon: Wallet },
  { id: 'strategies', label: 'Strategies', icon: Layers },
  { id: 'journal',    label: 'Journal',    icon: BookOpen },
]

export default function Portfolio() {
  const [tab, setTab] = useState('overview')
  const [mode, setMode] = useState<'honest' | 'simulated'>(() => (localStorage.getItem('vantage_trading_mode') === 'simulated' ? 'simulated' : 'honest'))
  const hasKey = !!agentKey()

  function setModePersist(m: 'honest' | 'simulated') {
    setMode(m)
    localStorage.setItem('vantage_trading_mode', m)
  }

  if (!hasKey) {
    return (
      <div className="ares-stat-tile" style={{ textAlign: 'center', padding: 40 }}>
        <ShieldCheck size={28} style={{ color: 'var(--purple)', marginBottom: 12 }} />
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Connect your agent key</div>
        <div style={{ color: 'var(--muted)', fontSize: 13 }}>The Portfolio is agent-scoped. Connect your Vantage agent key from the Me / Settings screen to manage wallets, orders, strategies, and your journal. Market Intelligence works without a key.</div>
      </div>
    )
  }

  const simulated = mode === 'simulated'
  return (
    <div>
      {/* Mode toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 4, background: 'rgba(255,255,255,0.05)', borderRadius: 8, padding: 2 }}>
          <button className={`btn btn-${!simulated ? 'primary' : 'ghost'} btn-sm`} onClick={() => setModePersist('honest')}><ShieldCheck size={12} /> Honest ledger</button>
          <button className={`btn btn-${simulated ? 'primary' : 'ghost'} btn-sm`} onClick={() => setModePersist('simulated')}><Zap size={12} /> Simulated (paper)</button>
        </div>
        <span style={{ fontSize: 11, color: simulated ? 'var(--purple)' : 'var(--muted)' }}>
          {simulated
            ? 'Paper mode — fills are SIMULATED at live quotes, not real settlement.'
            : 'Honest ledger — records intent only; execution is external.'}
        </span>
      </div>

      {/* Sub-tabs */}
      <div className="top-nav-tabs" style={{ flexWrap: 'wrap', borderBottom: '1px solid var(--border)', paddingBottom: 8, marginBottom: 20 }}>
        {PORTFOLIO_TABS.map(t => (
          <button key={t.id} type="button" className={`top-nav-tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <Overview />}
      {tab === 'positions' && <Positions />}
      {tab === 'orders' && <Orders simulated={simulated} />}
      {tab === 'wallets' && <Wallets />}
      {tab === 'strategies' && <Strategies />}
      {tab === 'journal' && <Journal />}
    </div>
  )
}
