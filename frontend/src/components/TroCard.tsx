import React, { useState } from 'react'
import { Zap, Clock, DollarSign } from 'lucide-react'

interface TroRequest {
  id: number
  agent_name: string
  service_type: string
  description: string
  parameters: Record<string, unknown>
  budget_usdc: number
  status: string
  matched_agent: string
  expires_at: string
  created_at: string
}

interface Props {
  tro: TroRequest
  onClick: () => void
}

const SERVICE_ICONS: Record<string, string> = {
  text_generation: '📝',
  image: '🖼️',
  analysis: '🔍',
  code: '💻',
  audio: '🎵',
  video: '🎬',
  research: '📚',
  translation: '🌐',
}

export default function TroCard({ tro, onClick }: Props) {
  const icon = SERVICE_ICONS[tro.service_type] || '⚡'
  const timeLeft = new Date(tro.expires_at).getTime() - Date.now()
  const hoursLeft = Math.max(0, Math.floor(timeLeft / 3_600_000))
  const minsLeft = Math.max(0, Math.floor((timeLeft % 3_600_000) / 60_000))
  const isUrgent = timeLeft < 30 * 60_000

  return (
    <div className="tro-card" onClick={onClick} data-status={tro.status}>
      <div className="tro-card-header">
        <span className="tro-icon">{icon}</span>
        <span className={`tro-type-pill`}>{tro.service_type.replace('_', ' ')}</span>
        {tro.status === 'matched' && <span className="tro-matched-pill">matched</span>}
      </div>
      <div className="tro-card-desc">{tro.description.slice(0, 120)}{tro.description.length > 120 ? '…' : ''}</div>
      <div className="tro-card-footer">
        <span className="tro-agent">{tro.agent_name}</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {tro.budget_usdc > 0 && (
            <span className="tro-budget">
              <DollarSign size={9} />{tro.budget_usdc.toFixed(2)}
            </span>
          )}
          <span className={`tro-time${isUrgent ? ' urgent' : ''}`}>
            <Clock size={9} />
            {hoursLeft > 0 ? `${hoursLeft}h` : `${minsLeft}m`}
          </span>
        </div>
      </div>
    </div>
  )
}
