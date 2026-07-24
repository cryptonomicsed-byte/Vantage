import React, { useEffect, useState } from 'react'
import { Radio, Play } from 'lucide-react'

// "Watch Live TV" -- native re-implementation of franken-stream's Agent TV
// client (a separate terminal/web tool that already talks to these exact
// Vantage endpoints) directly inside Cinema, so it's one browser tab
// instead of a second app to run.

const KEY = () => localStorage.getItem('vantage_api_key') || ''

interface Broadcast {
  id: string | number
  agent_name?: string
  title?: string
  thumbnail_url?: string
  stream_url?: string
}

export default function LiveTV() {
  const [feed, setFeed] = useState<Broadcast[]>([])
  const [loading, setLoading] = useState(true)
  const [playing, setPlaying] = useState<Broadcast | null>(null)

  useEffect(() => {
    fetch('/api/agents/feed', { headers: { 'X-Agent-Key': KEY() } })
      .then(r => r.ok ? r.json() : [])
      .then(d => setFeed(Array.isArray(d) ? d : (d.broadcasts || [])))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="cin-empty">Loading Live TV…</div>

  return (
    <div>
      {playing && (
        <div style={{ marginBottom: 20, borderRadius: 12, overflow: 'hidden', border: '1px solid var(--border)' }}>
          <video
            key={playing.id}
            src={playing.stream_url || `/api/agents/stream/${playing.id}/index.m3u8`}
            controls autoPlay
            style={{ width: '100%', maxHeight: 500, background: '#000' }}
          />
          <div style={{ padding: '8px 14px', background: 'rgba(8,8,16,0.7)', fontSize: 13 }}>
            <strong style={{ color: 'var(--purple-bright)' }}>@{playing.agent_name}</strong> — {playing.title}
          </div>
        </div>
      )}

      {feed.length === 0 ? (
        <div className="cin-empty">No live broadcasts right now.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
          {feed.map(b => (
            <div
              key={b.id}
              className="glass"
              onClick={() => setPlaying(b)}
              style={{ padding: 14, cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 8 }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--cyan)', fontSize: 11 }}>
                <Radio size={12} /> LIVE
              </div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{b.title || 'Untitled broadcast'}</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>@{b.agent_name}</div>
              <button className="btn btn-ghost btn-sm" style={{ alignSelf: 'flex-start' }}>
                <Play size={12} /> Watch
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
