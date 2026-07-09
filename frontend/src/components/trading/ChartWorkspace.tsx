import React, { useEffect, useRef, useState, useMemo } from 'react'
import { createChart, ColorType, IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import { useTradingStore } from './tradingStore'
import PineScriptPanel from './PineScriptPanel'
import SignalMarkerLayer from './SignalMarkerLayer'
import ThreatZoneShader from './ThreatZoneShader'

// ══════════════════════════════════════════════════════════════════════════════
// ChartWorkspace — center column. Chart is ALWAYS visible.
// Wraps Lightweight Charts + Pine Script overlays + signal markers.
// ══════════════════════════════════════════════════════════════════════════════

const COLORS = ['#00f5ff', '#ffaa00', '#8a4bff', '#39ff14', '#ff2d4a', '#ff5cf0']

function num(v: any): number | null {
  return typeof v === 'number' && isFinite(v) ? v : null
}

export default function ChartWorkspace() {
  const { state, navigateTo } = useTradingStore()
  const priceRef = useRef<HTMLDivElement>(null)
  const subRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const subChartRef = useRef<IChartApi | null>(null)
  const [loading, setLoading] = useState(true)
  const [crosshairData, setCrosshairData] = useState<any>(null)

  // Build chart on mount and when pair/timeframe changes
  useEffect(() => {
    if (!priceRef.current || !subRef.current) return

    const common = {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#6b7280', fontFamily: 'Inter, sans-serif' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true },
      crosshair: { mode: 0 },
    }

    const chart = createChart(priceRef.current, { ...common, height: priceRef.current.clientHeight || 400 })
    const sub = createChart(subRef.current, { ...common, height: subRef.current.clientHeight || 120 })
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

    // Crosshair data
    chart.subscribeCrosshairMove((param: any) => {
      if (!param || !param.time) { setCrosshairData(null); return }
      const candleData = param.seriesData.get(candle)
      const volData = param.seriesData.get(vol)
      setCrosshairData(candleData ? {
        time: param.time,
        open: candleData.open,
        high: candleData.high,
        low: candleData.low,
        close: candleData.close,
        volume: volData?.value,
      } : null)
    })

    let cancelled = false
    async function load() {
      setLoading(true)
      try {
        const symbol = state.activePair.split('/')[0].toLowerCase()
        const tfMap: Record<string, string> = { '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '4h': '4h', '1D': '1d', '1W': '1w' }
        const interval = tfMap[state.activeTimeframe] || '1h'

        const [oR, iR] = await Promise.all([
          fetch(`/api/intel/ohlc/${symbol}?interval=${interval}&limit=200`),
          fetch(`/api/intel/indicators/${symbol}?interval=${interval}`),
        ])
        if (cancelled) return
        const o = oR.ok ? await oR.json() : { candles: [] }
        const ind = iR.ok ? await iR.json() : { indicators: {} }
        const candles = (o.candles || []).filter((c: any) => num(c.close) != null)
        if (!candles.length) { setLoading(false); return }

        candle.setData(candles.map((c: any) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })))
        vol.setData(candles.map((c: any) => ({ time: c.time as UTCTimestamp, value: c.volume || 0, color: c.close >= c.open ? 'rgba(57,255,20,0.25)' : 'rgba(255,45,74,0.25)' })))

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

        chart.timeScale().fitContent()
        sub.timeScale().fitContent()
        setLoading(false)
      } catch {
        if (!cancelled) setLoading(false)
      }
    }
    load()

    const ro = new ResizeObserver(() => {
      if (priceRef.current) chart.applyOptions({ width: priceRef.current.clientWidth })
      if (subRef.current) sub.applyOptions({ width: subRef.current.clientWidth })
    })
    if (priceRef.current) ro.observe(priceRef.current)

    return () => { cancelled = true; ro.disconnect(); chart.remove(); sub.remove() }
  }, [state.activePair, state.activeTimeframe])

  // Build signal markers for overlay
  const signalMarkers = useMemo(() => {
    return state.signals
      .filter(s => s.timestamp && s.symbol)
      .map(s => ({
        id: s.id || `${s.symbol}-${s.timestamp}-${s.direction}`,
        time: typeof s.timestamp === 'string' ? new Date(s.timestamp).getTime() / 1000 : s.timestamp,
        symbol: s.symbol,
        direction: s.direction,
        conviction: s.conviction || 0.5,
        source: s.source,
        reasoning: s.reasoning,
        isPredictive: s.is_predictive,
        isAnomaly: s.is_anomaly,
      }))
  }, [state.signals])

  // Build threat zones for overlay
  const threatZones = useMemo(() => {
    return state.activeThreats
      .filter(t => t.timestamp)
      .map(t => {
        const ts = typeof t.timestamp === 'string' ? new Date(t.timestamp).getTime() / 1000 : t.timestamp
        return {
          id: t.id || t.name,
          name: t.name,
          type: t.type,
          startTime: ts,
          conviction: t.conviction || 0.7,
          impact: t.impact || 'medium',
          relatedEvents: t.related_events,
        }
      })
  }, [state.activeThreats])

  return (
    <div style={styles.container}>
      {/* Crosshair Data Bar */}
      <div style={styles.crosshairBar}>
        <span style={styles.pairLabel}>{state.activePair}</span>
        {crosshairData ? (
          <>
            <span style={styles.ohlcvLabel}>O</span><span style={styles.ohlcvVal}>{crosshairData.open?.toFixed(2)}</span>
            <span style={styles.ohlcvLabel}>H</span><span style={styles.ohlcvVal}>{crosshairData.high?.toFixed(2)}</span>
            <span style={styles.ohlcvLabel}>L</span><span style={styles.ohlcvVal}>{crosshairData.low?.toFixed(2)}</span>
            <span style={styles.ohlcvLabel}>C</span><span style={styles.ohlcvVal}>{crosshairData.close?.toFixed(2)}</span>
            {crosshairData.volume != null && (
              <><span style={styles.ohlcvLabel}>Vol</span><span style={styles.ohlcvVal}>{(crosshairData.volume / 1e6).toFixed(2)}M</span></>
            )}
          </>
        ) : (
          <span style={{ color: '#6b7280', fontSize: 11 }}>Hover chart for OHLCV</span>
        )}
        {/* Signal count badge */}
        {signalMarkers.length > 0 && (
          <span style={{ marginLeft: 'auto', fontSize: 10, color: '#ffaa00', background: 'rgba(255,170,0,0.12)', border: '1px solid rgba(255,170,0,0.25)', borderRadius: 4, padding: '1px 6px' }}>
            {signalMarkers.length} signals
          </span>
        )}
        {threatZones.length > 0 && (
          <span style={{ fontSize: 10, color: '#ff2d4a', background: 'rgba(255,45,74,0.12)', border: '1px solid rgba(255,45,74,0.25)', borderRadius: 4, padding: '1px 6px', marginLeft: 6 }}>
            ⚠ {threatZones.length} threats
          </span>
        )}
        {loading && <span style={{ color: '#6b7280', fontSize: 11, marginLeft: 'auto' }}>Loading...</span>}
      </div>

      {/* Price Chart with overlays */}
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <div ref={priceRef} style={{ width: '100%', height: '100%' }} />
        {/* Signal markers overlay */}
        <SignalMarkerLayer
          signals={signalMarkers}
          chartWidth={priceRef.current?.clientWidth || 600}
          timeRange={{ start: 0, end: 0 }}
          top={4}
          onMarkerClick={(s) => {
            navigateTo(`${s.symbol}/USDT`, state.activeTimeframe, s.time)
          }}
        />
        {/* Threat zones overlay */}
        <ThreatZoneShader
          threats={threatZones}
          chartWidth={priceRef.current?.clientWidth || 600}
          timeRange={{ start: 0, end: 0 }}
          chartHeight={priceRef.current?.clientHeight || 400}
        />
      </div>

      {/* RSI Sub-chart */}
      <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
        <div style={{ fontSize: 10, color: '#6b7280', padding: '4px 8px 2px' }}>RSI (14)</div>
        <div ref={subRef} style={{ height: 120 }} />
      </div>

      {/* Pine Script Panel (slides in) */}
      {state.pinePanelOpen && <PineScriptPanel />}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    position: 'relative',
  },
  crosshairBar: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '6px 12px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    fontSize: 12,
    fontFamily: 'monospace',
    flexShrink: 0,
  },
  pairLabel: {
    fontWeight: 700,
    color: '#e0e0e0',
    fontSize: 13,
  },
  ohlcvLabel: {
    color: '#6b7280',
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase' as const,
  },
  ohlcvVal: {
    color: '#e0e0e0',
    fontSize: 11,
  },
}
