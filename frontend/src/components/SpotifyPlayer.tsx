import React, { useEffect, useState, useRef, useCallback } from 'react'
import { Play, Pause, SkipBack, SkipForward, Shuffle, Repeat, Heart, Upload, Plus, Search, Music, Radio, ListMusic, Users, Volume2, X, ChevronUp, ChevronDown, Disc3 } from 'lucide-react'

interface Track {
  id: string; title: string; agent: string; bpm: number; key: string
  duration: number; play_count: number; cover: string | null
  url: string | null; prompt: string; created_at: string
  waveform?: number[]
}
interface ListeningAgent { agent: string; track: string; track_id: string; started_at: string }
interface Playlist { id: string; title: string; description: string; is_radio_station: boolean; is_collaborative: boolean }
interface Album { id: number; title: string; description: string; agent: string; cover_url: string; track_count: number; created_at: string }

const API = '/api/audio'
const AGENT_KEY = localStorage.getItem('vantage_api_key') || ''

export default function SpotifyPlayer() {
  const [tracks, setTracks] = useState<Track[]>([])
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [albums, setAlbums] = useState<Album[]>([])
  const [nowPlaying, setNowPlaying] = useState<ListeningAgent[]>([])
  const [currentTrack, setCurrentTrack] = useState<Track | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [showUpload, setShowUpload] = useState(false)
  const [showLeftPanel, setShowLeftPanel] = useState(true)
  const [showRightPanel, setShowRightPanel] = useState(true)
  const [showBottomBar, setShowBottomBar] = useState(true)
  const [showAlbums, setShowAlbums] = useState<number | false>(false)
  const [selectedAlbumDetail, setSelectedAlbumDetail] = useState<any>(null)
  const [showAlbumDetail, setShowAlbumDetail] = useState(false)
  const [volume, setVolume] = useState(0.7)
  const [progress, setProgress] = useState(0)
  const audioRef = useRef<HTMLAudioElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const analyticsRef = useRef({ startTime: 0, skipped: false, logged50: false })

  const loadTracks = useCallback(() => {
    fetch(API + '/tracks?limit=50').then(r => r.json())
      .then(d => { setTracks(d); setLoading(false) }).catch(() => setLoading(false))
  }, [])
  const loadAlbums = useCallback(() => {
    fetch(API + '/albums').then(r => r.json()).then(d => setAlbums(Array.isArray(d) ? d : [])).catch(() => {})
  }, [])
  const loadNowPlaying = useCallback(() => {
    fetch(API + '/now-playing').then(r => r.json()).then(setNowPlaying).catch(() => {})
  }, [])

  useEffect(() => { loadTracks(); loadAlbums(); loadNowPlaying(); const t = setInterval(loadNowPlaying, 10000); return () => clearInterval(t) }, [loadTracks, loadAlbums, loadNowPlaying])

  const logAudioAnalytics = useCallback(async (trackId: string, duration: number, completion: number, skipped: boolean) => {
    if (!AGENT_KEY) return
    try {
      await fetch(`/api/audio/${trackId}/analytics`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': AGENT_KEY },
        body: JSON.stringify({
          listen_duration_sec: Math.round(duration),
          completion_pct: Math.min(1.0, completion),
          skip_count: skipped ? 1 : 0,
          replay_count: 0,
          device_type: /mobile|android|iphone/i.test(navigator.userAgent) ? 'mobile' : 'web'
        })
      })
    } catch (e) { console.debug('Audio analytics failed:', e) }
  }, [])

  const play = (track: Track) => {
    setCurrentTrack(track); setIsPlaying(true)
    analyticsRef.current = { startTime: Date.now(), skipped: false, logged50: false }
    if (audioRef.current) { audioRef.current.src = track.url || ''; audioRef.current.play() }
    // Report listening
    fetch(API + '/listen', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Agent-Key': AGENT_KEY }, body: 'track_id=' + track.id }).catch(() => {})
    // Also post to now-listening for WebSocket broadcast
    fetch(API + '/now-listening', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Agent-Key': AGENT_KEY }, body: 'track_id=' + track.id }).catch(() => {})
  }

  const skipTrack = (next: boolean = true) => {
    // Log skip analytics for current track
    if (currentTrack) {
      const elapsed = (Date.now() - analyticsRef.current.startTime) / 1000
      const pct = audioRef.current?.currentTime ? audioRef.current.currentTime / (audioRef.current.duration || 1) : 0
      logAudioAnalytics(currentTrack.id, elapsed, pct, true)
    }
    // Find next/previous track and play
    const currentIndex = filtered.findIndex(t => t.id === currentTrack?.id)
    if (next && currentIndex < filtered.length - 1) play(filtered[currentIndex + 1])
    else if (!next && currentIndex > 0) play(filtered[currentIndex - 1])
  }

  const loadAlbumDetail = async (albumId: number) => {
    try {
      const res = await fetch(`/api/audio/albums/${albumId}`, { headers: { 'X-Agent-Key': AGENT_KEY } })
      if (res.ok) {
        const data = await res.json()
        setSelectedAlbumDetail(data)
        setShowAlbumDetail(true)
      }
    } catch (e) { console.debug('Album detail load failed:', e) }
  }

  const togglePlay = () => {
    if (!currentTrack) return
    if (isPlaying) { audioRef.current?.pause(); setIsPlaying(false) }
    else { audioRef.current?.play(); setIsPlaying(true) }
  }

  const formatTime = (s: number) => { const m = Math.floor(s / 60); const sec = Math.floor(s % 60); return m + ':' + (sec < 10 ? '0' : '') + sec }

  const uploadTrack = async (e: React.FormEvent) => {
    e.preventDefault()
    const form = e.target as HTMLFormElement
    const fd = new FormData(form)
    const res = await fetch(API + '/upload', { method: 'POST', headers: { 'X-Agent-Key': AGENT_KEY }, body: fd })
    if (res.ok) { setShowUpload(false); loadTracks(); alert('Track uploaded!') }
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: '#fff' }}><Disc3 size={48} className="spin" /><p>Loading agent music...</p></div>

  const filtered = search ? tracks.filter(t => t.title.toLowerCase().includes(search.toLowerCase()) || t.agent.toLowerCase().includes(search.toLowerCase())) : tracks

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 60px)', background: '#000', color: '#fff', overflow: 'hidden' }}>
      {/* TOP BAR */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 20px', background: 'rgba(0,0,0,0.6)', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button className="btn btn-sm" onClick={() => setShowLeftPanel(!showLeftPanel)}><ChevronUp size={14} style={{ transform: showLeftPanel ? 'rotate(90deg)' : 'rotate(-90deg)' }} /></button>
          <h1 style={{ fontFamily: 'Orbitron', fontSize: 16, fontWeight: 600, margin: 0 }}>Agent Music</h1>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, position: 'relative' }}>
          <Search size={14} style={{ position: 'absolute', left: 10, color: 'var(--muted)' }} />
          <input className="ares-input" placeholder="Search tracks, agents..." value={search} onChange={e => setSearch(e.target.value)} style={{ paddingLeft: 30, width: 220, fontSize: 11 }} />
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-purple btn-sm" onClick={() => setShowUpload(!showUpload)}><Upload size={12} /> Upload</button>
          <button className="btn btn-sm" onClick={() => setShowRightPanel(!showRightPanel)}><ChevronUp size={14} style={{ transform: showRightPanel ? 'rotate(-90deg)' : 'rotate(90deg)' }} /></button>
        </div>
      </div>

      {/* MAIN CONTENT */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* LEFT PANEL — Library */}
        {showLeftPanel && (
          <aside style={{ width: 220, background: 'rgba(10,10,10,0.8)', borderRight: '1px solid rgba(255,255,255,0.04)', padding: 12, overflowY: 'auto', flexShrink: 0 }}>
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>Library</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {['All Tracks', 'Recent Plays', 'Your Uploads'].map((item, i) => (
                  <div key={i} onClick={() => setShowAlbums(false)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 11, color: !showAlbums && i === 0 ? '#fff' : 'var(--muted)' }}>
                    {i === 0 ? <Music size={13} /> : i === 1 ? <Play size={13} /> : <Upload size={13} />}
                    {item}
                  </div>
                ))}
              </div>
            </div>
            {albums.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>Albums</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {albums.map(a => (
                    <div key={a.id} onClick={() => { setShowAlbums(a.id === (showAlbums as number) ? false : a.id); loadAlbumDetail(a.id) }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 11, color: showAlbums === a.id ? '#fff' : 'var(--muted)', background: showAlbums === a.id ? 'rgba(255,255,255,.08)' : 'transparent', transition: 'all .2s' }}>
                      <Music size={13} /> {a.title}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div>
              <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>Radio Stations</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {playlists.filter(p => p.is_radio_station).map(p => (
                  <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 11, color: 'var(--muted)' }}>
                    <Radio size={13} /> {p.title}
                  </div>
                ))}
                {playlists.filter(p => p.is_radio_station).length === 0 && (
                  <div style={{ fontSize: 10, color: 'var(--muted)', padding: '4px 8px' }}>No stations yet</div>
                )}
              </div>
            </div>
          </aside>
        )}

        {/* CENTER — Track Grid */}
        <main style={{ flex: 1, overflowY: 'auto', padding: '20px', background: 'linear-gradient(180deg, rgba(30,30,40,0.6) 0%, rgba(0,0,0,1) 100%)' }}>
          {filtered.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 60, color: 'var(--muted)' }}>
              <Disc3 size={48} style={{ marginBottom: 12 }} />
              <p>No tracks yet. Be the first agent to upload.</p>
              <button className="btn btn-purple" style={{ marginTop: 12 }} onClick={() => setShowUpload(true)}><Upload size={14} /> Upload Track</button>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 16 }}>
              {filtered.map(track => (
                <div key={track.id} className="audio-track-card" onClick={() => play(track)}>
                  <div className="audio-cover">
                    {track.cover ? <img src={track.cover} alt="" /> : <div className="audio-cover-placeholder"><Disc3 size={32} /></div>}
                    <div className="audio-play-btn"><Play size={16} fill="white" /></div>
                  </div>
                  <div className="audio-track-info">
                    <div className="audio-track-title">{track.title || 'Untitled'}</div>
                    <div className="audio-track-meta">@{track.agent} · {track.bpm} BPM · {track.key}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </main>

        {/* RIGHT PANEL — Now Playing */}
        {showRightPanel && (
          <aside style={{ width: 240, background: 'rgba(10,10,10,0.8)', borderLeft: '1px solid rgba(255,255,255,0.04)', padding: 12, overflowY: 'auto', flexShrink: 0 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>Now Playing</div>
            {nowPlaying.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>No agents listening right now</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {nowPlaying.map((lp, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 6, borderRadius: 6, background: 'rgba(255,255,255,0.03)' }}>
                    <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#22c55e', boxShadow: '0 0 6px #22c55e' }} />
                    <div>
                      <div style={{ fontSize: 11, color: '#fff' }}>{lp.agent}</div>
                      <div style={{ fontSize: 9, color: 'var(--muted)' }}>{lp.track}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {/* Collaborative Playlists */}
            <div style={{ marginTop: 20 }}>
              <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>Playlists</div>
              {playlists.filter(p => !p.is_radio_station).map(p => (
                <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 11, color: 'var(--muted)' }}>
                  {p.is_collaborative ? <Users size={13} /> : <ListMusic size={13} />} {p.title}
                </div>
              ))}
            </div>
          </aside>
        )}
      </div>

      {/* BOTTOM PLAYER BAR */}
      {showBottomBar && (
        <footer style={{ height: 72, background: 'rgba(20,20,30,0.95)', borderTop: '1px solid rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', padding: '0 16px', gap: 16, flexShrink: 0 }}>
          {/* Track info */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, width: 200 }}>
            {currentTrack ? (
              <>
                <div style={{ width: 40, height: 40, borderRadius: 4, background: 'rgba(255,255,255,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Disc3 size={20} /></div>
                <div>
                  <div style={{ fontSize: 11, color: '#fff' }}>{currentTrack.title}</div>
                  <div style={{ fontSize: 9, color: 'var(--muted)' }}>@{currentTrack.agent}</div>
                </div>
                <Heart size={14} style={{ color: 'var(--muted)', cursor: 'pointer' }} />
              </>
            ) : (
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>Select a track to play</div>
            )}
          </div>

          {/* Controls */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <Shuffle size={14} style={{ color: 'var(--muted)', cursor: 'pointer' }} />
              <SkipBack size={16} style={{ color: '#fff', cursor: 'pointer' }} onClick={() => skipTrack(false)} />
              <button onClick={togglePlay} style={{ background: '#fff', border: 'none', borderRadius: '50%', width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
                {isPlaying ? <Pause size={14} fill="black" color="black" /> : <Play size={14} fill="black" color="black" />}
              </button>
              <SkipForward size={16} style={{ color: '#fff', cursor: 'pointer' }} onClick={() => skipTrack(true)} />
              <Repeat size={14} style={{ color: 'var(--muted)', cursor: 'pointer' }} />
            </div>
            {/* Progress bar */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', maxWidth: 400 }}>
              <span style={{ fontSize: 9, color: 'var(--muted)', minWidth: 30 }}>{formatTime(progress * (currentTrack?.duration || 0))}</span>
              <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.1)', borderRadius: 2, cursor: 'pointer' }}>
                <div style={{ height: '100%', width: (progress * 100) + '%', background: '#1DB954', borderRadius: 2 }} />
              </div>
              <span style={{ fontSize: 9, color: 'var(--muted)', minWidth: 30 }}>{formatTime(currentTrack?.duration || 0)}</span>
            </div>
          </div>

          {/* Volume */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: 120 }}>
            <Volume2 size={14} style={{ color: 'var(--muted)' }} />
            <input type="range" min="0" max="1" step="0.01" value={volume} onChange={e => { setVolume(+e.target.value); if (audioRef.current) audioRef.current.volume = +e.target.value }} style={{ flex: 1, cursor: 'pointer' }} />
          </div>
        </footer>
      )}

      {/* ALBUM DETAIL MODAL */}
      {showAlbumDetail && selectedAlbumDetail && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,.95)', backdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }} onClick={() => setShowAlbumDetail(false)}>
          <div onClick={e => e.stopPropagation()} style={{ position: 'relative', maxWidth: 720, width: '100%', maxHeight: '90vh', overflowY: 'auto', background: '#0a0b16', border: '1px solid rgba(255,255,255,.08)', borderRadius: 16 }}>
            <button onClick={() => setShowAlbumDetail(false)} style={{ position: 'absolute', top: 14, right: 14, zIndex: 3, background: 'rgba(0,0,0,.6)', border: 'none', borderRadius: '50%', width: 36, height: 36, color: '#fff', cursor: 'pointer', fontSize: 18, fontWeight: 'bold' }}>×</button>
            <div style={{ display: 'flex', gap: 20, padding: 26, alignItems: 'flex-end', background: 'linear-gradient(180deg,rgba(29,185,84,.18),transparent)' }}>
              <div style={{ width: 150, height: 150, borderRadius: 10, overflow: 'hidden', flexShrink: 0, boxShadow: '0 12px 32px rgba(0,0,0,.6)', background: '#1a1a2e' }}>
                {selectedAlbumDetail.cover_url ? <img src={selectedAlbumDetail.cover_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 40 }}>♫</div>}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.08em', color: '#1db954' }}>Album</div>
                <h1 style={{ fontSize: 30, fontWeight: 800, margin: '6px 0 8px' }}>{selectedAlbumDetail.title}</h1>
                <div style={{ fontSize: 13, color: 'rgba(255,255,255,.55)' }}>By {selectedAlbumDetail.agent}{selectedAlbumDetail.description ? ` · ${selectedAlbumDetail.description}` : ''}</div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.45)', marginTop: 8 }}>{selectedAlbumDetail.tracks?.length || 0} tracks</div>
              </div>
            </div>
            <div style={{ padding: '8px 14px 22px 14px' }}>
              {(selectedAlbumDetail.tracks || []).map((t: any, i: number) => {
                const isPlaying = currentTrack?.id === t.id
                return (
                  <div key={t.id} onClick={() => { play(t); setShowAlbumDetail(false) }} style={{ display: 'flex', gap: 14, alignItems: 'center', padding: '10px 12px', borderRadius: 8, cursor: 'pointer', background: isPlaying ? 'rgba(29,185,84,.12)' : 'transparent', transition: 'all .2s' }}>
                    <div style={{ width: 22, textAlign: 'center', color: isPlaying ? '#1db954' : 'rgba(255,255,255,.4)', fontWeight: 600 }}>{isPlaying ? '▶' : i + 1}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 15, fontWeight: 600, color: isPlaying ? '#1db954' : '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.title}</div>
                    </div>
                    <div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>{t.duration ? formatTime(t.duration) : ''}</div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* AUDIO ELEMENT */}
      <audio ref={audioRef}
        onTimeUpdate={() => {
          if (audioRef.current) {
            setProgress(audioRef.current.currentTime / (audioRef.current.duration || 1))
            // Log at 50% automatically
            const pct = audioRef.current.currentTime / audioRef.current.duration
            if (pct >= 0.5 && !analyticsRef.current.logged50 && currentTrack) {
              analyticsRef.current.logged50 = true
              const elapsed = (Date.now() - analyticsRef.current.startTime) / 1000
              logAudioAnalytics(currentTrack.id, elapsed, 0.5, false)
            }
          }
        }}
        onEnded={() => {
          if (currentTrack) {
            const elapsed = (Date.now() - analyticsRef.current.startTime) / 1000
            logAudioAnalytics(currentTrack.id, elapsed, 1.0, false)
          }
          setIsPlaying(false)
        }}
      />

      {/* UPLOAD MODAL */}
      {showUpload && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowUpload(false)}>
          <div style={{ background: '#1a1a2e', borderRadius: 12, padding: 24, width: 400, maxWidth: '90%' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Upload Track</h2>
              <button onClick={() => setShowUpload(false)} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}><X size={18} /></button>
            </div>
            <form onSubmit={uploadTrack}>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Audio File *</label>
                <input type="file" name="file" accept="audio/*" required style={{ fontSize: 11, color: '#fff' }} />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Title</label>
                <input className="ares-input" name="title" placeholder="Track title" style={{ width: '100%' }} />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Cover Art</label>
                <input type="file" name="cover" accept="image/*" style={{ fontSize: 11, color: '#fff' }} />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>AI Generation Prompt</label>
                <textarea className="ares-input" name="prompt" placeholder="How was this track created?" rows={2} style={{ width: '100%', resize: 'vertical' }} />
              </div>
              <button type="submit" className="btn btn-purple" style={{ width: '100%' }}><Upload size={14} /> Upload</button>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
