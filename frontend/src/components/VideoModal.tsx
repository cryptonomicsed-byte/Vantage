import React, { useEffect, useRef } from 'react'
import videojs from 'video.js'
import 'video.js/dist/video-js.css'

interface Broadcast {
  id: number
  title: string
  stream_url: string
  agent_name: string
}

export default function VideoModal({ broadcast, onClose }: { broadcast: Broadcast; onClose: () => void }) {
  const videoRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<ReturnType<typeof videojs> | null>(null)

  useEffect(() => {
    if (!videoRef.current) return
    const videoEl = document.createElement('video-js')
    videoEl.classList.add('vjs-big-play-centered')
    videoRef.current.appendChild(videoEl)

    playerRef.current = videojs(videoEl, {
      controls: true,
      autoplay: true,
      fluid: true,
      sources: [{ src: broadcast.stream_url, type: 'application/x-mpegURL' }],
    })

    return () => {
      if (playerRef.current) {
        playerRef.current.dispose()
        playerRef.current = null
      }
    }
  }, [broadcast.stream_url])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div style={{ fontWeight: 700 }}>{broadcast.title}</div>
            <div style={{ fontSize: 13, color: 'var(--muted)' }}>{broadcast.agent_name}</div>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div ref={videoRef} data-vjs-player />
      </div>
    </div>
  )
}
