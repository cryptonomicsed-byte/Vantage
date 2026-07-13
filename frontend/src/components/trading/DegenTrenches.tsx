import React, { useEffect, useState, useCallback, useRef } from 'react'
import { Flame, TrendingUp, AlertTriangle, Zap, Shield, Rocket, GraduationCap, UserPlus } from 'lucide-react'
import { TokenLink, WalletLink } from './EntityProfileCard'

interface Token {
  symbol: string
  name: string
  price?: number
  volume_24h?: number
  price_change_24h?: number
  market_cap?: number
  market_cap_rank?: number
  score?: number
  address?: string
  volume_5m?: number
  volume_1h?: number
  surge_ratio?: number
  signal?: string
}

interface Deployer {
  mint: string
  symbol: string
  wallet_address: string
  discovered_at: string
  launch_count: number
}

// ── Persisted-cache fetch — same fix as Top5Degen.tsx: this page used to
// fetch once on mount with no refresh, so a transient upstream failure (or
// just navigating away and back) would show an empty page even though data
// had been there a moment ago. Seeds from localStorage, polls on an
// interval, and never lets an empty/failed response overwrite a non-empty
// cached value. ──────────────────────────────────────────────────────────
function loadCache<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`vantage_cache_${key}`)
    return raw ? JSON.parse(raw) : fallback
  } catch { return fallback }
}
function saveCache(key: string, value: any) {
  try { localStorage.setItem(`vantage_cache_${key}`, JSON.stringify(value)) } catch { /* quota — non-fatal */ }
}

function useCachedList<T>(key: string, endpoint: string, extractKey: string, intervalMs = 45000) {
  const [items, setItems] = useState<T[]>(() => loadCache(key, []))
  const [loading, setLoading] = useState(items.length === 0)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    const k = localStorage.getItem('vantage_api_key') || ''
    try {
      const r = await fetch(endpoint, { headers: { 'X-Agent-Key': k } })
      if (!r.ok) return
      const data = await r.json()
      const next = data?.[extractKey]
      if (Array.isArray(next) && next.length > 0) {
        if (mounted.current) setItems(next)
        saveCache(key, next)
      }
    } catch { /* offline — keep showing cached data */ }
    if (mounted.current) setLoading(false)
  }, [endpoint, extractKey, key])

  useEffect(() => {
    mounted.current = true
    load()
    const t = setInterval(load, intervalMs)
    return () => { mounted.current = false; clearInterval(t) }
  }, [load, intervalMs])

  return { items, loading, refresh: load }
}

const cardStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid rgba(255,255,255,0.05)',
  borderRadius: 8,
  padding: 10,
}

export default function DegenTrenches() {
  const trending = useCachedList<Token>('trenches_trending', '/api/intel/pumpfun/trending?limit=10', 'trending')
  const launches = useCachedList<Token>('trenches_launches', '/api/intel/pumpfun/new-launches?limit=10', 'launches')
  const surges = useCachedList<Token>('trenches_surges', '/api/intel/degen/volume-surge?limit=10', 'volume_surges', 30000)
  const graduations = useCachedList<any>('trenches_graduations', '/api/intel/pumpfun/graduations?limit=10', 'graduations', 60000)
  const deployers = useCachedList<Deployer>('trenches_deployers', '/api/intel/degen/fresh-deployers?limit=10', 'deployers', 60000)

  const initialLoading = trending.loading && trending.items.length === 0 && launches.loading && launches.items.length === 0

  if (initialLoading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>Loading trenches…</div>

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <Flame size={20} color="#f59e0b" />
        <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Degen Trenches</h2>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>Pump.fun Alpha</span>
      </div>

      {/* Trending */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <TrendingUp size={14} color="#22c55e" />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Trending (CoinGecko)</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
          {trending.items.length === 0 && <div style={{ fontSize: 11, color: 'var(--muted)' }}>No trending data yet.</div>}
          {trending.items.map((t, i) => (
            <div key={i} style={cardStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}><TokenLink symbol={t.symbol} ca={t.address} /></span>
                {t.market_cap_rank && (
                  <span style={{ fontSize: 10, color: 'var(--muted)' }}>#{t.market_cap_rank}</span>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>{t.name}</div>
            </div>
          ))}
        </div>
      </div>

      {/* New Launches */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Zap size={14} color="#f59e0b" />
          <span style={{ fontSize: 13, fontWeight: 600 }}>New Launches (Pump.fun)</span>
        </div>
        {launches.items.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
            No pump.fun launches yet. Data flows live from GeckoTerminal Solana new pools + pumpfun_monitor daemon.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
            {launches.items.map((l, i) => (
              <div key={i} style={cardStyle}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}><TokenLink symbol={l.symbol} ca={l.address} /></span>
                  <span style={{
                    fontSize: 11,
                    color: Number(l.price_change_24h || 0) >= 0 ? '#22c55e' : '#ef4444'
                  }}>
                    {l.price_change_24h != null ? Number(l.price_change_24h).toFixed(1) : '0.0'}%
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{l.name}</div>
                {l.price && <div style={{ fontSize: 11, color: 'var(--accent)' }}>${Number(l.price).toFixed(8)}</div>}
                {l.volume_24h && <div style={{ fontSize: 10, color: 'var(--muted)' }}>Vol: ${Number(l.volume_24h).toLocaleString()}</div>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Volume Surges */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Rocket size={14} color="#a855f7" />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Volume Surges</span>
          <span style={{ fontSize: 9, color: 'var(--muted)' }}>&gt;3x 6h average</span>
        </div>
        {surges.items.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>No surges detected this cycle.</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
            {surges.items.map((s, i) => (
              <div key={i} style={cardStyle}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}><TokenLink symbol={s.symbol} /></span>
                  <span style={{ fontSize: 11, color: '#a855f7', fontWeight: 700 }}>{s.surge_ratio?.toFixed(1)}x</span>
                </div>
                <div style={{ fontSize: 10, color: 'var(--muted)' }}>{s.signal}</div>
                <div style={{ fontSize: 10, color: 'var(--muted)' }}>1h vol: ${Number(s.volume_1h || 0).toLocaleString()}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent Graduations */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <GraduationCap size={14} color="#22d3ee" />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Recent Graduations</span>
        </div>
        {graduations.items.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>No graduations recorded yet.</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
            {graduations.items.map((g, i) => (
              <div key={i} style={cardStyle}>
                <span style={{ fontWeight: 600, fontSize: 13 }}><TokenLink symbol={g.symbol || g.detail || 'token'} /></span>
                <div style={{ fontSize: 10, color: 'var(--muted)' }}>{g.detail}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Fresh Deployers — who deployed a token currently surfacing as alpha */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <UserPlus size={14} color="#f5a623" />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Fresh Deployers</span>
          <span style={{ fontSize: 9, color: 'var(--muted)' }}>repeat launchers flagged</span>
        </div>
        {deployers.items.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>No deployer data yet — fills in as pumpfun_wallet_intel.py enriches new tokens.</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
            {deployers.items.map((d, i) => (
              <div key={i} style={cardStyle}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}><TokenLink symbol={d.symbol || d.mint.slice(0, 6)} ca={d.mint} /></span>
                  {d.launch_count > 1 && (
                    <span style={{ fontSize: 9, color: '#ef4444', border: '1px solid #ef4444', borderRadius: 4, padding: '1px 5px' }}>
                      {d.launch_count}x launcher
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                  Deployer: <WalletLink address={d.wallet_address} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Risk Warning */}
      <div style={{
        background: 'rgba(239,68,68,0.08)',
        border: '1px solid rgba(239,68,68,0.15)',
        borderRadius: 8,
        padding: 12,
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
      }}>
        <AlertTriangle size={14} color="#ef4444" />
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#ef4444', marginBottom: 4 }}>Degen Warning</div>
          <div style={{ fontSize: 11, color: 'rgba(239,68,68,0.6)', lineHeight: 1.5 }}>
            Pump.fun tokens are extremely high-risk. Most go to zero. Always check: no mint authority, no freeze authority, adequate liquidity. Use small position sizing only. A repeat-launcher flag above isn't automatically bad or good — check their track record before trusting it either way.
          </div>
        </div>
      </div>
    </div>
  )
}
