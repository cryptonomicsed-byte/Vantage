import React, { useEffect, useRef, useState } from 'react'
import { createChart, ColorType, IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'

// ══════════════════════════════════════════════════════════════════════════════
// CandleChart — TradingView Lightweight Charts candlesticks + volume + built-in
// indicator overlays (SMA/EMA/Bollinger), plus optional agent-authored Pine
// series. Reads /api/intel/ohlc + /api/intel/indicators. Pine series come from
// the sandboxed pine-runtime via the parent (numeric series only — never code).
// ══════════════════════════════════════════════════════════════════════════════

export type PineSeries = { name: string; color?: string; data: { time: number; value: number | null }[] }

const COLORS = ['#00f5ff', '#ffaa00', '#8a4bff', '#39ff14', '#ff2d4a', '#ff5cf0']

function num(v: any): number | null {
  return typeof v === 'number' && isFinite(v) ? v : null
}

export default function CandleChart({ symbol, interval, pineSeries = [] }: { symbol: string; interval: string; pineSeries?: PineSeries[] }) {
  const priceRef = useRef<HTMLDivElement>(null)
  const subRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const subChartRef = useRef<IChartApi | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [showOverlays, setShowOverlays] = useState(true)

  useEffect(() => {
    if (!priceRef.current) return
    const common = {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#6b7280', fontFamily: 'Inter, sans-serif' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true },
      crosshair: { mode: 0 },
    }
    const chart = createChart(priceRef.current, { ...common, height: 360 })
    const sub = createChart(subRef.current!, { ...common, height: 130 })
    chartRef.current = chart
    subChartRef.current = sub

    const candle = chart.addCandlestickSeries({
      upColor: '#39ff14', downColor: '#ff2d4a', borderVisible: false,
      wickUpColor: '#39ff14', wickDownColor: '#ff2d4a',
    })
    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '' })
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })

    const overlayMap: Record<string, ISeriesApi<'Line'>> = {}
    const ensureLine = (key: string, color: string) => {
      if (!overlayMap[key]) overlayMap[key] = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
      return overlayMap[key]
    }

    const subSeries = sub.addLineSeries({ color: '#00f5ff', lineWidth: 1 })
    const rsi70 = sub.addLineSeries({ color: 'rgba(255,45,74,0.4)', lineWidth: 1, lastValueVisible: false, priceLineVisible: false })
    const rsi30 = sub.addLineSeries({ color: 'rgba(57,255,20,0.4)', lineWidth: 1, lastValueVisible: false, priceLineVisible: false })

    let cancelled = false
    async function load() {
      setLoading(true); setErr('')
      try {
        const [oR, iR] = await Promise.all([
          fetch(`/api/intel/ohlc/${symbol}?interval=${interval}&limit=200`),
          fetch(`/api/intel/indicators/${symbol}?interval=${interval}`),
        ])
        if (cancelled) return
        const o = oR.ok ? await oR.json() : { candles: [] }
        const ind = iR.ok ? await iR.json() : { indicators: {} }
        const candles = (o.candles || []).filter((c: any) => num(c.close) != null)
        if (!candles.length) { setErr('No candle data for this symbol/interval.'); setLoading(false); return }

        candle.setData(candles.map((c: any) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })))
        vol.setData(candles.map((c: any) => ({ time: c.time as UTCTimestamp, value: c.volume || 0, color: c.close >= c.open ? 'rgba(57,255,20,0.3)' : 'rgba(255,45,74,0.3)' })))

        if (showOverlays) {
          const I = ind.indicators || {}
          const lineFrom = (arr: any[], field = 'value') => (arr || []).map((p: any) => ({ time: p.time as UTCTimestamp, value: num(p[field]) })).filter((p: any) => p.value != null)
          if (I.sma_20) ensureLine('sma', '#ffaa00').setData(lineFrom(I.sma_20))
          if (I.ema_50) ensureLine('ema', '#00f5ff').setData(lineFrom(I.ema_50))
          if (I.bollinger_20) {
            ensureLine('bb_u', 'rgba(138,75,255,0.5)').setData(lineFrom(I.bollinger_20, 'upper'))
            ensureLine('bb_l', 'rgba(138,75,255,0.5)').setData(lineFrom(I.bollinger_20, 'lower'))
          }
          if (I.rsi_14) {
            const r = lineFrom(I.rsi_14)
            subSeries.setData(r)
            if (r.length) {
              rsi70.setData(r.map((p: any) => ({ time: p.time, value: 70 })))
              rsi30.setData(r.map((p: any) => ({ time: p.time, value: 30 })))
            }
          }
        }
        chart.timeScale().fitContent()
        sub.timeScale().fitContent()
        setLoading(false)
      } catch {
        if (!cancelled) { setErr('Failed to load chart data.'); setLoading(false) }
      }
    }
    load()

    const ro = new ResizeObserver(() => {
      if (priceRef.current) chart.applyOptions({ width: priceRef.current.clientWidth })
      if (subRef.current) sub.applyOptions({ width: subRef.current.clientWidth })
    })
    if (priceRef.current) ro.observe(priceRef.current)

    return () => { cancelled = true; ro.disconnect(); chart.remove(); sub.remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, interval, showOverlays])

  // Apply agent Pine series as overlays without rebuilding the chart.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const handles: ISeriesApi<'Line'>[] = []
    pineSeries.forEach((s, i) => {
      const line = chart.addLineSeries({ color: s.color || COLORS[i % COLORS.length], lineWidth: 2, priceLineVisible: false })
      line.setData(s.data.map(p => ({ time: p.time as UTCTimestamp, value: num(p.value) })).filter(p => p.value != null) as any)
      handles.push(line)
    })
    return () => { handles.forEach(h => { try { chart.removeSeries(h) } catch {} }) }
  }, [pineSeries])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        <span className="ares-section-title" style={{ margin: 0 }}>{symbol.toUpperCase()} · {interval}</span>
        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="checkbox" checked={showOverlays} onChange={e => setShowOverlays(e.target.checked)} /> indicators
        </label>
        {loading && <span style={{ fontSize: 11, color: 'var(--muted)' }}>loading…</span>}
        {pineSeries.length > 0 && <span style={{ fontSize: 11, color: 'var(--purple)' }}>+{pineSeries.length} Pine overlay{pineSeries.length > 1 ? 's' : ''}</span>}
      </div>
      {err && <div style={{ color: 'var(--warning)', fontSize: 12, padding: 8 }}>{err}</div>}
      <div ref={priceRef} style={{ width: '100%' }} />
      <div style={{ fontSize: 10, color: 'var(--muted)', margin: '6px 0 2px' }}>RSI (14)</div>
      <div ref={subRef} style={{ width: '100%' }} />
    </div>
  )
}
