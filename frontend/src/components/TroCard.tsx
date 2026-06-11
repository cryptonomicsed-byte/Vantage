import React from 'react'
import { Clock, DollarSign, Users } from 'lucide-react'

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
  response_count?: number
}

interface Props {
  tro: TroRequest
  onClick: () => void
}

const SERVICE_ICONS: Record<string, string> = {
  text_generation: '📝',
  image:           '🖼️',
  analysis:        '🔍',
  code:            '💻',
  audio:           '🎵',
  video:           '🎬',
  research:        '📚',
  translation:     '🌐',
  graph:           '🕸️',
  debate:          '⚔️',
}

export default function TroCard({ tro, onClick }: Props) {
  const icon     = SERVICE_ICONS[tro.service_type] || '⚡'
  const timeLeft = new Date(tro.expires_at).getTime() - Date.now()
  const hoursLeft = Math.max(0, Math.floor(timeLeft / 3_600_000))
  const minsLeft  = Math.max(0, Math.floor((timeLeft % 3_600_000) / 60_000))
  const isUrgent  = timeLeft < 30 * 60_000
  const hasBids   = (tro.response_count ?? 0) > 0
  const isMatched = tro.status === 'matched' || tro.status === 'fulfilled'

  return (
    <div className="tro-card" onClick={onClick} data-status={tro.status}>
      <div className="tro-card-header">
        <span className="tro-icon">{icon}</span>
        <span className="tro-type-pill">{tro.service_type.replace('_', ' ')}</span>
        {tro.status === 'matched'   && <span className="tro-matched-pill">matched</span>}
        {tro.status === 'fulfilled' && (
          <span className="tro-matched-pill" style={{ background: 'rgba(74,222,128,0.15)', color: '#4ade80', borderColor: '#4ade8044' }}>
            fulfilled
          </span>
        )}
      </div>
      <div className="tro-card-desc">
        {tro.description.slice(0, 120)}{tro.description.length > 120 ? '…' : ''}
      </div>
      <div className="tro-card-footer">
        <span className="tro-agent">{tro.agent_name}</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {hasBids && (
            <span className="tro-bids" title={`${tro.response_count} bid${tro.response_count !== 1 ? 's' : ''}`}>
              <Users size={9} />
              <span>{tro.response_count}</span>
              {!isMatched && <span className="tro-bids-pulse" />}
            </span>
          )}
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
