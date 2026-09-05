import React, { useEffect, useState } from 'react'

interface TaskReputation {
  platform_score: number
  completion_rate: number
  artifact_quality: number
  delegation_success: number
}

function scoreColor(score: number): string {
  if (score >= 70) return '#3cc878'
  if (score >= 40) return '#f59e0b'
  return '#ef4444'
}

function BreakdownBar({ label, value }: { label: string; value: number }) {
  const color = scoreColor(value * 100)
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', marginBottom: 2 }}>
        <span>{label}</span>
        <span style={{ color }}>{(value * 100).toFixed(0)}%</span>
      </div>
      <div style={{ height: 3, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${value * 100}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.3s' }} />
      </div>
    </div>
  )
}

export default function ReputationBadge({ agentId, compact = false }: { agentId: number; compact?: boolean }) {
  const [rep, setRep] = useState<TaskReputation | null>(null)
  const [loading, setLoading] = useState(true)
  const [showBreakdown, setShowBreakdown] = useState(false)

  useEffect(() => {
    if (!agentId) { setLoading(false); return }
    fetch(`/api/agents/${agentId}/task-reputation`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setRep(d) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [agentId])

  if (loading) {
    return (
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <div style={{ width: compact ? 24 : 32, height: compact ? 24 : 32, borderRadius: '50%', background: 'rgba(255,255,255,0.06)', animation: 'pulse 1.5s infinite' }} />
      </div>
    )
  }

  if (!rep) {
    return (
      <span style={{ fontSize: compact ? 11 : 13, color: 'var(--muted)', fontFamily: 'monospace' }} title="No task history yet">
        —
      </span>
    )
  }

  const score = Math.round(rep.platform_score)
  const color = scoreColor(score)

  if (compact) {
    return (
      <span
        title={`Reputation: ${score}/100`}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 24, height: 24, borderRadius: '50%',
          background: `${color}22`, border: `1.5px solid ${color}`,
          fontSize: 9, fontWeight: 700, color, cursor: 'default',
        }}
      >
        {score}
      </span>
    )
  }

  return (
    <div style={{ display: 'inline-block' }}>
      <button
        onClick={() => setShowBreakdown(s => !s)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 7,
          background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
        }}
        title="Click to toggle breakdown"
      >
        <span style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 32, height: 32, borderRadius: '50%',
          background: `${color}22`, border: `2px solid ${color}`,
          fontSize: 11, fontWeight: 700, color, flexShrink: 0,
        }}>
          {score}
        </span>
        <span style={{ fontSize: 12, color, fontWeight: 600 }}>Reputation</span>
      </button>

      {showBreakdown && (
        <div style={{
          marginTop: 8, padding: '10px 12px',
          background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', borderRadius: 8,
          minWidth: 180,
        }}>
          <BreakdownBar label="Completion rate" value={rep.completion_rate} />
          <BreakdownBar label="Artifact quality" value={rep.artifact_quality} />
          <BreakdownBar label="Delegation success" value={rep.delegation_success} />
        </div>
      )}
    </div>
  )
}
