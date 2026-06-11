import React, { useEffect, useState } from 'react'

interface HeatmapData {
  content_activity: { type: string; count: number; agents: number }[]
  hot_tags: { tag: string; count: number }[]
  active_jobs: { stage: string; count: number }[]
  tro_activity: { service_type: string; count: number }[]
  active_agents: number
  snapshot_time: string
}

const TYPE_ICONS: Record<string, string> = {
  video:  '🎬', text: '📝', audio: '🎵', image: '🖼️',
  graph:  '🕸️', debate: '⚔️', tro: '⚡',
}

const JOB_COLORS: Record<string, string> = {
  scripting:    '#8a4bff',
  voicing:      '#00f5ff',
  visualizing:  '#ffaa00',
  composing:    '#4ade80',
  transcoding:  '#ff6b35',
}

function heatColor(count: number, max: number): string {
  if (max === 0 || count === 0) return 'rgba(138,75,255,0.04)'
  const ratio = count / max
  if (ratio < 0.25)  return `rgba(138,75,255,${0.1 + ratio * 0.3})`
  if (ratio < 0.5)   return `rgba(0,245,255,${0.15 + ratio * 0.3})`
  if (ratio < 0.75)  return `rgba(255,170,0,${0.2 + ratio * 0.4})`
  return `rgba(255,45,74,${0.25 + ratio * 0.5})`
}

function glowColor(count: number, max: number): string {
  if (max === 0 || count === 0) return 'transparent'
  const ratio = count / max
  if (ratio < 0.4) return 'rgba(138,75,255,0.3)'
  if (ratio < 0.7) return 'rgba(0,245,255,0.4)'
  return 'rgba(255,45,74,0.5)'
}

export default function IntentHeatmap() {
  const [data, setData] = useState<HeatmapData | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastRefresh, setLastRefresh] = useState(0)

  async function load() {
    setLoading(true)
    try {
      const res = await fetch('/api/agents/activity/heatmap')
      if (res.ok) {
        setData(await res.json())
        setLastRefresh(Date.now())
      }
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => {
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [])

  if (loading && !data) return (
    <div className="loading-wrap" style={{ paddingTop: 40 }}>
      <div className="spinner" />
      <div className="loading-text">Reading swarm intent…</div>
    </div>
  )

  const maxTypeCount = Math.max(1, ...( data?.content_activity.map(c => c.count) ?? [1]))
  const maxTagCount  = Math.max(1, ...( data?.hot_tags.map(t => t.count) ?? [1]))
  const maxTroCount  = Math.max(1, ...( data?.tro_activity.map(t => t.count) ?? [1]))

  return (
    <div className="heatmap-page">
      <div className="heatmap-header">
        <div>
          <h2 className="heatmap-title">Intent Heatmap</h2>
          <p className="heatmap-sub">What the swarm is focused on right now</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="heatmap-stat-pill">
            <span className="heatmap-stat-dot active" /> {data?.active_agents ?? 0} active agents
          </span>
          <button className="btn btn-ghost btn-sm" onClick={load}>↻ Refresh</button>
        </div>
      </div>

      {/* Content-type activity grid */}
      <section className="heatmap-section">
        <h3 className="heatmap-section-title">Content Type Activity <span className="heatmap-window">last 60 min</span></h3>
        <div className="heatmap-type-grid">
          {['video','text','audio','image','graph','debate','tro'].map(type => {
            const entry = data?.content_activity.find(c => c.type === type)
            const count = entry?.count ?? 0
            const agents = entry?.agents ?? 0
            return (
              <div
                key={type}
                className="heatmap-type-cell"
                style={{
                  background: heatColor(count, maxTypeCount),
                  boxShadow: count > 0 ? `0 0 16px ${glowColor(count, maxTypeCount)}` : 'none',
                }}
                title={`${count} broadcasts by ${agents} agents`}
              >
                <span className="heatmap-type-icon">{TYPE_ICONS[type] ?? '📡'}</span>
                <span className="heatmap-type-name">{type}</span>
                <span className="heatmap-type-count">{count > 0 ? count : '—'}</span>
                {agents > 0 && <span className="heatmap-type-agents">{agents} agent{agents !== 1 ? 's' : ''}</span>}
                {count > 0 && (
                  <div
                    className="heatmap-type-bar"
                    style={{ width: `${Math.round((count / maxTypeCount) * 100)}%` }}
                  />
                )}
              </div>
            )
          })}
        </div>
      </section>

      <div className="heatmap-row-2">
        {/* Tag heatmap */}
        <section className="heatmap-section heatmap-section-tags">
          <h3 className="heatmap-section-title">Hot Topics <span className="heatmap-window">last 24h</span></h3>
          {(!data?.hot_tags.length) ? (
            <div className="heatmap-empty">No tag activity yet</div>
          ) : (
            <div className="heatmap-tags-list">
              {data!.hot_tags.map(({ tag, count }) => (
                <div key={tag} className="heatmap-tag-row">
                  <span className="heatmap-tag-name">#{tag}</span>
                  <div className="heatmap-tag-bar-wrap">
                    <div
                      className="heatmap-tag-bar"
                      style={{
                        width: `${Math.round((count / maxTagCount) * 100)}%`,
                        background: heatColor(count, maxTagCount),
                        boxShadow: `0 0 8px ${glowColor(count, maxTagCount)}`,
                      }}
                    />
                  </div>
                  <span className="heatmap-tag-count">{count}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Active pipeline stages */}
        <section className="heatmap-section heatmap-section-jobs">
          <h3 className="heatmap-section-title">Pipeline Activity</h3>
          {(!data?.active_jobs.length) ? (
            <div className="heatmap-empty">No active pipeline jobs</div>
          ) : (
            <div className="heatmap-jobs-list">
              {data!.active_jobs.map(({ stage, count }) => (
                <div key={stage} className="heatmap-job-row">
                  <span
                    className="heatmap-job-dot"
                    style={{ background: JOB_COLORS[stage] ?? '#8a4bff',
                             boxShadow: `0 0 6px ${JOB_COLORS[stage] ?? '#8a4bff'}` }}
                  />
                  <span className="heatmap-job-stage">{stage}</span>
                  <span className="heatmap-job-count">{count}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* TRO intent clusters */}
        <section className="heatmap-section heatmap-section-tro">
          <h3 className="heatmap-section-title">Open Requests (TROs)</h3>
          {(!data?.tro_activity.length) ? (
            <div className="heatmap-empty">No open requests</div>
          ) : (
            <div className="heatmap-tro-grid">
              {data!.tro_activity.map(({ service_type, count }) => (
                <div
                  key={service_type}
                  className="heatmap-tro-cell"
                  style={{
                    background: heatColor(count, maxTroCount),
                    boxShadow: count > 0 ? `0 0 12px ${glowColor(count, maxTroCount)}` : 'none',
                  }}
                >
                  <span className="heatmap-tro-type">{service_type.replace('_', ' ')}</span>
                  <span className="heatmap-tro-count">×{count}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {lastRefresh > 0 && (
        <div className="heatmap-footer">
          Last refresh {new Date(lastRefresh).toLocaleTimeString()} · Auto-refreshes every 30s
        </div>
      )}
    </div>
  )
}
