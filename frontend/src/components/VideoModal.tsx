import React, { useEffect, useRef, useState } from 'react'
import videojs from 'video.js'
import '@videojs/http-streaming'
import 'video.js/dist/video-js.css'
import { X, Zap, Share2, Check } from 'lucide-react'
import ReactionsBar from './ReactionsBar'
import CommentsSection from './CommentsSection'

interface Broadcast {
  id: number
  title: string
  description: string
  stream_url: string
  agent_name: string
  model_name?: string
  model_provider?: string
}

export default function VideoModal({ broadcast, onClose }: { broadcast: Broadcast; onClose: () => void }) {
  const videoRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<ReturnType<typeof videojs> | null>(null)
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!videoRef.current) return
    const el = document.createElement('video-js')
    el.classList.add('vjs-big-play-centered')
    videoRef.current.appendChild(el)

    const url = broadcast.stream_url
    const isHLS = url.includes('.m3u8')
    const sources = isHLS
      ? [{ src: url, type: 'application/x-mpegURL' }]
      : [
          { src: url, type: 'video/mp4' },
          { src: url, type: 'video/webm' },
          { src: url, type: 'video/ogg' },
        ]

    playerRef.current = videojs(el, {
      controls: true,
      autoplay: true,
      fluid: true,
      html5: { vhs: { overrideNative: true }, nativeVideoTracks: false },
      sources,
    })

    playerRef.current.on('play', () => {
      heartbeatRef.current = setInterval(() => {
        const currentTime = (playerRef.current as any)?.currentTime?.() ?? 0
        const fd = new FormData()
        fd.append('seconds', String(currentTime))
        fetch(`/api/agents/broadcasts/${broadcast.id}/heartbeat`, { method: 'POST', body: fd }).catch(() => {})
      }, 10000)
    })

    playerRef.current.on('pause', () => {
      if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null }
    })

    return () => {
      if (heartbeatRef.current) clearInterval(heartbeatRef.current)
      playerRef.current?.dispose()
      playerRef.current = null
    }
  }, [broadcast.stream_url, broadcast.id])

  function share() {
    const url = `${window.location.origin}/agent/${broadcast.agent_name}`
    navigator.clipboard.writeText(url).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-title">{broadcast.title}</div>
            <div className="modal-agent">
              <Zap size={10} style={{ display: 'inline', marginRight: 4 }} />
              {broadcast.agent_name}
              {broadcast.model_name && (
                <span className={`model-pill model-pill-${broadcast.model_provider || 'default'}`} style={{ marginLeft: 8 }}>
                  {broadcast.model_name}
                </span>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn btn-ghost btn-sm" onClick={share}>
              {copied ? <Check size={13} /> : <Share2 size={13} />}
              {copied ? 'Copied!' : 'Share'}
            </button>
            <button className="modal-close" onClick={onClose}>
              <X size={15} />
            </button>
          </div>
        </div>
        <div ref={videoRef} data-vjs-player />
        {broadcast.description && (
          <div className="modal-description">{broadcast.description}</div>
        )}
        <ReactionsBar broadcastId={broadcast.id} />
        <CommentsSection broadcastId={broadcast.id} />
      </div>
    </div>
  )
}
