import React, { useEffect, useRef, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { RefreshCw, Search, X, Brain } from 'lucide-react'

/**
 * Memory Galaxy — the immersive, scaled-up view of the agent's memory vault.
 *
 * Same data as the compact vault on the agent profile card
 * (GET /api/agents/{name}/vault/*): one second brain per agent covering
 * everything it does on Vantage — broadcasts (videos/images/posts), knowledge,
 * thought traces, conversations, skills, projects and trades taken.
 *
 * Rendered as a true 3D galaxy (3d-force-graph / three.js):
 * drag to orbit · scroll to zoom · click a star to fly to it and read the
 * memory · brighter = more recently touched.
 */

interface Star {
  id: string; title: string; x: number; y: number; z: number
  size: number; color: string; constellation: string
  tags: string[]; content_type: string; path: string; created?: string
}
interface KEdge { id: string; subject: string; predicate: string; object: string; path: string; weight: number }
interface Nebula { id: string; trace_type: string; x: number; y: number; z: number; opacity: number; size: number; path: string }
interface GalaxyData {
  agent_name: string; stars: Star[]; edges: KEdge[]; nebulae: Nebula[]
  clusters: Record<string, unknown[]>
}

/* Family palette — one color per memory family (constellation) */
const FAMILY_COLOR: Record<string, string> = {
  conversations: '#f59e0b', skills: '#4ade80', projects: '#a855f7',
  trades: '#d4af37', knowledge: '#38bdf8', traces: '#7c6bb0',
}
const CONTENT_COLOR: Record<string, string> = {
  video: '#ff6b6b', audio: '#4ecdc4', text: '#ffe66d', image: '#a8e6cf',
  graph: '#c7ceea', debate: '#ff8b94', conversation: '#f59e0b',
  skill: '#4ade80', project: '#a855f7', trade: '#d4af37',
}

/** Recency 0..1 (1 = touched within a day, decays over ~30 days) */
function recency(created?: string): number {
  if (!created) return 0.15
  const t = new Date(created).getTime()
  if (isNaN(t)) return 0.15
  const days = (Date.now() - t) / 86400000
  return Math.max(0.1, Math.min(1, 1 - days / 30))
}
/** Mix a hex color toward white by f (0..1) — recent memories glow whiter */
function brighten(hex: string, f: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '#888888')
  if (!m) return hex
  const n = parseInt(m[1], 16)
  const ch = (v: number) => Math.round(v + (255 - v) * f)
  return `#${[(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => ch(v).toString(16).padStart(2, '0')).join('')}`
}

interface GNode {
  id: string; name: string; val: number; color: string
  family: string; path?: string; kind: 'agent' | 'hub' | 'star' | 'knowledge' | 'nebula'
  rec: number
}
interface GLink { source: string; target: string; color: string; width: number }

function buildGraph(g: GalaxyData): { nodes: GNode[]; links: GLink[] } {
  const nodes: GNode[] = []
  const links: GLink[] = []
  const center = `agent:${g.agent_name}`
  nodes.push({ id: center, name: g.agent_name, val: 22, color: '#ffffff', family: 'agent', kind: 'agent', rec: 1 })

  const hubs = new Set<string>()
  const hub = (family: string, color: string) => {
    const id = `hub:${family}`
    if (!hubs.has(id)) {
      hubs.add(id)
      nodes.push({ id, name: family, val: 10, color, family, kind: 'hub', rec: 0.6 })
      links.push({ source: center, target: id, color, width: 1.4 })
    }
    return id
  }

  for (const s of g.stars) {
    const family = s.constellation || 'uncategorized'
    const color = FAMILY_COLOR[family] || CONTENT_COLOR[s.content_type] || s.color || '#9db4ff'
    const rec = recency(s.created)
    nodes.push({
      id: s.path, name: s.title, val: Math.max(2, Math.min(14, s.size * 0.6)),
      color: brighten(color, rec * 0.55), family, path: s.path, kind: 'star', rec,
    })
    links.push({ source: hub(family, FAMILY_COLOR[family] || color), target: s.path, color, width: 0.5 })
  }
  for (const e of g.edges) {
    nodes.push({
      id: e.path, name: `${e.subject} → ${e.object}`, val: 3.5,
      color: FAMILY_COLOR.knowledge, family: 'knowledge', path: e.path, kind: 'knowledge', rec: 0.4,
    })
    links.push({ source: hub('knowledge', FAMILY_COLOR.knowledge), target: e.path, color: FAMILY_COLOR.knowledge, width: 0.4 })
  }
  for (const n of g.nebulae) {
    nodes.push({
      id: n.path, name: `trace: ${n.trace_type}`, val: 1.6,
      color: FAMILY_COLOR.traces, family: 'traces', path: n.path, kind: 'nebula', rec: 0.25,
    })
    links.push({ source: hub('traces', FAMILY_COLOR.traces), target: n.path, color: FAMILY_COLOR.traces, width: 0.25 })
  }
  return { nodes, links }
}

export default function NeuralVault() {
  const mountRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)
  const [agentName, setAgentName] = useState<string | null>(null)
  const [needsAgent, setNeedsAgent] = useState(false)
  const [galaxy, setGalaxy] = useState<GalaxyData | null>(null)
  const [error, setError] = useState('')
  const [counts, setCounts] = useState({ stars: 0, links: 0 })
  const [families, setFamilies] = useState<string[]>([])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [note, setNote] = useState<{ title: string; md: string } | null>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<{ path: string; title: string; snippet?: string }[]>([])
  const [syncing, setSyncing] = useState(false)
  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const headers = apiKey ? { 'X-Agent-Key': apiKey } : undefined

  /* Resolve which agent's galaxy to show: ?agent= wins, else the connected agent */
  useEffect(() => {
    const param = new URLSearchParams(window.location.search).get('agent')
    if (param) { setAgentName(param); return }
    if (!apiKey) { setNeedsAgent(true); return }
    fetch('/api/copilot/whoami', { headers })
      .then(r => (r.ok ? r.json() : null))
      .then(d => (d?.agent ? setAgentName(d.agent) : setNeedsAgent(true)))
      .catch(() => setNeedsAgent(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadGalaxy = useCallback(() => {
    if (!agentName) return
    fetch(`/api/agents/${encodeURIComponent(agentName)}/vault/galaxy`, { headers })
      .then(r => { if (!r.ok) throw new Error(r.status === 403 ? 'This vault is private.' : `vault ${r.status}`); return r.json() })
      .then((d: GalaxyData) => { setGalaxy(d); setError('') })
      .catch(e => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentName])

  useEffect(() => { loadGalaxy() }, [loadGalaxy])

  const openNote = useCallback((path: string, title: string) => {
    if (!agentName) return
    fetch(`/api/agents/${encodeURIComponent(agentName)}/vault/file/${path}`, { headers })
      .then(r => (r.ok ? r.text() : Promise.reject()))
      .then(md => setNote({ title, md }))
      .catch(() => setNote({ title, md: '_Could not load this memory._' }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentName])

  /* Mount / update the 3D galaxy */
  useEffect(() => {
    if (!galaxy || !mountRef.current) return
    let disposed = false
    ;(async () => {
      const [{ default: ForceGraph3D }, THREE] = await Promise.all([
        import('3d-force-graph'), import('three'),
      ])
      if (disposed || !mountRef.current) return

      const { nodes, links } = buildGraph(galaxy)
      setCounts({ stars: nodes.filter(n => n.kind !== 'hub' && n.kind !== 'agent').length, links: links.length })
      setFamilies(Array.from(new Set(nodes.filter(n => n.kind === 'hub').map(n => n.family))))

      // Shared radial-gradient glow texture — every memory renders as a star sprite
      const cv = document.createElement('canvas'); cv.width = cv.height = 64
      const cx = cv.getContext('2d')!
      const grad = cx.createRadialGradient(32, 32, 0, 32, 32, 32)
      grad.addColorStop(0, 'rgba(255,255,255,1)')
      grad.addColorStop(0.25, 'rgba(255,255,255,0.65)')
      grad.addColorStop(0.6, 'rgba(255,255,255,0.15)')
      grad.addColorStop(1, 'rgba(255,255,255,0)')
      cx.fillStyle = grad; cx.fillRect(0, 0, 64, 64)
      const starTex = new THREE.CanvasTexture(cv)

      if (!graphRef.current) {
        graphRef.current = new ForceGraph3D(mountRef.current!)
          // Fully transparent — three-render-objects creates the WebGLRenderer
          // with alpha:true and honors this color's alpha channel, so the
          // mount container's own glass (rgba + backdrop-filter) CSS shows
          // through instead of a solid backdrop.
          .backgroundColor('rgba(4,3,13,0)')
          .showNavInfo(false)
          .nodeLabel((n: any) => `<div style="font-family:monospace;font-size:11px;color:#dfe6ff;background:rgba(5,5,16,.9);padding:4px 8px;border-radius:6px;border:1px solid rgba(255,255,255,.12)">${n.name}</div>`)
          .nodeThreeObject((n: any) => {
            const mat = new THREE.SpriteMaterial({
              map: starTex, color: n.color, transparent: true,
              opacity: n.kind === 'nebula' ? 0.35 : 0.9,
              depthWrite: false, blending: THREE.AdditiveBlending,
            })
            const sprite = new THREE.Sprite(mat)
            const s = 4 + n.val * 1.6
            sprite.scale.set(s, s, 1)
            return sprite
          })
          .linkColor((l: any) => l.color)
          .linkOpacity(0.18)
          .linkWidth((l: any) => l.width)
          .onNodeClick((n: any) => {
            const g = graphRef.current
            const dist = 60
            const ratio = 1 + dist / Math.hypot(n.x || 1, n.y || 1, n.z || 1)
            g.cameraPosition({ x: (n.x || 1) * ratio, y: (n.y || 1) * ratio, z: (n.z || 1) * ratio }, n, 1200)
            if (n.path) openNote(n.path, n.name)
          })
        // Slow cinematic auto-orbit until the user grabs the controls
        const controls = graphRef.current.controls()
        controls.autoRotate = true
        controls.autoRotateSpeed = 0.55
        const stopSpin = () => { controls.autoRotate = false }
        mountRef.current!.addEventListener('pointerdown', stopSpin, { once: true })
        mountRef.current!.addEventListener('wheel', stopSpin, { once: true })
      }
      graphRef.current
        .width(mountRef.current!.clientWidth)
        .height(mountRef.current!.clientHeight)
        .graphData({ nodes, links })
    })()
    return () => { disposed = true }
  }, [galaxy, openNote])

  /* Family filter */
  useEffect(() => {
    const g = graphRef.current
    if (!g) return
    g.nodeVisibility((n: any) => n.kind === 'agent' || !hidden.has(n.family))
    g.linkVisibility((l: any) => {
      const fam = (typeof l.target === 'object' ? l.target.family : '') || ''
      return !hidden.has(fam)
    })
  }, [hidden])

  /* Resize + teardown */
  useEffect(() => {
    const onResize = () => {
      if (graphRef.current && mountRef.current)
        graphRef.current.width(mountRef.current.clientWidth).height(mountRef.current.clientHeight)
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      graphRef.current?._destructor?.()
      graphRef.current = null
    }
  }, [])

  /* Search the vault, fly to a result */
  useEffect(() => {
    if (!query || !agentName) { setResults([]); return }
    const t = setTimeout(() => {
      fetch(`/api/agents/${encodeURIComponent(agentName)}/vault/search?q=${encodeURIComponent(query)}`, { headers })
        .then(r => (r.ok ? r.json() : { results: [] }))
        .then(d => setResults((d.results || []).slice(0, 8)))
        .catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, agentName])

  const flyTo = (path: string, title: string) => {
    const g = graphRef.current
    if (!g) return
    const node = g.graphData().nodes.find((n: any) => n.id === path)
    setResults([]); setQuery('')
    if (node) {
      const ratio = 1 + 60 / Math.hypot(node.x || 1, node.y || 1, node.z || 1)
      g.cameraPosition({ x: node.x * ratio, y: node.y * ratio, z: node.z * ratio }, node, 1200)
    }
    openNote(path, title)
  }

  const sync = async () => {
    if (!agentName || !apiKey) return
    setSyncing(true)
    await fetch(`/api/agents/${encodeURIComponent(agentName)}/vault/sync`, { method: 'POST', headers }).catch(() => {})
    setSyncing(false)
    loadGalaxy()
  }

  if (needsAgent) return (
    <div style={{ padding: 80, textAlign: 'center', color: 'var(--muted)' }}>
      <Brain size={36} style={{ opacity: 0.5, marginBottom: 14 }} />
      <p style={{ fontSize: 15 }}>Connect your agent in the <a href="/dashboard" style={{ color: 'var(--cyan, #00f5ff)' }}>Dashboard</a> to enter its Memory Galaxy,<br />or open another agent's public vault via <code>/vault?agent=Name</code>.</p>
    </div>
  )
  if (error) return (
    <div style={{ padding: 80, textAlign: 'center', color: '#ef4444' }}>
      <p>{error}</p><button className="btn" onClick={loadGalaxy}>Retry</button>
    </div>
  )

  return (
    <div style={{ position: 'relative', height: 'calc(100vh - 175px)', minHeight: 480, borderRadius: 12, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)', background: 'radial-gradient(circle at 25% 15%, rgba(138,75,255,0.14), rgba(5,8,16,0.55) 55%)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)' }}>
      {/* HUD header */}
      <div style={{ position: 'absolute', top: 12, left: 14, zIndex: 10, pointerEvents: 'none' }}>
        <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#b9a8ff', letterSpacing: 1 }}>
          ✦ MEMORY GALAXY {agentName && <span style={{ color: '#fff' }}>— {agentName}</span>}
        </div>
        <div style={{ fontFamily: 'monospace', fontSize: 11, color: 'rgba(255,255,255,.55)', marginTop: 2 }}>
          {counts.stars} stars · {counts.links} links
        </div>
        <div style={{ fontFamily: 'monospace', fontSize: 10, color: 'rgba(255,255,255,.32)', marginTop: 2 }}>
          drag to orbit · scroll to zoom · click a star to read the memory · brighter = more recent
        </div>
      </div>

      {/* Controls: search + sync */}
      <div style={{ position: 'absolute', top: 12, right: 14, zIndex: 10, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <div style={{ position: 'relative' }}>
          <Search size={12} style={{ position: 'absolute', left: 9, top: 9, color: 'rgba(255,255,255,.4)' }} />
          <input
            value={query} onChange={e => setQuery(e.target.value)} placeholder="Search memories…"
            style={{ width: 210, padding: '6px 10px 6px 28px', fontSize: 12, borderRadius: 8, border: '1px solid rgba(255,255,255,.12)', background: 'rgba(8,8,20,.85)', color: '#fff', outline: 'none' }}
          />
          {results.length > 0 && (
            <div style={{ position: 'absolute', top: 34, right: 0, width: 280, background: 'rgba(8,8,20,.97)', border: '1px solid rgba(255,255,255,.1)', borderRadius: 10, overflow: 'hidden' }}>
              {results.map(r => (
                <div key={r.path} onClick={() => flyTo(r.path, r.title)}
                  style={{ padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid rgba(255,255,255,.05)' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'rgba(139,92,246,.15)')}
                  onMouseLeave={e => (e.currentTarget.style.background = '')}>
                  <div style={{ fontSize: 12, color: '#fff' }}>{r.title}</div>
                  {r.snippet && <div style={{ fontSize: 10, color: 'rgba(255,255,255,.45)', marginTop: 2 }} dangerouslySetInnerHTML={{ __html: r.snippet.replace(/\*\*(.*?)\*\*/g, '<b style="color:#b9a8ff">$1</b>') }} />}
                </div>
              ))}
            </div>
          )}
        </div>
        {apiKey && agentName && (
          <button className="btn btn-sm" onClick={sync} disabled={syncing} title="Re-sync the vault from everything this agent has done on Vantage"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <RefreshCw size={12} className={syncing ? 'spin' : ''} /> {syncing ? 'Syncing…' : 'Sync'}
          </button>
        )}
      </div>

      {/* Family filter chips */}
      {families.length > 0 && (
        <div style={{ position: 'absolute', bottom: 12, left: 14, zIndex: 10, display: 'flex', gap: 6, flexWrap: 'wrap', maxWidth: '55%' }}>
          {families.map(f => (
            <button key={f} onClick={() => setHidden(h => { const n = new Set(h); n.has(f) ? n.delete(f) : n.add(f); return n })}
              style={{
                padding: '3px 10px', borderRadius: 12, fontSize: 10, cursor: 'pointer', fontFamily: 'monospace',
                border: `1px solid ${FAMILY_COLOR[f] || '#888'}${hidden.has(f) ? '33' : '88'}`,
                background: hidden.has(f) ? 'transparent' : `${FAMILY_COLOR[f] || '#888'}22`,
                color: hidden.has(f) ? 'rgba(255,255,255,.3)' : (FAMILY_COLOR[f] || '#ccc'),
              }}>
              {f}
            </button>
          ))}
        </div>
      )}

      {/* 3D mount */}
      <div ref={mountRef} style={{ position: 'absolute', inset: 0 }} />

      {!galaxy && !error && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,.4)', fontFamily: 'monospace', fontSize: 12 }}>
          ✦ charting the galaxy…
        </div>
      )}
      {galaxy && counts.stars === 0 && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,.45)', pointerEvents: 'none' }}>
          <Brain size={32} style={{ opacity: 0.5, marginBottom: 10 }} />
          <p style={{ fontSize: 13 }}>This galaxy is empty — press <b>Sync</b> to import everything the agent has done on Vantage.</p>
        </div>
      )}

      {/* Memory note panel (click a star) */}
      {note && (
        <div style={{ position: 'absolute', bottom: 12, right: 14, zIndex: 11, width: 'min(420px, 90%)', maxHeight: '55%', overflowY: 'auto', background: 'rgba(6,6,16,.96)', border: '1px solid rgba(185,168,255,.25)', borderRadius: 12, padding: '14px 16px', backdropFilter: 'blur(14px)' }}>
          <button onClick={() => setNote(null)} style={{ position: 'absolute', top: 8, right: 10, background: 'none', border: 'none', color: 'rgba(255,255,255,.5)', cursor: 'pointer' }}><X size={14} /></button>
          <div style={{ fontFamily: 'monospace', fontSize: 10, color: '#b9a8ff', letterSpacing: 1, marginBottom: 6 }}>✦ MEMORY</div>
          <div style={{ fontSize: 13, lineHeight: 1.6, color: 'rgba(255,255,255,.85)' }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{note.md.replace(/^---[\s\S]*?---/, '')}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}
