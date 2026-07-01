import React, { useEffect, useState, useMemo } from 'react'

interface GalaxyNode {
  id: string; label: string; type: string; group: string; strength: number; color: string
  glow_intensity?: number; pulse_rate?: number; size?: number; conviction?: number
  source_daemon?: string; last_updated?: string; metadata?: any
}
interface GalaxyEdge { source: string; target: string; type?: string; strength: number; last_seen?: string }
interface GalaxyData {
  nodes: GalaxyNode[]; edges: GalaxyEdge[]
  galaxies?: Record<string, any>
  high_value_insights_current?: string[]
  high_value_clusters_insights?: { insight: string }[]
}

/** Evenly place each galaxy/group around an ellipse — works for any group set,
 *  so both the live endpoint's named nebulae and the seed file's raw groups cluster. */
function groupCenters(groups: string[], w: number, h: number) {
  const centers: Record<string, { x: number; y: number }> = {}
  const n = groups.length || 1
  groups.forEach((g, i) => {
    if (n === 1) { centers[g] = { x: w / 2, y: h / 2 }; return }
    const ang = (i / n) * Math.PI * 2 - Math.PI / 2
    centers[g] = { x: w / 2 + Math.cos(ang) * w * 0.30, y: h / 2 + Math.sin(ang) * h * 0.32 }
  })
  return centers
}

function simulate(nodes: GalaxyNode[], edges: GalaxyEdge[], w: number, h: number, centers: Record<string, {x:number,y:number}>) {
  const simNodes = nodes.map(n => ({ ...n, x: Math.random() * w, y: Math.random() * h, vx: 0, vy: 0 }))
  const nodeMap = new Map(simNodes.map(n => [n.id, n]))
  for (let iter = 0; iter < 70; iter++) {
    for (let i = 0; i < simNodes.length; i++) { for (let j = i + 1; j < simNodes.length; j++) { const dx = simNodes[j].x - simNodes[i].x; const dy = simNodes[j].y - simNodes[i].y; const dist = Math.sqrt(dx * dx + dy * dy) || 1; const force = 320 / (dist * dist); simNodes[i].vx -= dx / dist * force; simNodes[i].vy -= dy / dist * force; simNodes[j].vx += dx / dist * force; simNodes[j].vy += dy / dist * force } }
    for (const e of edges) { const src = nodeMap.get(e.source); const tgt = nodeMap.get(e.target); if (!src || !tgt) continue; const dx = tgt.x - src.x; const dy = tgt.y - src.y; const dist = Math.sqrt(dx * dx + dy * dy) || 1; const force = (dist - 80) * 0.004 * (e.strength || 0.3); src.vx += dx / dist * force; src.vy += dy / dist * force; tgt.vx -= dx / dist * force; tgt.vy -= dy / dist * force }
    for (const n of simNodes) { n.x += n.vx * 0.25; n.y += n.vy * 0.25; const gc = centers[n.group]; if (gc) { n.x += (gc.x - n.x) * 0.006; n.y += (gc.y - n.y) * 0.006 } n.vx *= 0.88; n.vy *= 0.88; n.x = Math.max(24, Math.min(w - 24, n.x)); n.y = Math.max(28, Math.min(h - 24, n.y)) }
  }
  return simNodes
}

const shortName = (s: string) => s.replace(' Nebula', '').replace(' Cluster', '').replace(' Constellation', '').replace(' Cloud', '').replace(' Nexus', '').replace(' Archive', '').replace(' Perimeter', '')
const nodeRadius = (n: GalaxyNode) => n.size ? Math.max(3, Math.min(22, 3 + n.size * 0.45)) : Math.max(3, (n.strength || 0.4) * 7)
const pulseDur = (n: GalaxyNode) => n.pulse_rate ? Math.max(1, Math.min(8, 1 / n.pulse_rate)) : (2 + (n.strength || 0.5) * 3)

export default function NeuralVault() {
  const [data, setData] = useState<GalaxyData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedNode, setSelectedNode] = useState<any>(null)
  const [selectedGalaxy, setSelectedGalaxy] = useState<string>('all')
  const [dims, setDims] = useState({ w: 900, h: 600 })

  useEffect(() => {
    const m = () => setDims({ w: window.innerWidth - 250, h: window.innerHeight - 190 })
    m(); window.addEventListener('resize', m); return () => window.removeEventListener('resize', m)
  }, [])

  const load = () => {
    // Live Memory Galaxy from the vault endpoint; fall back to the committed
    // seed snapshot only if the API is unreachable (offline / first boot).
    fetch('/api/intel/memory/graph?limit=120')
      .then(r => { if (!r.ok) throw new Error('api ' + r.status); return r.json() })
      .then(d => { if (!d.nodes || !d.nodes.length) throw new Error('empty'); setData(d); setLoading(false); setError('') })
      .catch(() => fetch('/data/memory_galaxy.json')
        .then(r => r.json()).then(d => { setData(d); setLoading(false); setError('') })
        .catch(e => { setLoading(false); setError(e.message) }))
  }

  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [])

  // Galaxies: prefer the API's taxonomy; otherwise derive from node groups so
  // the richer seed file (raw groups) still gets cluster buttons + a legend.
  const galaxyMeta = useMemo(() => {
    if (!data) return {} as Record<string, { node_count: number; color: string }>
    if (data.galaxies && Object.keys(data.galaxies).length) {
      const out: Record<string, { node_count: number; color: string }> = {}
      for (const [name, g] of Object.entries<any>(data.galaxies)) {
        const members = data.nodes.filter(n => n.group === name)
        out[name] = { node_count: g.node_count ?? members.length, color: repColor(members) }
      }
      return out
    }
    const groups = Array.from(new Set(data.nodes.map(n => n.group)))
    const out: Record<string, { node_count: number; color: string }> = {}
    for (const g of groups) { const members = data.nodes.filter(n => n.group === g); out[g] = { node_count: members.length, color: repColor(members) } }
    return out
  }, [data])

  const centers = useMemo(() => groupCenters(Object.keys(galaxyMeta), dims.w, dims.h), [galaxyMeta, dims])

  const simNodes = useMemo(() => {
    if (!data) return []
    const filtered = selectedGalaxy === 'all' ? data.nodes : data.nodes.filter(n => n.group === selectedGalaxy)
    const ids = new Set(filtered.map(n => n.id))
    return simulate(filtered, data.edges.filter(e => ids.has(e.source) && ids.has(e.target)), dims.w, dims.h, centers)
  }, [data, selectedGalaxy, dims, centers])

  const posById = useMemo(() => new Map(simNodes.map(n => [n.id, n])), [simNodes])

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><div className="vf-spinner" /></div>
  if (error) return <div style={{ padding: 40, textAlign: 'center', color: '#ef4444' }}><p>{error}</p><button className="btn" onClick={load}>Retry</button></div>
  if (!data) return null

  const insights = data.high_value_insights_current || (data.high_value_clusters_insights || []).map(c => c.insight)
  const galaxyNames = Object.keys(galaxyMeta)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <h1 className="page-title" style={{ margin: 0 }}>Memory Galaxy · {data.nodes.length} nodes · {data.edges.length} edges</h1>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button className={`btn btn-sm ${selectedGalaxy === 'all' ? 'btn-purple' : ''}`} onClick={() => setSelectedGalaxy('all')}>All</button>
          {galaxyNames.map(g => (
            <button key={g} className={`btn btn-sm ${selectedGalaxy === g ? 'btn-purple' : ''}`} onClick={() => setSelectedGalaxy(g)} style={{ fontSize: 10, padding: '3px 8px' }}>{shortName(g)}</button>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        {insights.slice(0, 3).map((ins: string, i: number) => (
          <span key={i} style={{ fontSize: 10, color: 'var(--muted)', background: 'rgba(255,255,255,0.03)', padding: '3px 10px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.05)' }}>{ins}</span>
        ))}
      </div>
      <svg width={dims.w} height={dims.h} style={{ background: 'radial-gradient(ellipse at center, rgba(20,10,40,0.85) 0%, rgba(0,0,0,0.96) 100%)', borderRadius: 12 }}>
        <defs>
          <filter id="mg-glow"><feGaussianBlur stdDeviation="3" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <filter id="mg-glow-strong"><feGaussianBlur stdDeviation="6" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        {/* Luminous energy lines — colored by source, thickness/opacity ∝ strength,
            animated dash offset simulates particle flow from source → target. */}
        {data.edges.map((e: GalaxyEdge, i: number) => {
          const src = posById.get(e.source); const tgt = posById.get(e.target)
          if (!src || !tgt) return null
          const s = e.strength || 0.3
          const flow = s > 0.45
          return (
            <line key={i} x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y}
              stroke={src.color || '#8888ff'} strokeOpacity={0.08 + s * 0.35}
              strokeWidth={Math.max(0.4, s * 2.2)} strokeLinecap="round"
              strokeDasharray={flow ? '3 7' : undefined}>
              {flow && <animate attributeName="stroke-dashoffset" from="0" to="-20" dur={`${Math.max(0.6, 2 - s)}s`} repeatCount="indefinite" />}
            </line>
          )
        })}
        {simNodes.map((n: any) => {
          const r = nodeRadius(n); const glow = n.glow_intensity ?? n.strength ?? 0.5
          return (
            <g key={n.id} onClick={() => setSelectedNode(n)} style={{ cursor: 'pointer' }}>
              <circle cx={n.x} cy={n.y} r={r * 1.9} fill={n.color} opacity={0.10 + glow * 0.22} filter="url(#mg-glow-strong)">
                <animate attributeName="r" values={`${r * 1.7};${r * 2.1};${r * 1.7}`} dur={`${pulseDur(n)}s`} repeatCount="indefinite" />
                <animate attributeName="opacity" values={`${0.08 + glow * 0.18};${0.14 + glow * 0.26};${0.08 + glow * 0.18}`} dur={`${pulseDur(n)}s`} repeatCount="indefinite" />
              </circle>
              <circle cx={n.x} cy={n.y} r={r} fill={n.color} opacity={0.85} filter="url(#mg-glow)" />
              <text x={n.x} y={n.y - r - 3} textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="7" fontFamily="monospace">{n.label.length > 18 ? n.label.slice(0, 16) + '..' : n.label}</text>
            </g>
          )
        })}
      </svg>
      {selectedNode && (
        <div style={{ position: 'fixed', bottom: 24, right: 24, zIndex: 100, background: 'rgba(8,8,20,0.97)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 16, minWidth: 260, maxWidth: 340, backdropFilter: 'blur(12px)' }}>
          <button onClick={() => setSelectedNode(null)} style={{ position: 'absolute', top: 6, right: 10, background: 'none', border: 'none', color: 'var(--muted)', fontSize: 16, cursor: 'pointer' }}>×</button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}><span style={{ width: 12, height: 12, borderRadius: '50%', background: selectedNode.color, boxShadow: `0 0 12px ${selectedNode.color}` }} /><span style={{ fontFamily: 'Orbitron', fontSize: 14, fontWeight: 600, color: '#fff' }}>{selectedNode.label}</span></div>
          <Row k="Galaxy" v={shortName(selectedNode.group)} />
          <Row k="Type" v={selectedNode.type} />
          {selectedNode.source_daemon && <Row k="Daemon" v={selectedNode.source_daemon} />}
          {selectedNode.strength != null && <Row k="Strength" v={`${(selectedNode.strength * 100).toFixed(0)}%`} />}
          {selectedNode.conviction != null && <Row k="Conviction" v={`${(selectedNode.conviction * 100).toFixed(0)}%`} />}
          {selectedNode.metadata?.price_usd != null && <Row k="Price" v={`$${Number(selectedNode.metadata.price_usd).toLocaleString()}`} />}
          {selectedNode.metadata?.insight && <div style={{ fontSize: 10, color: '#aab', marginTop: 8, lineHeight: 1.4 }}>{selectedNode.metadata.insight}</div>}
        </div>
      )}
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8, justifyContent: 'center' }}>
        {Object.entries(galaxyMeta).map(([name, g]) => (
          <div key={name} style={{ fontSize: 9, color: 'var(--muted)', cursor: 'pointer' }} onClick={() => setSelectedGalaxy(name)}>
            <span style={{ fontWeight: 600, color: g.color }}>{shortName(name)}</span>
            <span> {g.node_count} nodes</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', fontSize: 11 }}><span style={{ color: 'var(--muted)' }}>{k}</span><span style={{ color: '#ccc' }}>{v}</span></div>
}

/** Most common node color in a group — a schema-agnostic legend swatch. */
function repColor(members: GalaxyNode[]): string {
  const counts: Record<string, number> = {}
  for (const m of members) counts[m.color] = (counts[m.color] || 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] || '#06b6d4'
}
