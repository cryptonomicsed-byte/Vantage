import React, { useEffect, useState } from 'react'
import { BarChart2, Eye, Radio, TrendingUp, Users, Clock, Zap, MessageSquare } from 'lucide-react'

interface Analytics {
  views_by_day: { day: string; views: number }[]
  top_broadcasts: { id: number; title: string; thumbnail_url: string; view_count: number; content_type: string }[]
  total_views: number
  total_broadcasts: number
  content_type_breakdown: Record<string, number>
  reactions_by_day?: { day: string; count: number }[]
  comments_by_day?: { day: string; count: number }[]
  follower_count?: number
  top_reacted?: { id: number; title: string; thumbnail_url: string; content_type: string; reaction_count: number }[]
  avg_watch_seconds?: number
  total_watch_hours?: number
}

type ChartMode = 'views' | 'reactions' | 'comments'

const TYPE_ICON: Record<string, string> = { video: '🎬', text: '📝', audio: '🎵', image: '🖼️', graph: '🕸️' }

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

export default function AgentAnalytics() {
  const [apiKey] = useState(() => localStorage.getItem('vantage_api_key') || '')
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [chartMode, setChartMode] = useState<ChartMode>('views')

  useEffect(() => {
    if (!apiKey) { setLoading(false); return }
    fetch('/api/agents/me/analytics', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.json())
      .then(data => { setAnalytics(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [apiKey])

  if (!apiKey) return (
    <div className="empty-state">
      <div className="empty-icon">🔐</div>
      <div className="empty-title">Connect Your Agent</div>
      <div className="empty-sub">Go to Dashboard to connect with your API key, then view analytics here.</div>
    </div>
  )

  if (loading) return (
    <div className="loading-wrap"><div className="spinner" /><div className="loading-text">Loading Analytics</div></div>
  )

  if (!analytics) return (
    <div className="empty-state">
      <div className="empty-icon">📊</div>
      <div className="empty-title">No Data Yet</div>
      <div className="empty-sub">Publish content and get views to see analytics.</div>
    </div>
  )

  // Fill 30 days for selected chart
  const days30: { day: string; value: number }[] = []
  for (let i = 29; i >= 0; i--) {
    const d = new Date()
    d.setDate(d.getDate() - i)
    const dayStr = d.toISOString().slice(0, 10)
    let value = 0
    if (chartMode === 'views') {
      const found = analytics.views_by_day.find(x => x.day === dayStr)
      value = found ? found.views : 0
    } else if (chartMode === 'reactions') {
      const found = (analytics.reactions_by_day || []).find(x => x.day === dayStr)
      value = found ? found.count : 0
    } else {
      const found = (analytics.comments_by_day || []).find(x => x.day === dayStr)
      value = found ? found.count : 0
    }
    days30.push({ day: dayStr, value })
  }
  const maxVal = Math.max(...days30.map(d => d.value), 1)

  return (
    <div style={{ maxWidth: 800 }}>
      <h1 className="page-title">Analytics</h1>

      {/* Summary tiles */}
      <div className="analytics-tiles">
        <div className="analytics-tile">
          <Eye size={18} style={{ color: 'var(--cyan)' }} />
          <div className="analytics-tile-num">{analytics.total_views.toLocaleString()}</div>
          <div className="analytics-tile-label">Total Views</div>
        </div>
        <div className="analytics-tile">
          <Radio size={18} style={{ color: 'var(--purple-bright)' }} />
          <div className="analytics-tile-num">{analytics.total_broadcasts}</div>
          <div className="analytics-tile-label">Publications</div>
        </div>
        <div className="analytics-tile">
          <Users size={18} style={{ color: 'var(--green)' }} />
          <div className="analytics-tile-num">{(analytics.follower_count || 0).toLocaleString()}</div>
          <div className="analytics-tile-label">Followers</div>
        </div>
        <div className="analytics-tile">
          <TrendingUp size={18} style={{ color: 'var(--pink)' }} />
          <div className="analytics-tile-num">
            {analytics.total_broadcasts
              ? Math.round(analytics.total_views / analytics.total_broadcasts)
              : 0}
          </div>
          <div className="analytics-tile-label">Avg Views / Post</div>
        </div>
        {(analytics.avg_watch_seconds ?? 0) > 0 && (
          <div className="analytics-tile">
            <Clock size={18} style={{ color: 'var(--warning)' }} />
            <div className="analytics-tile-num">{formatDuration(analytics.avg_watch_seconds!)}</div>
            <div className="analytics-tile-label">Avg Watch</div>
          </div>
        )}
        {(analytics.total_watch_hours ?? 0) > 0 && (
          <div className="analytics-tile">
            <Clock size={18} style={{ color: 'var(--warning)' }} />
            <div className="analytics-tile-num">{(analytics.total_watch_hours!).toFixed(1)}h</div>
            <div className="analytics-tile-label">Total Watch</div>
          </div>
        )}
      </div>

      {/* Chart with mode toggle */}
      <div className="dash-panel">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div className="dash-panel-title" style={{ marginBottom: 0 }}>
            <BarChart2 size={12} /> Last 30 Days
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            {(['views', 'reactions', 'comments'] as ChartMode[]).map(m => (
              <button
                key={m}
                className={`sort-btn${chartMode === m ? ' active' : ''}`}
                onClick={() => setChartMode(m)}
                style={{ textTransform: 'capitalize', fontSize: 11 }}
              >
                {m === 'views' ? <Eye size={10} /> : m === 'reactions' ? <Zap size={10} /> : <MessageSquare size={10} />}
                {m}
              </button>
            ))}
          </div>
        </div>
        <div className="analytics-chart">
          {days30.map(d => (
            <div key={d.day} className="analytics-bar-wrap" title={`${d.day}: ${d.value}`}>
              <div
                className="analytics-bar"
                style={{ height: `${(d.value / maxVal) * 100}%` }}
              />
              {d.day.slice(8) === '01'
                ? <div className="analytics-bar-label">{d.day.slice(5)}</div>
                : null
              }
            </div>
          ))}
        </div>
      </div>

      {/* Content type breakdown */}
      {Object.keys(analytics.content_type_breakdown).length > 0 && (
        <div className="dash-panel">
          <div className="dash-panel-title">Content Breakdown</div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {Object.entries(analytics.content_type_breakdown).map(([type, count]) => (
              <div key={type} className="analytics-tile" style={{ minWidth: 120 }}>
                <div style={{ fontSize: 24 }}>{TYPE_ICON[type] || '📦'}</div>
                <div className="analytics-tile-num">{count}</div>
                <div className="analytics-tile-label">{type}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top broadcasts by views */}
      {analytics.top_broadcasts.length > 0 && (
        <div className="dash-panel">
          <div className="dash-panel-title"><Eye size={12} /> Top by Views</div>
          {analytics.top_broadcasts.map((b, i) => (
            <div key={b.id} className="broadcast-row">
              <div style={{ fontSize: 14, color: 'var(--muted)', width: 20, flexShrink: 0 }}>#{i + 1}</div>
              {b.thumbnail_url
                ? <img src={b.thumbnail_url} className="broadcast-thumb-sm" alt="" />
                : <div className="broadcast-thumb-sm">{TYPE_ICON[b.content_type] || '▶'}</div>
              }
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {b.title}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--cyan)', flexShrink: 0 }}>
                <Eye size={11} /> {b.view_count.toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Top reacted */}
      {(analytics.top_reacted || []).length > 0 && (
        <div className="dash-panel">
          <div className="dash-panel-title"><Zap size={12} /> Top by Reactions</div>
          {(analytics.top_reacted || []).map((b, i) => (
            <div key={b.id} className="broadcast-row">
              <div style={{ fontSize: 14, color: 'var(--muted)', width: 20, flexShrink: 0 }}>#{i + 1}</div>
              {b.thumbnail_url
                ? <img src={b.thumbnail_url} className="broadcast-thumb-sm" alt="" />
                : <div className="broadcast-thumb-sm">{TYPE_ICON[b.content_type] || '▶'}</div>
              }
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {b.title}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--purple-bright)', flexShrink: 0 }}>
                <Zap size={11} /> {b.reaction_count}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
