import React from 'react'
import { Signal } from './tradingStore'

// ══════════════════════════════════════════════════════════════════════════════
// SignalMarkerLayer — renders colored markers on the chart at signal timestamps.
// Used as an overlay above the TradingView chart area. Each marker corresponds
// to a signal in the Intel Feed — clicking a marker highlights the signal card.
//
// This is a RELATIVE overlay: it maps signal timestamps to chart X positions
// based on the candle data currently displayed. The parent ChartWorkspace
// manages positioning.
// ══════════════════════════════════════════════════════════════════════════════

export interface SignalMarker {
  id: string
  time: number       // unix timestamp
  symbol: string
  direction: 'BUY' | 'SELL' | 'LONG' | 'SHORT' | 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  conviction: number  // 0-1
  source: string
  reasoning?: string
  isPredictive?: boolean
  isAnomaly?: boolean
}

interface Props {
  signals: SignalMarker[]
  chartWidth: number
  timeRange: { start: number; end: number }  // chart's visible time range
  top: number       // Y position of marker area
  onMarkerClick?: (signal: SignalMarker) => void
  onMarkerHover?: (signal: SignalMarker | null) => void
}

export default function SignalMarkerLayer({ signals, chartWidth, timeRange, top, onMarkerClick, onMarkerHover }: Props) {
  if (!signals.length || !chartWidth || timeRange.start >= timeRange.end) return null

  const duration = timeRange.end - timeRange.start

  return (
    <div
      style={{
        position: 'absolute',
        top: top,
        left: 0,
        width: '100%',
        height: 28,
        pointerEvents: 'none',
        zIndex: 5,
      }}
    >
      {signals.map((s, i) => {
        // Map timestamp to X position
        const pct = ((s.time - timeRange.start) / duration) * 100
        if (pct < 0 || pct > 100) return null

        const isUp = ['BUY', 'LONG', 'BULLISH'].includes(s.direction)
        const isDown = ['SELL', 'SHORT', 'BEARISH'].includes(s.direction)
        const color = isUp ? '#39ff14' : isDown ? '#ff2d4a' : '#ffaa00'
        const shape = isUp ? '▲' : isDown ? '▼' : '◆'
        const size = 14 + (s.conviction || 0) * 8

        return (
          <div
            key={s.id || i}
            title={`${s.symbol} ${s.direction} · ${((s.conviction || 0) * 100).toFixed(0)}% · ${s.source}${s.reasoning ? '\n' + s.reasoning.slice(0, 80) : ''}`}
            style={{
              position: 'absolute',
              left: `${pct}%`,
              top: '50%',
              transform: 'translate(-50%, -50%)',
              fontSize: size,
              color,
              cursor: 'pointer',
              pointerEvents: 'auto',
              textShadow: `0 0 ${size/3}px ${color}`,
              transition: 'transform 0.1s',
              zIndex: s.isAnomaly ? 6 : 5,
              ...(s.isPredictive ? { filter: 'drop-shadow(0 0 6px #00f5ff)' } : {}),
              ...(s.isAnomaly ? { animation: 'pulse 1.5s infinite' } : {}),
            }}
            onClick={() => onMarkerClick?.(s)}
            onMouseEnter={() => onMarkerHover?.(s)}
            onMouseLeave={() => onMarkerHover?.(null)}
          >
            {shape}
          </div>
        )
      })}
    </div>
  )
}
