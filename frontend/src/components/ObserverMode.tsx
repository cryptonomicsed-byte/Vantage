import React, { useEffect, useRef, useState } from 'react'
import { Eye, EyeOff, X } from 'lucide-react'

interface TraceEntry {
  id: number
  agent_name: string
  trace_type: string
  message: string
  metadata: Record<string, unknown>
  created_at: string
  avatar_url?: string
  _fresh?: boolean
}

const TYPE_CONFIG: Record<string, { color: string; icon: string; label: string }> = {
  thought:     { color: '#8a4bff', icon: '💭', label: 'Thought'     },
  action:      { color: '#00f5ff', icon: '⚡', label: 'Action'      },
  negotiation: { color: '#ffaa00', icon: '🤝', label: 'Negotiation' },
  system:      { color: '#6b7280', icon: '⚙️', label: 'System'      },
  error:       { color: '#ff2d4a', icon: '⛔', label: 'Error'       },
  decision:    { color: '#4ade80', icon: '✅', label: 'Decision'    },
}

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

interface Props {
  enabled: boolean
  onToggle: () => void
}

export default function ObserverMode({ enabled, onToggle }: Props) {
  const [traces, setTraces] = useState<TraceEntry[]>([])
  const [filter, setFilter] = useState<string>('all')
  const scrollRef = useRef<HTMLDivElement>(null)
  const lastIdRef = useRef<number>(0)
  const [pinBottom, setPinBottom] = useState(true)

  useEffect(() => {
    if (!enabled) return
    async function loadTraces() {
      try {
        const res = await fetch('/api/agents/activity-log?limit=60')
        if (!res.ok) return
        const data: TraceEntry[] = await res.json()
        setTraces(data)
        if (data.length) lastIdRef.current = data[0].id
      } catch { /* ignore */ }
    }
    loadTraces()
    const interval = setInterval(async () => {
      try {
        const res = await fetch('/api/agents/activity-log?limit=20')
        if (!res.ok) return
        const data: TraceEntry[] = await res.json()
        const newEntries = data.filter(t => t.id > lastIdRef.current)
        if (newEntries.length) {
          lastIdRef.current = newEntries[0].id
          setTraces(prev => [
            ...newEntries.map(t => ({ ...t, _fresh: true })),
            ...prev,
          ].slice(0, 200))
          if (pinBottom && scrollRef.current) {
            setTimeout(() => scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' }), 50)
          }
        }
      } catch { /* ignore */ }
    }, 4000)
    return () => clearInterval(interval)
  }, [enabled, pinBottom])

  const visible = filter === 'all' ? traces : traces.filter(t => t.trace_type === filter)

  return (
    <>
      {/* Toggle button — always visible */}
      <button
        className={`observer-toggle${enabled ? ' active' : ''}`}
        onClick={onToggle}
        title={enabled ? 'Disable Observer Mode' : 'Enable Observer Mode — watch agent thought streams'}
      >
        {enabled ? <EyeOff size={14} /> : <Eye size={14} />}
        <span>Observer</span>
        {enabled && <span className="observer-live-dot" />}
      </button>

      {/* Side panel */}
      {enabled && (
        <div className="observer-panel">
          <div className="observer-header">
            <span className="observer-title">
              <Eye size={12} /> Ghost Feed
            </span>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span className="observer-live-badge">LIVE</span>
              <button className="observer-close" onClick={onToggle}><X size={12} /></button>
            </div>
          </div>

          {/* Type filter chips */}
          <div className="observer-filters">
            {['all', 'thought', 'action', 'negotiation', 'decision', 'system', 'error'].map(t => (
              <button
                key={t}
                className={`observer-filter-chip${filter === t ? ' active' : ''}`}
                onClick={() => setFilter(t)}
                style={filter === t && t !== 'all' ? { borderColor: TYPE_CONFIG[t]?.color, color: TYPE_CONFIG[t]?.color } : {}}
              >
                {t === 'all' ? 'All' : (TYPE_CONFIG[t]?.icon + ' ' + TYPE_CONFIG[t]?.label)}
              </button>
            ))}
          </div>

          <div
            ref={scrollRef}
            className="observer-feed"
            onScroll={e => {
              const el = e.currentTarget
              setPinBottom(el.scrollTop < 20)
            }}
          >
            {visible.length === 0 && (
              <div className="observer-empty">No traces yet. Agents push traces via POST /me/trace</div>
            )}
            {visible.map(entry => {
              const cfg = TYPE_CONFIG[entry.trace_type] || TYPE_CONFIG.thought
              return (
                <div
                  key={entry.id}
                  className={`observer-entry${entry._fresh ? ' fresh' : ''}`}
                  style={{ borderLeftColor: cfg.color }}
                >
                  <div className="observer-entry-head">
                    <span className="observer-entry-agent" style={{ color: cfg.color }}>
                      {cfg.icon} {entry.agent_name}
                    </span>
                    <span className="observer-entry-type" style={{ color: cfg.color }}>
                      {cfg.label}
                    </span>
                    <span className="observer-entry-time">{timeAgo(entry.created_at)}</span>
                  </div>
                  <div className="observer-entry-msg">{entry.message}</div>
                  {Object.keys(entry.metadata || {}).length > 0 && (
                    <details className="observer-entry-meta">
                      <summary>metadata</summary>
                      <pre>{JSON.stringify(entry.metadata, null, 2)}</pre>
                    </details>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </>
  )
}
