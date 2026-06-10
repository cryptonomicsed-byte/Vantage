import React, { useRef, useState } from 'react'
import { Play, Pause, Eye, AlertCircle } from 'lucide-react'

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
  const [progress, setProgress] = useState(0)
  const [duration, setDuration] = useState(0)
  const [error, setError] = useState('')
  const date = new Date(b.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  function togglePlay() {
    if (!audioRef.current) return
    if (error) return
    if (playing) {
      audioRef.current.pause()
    } else {
      const p = audioRef.current.play()
      if (p) p.catch(() => setError('Playback failed — format may not be supported by this browser'))
    }
    setPlaying(!playing)
  }

  function onTimeUpdate() {
    const el = audioRef.current
    if (!el || !el.duration) return
    setProgress((el.currentTime / el.duration) * 100)
  }

  function onLoadedMetadata() {
    if (audioRef.current) setDuration(audioRef.current.duration)
  }

  function onError() {
    setError('Audio format not supported or file unavailable')
    setPlaying(false)
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const el = audioRef.current
    if (!el || !el.duration) return
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const pct = (e.clientX - rect.left) / rect.width
    el.currentTime = pct * el.duration
    setProgress(pct * 100)
  }

  function fmt(s: number) {
    if (!s || !isFinite(s)) return '0:00'
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60).toString().padStart(2, '0')
    return `${m}:${sec}`
  }

  return (
    <div className="broadcast-card audio-card">
      <div className="audio-card-header">
        <div className="audio-icon">🎵</div>
        <button className="audio-play-btn" onClick={togglePlay} disabled={!!error}>
          {playing ? <Pause size={20} /> : <Play size={20} fill="white" color="white" />}
        </button>
      </div>
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        <audio
          ref={audioRef}
          src={b.stream_url}
          preload="metadata"
          onEnded={() => { setPlaying(false); setProgress(0) }}
          onTimeUpdate={onTimeUpdate}
          onLoadedMetadata={onLoadedMetadata}
          onError={onError}
        />
        {error ? (
          <div className="audio-error">
            <AlertCircle size={12} style={{ display: 'inline', marginRight: 4 }} />
            {error}
          </div>
        ) : (
          <>
            <div className="audio-progress-track" onClick={seek} title="Seek">
              <div className="audio-progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="audio-time">{fmt((progress / 100) * duration)} / {fmt(duration)}</div>
          </>
        )}
        <div className="audio-waveform">
          {Array.from({ length: 24 }).map((_, i) => (
            <div key={i} className={`wave-bar${playing ? ' playing' : ''}`}
              style={{ height: `${20 + Math.sin(i * 0.8) * 14}px`, animationDelay: `${i * 0.05}s` }}
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
