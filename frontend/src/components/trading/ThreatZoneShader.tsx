import React from 'react'
import { Threat } from './tradingStore'

// ══════════════════════════════════════════════════════════════════════════════
// ThreatZoneShader — renders semi-transparent red zones on the chart timeline
// corresponding to active STIX threat periods.
//
// Each threat maps to a shaded band on the chart. Threats with higher conviction
// have more opacity. The zone extends from the threat's activation timestamp
// forward to the current time (or resolution date if known).
// ══════════════════════════════════════════════════════════════════════════════

export interface ThreatZone {
  id: string
  name: string
  type: string
  startTime: number         // unix timestamp — when threat was detected
  endTime?: number          // unix timestamp — when threat was resolved (optional, defaults to now)
  conviction: number        // 0-1
  impact: string            // "high", "medium", "low"
  relatedEvents?: string[]
  description?: string
}

interface Props {
  threats: ThreatZone[]
  chartWidth: number
  timeRange: { start: number; end: number }
  chartHeight: number
  onThreatClick?: (threat: ThreatZone) => void
}

export default function ThreatZoneShader({ threats, chartWidth, timeRange, chartHeight, onThreatClick }: Props) {
  if (!threats.length || !chartWidth || timeRange.start >= timeRange.end) return null

  const duration = timeRange.end - timeRange.start

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 3,
        overflow: 'hidden',
      }}
    >
      {threats.map((t, i) => {
        const startPct = ((t.startTime - timeRange.start) / duration) * 100
        const endTime = t.endTime || timeRange.end
        const endPct = ((endTime - timeRange.start) / duration) * 100

        // Clamp to visible range
        const visibleStart = Math.max(0, startPct)
        const visibleEnd = Math.min(100, endPct)
        const visibleWidth = visibleEnd - visibleStart

        if (visibleWidth <= 0) return null

        const opacity = Math.min(0.18, (t.conviction || 0.5) * 0.25)
        const color = t.impact === 'high' ? '255,45,74' : t.impact === 'medium' ? '255,170,0' : '138,75,255'

        return (
          <div
            key={t.id || i}
            title={`${t.name} · ${t.impact.toUpperCase()} IMPACT · ${((t.conviction || 0) * 100).toFixed(0)}% conviction\n${t.description || ''}`}
            style={{
              position: 'absolute',
              left: `${visibleStart}%`,
              top: 0,
              width: `${visibleWidth}%`,
              height: '100%',
              background: `rgba(${color}, ${opacity})`,
              borderLeft: startPct >= 0 ? `2px solid rgba(${color}, 0.5)` : 'none',
              borderRight: endPct <= 100 ? `2px solid rgba(${color}, 0.3)` : 'none',
              pointerEvents: 'auto',
              cursor: 'pointer',
              zIndex: 3,
            }}
            onClick={() => onThreatClick?.(t)}
          >
            {/* Threat label at the top of the zone */}
            {visibleWidth > 15 && (
              <div
                style={{
                  position: 'absolute',
                  top: 4,
                  left: 4,
                  fontSize: 10,
                  fontWeight: 700,
                  color: `rgb(${color})`,
                  textShadow: '0 1px 3px rgba(0,0,0,0.8)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: `${visibleWidth - 2}%`,
                }}
              >
                ⚠ {t.name}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
