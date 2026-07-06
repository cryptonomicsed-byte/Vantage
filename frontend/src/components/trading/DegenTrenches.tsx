import React, { useEffect, useState } from 'react'
import { Flame, TrendingUp, AlertTriangle, Zap, Shield } from 'lucide-react'

interface Token {
  symbol: string
  name: string
  price?: number
  volume_24h?: number
  price_change_24h?: number
  market_cap?: number
  score?: number
}

export default function DegenTrenches() {
  const [trending, setTrending] = useState<Token[]>([])
  const [launches, setLaunches] = useState<Token[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const key = localStorage.getItem('vantage_api_key') || ''
    const headers = { 'X-Agent-Key': key }

    Promise.all([
      fetch('/api/intel/pumpfun/trending?limit=10', { headers }).then(r => r.json()),
      fetch('/api/intel/pumpfun/new-launches?limit=10', { headers }).then(r => r.json()),
    ]).then(([t, l]) => {
      setTrending(t.trending || [])
      setLaunches(l.launches || [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>Loading trenches…</div>

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
          {trending.map((t, i) => (
            <div key={i} style={{
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              borderRadius: 8,
              padding: 10,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{t.symbol}</span>
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
        {launches.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
            No pump.fun launches yet. Data flows live from GeckoTerminal Solana new pools + pumpfun_monitor daemon.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
            {launches.map((l, i) => (
              <div key={i} style={{
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.05)',
                borderRadius: 8,
                padding: 10,
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{l.symbol}</span>
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
            Pump.fun tokens are extremely high-risk. Most go to zero. Always check: no mint authority, no freeze authority, adequate liquidity. Use small position sizing only.
          </div>
        </div>
      </div>
    </div>
  )
}
