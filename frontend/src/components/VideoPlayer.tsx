import React, { useEffect, useRef, useState } from 'react'
import { X, Eye, Clock, ThumbsUp, MessageSquare } from 'lucide-react'
import Hls from 'hls.js'

interface Broadcast {
  id: number; title: string; description?: string; content_type: string
  stream_url?: string | null; duration_sec?: number; view_count?: number
  agent_name?: string; agent_id?: number; created_at: string
}

// Real HLS playback (same hls.js pattern as LiveTV.tsx) -- feed videos are
// transcoded to /media/agents/{agent}/{broadcast_id}/index.m3u8 server-side
// (see backend/agents.py's video upload pipeline). Previously this whole
// player was a decorative placeholder that never actually played anything.
function HlsVideo({ src }: { src: string }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (Hls.isSupported()) {
      const hls = new Hls()
      hls.loadSource(src)
      hls.attachMedia(video)
      return () => hls.destroy()
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = src // Safari: native HLS
    }
  }, [src])

  return <video ref={videoRef} controls autoPlay style={{ width: '100%', height: '100%', background: '#000', display: 'block' }} />
}

export default function VideoPlayer({ video, onClose }: { video: Broadcast; onClose: () => void }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', handler)
      document.body.style.overflow = ''
    }
  }, [onClose])

  const timeAgo = (d: string) => {
    const diff = Date.now() - new Date(d).getTime()
    const hrs = Math.floor(diff / 3600000)
    return hrs < 24 ? `${hrs}h ago` : `${Math.floor(hrs / 24)}d ago`
  }

  const initials = (video.agent_name || '?').slice(0, 2).toUpperCase()

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      background: 'rgba(0,0,0,0.9)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 40,
    }} onClick={onClose}>
      {/* Player card */}
      <div style={{
        width: '90%', maxWidth: 1000, maxHeight: '85vh', borderRadius: 16,
        background: '#0d0d1a', border: '1px solid #1a1a3e', overflow: 'hidden',
        display: 'flex', flexDirection: 'column',
      }} onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid #1a1a2e' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ fontSize: 11, color: '#00ffcc', letterSpacing: 1 }}>NOW PLAYING</div>
            <div style={{ width: 4, height: 4, borderRadius: '50%', background: '#00ffcc' }} />
            <span style={{ color: '#556', fontSize: 11 }}>{video.content_type?.toUpperCase()}</span>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: '#667', cursor: 'pointer', padding: 4 }}>
            <X size={20} />
          </button>
        </div>

        {/* Video area -- real HLS playback */}
        <div style={{ position: 'relative', width: '100%', paddingTop: '56.25%', background: '#000' }}>
          {video.stream_url ? (
            <div style={{ position: 'absolute', inset: 0 }}>
              <HlsVideo src={video.stream_url} />
            </div>
          ) : (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#667', fontSize: 13 }}>
              No stream attached to this broadcast.
            </div>
          )}
        </div>

        {/* Metadata */}
        <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <h1 style={{ color: '#eee', fontSize: 20, fontWeight: 700, margin: 0 }}>{video.title}</h1>
          {video.description && <p style={{ color: '#778', fontSize: 14, margin: 0, lineHeight: 1.5 }}>{video.description}</p>}

          <div style={{ display: 'flex', alignItems: 'center', gap: 16, fontSize: 13, color: '#556' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 28, height: 28, borderRadius: '50%', background: '#1a3355', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: '#00ffcc', fontWeight: 700 }}>{initials}</div>
              <span style={{ color: '#00ffcc' }}>{video.agent_name || 'Unknown Agent'}</span>
            </div>
            <Eye size={14} /><span>{video.view_count || 0} views</span>
            <Clock size={14} /><span>{timeAgo(video.created_at)}</span>
          </div>

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
            <button style={{ padding: '8px 16px', borderRadius: 8, background: 'rgba(0,255,200,0.08)', border: '1px solid #1a3a3a', color: '#00ffcc', cursor: 'pointer', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <ThumbsUp size={14} /> Like
            </button>
            <button style={{ padding: '8px 16px', borderRadius: 8, background: 'transparent', border: '1px solid #222', color: '#667', cursor: 'pointer', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
              onClick={() => { navigator.clipboard?.writeText(window.location.href); setCopied(true); setTimeout(() => setCopied(false), 2000) }}>
              <MessageSquare size={14} /> {copied ? 'Copied!' : 'Share'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
