import React, { useEffect, useRef, useState } from 'react'
import videojs from 'video.js'
import 'video.js/dist/video-js.css'
import { X, Zap, Share2, Check } from 'lucide-react'

interface Broadcast {
  id: number
  title: string
  description: string
  stream_url: string
  agent_name: string
}

export default function VideoModal({ broadcast, onClose }: { broadcast: Broadcast; onClose: () => void }) {
  const videoRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<ReturnType<typeof videojs> | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!videoRef.current) return
    const el = document.createElement('video-js')
    el.classList.add('vjs-big-play-centered')
    videoRef.current.appendChild(el)

    playerRef.current = videojs(el, {
      controls: true,
      autoplay: true,
      fluid: true,
      sources: [{ src: broadcast.stream_url, type: 'application/x-mpegURL' }],
    })

    return () => {
      playerRef.current?.dispose()
      playerRef.current = null
    }
  }, [broadcast.stream_url])

  function share() {
    const url = `${window.location.origin}/agent/${broadcast.agent_name}`
    navigator.clipboard.writeText(url).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-title">{broadcast.title}</div>
            <div className="modal-agent">
              <Zap size={10} style={{ display: 'inline', marginRight: 4 }} />
              {broadcast.agent_name}
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
      </div>
    </div>
  )
}
