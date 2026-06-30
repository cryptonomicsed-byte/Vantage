import React, { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { Film, Play, Plus, RefreshCw, Download, Copy, Eye } from 'lucide-react'

const API = '/api/video'
const getKey = () => localStorage.getItem('vantage_api_key') || ''

interface VideoProject {
  id: number
  title: string
  description: string
  template: string
  status: string
  render_url: string | null
  view_url: string | null
  duration_sec: number
  created_at: string
  agent_name?: string
}

export default function VideoStudio() {
  const apiKey = getKey()
  const [videos, setVideos] = useState<VideoProject[]>([])
  const [myVideos, setMyVideos] = useState<VideoProject[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<'library' | 'mine' | 'create'>('library')
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newTemplate, setNewTemplate] = useState('custom')
  const [creating, setCreating] = useState(false)

  const headers = { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' }

  const loadLibrary = () => {
    if (!apiKey) return
    fetch(`${API}/library`, { headers }).then(r => r.json()).then(setVideos).catch(() => {})
    fetch(`${API}/library/mine`, { headers }).then(r => r.json()).then(setMyVideos).catch(() => {})
    setLoading(false)
  }

  useEffect(() => { loadLibrary() }, [])

  const createProject = async () => {
    if (!newTitle.trim()) return
    setCreating(true)
    const r = await fetch(`${API}/projects`, {
      method: 'POST', headers,
      body: JSON.stringify({ title: newTitle, description: newDesc, template: newTemplate, duration_sec: 15 })
    })
    const data = await r.json()
    if (data.id) {
      setNewTitle(''); setNewDesc('')
      setActiveTab('mine')
      loadLibrary()
    }
    setCreating(false)
  }

  const renderVideo = async (projectId: number, engine: string) => {
    const r = await fetch(`${API}/projects/${projectId}/render`, {
      method: 'POST', headers,
      body: JSON.stringify({ engine })
    })
    const data = await r.json()
    if (data.status === 'completed') loadLibrary()
  }

  if (!apiKey) return (
    <div className="p-8 text-center" style={{ color: '#8899aa' }}>
      <Film size={48} style={{ marginBottom: 16, opacity: 0.3 }} />
      <h2 style={{ color: '#00ffcc', marginBottom: 8 }}>Video Studio</h2>
      <p>Connect your API key in <NavLink to="/dashboard" style={{ color: '#00ffcc' }}>Dashboard</NavLink> to create videos.</p>
    </div>
  )

  const templates = [
    { id: 'custom', label: 'Custom (HyperFrames HTML)' },
    { id: 'trading-recap', label: 'Trading Recap' },
    { id: 'agent-birth', label: 'Agent Birth' },
    { id: 'market-update', label: 'Market Update' },
  ]

  return (
    <div className="p-6" style={{ maxWidth: 1200, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <Film size={28} color="#00ffcc" />
        <h1 style={{ color: '#00ffcc', fontSize: 28, fontWeight: 700, margin: 0 }}>Video Studio</h1>
        <span style={{ color: '#556', fontSize: 13 }}>
          HyperFrames · ViMax · Remotion · Rendervid
        </span>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 24, borderBottom: '1px solid #222' }}>
        {(['library', 'mine', 'create'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '8px 20px', border: 'none', background: 'transparent',
              color: activeTab === tab ? '#00ffcc' : '#667', cursor: 'pointer',
              borderBottom: activeTab === tab ? '2px solid #00ffcc' : '2px solid transparent',
              fontSize: 14, fontWeight: 600,
            }}
          >
            {tab === 'library' ? '🌐 Library' : tab === 'mine' ? '🎬 My Videos' : '➕ Create'}
          </button>
        ))}
        <button onClick={loadLibrary} style={{ marginLeft: 'auto', background: 'transparent', border: 'none', color: '#667', cursor: 'pointer' }}>
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Create Tab */}
      {activeTab === 'create' && (
        <div style={{ background: '#111122', borderRadius: 12, padding: 24, border: '1px solid #223' }}>
          <h3 style={{ color: '#00ffcc', marginBottom: 16 }}>New Video Project</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <input
              placeholder="Video title..."
              value={newTitle}
              onChange={e => setNewTitle(e.target.value)}
              style={{
                padding: '10px 14px', borderRadius: 8, border: '1px solid #334',
                background: '#0a0a1a', color: '#fff', fontSize: 15, outline: 'none'
              }}
            />
            <input
              placeholder="Description (optional)"
              value={newDesc}
              onChange={e => setNewDesc(e.target.value)}
              style={{
                padding: '10px 14px', borderRadius: 8, border: '1px solid #334',
                background: '#0a0a1a', color: '#aabbcc', fontSize: 14, outline: 'none'
              }}
            />
            <select
              value={newTemplate}
              onChange={e => setNewTemplate(e.target.value)}
              style={{
                padding: '10px 14px', borderRadius: 8, border: '1px solid #334',
                background: '#0a0a1a', color: '#00ffcc', fontSize: 14, outline: 'none'
              }}
            >
              {templates.map(t => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </select>
            <button
              onClick={createProject}
              disabled={creating || !newTitle.trim()}
              style={{
                padding: '10px 24px', borderRadius: 8, border: 'none',
                background: creating ? '#334' : '#00ffcc',
                color: creating ? '#667' : '#0a0a1a',
                fontWeight: 700, cursor: creating ? 'default' : 'pointer',
                alignSelf: 'flex-start',
              }}
            >
              <Plus size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
              {creating ? 'Creating...' : 'Create Project'}
            </button>
          </div>
          <p style={{ color: '#556', fontSize: 12, marginTop: 12 }}>
            After creating, add scenes and render with HyperFrames, ViMax, Remotion, or Rendervid.
          </p>
        </div>
      )}

      {/* Library Tab */}
      {activeTab === 'library' && (
        <VideoGrid videos={videos} loading={loading} empty="No videos in the library yet." />
      )}

      {/* My Videos Tab */}
      {activeTab === 'mine' && (
        <VideoGrid videos={myVideos} loading={loading} showRender onRender={renderVideo} empty="No videos yet. Create your first project!" />
      )}
    </div>
  )
}

function VideoGrid({ videos, loading, showRender, onRender, empty }: {
  videos: VideoProject[]
  loading: boolean
  showRender?: boolean
  onRender?: (id: number, engine: string) => void
  empty: string
}) {
  if (loading) return <p style={{ color: '#667' }}>Loading...</p>
  if (!videos.length) return <p style={{ color: '#556' }}>{empty}</p>

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
      {videos.map(v => (
        <div key={v.id} style={{
          background: '#111122', borderRadius: 12, padding: 20, border: '1px solid #223',
          display: 'flex', flexDirection: 'column', gap: 10
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
            <div>
              <h3 style={{ color: '#fff', fontSize: 16, fontWeight: 600, margin: 0 }}>{v.title}</h3>
              {v.agent_name && <span style={{ color: '#00ffcc', fontSize: 12 }}>by {v.agent_name}</span>}
            </div>
            <span style={{
              padding: '2px 10px', borderRadius: 10, fontSize: 11, fontWeight: 600,
              background: v.status === 'rendered' ? 'rgba(0,255,136,0.15)' : 'rgba(255,200,0,0.15)',
              color: v.status === 'rendered' ? '#00ff88' : '#ffcc00',
            }}>
              {v.status}
            </span>
          </div>

          <p style={{ color: '#889', fontSize: 13, margin: 0 }}>{v.description || 'No description'}</p>

          <div style={{ display: 'flex', gap: 20, fontSize: 12, color: '#556' }}>
            <span>⏱ {v.duration_sec}s</span>
            <span>🎨 {v.template}</span>
            <span>📅 {v.created_at?.slice(0, 10)}</span>
          </div>

          {v.render_url ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <a href={v.view_url || '#'} target="_blank" rel="noreferrer"
                style={{
                  padding: '6px 14px', borderRadius: 6, background: 'rgba(0,255,200,0.15)',
                  color: '#00ffcc', textDecoration: 'none', fontSize: 13, display: 'flex', alignItems: 'center', gap: 4,
                }}>
                <Play size={12} /> Watch
              </a>
              <button onClick={() => navigator.clipboard?.writeText(v.render_url || '')}
                style={{ padding: '6px 10px', background: 'transparent', border: '1px solid #334', borderRadius: 6, color: '#667', cursor: 'pointer', fontSize: 12 }}>
                <Copy size={12} />
              </button>
            </div>
          ) : showRender ? (
            <div style={{ display: 'flex', gap: 6 }}>
              <button onClick={() => onRender?.(v.id, 'hyperframes')}
                style={{ padding: '4px 10px', borderRadius: 4, background: '#1a2a2a', border: '1px solid #334', color: '#00ffcc', cursor: 'pointer', fontSize: 11 }}>
                Render HyFrame
              </button>
              <button onClick={() => onRender?.(v.id, 'vimax')}
                style={{ padding: '4px 10px', borderRadius: 4, background: '#1a2a2a', border: '1px solid #334', color: '#00ffcc', cursor: 'pointer', fontSize: 11 }}>
                Render ViMax
              </button>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}
