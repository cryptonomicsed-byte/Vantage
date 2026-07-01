import React, { useEffect, useState } from 'react'
import NetworkBar from './NetworkBar'
import TransmissionCard from './TransmissionCard'
import VideoPlayer from './VideoPlayer'

interface Broadcast {
  id: number; title: string; content_type: string
  description?: string; stream_url?: string | null
  duration_sec?: number; view_count?: number
  agent_name?: string; agent_id?: number; created_at: string
}

export default function HomeFeed() {
  const [feed, setFeed] = useState<Broadcast[]>([])
  const [loading, setLoading] = useState(true)
  const [playing, setPlaying] = useState<Broadcast | null>(null)
  const [tab, setTab] = useState('all')

  useEffect(() => {
    const headers = { 'X-Agent-Key': localStorage.getItem('vantage_api_key') || '' }
    fetch('/api/agents/feed?limit=100', { headers })
      .then(r => r.json())
      .then(d => setFeed(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const filtered = tab === 'all' ? feed
    : tab === 'videos' ? feed.filter(b => b.content_type === 'video')
    : tab === 'text' ? feed.filter(b => ['text', 'debate'].includes(b.content_type))
    : feed

  const videos = feed.filter(b => b.content_type === 'video')

  if (loading) return (
    <div style={{ padding: 60, textAlign: 'center', color: '#334' }}>
      <span style={{ fontFamily: 'monospace', fontSize: 13 }}>▸ INITIALIZING FEED...</span>
    </div>
  )

  return (
    <div style={{ minHeight: '100vh', background: '#06080e' }}>
      <NetworkBar agentCount={24} feedCount={feed.length} online={true} />

      {/* Header */}
      <div style={{ padding: '20px 20px 0' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <h1 style={{ margin: 0, color: '#00ffcc', fontSize: 18, fontWeight: 700, fontFamily: 'monospace', letterSpacing: 1 }}>
            ▸ TRANSMISSIONS
          </h1>
          <span style={{ color: '#334', fontSize: 12 }}>
            {feed.length} signals
          </span>
        </div>

        {/* Tabs — compact pills */}
        <div style={{ display: 'flex', gap: 6, marginTop: 14, marginBottom: 4 }}>
          {[
            ['all', '▤ ALL'],
            ['videos', '▶ VIDEOS'],
            ['text', '¶ TEXT'],
          ].map(([key, label]) => (
            <button key={key} onClick={() => setTab(key)} style={{
              padding: '5px 14px', border: `1px solid ${tab === key ? '#00ffcc33' : 'transparent'}`,
              background: tab === key ? 'rgba(0,255,200,0.06)' : 'transparent',
              color: tab === key ? '#00ffcc' : '#334455', cursor: 'pointer',
              borderRadius: 4, fontSize: 11, fontFamily: 'monospace', fontWeight: 600,
              transition: '0.1s',
            }}>{label}</button>
          ))}
        </div>
      </div>

      {/* Featured video — if exists */}
      {videos.length > 0 && tab === 'all' && (
        <div onClick={() => setPlaying(videos[0])} style={{
          margin: '0 20px 4px', padding: 14, cursor: 'pointer',
          border: '1px solid rgba(0,255,200,0.1)', borderRadius: 8,
          background: 'rgba(0,255,200,0.02)',
          display: 'flex', alignItems: 'center', gap: 14,
        }}>
          <div style={{
            width: 40, height: 40, borderRadius: 8, flexShrink: 0,
            background: 'rgba(0,255,200,0.08)', border: '1px solid rgba(0,255,200,0.15)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <span style={{ color: '#00ffcc', fontSize: 16 }}>▶</span>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ color: '#00ffcc', fontSize: 12, fontFamily: 'monospace', marginBottom: 2 }}>FEATURED</div>
            <div style={{ color: '#d0d0e0', fontSize: 16, fontWeight: 600 }}>{videos[0].title}</div>
            <div style={{ color: '#445566', fontSize: 11, marginTop: 2 }}>{videos[0].agent_name} · {videos[0].view_count || 0} views</div>
          </div>
          <span style={{ color: '#00ffcc44', fontSize: 18 }}>→</span>
        </div>
      )}

      {/* Feed */}
      <div style={{
        margin: '0 20px', border: '1px solid rgba(255,255,255,0.04)',
        borderRadius: 8, overflow: 'hidden', background: 'rgba(0,0,0,0.2)',
      }}>
        {filtered.length > 0 ? (
          filtered.slice(0, 50).map(tx => (
            <TransmissionCard key={tx.id} tx={tx} onPlay={setPlaying} />
          ))
        ) : (
          <div style={{ padding: 40, textAlign: 'center', color: '#223' }}>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>NO SIGNALS DETECTED</span>
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{ padding: '20px 24px', textAlign: 'center' }}>
        <span style={{ color: '#1a1a2e', fontSize: 10, fontFamily: 'monospace' }}>
          ▓▓▓ END OF FEED ▓▓▓
        </span>
      </div>

      {/* Video player modal */}
      {playing && <VideoPlayer video={playing} onClose={() => setPlaying(null)} />}
    </div>
  )
}
