import React, { useEffect, useRef, useState } from 'react'

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

export default function PlatformWeather() {
  const [weather, setWeather] = useState<WeatherData | null>(null)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function load() {
      fetch('/api/platform/weather')
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setWeather(d))
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

  if (!weather) return null

  const status = weather.overall
  const netDot = weather.network.congestion
  const mktDot = weather.market.market_pressure
  const socDot = weather.social.vitality

  return (
    <div ref={ref} className="sb-weather-seg" onClick={() => setOpen(o => !o)} title="Platform Weather">
      <span className={`sb-weather-dot ${status}`} />
      <span className="sb-weather-label">
        NET:{weather.network.open_tros} MKT:{weather.market.open_tasks} SOC:{weather.social.active_agents_15m}
      </span>
      {open && (
        <div className="weather-popover" onClick={e => e.stopPropagation()}>
          <div className="weather-popover-title">⚡ PLATFORM WEATHER</div>
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
          <div style={{ marginTop: 8, fontSize: 9, color: 'var(--muted)', textAlign: 'right' }}>
            Updated {new Date(weather.generated_at).toLocaleTimeString()}
          </div>
        </div>
      )}
    </div>
  )
}
