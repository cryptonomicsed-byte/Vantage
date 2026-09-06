import React from 'react'

const TIER_COLORS: Record<number, string> = {
  0: '#6b7280',
  1: '#3b82f6',
  2: '#10b981',
  3: '#8b5cf6',
  4: '#f59e0b',
}

const SIZE_MAP = {
  sm: { fontSize: 9,  padding: '1px 5px',  borderRadius: 4 },
  md: { fontSize: 11, padding: '2px 7px',  borderRadius: 5 },
  lg: { fontSize: 14, padding: '4px 10px', borderRadius: 6 },
}

interface TierBadgeProps {
  tier: number
  size?: 'sm' | 'md' | 'lg'
}

export default function TierBadge({ tier, size = 'md' }: TierBadgeProps) {
  const color = TIER_COLORS[tier] ?? '#6b7280'
  const { fontSize, padding, borderRadius } = SIZE_MAP[size]
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize,
      fontWeight: 700,
      padding,
      borderRadius,
      color,
      background: `${color}22`,
      border: `1px solid ${color}66`,
      letterSpacing: '0.04em',
      whiteSpace: 'nowrap',
    }}>
      T{tier}
    </span>
  )
}
