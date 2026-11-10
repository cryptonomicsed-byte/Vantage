import React, { useCallback, useEffect, useRef, useState } from 'react'

// Shared live-feed plumbing for the Analytics panels. Unlike a bare fetch,
// every panel built on this knows when its data last arrived, how long the
// request took, and why it failed -- and says so via <FeedBanner/>, instead
// of silently showing a cached or empty table that looks live.

export type Feed<T = any> = {
  path: string
  data: T | null
  loading: boolean
  error: string | null
  lastOk: number | null
  latencyMs: number | null
  refresh: () => void
}

function agentKey(): string {
  try { return localStorage.getItem('vantage_api_key') || '' } catch { return '' }
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const k = agentKey()
  return { ...(k ? { 'X-Agent-Key': k } : {}), ...extra }
}

const cacheKey = (p: string) => `vantage_cache_analytics_${p}`

// path === null disables the feed (e.g. no query yet)
export function useFeed<T = any>(path: string | null, intervalMs = 60000): Feed<T> {
  const [data, setData] = useState<T | null>(() => {
    if (!path) return null
    try { const raw = localStorage.getItem(cacheKey(path)); return raw ? JSON.parse(raw) : null } catch { return null }
  })
  const [loading, setLoading] = useState(!!path)
  const [error, setError] = useState<string | null>(null)
  const [lastOk, setLastOk] = useState<number | null>(null)
  const [latencyMs, setLatency] = useState<number | null>(null)
  const alive = useRef(true)
  const load = useCallback(async () => {
    if (!path) return
    setLoading(true)
    const t0 = performance.now()
    try {
      const r = await fetch(path, { headers: authHeaders() })
      const ms = Math.round(performance.now() - t0)
      if (!alive.current) return
      setLatency(ms)
      if (r.ok) {
        const j = await r.json()
        if (!alive.current) return
        setData(j); setError(null); setLastOk(Date.now())
        try { localStorage.setItem(cacheKey(path), JSON.stringify(j)) } catch { /* quota */ }
      } else {
        let detail = ''
        try { const j = await r.json(); detail = typeof j?.detail === 'string' ? `: ${j.detail}` : '' } catch { /* */ }
        setError(`HTTP ${r.status}${detail}`)
      }
    } catch (e: any) {
      if (alive.current) setError(e?.message || 'network error')
    }
    if (alive.current) setLoading(false)
  }, [path])
  useEffect(() => {
    alive.current = true
    if (!path) { setLoading(false); return }
    setError(null); setLastOk(null)
    try { const raw = localStorage.getItem(cacheKey(path)); setData(raw ? JSON.parse(raw) : null) } catch { setData(null) }
    load()
    const t = intervalMs > 0 ? setInterval(load, intervalMs) : null
    return () => { alive.current = false; if (t) clearInterval(t) }
  }, [path, intervalMs, load])
  return { path: path || '', data, loading, error, lastOk, latencyMs, refresh: load }
}

export function fmtAge(sec?: number | null): string {
  if (sec == null || !isFinite(sec)) return '—'
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${Math.round(sec / 60)}m`
  if (sec < 172800) return `${Math.round(sec / 3600)}h`
  return `${Math.round(sec / 86400)}d`
}

export function ageSince(ts?: string | number | null): number | null {
  if (ts == null || ts === '') return null
  const ms = typeof ts === 'number' ? (ts > 1e12 ? ts : ts * 1000) : Date.parse(String(ts).replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(String(ts)) ? '' : 'Z'))
  return isNaN(ms) ? null : Math.max(0, (Date.now() - ms) / 1000)
}

export function fmtUsd(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '—'
  const a = Math.abs(v)
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
  if (a > 0 && a < 0.01) return `$${v.toPrecision(3)}`
  return `$${v.toFixed(digits)}`
}

export function fmtPct(v?: number | null, digits = 2, sign = true): string {
  if (v == null || !isFinite(v)) return '—'
  return `${sign && v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

// One line per feed: live / stale / failed, with latency, age and source.
// `reason` is a server-provided "why this is empty" (e.g. a source is down).
export function FeedBanner({ feeds, intervalMs = 60000, source, reason }: {
  feeds: Feed[]; intervalMs?: number; source?: string; reason?: string | null
}) {
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick(x => x + 1), 15000); return () => clearInterval(t) }, [])
  const now = Date.now()
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 10 }}>
      {feeds.filter(f => f.path).map(f => {
        const stale = f.lastOk != null && now - f.lastOk > 3 * intervalMs
        const bad = !!f.error || stale
        const color = bad ? '#f59e0b' : f.lastOk ? '#39ff14' : 'var(--muted)'
        return (
          <div key={f.path} style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ color, fontWeight: 700 }}>{f.error ? '● unavailable' : stale ? '● stale' : f.lastOk ? '● live' : f.loading ? '○ loading' : '○ idle'}</span>
            <code style={{ fontSize: 10 }}>{f.path.split('?')[0]}</code>
            {f.latencyMs != null && <span>{f.latencyMs} ms</span>}
            {f.lastOk && <span>updated {fmtAge((now - f.lastOk) / 1000)} ago</span>}
            {source && <span>source: {source}</span>}
            {f.error && <span style={{ color: '#f59e0b' }}>— {f.error}{f.data ? '; showing the last good data' : ''}</span>}
            {f.loading && f.lastOk && <span>refreshing…</span>}
          </div>
        )
      })}
      {reason && <div style={{ fontSize: 11, color: '#f59e0b' }}>Unavailable: {reason}</div>}
    </div>
  )
}

export const th: React.CSSProperties = { cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }

// Generic column sort: click a header to sort, again to flip.
export function useSort<T>(rows: T[], initial: { key: string; dir: 1 | -1 }, getters: Record<string, (r: T) => any>) {
  const [sort, setSort] = useState(initial)
  const sorted = [...rows].sort((a, b) => {
    const g = getters[sort.key]; if (!g) return 0
    const x = g(a), y = g(b)
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    return (x > y ? 1 : x < y ? -1 : 0) * sort.dir
  })
  const header = (key: string, label: string, title?: string) => (
    <th style={th} title={title} onClick={() => setSort(s => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : -1 }))}>
      {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
    </th>
  )
  return { sorted, header, sort }
}
