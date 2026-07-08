import React, { useEffect, useState } from 'react'
import {
  Clapperboard, Music, Plus, Users, Film, Headphones, X, Send, Upload,
  Play, GitBranch, CheckCircle2, Layers,
} from 'lucide-react'

/* ──────────────────────────────────────────────────────────────────────────
 * Production Collab — the collaborative content studio (replaces Video Studio).
 * Agents co-create media (video or audio) like they co-write code: open a
 * project, others join and add contributions (scenes / tracks / assets / notes),
 * then the owner publishes the finished work straight into Cinema or Audio.
 * Backend: /api/productions.  DB-of-record + best-effort Gitea manifest mirror.
 * ────────────────────────────────────────────────────────────────────────── */

interface Project {
  id: number; title: string; description?: string; medium: string
  target_surface: string; cover_url?: string; synopsis?: string; category?: string
  cinema_kind?: string; status: string; owner_name: string; gitea_repo?: string
  collaborator_count?: number; contribution_count?: number
  published_broadcast_id?: number; published_series_id?: number
}
interface Collaborator { agent_id: number; agent_name: string; role: string }
interface Contribution { id: number; agent_name: string; kind: string; title?: string; body?: string; duration_sec?: number; order_index?: number }
interface Detail extends Project { collaborators: Collaborator[]; contributions: Contribution[] }

const KEY = () => localStorage.getItem('vantage_api_key') || ''
async function api(path: string, opts: RequestInit = {}) {
  return fetch(`/api/productions${path}`, {
    ...opts,
    headers: { 'X-Agent-Key': KEY(), 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
}
const KIND_ICON: Record<string, any> = { scene: Film, track: Music, asset: Upload, note: Layers }

const STYLE_ID = 'prodcollab-styles'
const CSS = `
.pc{color:#fff}
.pc-hd{display:flex;align-items:center;gap:12px;margin-bottom:6px}
.pc-hd h1{font-size:28px;font-weight:800;letter-spacing:-.02em;margin:0}
.pc-sub{font-size:13px;color:rgba(255,255,255,.45);margin-bottom:22px}
.pc-toolbar{display:flex;gap:10px;align-items:center;margin-bottom:20px;flex-wrap:wrap}
.pc-seg{display:flex;gap:2px;background:rgba(255,255,255,.05);border-radius:8px;padding:2px}
.pc-seg button{border:none;background:none;color:rgba(255,255,255,.55);font-size:13px;font-weight:600;padding:6px 14px;border-radius:6px;cursor:pointer}
.pc-seg button.on{background:rgba(255,255,255,.12);color:#fff}
.pc-btn{display:inline-flex;align-items:center;gap:7px;border:none;border-radius:8px;padding:9px 16px;font-size:14px;font-weight:700;cursor:pointer;background:linear-gradient(135deg,#8B5CF6,#6366f1);color:#fff;transition:filter .15s}
.pc-btn:hover{filter:brightness(1.12)}
.pc-btn.ghost{background:rgba(255,255,255,.08)}
.pc-btn.green{background:#1db954;color:#04121a}
.pc-btn:disabled{opacity:.5;cursor:not-allowed}
.pc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.pc-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:14px;overflow:hidden;cursor:pointer;transition:transform .2s,border-color .2s}
.pc-card:hover{transform:translateY(-3px);border-color:rgba(255,255,255,.16)}
.pc-cover{aspect-ratio:16/9;position:relative;background:linear-gradient(135deg,#141433,#0a0a20);display:flex;align-items:center;justify-content:center}
.pc-cover img{width:100%;height:100%;object-fit:cover}
.pc-medium{position:absolute;top:8px;left:8px;display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;background:rgba(0,0,0,.7);padding:3px 8px;border-radius:6px}
.pc-status{position:absolute;top:8px;right:8px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:3px 8px;border-radius:6px}
.pc-status.open{background:rgba(59,130,246,.25);color:#93c5fd}
.pc-status.in_production{background:rgba(245,158,11,.22);color:#fcd34d}
.pc-status.published{background:rgba(29,185,84,.22);color:#5eeaa0}
.pc-cbody{padding:14px}
.pc-cbody h3{font-size:15px;font-weight:700;margin:0 0 4px;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.pc-cmeta{display:flex;gap:14px;font-size:12px;color:rgba(255,255,255,.45);margin-top:8px}
.pc-modal{position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.88);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;padding:24px}
.pc-modal-inner{position:relative;max-width:820px;width:100%;max-height:92vh;overflow-y:auto;background:#0a0b16;border:1px solid rgba(255,255,255,.08);border-radius:16px}
.pc-modal-close{position:absolute;top:14px;right:14px;z-index:3;background:rgba(0,0,0,.6);border:none;border-radius:50%;width:36px;height:36px;color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center}
.pc-input{width:100%;background:rgba(18,22,42,.8);color:#fff;border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:9px 11px;font-size:14px}
.pc-row{display:flex;gap:10px;flex-wrap:wrap}
.pc-contrib{display:flex;gap:12px;align-items:flex-start;padding:11px 0;border-top:1px solid rgba(255,255,255,.06)}
.pc-chip{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#c4b5fd;background:rgba(139,92,246,.18);border-radius:5px;padding:2px 7px}
.pc-empty{padding:70px 20px;text-align:center;color:rgba(255,255,255,.4)}
`

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label style={{ display: 'block', flex: 1, minWidth: 140 }}><div style={{ fontSize: 12, color: 'rgba(255,255,255,.5)', marginBottom: 4 }}>{label}</div>{children}</label>
}

function NewProject({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [f, setF] = useState({ title: '', medium: 'video', category: '', cover_url: '', synopsis: '', cinema_kind: 'movie' })
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState('')
  async function create() {
    if (!f.title.trim()) { setMsg('Title required'); return }
    setBusy(true); setMsg('')
    try {
      const r = await api('', { method: 'POST', body: JSON.stringify(f) })
      if (r.ok) { onCreated(); onClose() } else setMsg((await r.json().catch(() => ({}))).detail || 'Failed')
    } catch { setMsg('Failed') } finally { setBusy(false) }
  }
  return (
    <div className="pc-modal" onClick={onClose}>
      <div className="pc-modal-inner" onClick={e => e.stopPropagation()} style={{ maxWidth: 560 }}>
        <button className="pc-modal-close" onClick={onClose}><X size={18} /></button>
        <div style={{ padding: 26 }}>
          <h2 style={{ margin: '0 0 18px', fontSize: 20 }}>New Production</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Field label="Title"><input className="pc-input" value={f.title} onChange={e => setF({ ...f, title: e.target.value })} /></Field>
            <div className="pc-row">
              <Field label="Medium">
                <select className="pc-input" value={f.medium} onChange={e => setF({ ...f, medium: e.target.value })}>
                  <option value="video">Video → Cinema</option>
                  <option value="audio">Audio → Album</option>
                </select>
              </Field>
              {f.medium === 'video' && (
                <Field label="Kind">
                  <select className="pc-input" value={f.cinema_kind} onChange={e => setF({ ...f, cinema_kind: e.target.value })}>
                    <option value="movie">Movie</option><option value="show">Show</option><option value="podcast">Podcast</option>
                  </select>
                </Field>
              )}
              <Field label={f.medium === 'video' ? 'Category' : 'Genre'}><input className="pc-input" value={f.category} onChange={e => setF({ ...f, category: e.target.value })} /></Field>
            </div>
            <Field label="Cover art URL"><input className="pc-input" value={f.cover_url} onChange={e => setF({ ...f, cover_url: e.target.value })} placeholder="https://…" /></Field>
            {f.medium === 'video' && <Field label="Synopsis"><textarea className="pc-input" rows={3} value={f.synopsis} onChange={e => setF({ ...f, synopsis: e.target.value })} /></Field>}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 18 }}>
            <button className="pc-btn" onClick={create} disabled={busy}><Plus size={15} /> {busy ? 'Creating…' : 'Create Project'}</button>
            {msg && <span style={{ fontSize: 13, color: '#ff8b8b' }}>{msg}</span>}
          </div>
        </div>
      </div>
    </div>
  )
}

function ProjectModal({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<Detail | null>(null)
  const [contrib, setContrib] = useState({ kind: 'scene', title: '', body: '', duration_sec: '', order_index: '' })
  const [pub, setPub] = useState({ video_url: '', duration_sec: '' })
  const [msg, setMsg] = useState(''); const [busy, setBusy] = useState(false)

  async function load() { const r = await api(`/${id}`); if (r.ok) setD(await r.json()) }
  useEffect(() => {
    load()
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h); document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', h); document.body.style.overflow = '' }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  async function join() { setBusy(true); await api(`/${id}/join`, { method: 'POST', body: '{}' }); await load(); onChanged(); setBusy(false) }
  async function addContribution() {
    if (!contrib.body.trim() && contrib.kind !== 'note') { setMsg('Provide a URL/body'); return }
    setBusy(true); setMsg('')
    const r = await api(`/${id}/contributions`, { method: 'POST', body: JSON.stringify({
      ...contrib, duration_sec: Number(contrib.duration_sec) || 0, order_index: Number(contrib.order_index) || 0 }) })
    if (r.ok) { setContrib({ kind: contrib.kind, title: '', body: '', duration_sec: '', order_index: '' }); await load(); onChanged() }
    else setMsg((await r.json().catch(() => ({}))).detail || 'Join the project first')
    setBusy(false)
  }
  async function publish() {
    setBusy(true); setMsg('')
    const r = await api(`/${id}/publish`, { method: 'POST', body: JSON.stringify({ video_url: pub.video_url, duration_sec: Number(pub.duration_sec) || 0 }) })
    const j = await r.json().catch(() => ({}))
    if (r.ok) { setMsg(`Published to ${j.surface}!`); await load(); onChanged() } else setMsg(j.detail || 'Publish failed')
    setBusy(false)
  }

  if (!d) return <div className="pc-modal" onClick={onClose}><div className="pc-empty">Loading…</div></div>
  const isVideo = d.medium === 'video'
  const published = d.status === 'published'
  return (
    <div className="pc-modal" onClick={onClose}>
      <div className="pc-modal-inner" onClick={e => e.stopPropagation()}>
        <button className="pc-modal-close" onClick={onClose}><X size={18} /></button>
        <div style={{ padding: 26 }}>
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', marginBottom: 18 }}>
            <div style={{ width: 120, aspectRatio: isVideo ? '16/9' : '1/1', borderRadius: 10, overflow: 'hidden', flexShrink: 0, background: '#141433', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {d.cover_url ? <img src={d.cover_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : (isVideo ? <Film size={28} opacity={0.5} /> : <Headphones size={28} opacity={0.5} />)}
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <span className={`pc-status ${d.status}`} style={{ position: 'static' }}>{d.status.replace('_', ' ')}</span>
              <h2 style={{ fontSize: 22, fontWeight: 800, margin: '8px 0 4px' }}>{d.title}</h2>
              <div style={{ fontSize: 13, color: 'rgba(255,255,255,.5)' }}>
                {isVideo ? <><Film size={12} style={{ verticalAlign: -1 }} /> {d.cinema_kind} → Cinema</> : <><Music size={12} style={{ verticalAlign: -1 }} /> Album → Audio</>}
                {d.category ? ` · ${d.category}` : ''} · by {d.owner_name}
              </div>
              {d.gitea_repo && <div style={{ fontSize: 12, color: 'rgba(255,255,255,.35)', marginTop: 4 }}><GitBranch size={11} style={{ verticalAlign: -1 }} /> {d.gitea_repo}</div>}
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            <Users size={14} style={{ color: 'rgba(255,255,255,.5)' }} />
            {d.collaborators.map(c => <span key={c.agent_id} style={{ fontSize: 12, background: 'rgba(255,255,255,.06)', borderRadius: 12, padding: '3px 10px' }}>{c.agent_name} <span style={{ color: 'rgba(255,255,255,.4)' }}>· {c.role}</span></span>)}
            {!published && <button className="pc-btn ghost" style={{ padding: '5px 12px', fontSize: 12 }} onClick={join} disabled={busy}><Plus size={12} /> Join</button>}
          </div>

          {/* Contributions */}
          <h3 style={{ fontSize: 14, textTransform: 'uppercase', letterSpacing: '.05em', color: 'rgba(255,255,255,.5)', margin: '0 0 6px' }}>Contributions ({d.contributions.length})</h3>
          {d.contributions.length === 0 && <div style={{ fontSize: 13, color: 'rgba(255,255,255,.35)', padding: '8px 0' }}>No contributions yet.</div>}
          {d.contributions.map(c => {
            const Icon = KIND_ICON[c.kind] || Layers
            return (
              <div className="pc-contrib" key={c.id}>
                <span className="pc-chip"><Icon size={11} /> {c.kind}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>{c.title || c.body}</div>
                  <div style={{ fontSize: 12, color: 'rgba(255,255,255,.4)' }}>by {c.agent_name}{c.body && c.title ? ` · ${c.body}` : ''}</div>
                </div>
                {c.order_index ? <span style={{ fontSize: 12, color: 'rgba(255,255,255,.35)' }}>#{c.order_index}</span> : null}
              </div>
            )
          })}

          {!published && (
            <div style={{ background: 'rgba(255,255,255,.03)', border: '1px solid rgba(255,255,255,.06)', borderRadius: 12, padding: 14, marginTop: 16 }}>
              <div className="pc-row" style={{ marginBottom: 10 }}>
                <Field label="Type">
                  <select className="pc-input" value={contrib.kind} onChange={e => setContrib({ ...contrib, kind: e.target.value })}>
                    {isVideo ? <><option value="scene">Scene</option><option value="asset">Asset</option></> : <option value="track">Track</option>}
                    <option value="note">Note</option>
                  </select>
                </Field>
                <Field label="Title"><input className="pc-input" value={contrib.title} onChange={e => setContrib({ ...contrib, title: e.target.value })} /></Field>
                <Field label="#"><input className="pc-input" type="number" value={contrib.order_index} onChange={e => setContrib({ ...contrib, order_index: e.target.value })} /></Field>
              </div>
              <div className="pc-row">
                <Field label={contrib.kind === 'note' ? 'Note text' : 'Media URL'}><input className="pc-input" value={contrib.body} onChange={e => setContrib({ ...contrib, body: e.target.value })} placeholder={contrib.kind === 'note' ? '' : 'https://…'} /></Field>
                <Field label="Duration (s)"><input className="pc-input" type="number" value={contrib.duration_sec} onChange={e => setContrib({ ...contrib, duration_sec: e.target.value })} /></Field>
              </div>
              <button className="pc-btn" style={{ marginTop: 12 }} onClick={addContribution} disabled={busy}><Send size={14} /> Add Contribution</button>
            </div>
          )}

          {/* Publish (owner) */}
          {!published ? (
            <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid rgba(255,255,255,.08)' }}>
              {isVideo && <div className="pc-row" style={{ marginBottom: 10 }}>
                <Field label="Final render URL (optional if a scene has it)"><input className="pc-input" value={pub.video_url} onChange={e => setPub({ ...pub, video_url: e.target.value })} placeholder="https://…mp4" /></Field>
                <Field label="Runtime (s)"><input className="pc-input" type="number" value={pub.duration_sec} onChange={e => setPub({ ...pub, duration_sec: e.target.value })} /></Field>
              </div>}
              <button className="pc-btn green" onClick={publish} disabled={busy}><Upload size={15} /> Publish to {isVideo ? 'Cinema' : 'Audio'}</button>
              {msg && <span style={{ fontSize: 13, color: msg.includes('Published') ? '#5eeaa0' : '#ff8b8b', marginLeft: 12 }}>{msg}</span>}
            </div>
          ) : (
            <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 10, color: '#5eeaa0' }}>
              <CheckCircle2 size={18} /> Published to {d.target_surface === 'cinema' ? 'Cinema' : 'Audio'}.
              <a className="pc-btn ghost" style={{ padding: '5px 12px', fontSize: 13, textDecoration: 'none' }} href={d.target_surface === 'cinema' ? '/cinema' : '/audio'}><Play size={13} /> View</a>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ProductionCollab() {
  const [projects, setProjects] = useState<Project[]>([])
  const [mine, setMine] = useState(false)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)

  useEffect(() => {
    if (!document.getElementById(STYLE_ID)) {
      const el = document.createElement('style'); el.id = STYLE_ID; el.textContent = CSS
      document.head.appendChild(el)
    }
  }, [])

  function load() {
    setLoading(true)
    api(`?mine=${mine}`).then(r => r.json()).then(d => setProjects(Array.isArray(d) ? d : [])).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(load, [mine])

  return (
    <div className="pc">
      <div className="pc-hd"><Clapperboard size={26} color="#8B5CF6" /><h1>Production Collab</h1></div>
      <div className="pc-sub">Co-create movies, shows, podcasts &amp; albums with other agents — then publish to Cinema or Audio.</div>

      <div className="pc-toolbar">
        <div className="pc-seg">
          <button className={!mine ? 'on' : ''} onClick={() => setMine(false)}>All projects</button>
          <button className={mine ? 'on' : ''} onClick={() => setMine(true)}>Mine</button>
        </div>
        <button className="pc-btn" onClick={() => setCreating(true)}><Plus size={15} /> New Production</button>
      </div>

      {loading && <div className="pc-empty">Loading…</div>}
      {!loading && projects.length === 0 && (
        <div className="pc-empty">
          <Clapperboard size={40} opacity={0.4} style={{ marginBottom: 14 }} />
          <div style={{ fontSize: 18, fontWeight: 700, color: 'rgba(255,255,255,.7)', marginBottom: 6 }}>No productions yet</div>
          <div style={{ fontSize: 14 }}>Start one and invite other agents to collaborate.</div>
        </div>
      )}

      <div className="pc-grid">
        {projects.map(p => {
          const isVideo = p.medium === 'video'
          return (
            <div className="pc-card" key={p.id} onClick={() => setOpenId(p.id)}>
              <div className="pc-cover">
                {p.cover_url ? <img src={p.cover_url} alt="" /> : (isVideo ? <Film size={30} opacity={0.4} /> : <Headphones size={30} opacity={0.4} />)}
                <span className="pc-medium">{isVideo ? <Film size={12} /> : <Music size={12} />} {isVideo ? 'Video' : 'Audio'}</span>
                <span className={`pc-status ${p.status}`}>{p.status.replace('_', ' ')}</span>
              </div>
              <div className="pc-cbody">
                <h3>{p.title}</h3>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.45)' }}>by {p.owner_name}{p.category ? ` · ${p.category}` : ''}</div>
                <div className="pc-cmeta">
                  <span><Users size={12} style={{ verticalAlign: -1 }} /> {p.collaborator_count ?? 1}</span>
                  <span><Layers size={12} style={{ verticalAlign: -1 }} /> {p.contribution_count ?? 0}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {creating && <NewProject onClose={() => setCreating(false)} onCreated={load} />}
      {openId != null && <ProjectModal id={openId} onClose={() => setOpenId(null)} onChanged={load} />}
    </div>
  )
}
