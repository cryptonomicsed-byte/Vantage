import React, { useEffect, useState } from 'react'
import { BarChart3, TrendingUp, Users, Play, Zap, SkipBack, Repeat, Eye, Music } from 'lucide-react'

interface CinemaStats {
  broadcast_id: number
  total_views: number
  avg_completion_pct: number
  avg_watch_duration_sec: number
  total_seeks: number
  device_breakdown: Record<string, number>
  completion_distribution: Record<string, number>
}

interface AudioStats {
  track_id: string
  total_plays: number
  avg_completion_pct: number
  skip_rate_pct: number
  total_skips: number
  replay_rate: number
}

interface Broadcast {
  id: number
  title: string
  cinema_kind?: string
  view_count?: number
}

interface Track {
  id: string
  title: string
  play_count?: number
}

const KEY = () => localStorage.getItem('vantage_api_key') || ''

const STYLE = `
.analytics-dashboard {
  padding: 20px;
  color: #fff;
}

.analytics-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.analytics-header h1 {
  font-size: 28px;
  font-weight: 800;
  margin: 0;
}

.analytics-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
  border-bottom: 1px solid rgba(255,255,255,.1);
  padding-bottom: 12px;
}

.analytics-tab {
  padding: 8px 16px;
  border: none;
  background: none;
  color: rgba(255,255,255,.5);
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  border-bottom: 2px solid transparent;
  transition: all .2s;
}

.analytics-tab.active {
  color: #fff;
  border-bottom-color: #1db954;
}

.analytics-tab:hover {
  color: rgba(255,255,255,.8);
}

.analytics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.analytics-card {
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 12px;
  padding: 16px;
  transition: all .2s;
}

.analytics-card:hover {
  background: rgba(255,255,255,.06);
  border-color: rgba(255,255,255,.12);
}

.analytics-card-label {
  font-size: 12px;
  color: rgba(255,255,255,.5);
  text-transform: uppercase;
  letter-spacing: .5px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.analytics-card-value {
  font-size: 32px;
  font-weight: 700;
  margin-bottom: 4px;
}

.analytics-card-unit {
  font-size: 14px;
  color: rgba(255,255,255,.4);
}

.analytics-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.analytics-item {
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 10px;
  padding: 14px;
  cursor: pointer;
  transition: all .2s;
}

.analytics-item:hover {
  background: rgba(255,255,255,.06);
  border-color: rgba(255,255,255,.12);
  transform: translateX(2px);
}

.analytics-item-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 8px;
}

.analytics-item-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: rgba(255,255,255,.5);
}

.analytics-item-stat {
  display: flex;
  align-items: center;
  gap: 4px;
}

.analytics-empty {
  text-align: center;
  padding: 40px 20px;
  color: rgba(255,255,255,.4);
}

.analytics-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.analytics-bar-label {
  font-size: 12px;
  min-width: 80px;
  color: rgba(255,255,255,.6);
}

.analytics-bar-fill {
  flex: 1;
  height: 24px;
  background: rgba(255,255,255,.1);
  border-radius: 4px;
  overflow: hidden;
}

.analytics-bar-progress {
  height: 100%;
  background: linear-gradient(90deg, #1db954, #1ed760);
  transition: width .3s ease;
}

.analytics-bar-value {
  font-size: 12px;
  min-width: 40px;
  text-align: right;
  color: rgba(255,255,255,.7);
  font-weight: 600;
}
`

export default function AnalyticsDashboard() {
  const [activeTab, setActiveTab] = useState<'cinema' | 'audio'>('cinema')
  const [cinemaData, setCinemaData] = useState<{ [key: number]: CinemaStats }>({})
  const [audioData, setAudioData] = useState<{ [key: string]: AudioStats }>({})
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([])
  const [tracks, setTracks] = useState<Track[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!document.getElementById('analytics-styles')) {
      const el = document.createElement('style')
      el.id = 'analytics-styles'
      el.textContent = STYLE
      document.head.appendChild(el)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    // Load cinema broadcasts
    fetch('/api/cinema?limit=20', { headers: { 'X-Agent-Key': KEY() } })
      .then(r => r.json())
      .then(d => {
        const broads = d.broadcasts || []
        setBroadcasts(broads)
        // Load stats for each broadcast
        return Promise.all(
          broads.slice(0, 5).map((b: Broadcast) =>
            fetch(`/api/cinema/${b.id}/stats`, { headers: { 'X-Agent-Key': KEY() } })
              .then(r => r.json())
              .then(stats => ({ [b.id]: stats }))
              .catch(() => ({}))
          )
        )
      })
      .then(results => {
        const merged = Object.assign({}, ...results)
        setCinemaData(merged)
        setLoading(false)
      })
      .catch(() => setLoading(false))

    // Load audio tracks
    fetch('/api/audio/tracks?limit=20', { headers: { 'X-Agent-Key': KEY() } })
      .then(r => r.json())
      .then(d => {
        const tracksList = Array.isArray(d) ? d : []
        setTracks(tracksList)
        // Load stats for each track
        return Promise.all(
          tracksList.slice(0, 5).map((t: Track) =>
            fetch(`/api/audio/${t.id}/stats`, { headers: { 'X-Agent-Key': KEY() } })
              .then(r => r.json())
              .then(stats => ({ [t.id]: stats }))
              .catch(() => ({}))
          )
        )
      })
      .then(results => {
        const merged = Object.assign({}, ...results)
        setAudioData(merged)
      })
      .catch(() => {})
  }, [])

  const cinemaStats = Object.values(cinemaData)
  const audioStats = Object.values(audioData)

  const totalCinemaViews = cinemaStats.reduce((sum, s) => sum + s.total_views, 0)
  const avgCinemaCompletion = cinemaStats.length
    ? (cinemaStats.reduce((sum, s) => sum + s.avg_completion_pct, 0) / cinemaStats.length).toFixed(1)
    : 0

  const totalAudioPlays = audioStats.reduce((sum, s) => sum + s.total_plays, 0)
  const avgAudioCompletion = audioStats.length
    ? (audioStats.reduce((sum, s) => sum + s.avg_completion_pct, 0) / audioStats.length).toFixed(1)
    : 0
  const avgSkipRate = audioStats.length
    ? (audioStats.reduce((sum, s) => sum + s.skip_rate_pct, 0) / audioStats.length).toFixed(1)
    : 0

  return (
    <div className="analytics-dashboard">
      <style>{STYLE}</style>

      <div className="analytics-header">
        <BarChart3 size={28} color="#1db954" />
        <h1>Creator Analytics</h1>
      </div>

      <div className="analytics-tabs">
        <button
          className={`analytics-tab ${activeTab === 'cinema' ? 'active' : ''}`}
          onClick={() => setActiveTab('cinema')}
        >
          <Eye size={14} style={{ marginRight: 6 }} />
          Cinema (Video)
        </button>
        <button
          className={`analytics-tab ${activeTab === 'audio' ? 'active' : ''}`}
          onClick={() => setActiveTab('audio')}
        >
          <Music size={14} style={{ marginRight: 6 }} />
          Audio (Music)
        </button>
      </div>

      {activeTab === 'cinema' && (
        <>
          <div className="analytics-grid">
            <div className="analytics-card">
              <div className="analytics-card-label"><Eye size={14} /> Total Views</div>
              <div className="analytics-card-value">{totalCinemaViews}</div>
              <div className="analytics-card-unit">across {cinemaStats.length} videos</div>
            </div>
            <div className="analytics-card">
              <div className="analytics-card-label"><TrendingUp size={14} /> Avg Completion</div>
              <div className="analytics-card-value">{avgCinemaCompletion}%</div>
              <div className="analytics-card-unit">watch completion rate</div>
            </div>
            <div className="analytics-card">
              <div className="analytics-card-label"><Users size={14} /> Avg Duration</div>
              <div className="analytics-card-value">{cinemaStats.length ? Math.round(cinemaStats.reduce((sum, s) => sum + s.avg_watch_duration_sec, 0) / cinemaStats.length) : 0}</div>
              <div className="analytics-card-unit">seconds watched</div>
            </div>
            <div className="analytics-card">
              <div className="analytics-card-label"><Zap size={14} /> Total Seeks</div>
              <div className="analytics-card-value">{cinemaStats.reduce((sum, s) => sum + s.total_seeks, 0)}</div>
              <div className="analytics-card-unit">interaction events</div>
            </div>
          </div>

          {cinemaStats.length > 0 && (
            <>
              <h3 style={{ marginBottom: 12, fontSize: 16, fontWeight: 700 }}>Device Breakdown</h3>
              <div style={{ marginBottom: 24, background: 'rgba(255,255,255,.02)', padding: 16, borderRadius: 10 }}>
                {Object.entries(cinemaStats[0]?.device_breakdown || {}).map(([device, count]) => (
                  <div key={device} className="analytics-bar">
                    <div className="analytics-bar-label">{device === 'web' ? '🌐 Web' : '📱 Mobile'}</div>
                    <div className="analytics-bar-fill">
                      <div
                        className="analytics-bar-progress"
                        style={{
                          width: `${
                            (count as number) /
                            Math.max(
                              ...Object.values(cinemaStats[0]?.device_breakdown || {}).filter((v) => typeof v === 'number')
                            ) *
                            100
                          }%`
                        }}
                      />
                    </div>
                    <div className="analytics-bar-value">{count}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          <h3 style={{ marginBottom: 12, fontSize: 16, fontWeight: 700 }}>Videos</h3>
          {broadcasts.length === 0 ? (
            <div className="analytics-empty">No videos published yet</div>
          ) : (
            <div className="analytics-list">
              {broadcasts.slice(0, 10).map(b => {
                const stats = cinemaData[b.id]
                return (
                  <div key={b.id} className="analytics-item">
                    <div className="analytics-item-title">{b.title}</div>
                    <div className="analytics-item-meta">
                      <div className="analytics-item-stat">
                        <Eye size={12} /> {stats?.total_views || b.view_count || 0} views
                      </div>
                      <div className="analytics-item-stat">
                        <TrendingUp size={12} /> {stats?.avg_completion_pct?.toFixed(0) || 0}% completion
                      </div>
                      <div className="analytics-item-stat">
                        <Zap size={12} /> {stats?.total_seeks || 0} seeks
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {activeTab === 'audio' && (
        <>
          <div className="analytics-grid">
            <div className="analytics-card">
              <div className="analytics-card-label"><Play size={14} /> Total Plays</div>
              <div className="analytics-card-value">{totalAudioPlays}</div>
              <div className="analytics-card-unit">across {audioStats.length} tracks</div>
            </div>
            <div className="analytics-card">
              <div className="analytics-card-label"><TrendingUp size={14} /> Avg Completion</div>
              <div className="analytics-card-value">{avgAudioCompletion}%</div>
              <div className="analytics-card-unit">listen completion rate</div>
            </div>
            <div className="analytics-card">
              <div className="analytics-card-label"><SkipBack size={14} /> Skip Rate</div>
              <div className="analytics-card-value">{avgSkipRate}%</div>
              <div className="analytics-card-unit">of listens skipped</div>
            </div>
            <div className="analytics-card">
              <div className="analytics-card-label"><Repeat size={14} /> Avg Replays</div>
              <div className="analytics-card-value">
                {audioStats.length ? (audioStats.reduce((sum, s) => sum + s.replay_rate, 0) / audioStats.length).toFixed(2) : 0}
              </div>
              <div className="analytics-card-unit">replays per listener</div>
            </div>
          </div>

          <h3 style={{ marginBottom: 12, fontSize: 16, fontWeight: 700 }}>Tracks</h3>
          {tracks.length === 0 ? (
            <div className="analytics-empty">No tracks uploaded yet</div>
          ) : (
            <div className="analytics-list">
              {tracks.slice(0, 10).map(t => {
                const stats = audioData[t.id]
                return (
                  <div key={t.id} className="analytics-item">
                    <div className="analytics-item-title">{t.title}</div>
                    <div className="analytics-item-meta">
                      <div className="analytics-item-stat">
                        <Play size={12} /> {stats?.total_plays || t.play_count || 0} plays
                      </div>
                      <div className="analytics-item-stat">
                        <TrendingUp size={12} /> {stats?.avg_completion_pct?.toFixed(0) || 0}% completion
                      </div>
                      <div className="analytics-item-stat">
                        <SkipBack size={12} /> {stats?.skip_rate_pct?.toFixed(1) || 0}% skip rate
                      </div>
                      <div className="analytics-item-stat">
                        <Repeat size={12} /> {stats?.replay_rate?.toFixed(2) || 0}x replays
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
