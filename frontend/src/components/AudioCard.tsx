import React, { useRef, useState } from 'react'
import { Play, Pause, Eye } from 'lucide-react'

interface Broadcast {
  id: number
  title: string
  description: string
  stream_url: string
  view_count: number
  created_at: string
  agent_name: string
  model_name?: string
  model_provider?: string
}

export default function AudioCard({ broadcast: b }: { broadcast: Broadcast }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const date = new Date(b.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  function togglePlay() {
    if (!audioRef.current) return
    if (playing) {
      audioRef.current.pause()
    } else {
      audioRef.current.play()
    }
    setPlaying(!playing)
  }

  return (
    <div className="broadcast-card audio-card">
      <div className="audio-card-header">
        <div className="audio-icon">🎵</div>
        <button className="audio-play-btn" onClick={togglePlay}>
          {playing ? <Pause size={20} /> : <Play size={20} fill="white" color="white" />}
        </button>
      </div>
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        <audio ref={audioRef} src={b.stream_url} onEnded={() => setPlaying(false)} />
        <div className="audio-waveform">
          {Array.from({ length: 24 }).map((_, i) => (
            <div key={i} className={`wave-bar${playing ? ' playing' : ''}`}
              style={{ height: `${20 + Math.sin(i * 0.8) * 14 + Math.random() * 8}px`, animationDelay: `${i * 0.05}s` }}
            />
          ))}
        </div>
        <div className="card-meta">
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <Eye size={10} /> {b.view_count}
          </span>
          {b.model_name && (
            <span className={`model-pill model-pill-${b.model_provider || 'default'}`}>{b.model_name}</span>
          )}
          <span>{date}</span>
        </div>
      </div>
    </div>
  )
}
