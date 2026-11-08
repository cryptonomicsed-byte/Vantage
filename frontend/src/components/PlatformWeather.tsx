import React, { useEffect, useRef, useState } from 'react'
import SearchPanel from './SearchPanel'
import NotificationPanel from './NotificationPanel'

interface WeatherData {
  overall: 'green' | 'amber' | 'red'
  generated_at: string
  network: {
    avg_tro_fulfill_minutes: number
    open_tros: number
    stuck_tros: number
    congestion: string
  }
  market: {
    open_tasks: number
    highest_pressure_capability: string
    market_pressure: string
  }
  social: {
    new_agents_today: number
    broadcasts_today: number
    active_agents_15m: number
    vitality: string
  }
  bottlenecks: Array<{ capability: string; avg_wait_hours: number; open_count: number }>
  trending_tags: Array<{ tag: string; count: number }>
}

// Bitcoin clock (/api/clock/height) — the same surface agents gate on.
interface ClockData {
  height?: number
  verified?: boolean
  verified_depth?: number | null
  verified_lowest?: number | null
  rooted?: boolean
  pin?: { height?: number; class?: string; rooted_by?: { height?: number; class?: string } | null } | null
  next_block_eta_s?: number
  sabbath?: boolean
  domain?: string
  uncertainty_s?: number | null
}

// Latest receipt (/api/receipts/latest) — what the organism last proved.
interface ReceiptData {
  ok?: boolean
  latest?: {
    receipt_id: string
    findings: number
    max_cvss: number | null
    ots: 'none' | 'pending' | 'attested' | 'verified' | 'unreadable'
    ots_block?: number | null
  } | null
  total?: number
}

export default function PlatformWeather() {
  const [weather, setWeather] = useState<WeatherData | null>(null)
  const [clock, setClock] = useState<ClockData | null>(null)
  const [receipt, setReceipt] = useState<ReceiptData | null>(null)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function load() {
      fetch('/api/platform/weather')
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setWeather(d))
        .catch(() => {})
      fetch('/api/clock/height')
        .then(r => r.ok ? r.json() : null)
        .then(d => d && typeof d.height === 'number' && setClock(d))
        .catch(() => {})
      fetch('/api/receipts/latest')
        .then(r => r.ok ? r.json() : null)
        .then(d => d?.ok && setReceipt(d))
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 62000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const status = weather?.overall
  const netDot = weather?.network.congestion
  const mktDot = weather?.market.market_pressure
  const socDot = weather?.social.vitality

  const hasData = !!(weather || clock)
  const clk = clock?.height ? clock : null
  const rootedBy = clk?.pin?.rooted_by
  const r = receipt?.latest
  const proof =
    r?.ots === 'verified' ? `btc ✓${r.ots_block ?? ''}` :
    r?.ots === 'attested' ? `btc ?${r.ots_block ?? ''}` :
    r?.ots === 'pending' ? 'ots pending' : 'no proof'

  return (
    <div ref={ref} className="sb-weather-seg" title="Platform Weather">
      {/* Search + Notifications sit inside this cluster, clicks don't open weather */}
      {hasData && (
        <span className="sb-weather-data" onClick={() => setOpen(o => !o)}>
          {weather && (
            <>
              <span className={`sb-weather-dot ${status}`} />
              <span className="sb-weather-label">
                NET:{weather.network.open_tros} MKT:{weather.market.open_tasks} SOC:{weather.social.active_agents_15m}
              </span>
            </>
          )}
          {clk && (
            <span
              className="sb-weather-label"
              style={{ color: clk.rooted ? '#22c55e' : 'var(--muted)' }}
              title={
                `Bitcoin clock: block ${clk.height?.toLocaleString()}` +
                (clk.verified ? ` · ${clk.verified_depth ?? '?'} headers PoW-verified` : ' · NOT verified') +
                (clk.rooted
                  ? ` · rooted (${rootedBy?.class || clk.pin?.class || 'pin'} @${rootedBy?.height || clk.pin?.height})`
                  : ' · NOT rooted') +
                (clk.sabbath ? ' · SABBATH' : '')
              }
            >
              {' '}₿:{clk.height?.toLocaleString()}{clk.verified ? '✓' : '?'}
            </span>
          )}
        </span>
      )}
      {open && (weather || clk || r) && (
        <div className="weather-popover" onClick={e => e.stopPropagation()}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <div className="weather-popover-title" style={{ marginBottom: 0 }}>PLATFORM WEATHER</div>
            <div className="sb-holo-group" style={{ display: 'flex', gap: 2 }}>
              <SearchPanel bottomBarMode />
              <NotificationPanel bottomBarMode />
            </div>
          </div>
          {clk && (
            <>
              <div className="weather-row">
                <span className="weather-row-label">Bitcoin</span>
                <span className="weather-row-value">
                  <span className={`sb-weather-dot ${clk.rooted ? 'green' : clk.verified ? 'amber' : 'red'}`} style={{ marginRight: 4 }} />
                  ₿{clk.height?.toLocaleString()}
                  {clk.domain && ` · ${clk.domain} day`}
                  {clk.verified && ` · ${clk.verified_depth ?? '?'} hdrs ✓`}
                  {clk.rooted && ` · rooted ${rootedBy?.class || clk.pin?.class || ''}@${rootedBy?.height ?? clk.pin?.height ?? '?'}`}
                </span>
              </div>
              <div className="weather-row">
                <span className="weather-row-label">Chain</span>
                <span className="weather-row-value">
                  {clk.verified
                    ? `PoW + linkage OK back to ${clk.verified_lowest?.toLocaleString() ?? '?'}`
                    : 'NOT verified — height is a quorum claim only'}
                  {clk.uncertainty_s != null && ` · ±${clk.uncertainty_s}s`}
                  {clk.next_block_eta_s ? ` · next ~${Math.floor(clk.next_block_eta_s / 60)}m` : ''}
                </span>
              </div>
            </>
          )}
          {r && (
            <div className="weather-row">
              <span className="weather-row-label">Receipt</span>
              <span className="weather-row-value">
                {r.receipt_id.slice(0, 8)} · {r.findings} findings
                {r.max_cvss != null && ` · max ${r.max_cvss}`} · {proof} · {receipt?.total ?? '?'} chained
              </span>
            </div>
          )}
          {weather && (
            <>
              <div className="weather-row">
                <span className="weather-row-label">Network</span>
                <span className="weather-row-value">
                  <span className={`sb-weather-dot ${netDot}`} style={{ marginRight: 4 }} />
                  {weather.network.avg_tro_fulfill_minutes}m avg · {weather.network.open_tros} open TROs
                  {weather.network.stuck_tros > 0 && ` · ${weather.network.stuck_tros} stuck`}
                </span>
              </div>
              <div className="weather-row">
                <span className="weather-row-label">Market</span>
                <span className="weather-row-value">
                  <span className={`sb-weather-dot ${mktDot}`} style={{ marginRight: 4 }} />
                  {weather.market.open_tasks} tasks
                  {weather.market.highest_pressure_capability && ` · ↑${weather.market.highest_pressure_capability}`}
                </span>
              </div>
              <div className="weather-row">
                <span className="weather-row-label">Social</span>
                <span className="weather-row-value">
                  <span className={`sb-weather-dot ${socDot}`} style={{ marginRight: 4 }} />
                  {weather.social.active_agents_15m} active · {weather.social.broadcasts_today} posts today
                </span>
              </div>
              {weather.bottlenecks.length > 0 && (
                <div className="weather-row">
                  <span className="weather-row-label">Bottleneck</span>
                  <span className="weather-row-value">{weather.bottlenecks[0].capability} ({weather.bottlenecks[0].avg_wait_hours}h)</span>
                </div>
              )}
              {weather.trending_tags.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div className="weather-popover-title" style={{ marginBottom: 4 }}>TRENDING</div>
                  <div className="weather-tag-strip">
                    {weather.trending_tags.slice(0, 6).map(t => (
                      <span key={t.tag} className="weather-tag-pill">#{t.tag}</span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
          <div style={{ marginTop: 8, fontSize: 9, color: 'var(--muted)', textAlign: 'right' }}>
            {weather ? <>Updated {new Date(weather.generated_at).toLocaleTimeString()}</> : <>Updated {new Date().toLocaleTimeString()}</>}
          </div>
        </div>
      )}
    </div>
  )
}
