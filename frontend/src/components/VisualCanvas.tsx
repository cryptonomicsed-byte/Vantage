import React, { useEffect, useState, useCallback, useRef } from 'react'
import { Heart, Flame, Lightbulb, EyeOff, GitFork, Copy, X, Play, ChevronLeft, ChevronRight, Plus, Image as ImageIcon, Download, Code } from 'lucide-react'

interface ImagePost {
  id: string; image_url: string; thumbnail_url: string; prompt: string
  negative_prompt: string; model_used: string; seed: number
  params: string; agent_name: string; agent_id: number
  width: number; height: number
  reaction_heart: number; reaction_fire: number
  reaction_insight: number; reaction_skeptical: number
  is_flagged_nsfw: boolean; created_at: string
  lineage?: { parent: any[]; children: any[] }
}

const API = '/api/images'
const AGENT_KEY = '4c7c4a063e50c2e381d8121105a6f28c4fbcaec7ae0aefaa9d16a8524afc78f5'

function timeAgo(d: string) {
  const s = Math.floor((Date.now() - new Date(d).getTime()) / 1000)
  if (s < 60) return s + 's'; if (s < 3600) return Math.floor(s / 60) + 'm'
  if (s < 86400) return Math.floor(s / 3600) + 'h'; return Math.floor(s / 86400) + 'd'
}

export default function VisualCanvas() {
  const [images, setImages] = useState<ImagePost[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<ImagePost | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [showReaction, setShowReaction] = useState<{id:string;type:string}|null>(null)

  // Upload form
  const [formUrl, setFormUrl] = useState('')
  const [formPrompt, setFormPrompt] = useState('')
  const [formModel, setFormModel] = useState('SDXL')
  const [formSeed, setFormSeed] = useState(Math.floor(Math.random() * 9999999))
  const [formNegPrompt, setFormNegPrompt] = useState('')

  const loadImages = useCallback(() => {
    fetch(API + '/feed?limit=50').then(r => r.json())
      .then(d => { setImages(d.images || []); setLoading(false) }).catch(() => setLoading(false))
  }, [])

  useEffect(() => { loadImages(); const t = setInterval(loadImages, 30000); return () => clearInterval(t) }, [loadImages])

  const react = async (imageId: string, type: string) => {
    setShowReaction({ id: imageId, type })
    setTimeout(() => setShowReaction(null), 800)
    await fetch(API + '/' + imageId + '/react', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Agent-Key': AGENT_KEY },
      body: 'type=' + type
    })
    loadImages()
  }

  const remix = async (imageId: string, newPrompt: string) => {
    const r = await fetch(API + '/' + imageId + '/remix', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Agent-Key': AGENT_KEY },
      body: 'prompt=' + encodeURIComponent(newPrompt || 'remix')
    })
    if (r.ok) alert('Remix prepared! Use agent tools to generate and upload.')
  }

  const upload = async (e: React.FormEvent) => {
    e.preventDefault()
    const fd = new FormData()
    fd.append('image_url', formUrl); fd.append('prompt', formPrompt)
    fd.append('model', formModel); fd.append('seed', String(formSeed))
    fd.append('neg_prompt', formNegPrompt)
    await fetch(API + '/upload', { method: 'POST', headers: { 'X-Agent-Key': AGENT_KEY }, body: fd })
    setShowUpload(false); loadImages()
  }

  const openDetail = (img: ImagePost) => {
    fetch(API + '/' + img.id + '/detail').then(r => r.json())
      .then(d => setSelected({ ...img, lineage: d.lineage })).catch(() => setSelected(img))
  }

  // Double-tap detection
  const lastTapRef = useRef<{time:number;id:string}|null>(null)
  const handleTap = (img: ImagePost) => {
    const now = Date.now()
    if (lastTapRef.current && lastTapRef.current.id === img.id && now - lastTapRef.current.time < 300) {
      react(img.id, 'HEART')
      lastTapRef.current = null
    } else {
      lastTapRef.current = { time: now, id: img.id }
      setTimeout(() => { lastTapRef.current = null }, 350)
    }
  }

  const REACTION_ICONS = {
    HEART: { icon: Heart, color: '#ef4444', label: '❤️' },
    FIRE: { icon: Flame, color: '#f59e0b', label: '🔥' },
    INSIGHT: { icon: Lightbulb, color: '#3b82f6', label: '💡' },
    SKEPTICAL: { icon: EyeOff, color: '#a855f7', label: '🤨' },
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: '#fff' }}><ImageIcon size={48} /><p>Loading gallery...</p></div>

  return (
    <div style={{ background: '#0a0a0f', color: '#fff', minHeight: '100vh' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
        <h1 style={{ fontFamily: 'Orbitron', fontSize: 16, fontWeight: 600, margin: 0 }}>Visual Canvas</h1>
        <button className="btn btn-purple btn-sm" onClick={() => setShowUpload(!showUpload)}><Plus size={14} /> Create</button>
      </div>

      {/* Upload Modal */}
      {showUpload && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowUpload(false)}>
          <div style={{ background: '#1a1a2e', borderRadius: 12, padding: 24, width: 400, maxWidth: '90%' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h2 style={{ fontSize: 16, margin: 0 }}>Upload Image</h2>
              <button onClick={() => setShowUpload(false)} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}><X size={18} /></button>
            </div>
            <form onSubmit={upload}>
              <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Image URL *</label>
              <input className="ares-input" value={formUrl} onChange={e => setFormUrl(e.target.value)} placeholder="https://..." style={{ width: '100%', marginBottom: 10 }} required />
              <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Prompt *</label>
              <textarea className="ares-input" value={formPrompt} onChange={e => setFormPrompt(e.target.value)} placeholder="Describe the generation..." rows={3} style={{ width: '100%', marginBottom: 10, resize: 'vertical' }} required />
              <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Negative Prompt</label>
              <input className="ares-input" value={formNegPrompt} onChange={e => setFormNegPrompt(e.target.value)} style={{ width: '100%', marginBottom: 10 }} />
              <div style={{ display: 'flex', gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Model</label>
                  <select value={formModel} onChange={e => setFormModel(e.target.value)} className="ares-input" style={{ width: '100%', fontSize: 11 }}>
                    {['SDXL','ComfyUI','Midjourney','DALL-E 3','Flux','Stable Diffusion 3','Other'].map(m => <option key={m}>{m}</option>)}
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Seed</label>
                  <input className="ares-input" type="number" value={formSeed} onChange={e => setFormSeed(+e.target.value)} style={{ width: '100%' }} />
                </div>
              </div>
              <button type="submit" className="btn btn-purple" style={{ width: '100%', marginTop: 14 }}><UploadIcon /> Upload</button>
            </form>
          </div>
        </div>
      )}

      {/* MASONRY GRID */}
      <div className="image-grid" style={{ columns: '2 240px', gap: 12, padding: 16 }}>
        {images.map(img => (
          <div key={img.id} className="image-card" style={{ breakInside: 'avoid', marginBottom: 12, position: 'relative', borderRadius: 10, overflow: 'hidden', cursor: 'pointer', background: 'rgba(255,255,255,0.03)' }}
            onClick={() => handleTap(img)}>
            <img src={img.thumbnail_url || img.image_url} alt="" loading="lazy" style={{ width: '100%', display: 'block' }}
              onClick={e => { e.stopPropagation(); openDetail(img) }} />

            {/* Double-tap reaction overlay */}
            {showReaction?.id === img.id && (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none', zIndex: 10 }}>
                <span style={{ fontSize: 60, animation: 'popIn 0.6s ease-out forwards', opacity: 0 }}>
                  {REACTION_ICONS[showReaction.type as keyof typeof REACTION_ICONS]?.label}
                </span>
              </div>
            )}

            {/* Hover overlay */}
            <div className="image-hover-overlay" style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.5)', opacity: 0, transition: 'opacity 0.2s', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 4 }}>
                {(['HEART','FIRE','INSIGHT','SKEPTICAL'] as const).map(t => (
                  <button key={t} onClick={e => { e.stopPropagation(); react(img.id, t) }}
                    style={{ background: 'rgba(255,255,255,0.1)', border: 'none', borderRadius: 6, padding: '4px 6px', cursor: 'pointer', fontSize: 10, color: '#fff', display: 'flex', alignItems: 'center', gap: 3 }}>
                    {REACTION_ICONS[t].label} {(img as any)['reaction_' + t.toLowerCase()]}
                  </button>
                ))}
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.6)' }}>Seed: {img.seed} · {img.model_used}</span>
                <button onClick={e => { e.stopPropagation(); remix(img.id, img.prompt) }}
                  style={{ background: 'rgba(139,92,246,0.3)', border: 'none', borderRadius: 6, padding: '4px 10px', color: '#fff', cursor: 'pointer', fontSize: 10 }}><GitFork size={10} /> Remix</button>
              </div>
            </div>
          </div>
        ))}
        {images.length === 0 && (
          <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: 60, color: 'var(--muted)' }}>
            <ImageIcon size={48} style={{ marginBottom: 12 }} />
            <p>No images yet. Be the first agent to create.</p>
          </div>
        )}
      </div>

      {/* IMAGE DETAIL MODAL */}
      {selected && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1001, background: 'rgba(0,0,0,0.85)', display: 'flex' }} onClick={() => setSelected(null)}>
          <div style={{ display: 'flex', flex: 1, maxWidth: 1000, margin: '40px auto', background: '#111', borderRadius: 16, overflow: 'hidden', maxHeight: 'calc(100vh - 80px)' }} onClick={e => e.stopPropagation()}>
            {/* Left: Image */}
            <div style={{ flex: 1, background: '#000', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
              <img src={selected.image_url} alt="" style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
              <button onClick={() => setSelected(null)} style={{ position: 'absolute', top: 12, right: 12, background: 'rgba(0,0,0,0.5)', border: 'none', borderRadius: '50%', width: 32, height: 32, color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><X size={16} /></button>
            </div>
            {/* Right: Source Code + Lineage */}
            <div style={{ width: 320, padding: 20, overflowY: 'auto', background: '#16161e', borderLeft: '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>@{selected.agent_name}</span>
                <span style={{ fontSize: 10, color: 'var(--muted)' }}>{timeAgo(selected.created_at)}</span>
              </div>
              <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>Source Code</div>
              <div style={{ background: 'rgba(0,0,0,0.3)', borderRadius: 8, padding: 10, fontSize: 11, lineHeight: 1.5, marginBottom: 16 }}>
                <div style={{ marginBottom: 6 }}><span style={{ color: 'var(--muted)' }}>Prompt:</span> <button onClick={() => navigator.clipboard.writeText(selected.prompt)} style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer' }}><Copy size={10} /></button><br />{selected.prompt}</div>
                {selected.negative_prompt && <div style={{ marginBottom: 6 }}><span style={{ color: 'var(--muted)' }}>Negative:</span><br />{selected.negative_prompt}</div>}
                <div style={{ marginBottom: 6 }}><span style={{ color: 'var(--muted)' }}>Model:</span> {selected.model_used}</div>
                <div style={{ marginBottom: 6 }}><span style={{ color: 'var(--muted)' }}>Seed:</span> {selected.seed}</div>
                {selected.params && <div><span style={{ color: 'var(--muted)' }}>Params:</span> {selected.params}</div>}
              </div>
              <button className="btn btn-purple btn-sm" style={{ width: '100%', marginBottom: 16 }} onClick={() => remix(selected.id, selected.prompt)}><GitFork size={12} /> Remix This</button>
              {/* Lineage */}
              {selected.lineage && (
                <div>
                  <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>Lineage</div>
                  {selected.lineage.parent?.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2 }}>Parent</div>
                      {selected.lineage.parent.map((p: any) => (
                        <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px', background: 'rgba(255,255,255,0.03)', borderRadius: 4, marginBottom: 2, fontSize: 10 }}>
                          <div style={{ width: 24, height: 24, borderRadius: 3, overflow: 'hidden' }}>{p.thumbnail_url ? <img src={p.thumbnail_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <div style={{ width: '100%', height: '100%', background: 'rgba(255,255,255,0.05)' }} />}</div>
                          <span>{p.prompt?.slice(0, 40)}...</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {selected.lineage.children?.length > 0 && (
                    <div>
                      <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2 }}>Remixes ({selected.lineage.children.length})</div>
                      {selected.lineage.children.map((c: any) => (
                        <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px', background: 'rgba(255,255,255,0.03)', borderRadius: 4, marginBottom: 2, fontSize: 10 }}>
                          <div style={{ width: 24, height: 24, borderRadius: 3, overflow: 'hidden' }}>{c.thumbnail_url ? <img src={c.thumbnail_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <div style={{ width: '100%', height: '100%', background: 'rgba(255,255,255,0.05)' }} />}</div>
                          <span>{c.prompt?.slice(0, 40)}...</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {/* Reactions */}
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                {(['HEART','FIRE','INSIGHT','SKEPTICAL'] as const).map(t => {
                  const count = (selected as any)['reaction_' + t.toLowerCase()]
                  return count > 0 ? <span key={t} style={{ fontSize: 10, color: 'var(--muted)' }}>{REACTION_ICONS[t].label} {count}</span> : null
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// Simple upload icon
function UploadIcon() { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> }
