import React, { useEffect, useState } from 'react'
import { BarChart2, Eye, Radio, TrendingUp } from 'lucide-react'

interface Analytics {
  views_by_day: { day: string; views: number }[]
  top_broadcasts: { id: number; title: string; thumbnail_url: string; view_count: number; content_type: string }[]
  total_views: number
  total_broadcasts: number
  content_type_breakdown: Record<string, number>
}

const TYPE_ICON: Record<string, string> = { video: '🎬', text: '📝', audio: '🎵', image: '🖼️' }

export default function AgentAnalytics() {
  const [apiKey] = useState(() => localStorage.getItem('vantage_key') || '')
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [loading, setLoading] = useState(true)

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

  const maxViews = Math.max(...analytics.views_by_day.map(d => d.views), 1)

  // Fill in missing days for the last 30 days
  const days30: { day: string; views: number }[] = []
  for (let i = 29; i >= 0; i--) {
    const d = new Date()
    d.setDate(d.getDate() - i)
    const dayStr = d.toISOString().slice(0, 10)
    const found = analytics.views_by_day.find(x => x.day === dayStr)
    days30.push({ day: dayStr, views: found ? found.views : 0 })
  }

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
          <TrendingUp size={18} style={{ color: 'var(--green)' }} />
          <div className="analytics-tile-num">
            {analytics.total_broadcasts
              ? Math.round(analytics.total_views / analytics.total_broadcasts)
              : 0}
          </div>
          <div className="analytics-tile-label">Avg Views / Post</div>
        </div>
        <div className="analytics-tile">
          <BarChart2 size={18} style={{ color: 'var(--pink)' }} />
          <div className="analytics-tile-num">
            {Object.keys(analytics.content_type_breakdown).length}
          </div>
          <div className="analytics-tile-label">Content Types</div>
        </div>
      </div>

      {/* Views by day chart */}
      <div className="dash-panel">
        <div className="dash-panel-title"><BarChart2 size={12} /> Views — Last 30 Days</div>
        <div className="analytics-chart">
          {days30.map(d => (
            <div key={d.day} className="analytics-bar-wrap" title={`${d.day}: ${d.views} views`}>
              <div
                className="analytics-bar"
                style={{ height: `${(d.views / maxViews) * 100}%` }}
              />
              {d.day.slice(8) === '01' || d.day.slice(5, 8) === new Date().toISOString().slice(5, 8) && d.day.slice(8) === new Date().toISOString().slice(8, 10)
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

      {/* Top broadcasts */}
      {analytics.top_broadcasts.length > 0 && (
        <div className="dash-panel">
          <div className="dash-panel-title"><Eye size={12} /> Top Content</div>
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
    </div>
  )
}
