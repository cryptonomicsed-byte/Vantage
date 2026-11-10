import React, { useCallback, useEffect, useState } from 'react'
import { WalletLink } from '../EntityProfileCard'
import { LabelChips } from '../EarlyRunnerIntel'
import { FeedBanner, fmtAge, authHeaders, type Feed } from './feed'

type Hop = { chain: string; address: string }
const PARAM = 'trace'   // ?trace=solana:addr1,addr2,addr3 -- shareable, survives reload

function readPath(): Hop[] {
  try {
    const v = new URLSearchParams(window.location.search).get(PARAM)
    if (!v) return []
    const [chain, rest] = v.split(':')
    if (!chain || !rest) return []
    return rest.split(',').filter(Boolean).slice(0, 8).map(address => ({ chain, address }))
  } catch { return [] }
}

function writePath(path: Hop[]) {
  try {
    const u = new URL(window.location.href)
    if (path.length) u.searchParams.set(PARAM, `${path[0].chain}:${path.map(h => h.address).join(',')}`)
    else u.searchParams.delete(PARAM)
    window.history.replaceState(window.history.state, '', u.toString())
  } catch { /* non-browser */ }
}

const KNOWN = new Set(['dev', 'sniper', 'bundler', 'insider', 'bot', 'funding_hub', 'kol', 'whale'])
function Labels({ labels }: { labels?: string[] }) {
  if (!labels?.length) return null
  const known = labels.filter(l => KNOWN.has(l))
  const other = labels.filter(l => !KNOWN.has(l))
  return <><LabelChips labels={known} />{other.map(l => (
    <span key={l} title={l} style={{ marginLeft: 3, fontSize: 9, padding: '0 4px', borderRadius: 3, border: '1px solid #22d3ee55', color: '#22d3ee' }}>{l}</span>
  ))}</>
}

export default function TracePanel({ tokenMode }: { tokenMode?: React.ReactNode }) {
  const initial = readPath()
  const [mode, setMode] = useState<'wallet' | 'token'>('wallet')
  const [chain, setChain] = useState(initial[0]?.chain || 'solana')
  const [addressInput, setAddressInput] = useState(initial[initial.length - 1]?.address || '')
  const [path, setPath] = useState<Hop[]>(initial)
  const [data, setData] = useState<any>(null)
  const [feed, setFeed] = useState<Feed>({ path: '', data: null, loading: false, error: null, lastOk: null, latencyMs: null, refresh: () => {} })

  const runTrace = useCallback(async (hop: Hop) => {
    const p = `/api/intel/trace/${hop.chain}/${encodeURIComponent(hop.address)}?limit=15`
    setFeed(f => ({ ...f, path: p, loading: true, error: null }))
    const t0 = performance.now()
    try {
      const r = await fetch(p, { headers: authHeaders() })
      const ms = Math.round(performance.now() - t0)
      const j = await r.json().catch(() => null)
      if (r.ok) { setData(j); setFeed(f => ({ ...f, loading: false, lastOk: Date.now(), latencyMs: ms, data: j })) }
      else { setData(null); setFeed(f => ({ ...f, loading: false, latencyMs: ms, error: `HTTP ${r.status}${typeof j?.detail === 'string' ? `: ${j.detail}` : ''}` })) }
    } catch (e: any) {
      setData(null); setFeed(f => ({ ...f, loading: false, error: e?.message || 'network error' }))
    }
  }, [])

  useEffect(() => { if (initial.length) runTrace(initial[initial.length - 1]) }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const go = (next: Hop[]) => { setPath(next); writePath(next); const h = next[next.length - 1]; if (h) { setAddressInput(h.address); setChain(h.chain); runTrace(h) } }
  const submit = () => { const a = addressInput.trim(); if (a) go([{ chain, address: a }]) }
  const pivot = (address: string) => {
    const c = path[path.length - 1]?.chain || chain
    const at = path.findIndex(h => h.address === address)
    go(at >= 0 ? path.slice(0, at + 1) : [...path, { chain: c, address }].slice(-8))
  }
  const labels: Record<string, string[]> = data?.counterparty_labels || {}
  const activeChain = path[path.length - 1]?.chain || chain
  const Cp = ({ c }: { c: any }) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', marginRight: 6, marginBottom: 2 }}>
      <button className="btn btn-ghost btn-sm" onClick={() => pivot(c.address)} title={`pivot · ${c.role} · ${c.amount}`}
        style={{ fontFamily: 'monospace', fontSize: 10, padding: '1px 4px' }}>{c.address.slice(0, 6)}…{c.address.slice(-4)}</button>
      <Labels labels={labels[c.address]} />
    </span>
  )

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        <button className={`btn btn-sm ${mode === 'wallet' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMode('wallet')}>By Wallet</button>
        <button className={`btn btn-sm ${mode === 'token' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMode('token')}>By Token</button>
      </div>
      {mode === 'token' ? tokenMode : <>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="ares-input" value={chain} onChange={e => setChain(e.target.value)} style={{ maxWidth: 120 }}>
            <option value="solana">Solana</option><option value="bitcoin">Bitcoin</option>
          </select>
          <input className="ares-input" placeholder="Wallet address" value={addressInput} onChange={e => setAddressInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()} style={{ minWidth: 280, flex: 1, fontFamily: 'monospace' }} />
          <button className="btn btn-primary btn-sm" onClick={submit} disabled={feed.loading}>{feed.loading ? 'Tracing…' : 'Trace'}</button>
          {path.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => { try { navigator.clipboard.writeText(window.location.href) } catch { /* */ } }} title="the hop path is in the URL">Copy link</button>}
        </div>
        {feed.path && <FeedBanner feeds={[feed]} intervalMs={600000} source={data?.source}
          reason={data?.rpc_error && !data?.partial ? data.reason : data?.supported === false ? data.reason : null} />}
        {path.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>Hop path ({path.length}):</span>
            {path.map((h, i) => (
              <React.Fragment key={`${i}-${h.address}`}>
                {i > 0 && <span style={{ color: 'var(--muted)' }}>→</span>}
                <button className="btn btn-ghost btn-sm" onClick={() => go(path.slice(0, i + 1))}
                  style={{ fontFamily: 'monospace', fontSize: 11, opacity: i === path.length - 1 ? 1 : 0.6 }}>{h.address.slice(0, 6)}…{h.address.slice(-4)}</button>
                <Labels labels={i === path.length - 1 ? labels[h.address] : undefined} />
              </React.Fragment>
            ))}
          </div>
        )}
        {!path.length && <div style={{ color: 'var(--muted)', padding: 20 }}>Enter a Solana or Bitcoin address. Click any counterparty to pivot; the hop path is kept in the page URL so it can be shared or reloaded.</div>}
        {data?.supported && (
          <div>
            <div className="ares-stat-grid" style={{ marginBottom: 12 }}>
              <div className="ares-stat-tile"><div className="ares-stat-label">Balance</div><div className="ares-stat-value">{data.balance?.amount?.toLocaleString(undefined, { maximumFractionDigits: 4 })} {data.balance?.unit}</div></div>
              <div className="ares-stat-tile"><div className="ares-stat-label">Recent txs</div><div className="ares-stat-value">{data.tx_count ?? 'unavailable'}</div></div>
              <div className="ares-stat-tile"><div className="ares-stat-label">Labelled counterparties</div><div className="ares-stat-value">{Object.keys(labels).filter(a => a !== data.address).length}</div></div>
              <div className="ares-stat-tile"><div className="ares-stat-label">This wallet</div><div className="ares-stat-value" style={{ fontSize: 12 }}><WalletLink address={data.address} chain={activeChain} /> <Labels labels={labels[data.address]} /></div></div>
            </div>
            {data.partial && <div style={{ fontSize: 11, color: '#f59e0b', marginBottom: 8 }}>Partial: {data.tx_fetch_failed} of {data.tx_count} transactions could not be fetched ({data.rpc_error}); trace again for the rest.</div>}
            {(data.transactions || []).length === 0 ? (
              <div style={{ color: 'var(--muted)', padding: 20 }}>{data.reason || 'No recent transactions.'}</div>
            ) : (
              <table className="ares-table">
                <thead><tr><th>When</th><th>Dir</th><th>Amount</th><th>Fee</th><th>Counterparties</th><th>Token transfers</th></tr></thead>
                <tbody>
                  {data.transactions.map((t: any, i: number) => (
                    <tr key={t.txid || i}>
                      <td style={{ fontSize: 11, color: 'var(--muted)' }} title={t.txid}>{t.timestamp ? `${fmtAge(Date.now() / 1000 - t.timestamp)} ago` : 'pending'}</td>
                      <td style={{ color: t.direction === 'in' ? 'var(--green)' : 'var(--danger)' }}>{t.direction}</td>
                      <td style={{ fontFamily: 'monospace', fontWeight: 700 }}>{t.amount}</td>
                      <td style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>{t.fee}</td>
                      <td>{(t.counterparties || []).map((c: any, j: number) => <Cp key={j} c={c} />)}</td>
                      <td>{(t.token_transfers || []).map((tt: any, k: number) => (
                        <div key={k} style={{ marginBottom: 4 }}>
                          <span style={{ fontFamily: 'monospace', fontSize: 10, color: tt.direction === 'in' ? 'var(--green)' : 'var(--danger)' }}>
                            {tt.direction === 'in' ? '+' : '−'}{tt.amount} <span style={{ color: 'var(--muted)' }}>{tt.mint.slice(0, 4)}…{tt.mint.slice(-4)}</span></span>
                          {(tt.counterparties || []).map((c: any, j: number) => <Cp key={j} c={c} />)}
                        </div>))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </>}
    </div>
  )
}
