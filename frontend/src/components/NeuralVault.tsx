import React, { useEffect, useState, useMemo } from 'react'

interface GalaxyNode { id: string; label: string; type: string; group: string; strength: number; color: string; glow_intensity: number; last_updated: string; conviction: number; source_daemon: string; confidence: number; metadata?: any }
interface GalaxyEdge { source: string; target: string; type: string; strength: number; last_seen: string }
interface Galaxy { name: string; node_count: number; description: string; key_insight: string }
interface GalaxyData { galaxy: { total_nodes: number; total_edges: number }; nodes: GalaxyNode[]; edges: GalaxyEdge[]; galaxies: Record<string, Galaxy>; high_value_insights_current: string[] }

function simulate(nodes: GalaxyNode[], edges: GalaxyEdge[], w: number, h: number) {
  const simNodes = nodes.map(n => ({ ...n, x: Math.random() * w, y: Math.random() * h, vx: 0, vy: 0 }))
  const nodeMap = new Map(simNodes.map(n => [n.id, n]))
  const galaxyCenters: Record<string, {x:number,y:number}> = { 'Trading Nebula': {x:w*0.28,y:h*0.35}, 'Security Cluster': {x:w*0.72,y:h*0.28}, 'Code Nebula': {x:w*0.72,y:h*0.7}, 'Agent Constellation': {x:w*0.28,y:h*0.7}, 'Memory Nebula': {x:w*0.5,y:h*0.5}, 'External Intel Cloud': {x:w*0.5,y:h*0.15} }
  for (let iter = 0; iter < 60; iter++) {
    for (let i = 0; i < simNodes.length; i++) { for (let j = i + 1; j < simNodes.length; j++) { const dx = simNodes[j].x - simNodes[i].x; const dy = simNodes[j].y - simNodes[i].y; const dist = Math.sqrt(dx * dx + dy * dy) || 1; const force = 300 / (dist * dist); simNodes[i].vx -= dx / dist * force; simNodes[i].vy -= dy / dist * force; simNodes[j].vx += dx / dist * force; simNodes[j].vy += dy / dist * force } }
    for (const e of edges) { const src = nodeMap.get(e.source); const tgt = nodeMap.get(e.target); if (!src || !tgt) continue; const dx = tgt.x - src.x; const dy = tgt.y - src.y; const dist = Math.sqrt(dx * dx + dy * dy) || 1; const force = (dist - 80) * 0.004 * e.strength; src.vx += dx / dist * force; src.vy += dy / dist * force; tgt.vx -= dx / dist * force; tgt.vy -= dy / dist * force }
    for (const n of simNodes) { n.x += n.vx * 0.25; n.y += n.vy * 0.25; const gc = galaxyCenters[n.group]; if (gc) { n.x += (gc.x - n.x) * 0.002; n.y += (gc.y - n.y) * 0.002 } n.vx *= 0.88; n.vy *= 0.88; n.x = Math.max(20, Math.min(w - 20, n.x)); n.y = Math.max(20, Math.min(h - 20, n.y)) }
  }
  return simNodes
}

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
    // Live Memory Galaxy from the vault endpoint; fall back to the static
    // snapshot if the API is unreachable (offline / first boot).
    fetch('/api/intel/memory/graph?limit=120')
      .then(r => { if (!r.ok) throw new Error('api ' + r.status); return r.json() })
      .then(d => { setData(d); setLoading(false); setError('') })
      .catch(() => fetch('/data/memory_galaxy.json')
        .then(r => r.json()).then(d => { setData(d); setLoading(false); setError('') })
        .catch(e => { setLoading(false); setError(e.message) }))
  }

  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [])

  const simNodes = useMemo(() => {
    if (!data) return []
    const filtered = selectedGalaxy === 'all' ? data.nodes : data.nodes.filter(n => n.group === selectedGalaxy)
    return simulate(filtered, data.edges.filter(e => filtered.some(n => n.id === e.source) && filtered.some(n => n.id === e.target)), dims.w, dims.h)
  }, [data, selectedGalaxy, dims])

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><div className="vf-spinner" /></div>
  if (error) return <div style={{ padding: 40, textAlign: 'center', color: '#ef4444' }}><p>{error}</p><button className="btn" onClick={load}>Retry</button></div>
  if (!data) return null

  const galaxies = data.galaxies || {}
  const insights = data.high_value_insights_current || []

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <h1 className="page-title" style={{ margin: 0 }}>Memory Galaxy · {data.nodes.length} nodes · {data.edges.length} edges</h1>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className={`btn btn-sm ${selectedGalaxy === 'all' ? 'btn-purple' : ''}`} onClick={() => setSelectedGalaxy('all')}>All</button>
          {Object.keys(galaxies).map(g => (
            <button key={g} className={`btn btn-sm ${selectedGalaxy === g ? 'btn-purple' : ''}`} onClick={() => setSelectedGalaxy(g)} style={{ fontSize: 10, padding: '3px 8px' }}>{g.replace(' Nebula','').replace(' Cluster','').replace(' Constellation','').replace(' Cloud','')}</button>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        {insights.slice(0, 3).map((ins: string, i: number) => (
          <span key={i} style={{ fontSize: 10, color: 'var(--muted)', background: 'rgba(255,255,255,0.03)', padding: '3px 10px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.05)' }}>{ins}</span>
        ))}
      </div>
      <svg width={dims.w} height={dims.h} style={{ background: 'radial-gradient(ellipse at center, rgba(20,10,40,0.8) 0%, rgba(0,0,0,0.95) 100%)', borderRadius: 12 }}>
        <defs>
          <filter id="mg-glow"><feGaussianBlur stdDeviation="3" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <filter id="mg-glow-strong"><feGaussianBlur stdDeviation="6" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        {data.edges.map((e: GalaxyEdge, i: number) => {
          const src = simNodes.find(n => n.id === e.source); const tgt = simNodes.find(n => n.id === e.target)
          if (!src || !tgt) return null
          return <line key={i} x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y} stroke="rgba(255,255,255,0.03)" strokeWidth={Math.max(0.3, e.strength * 2)} />
        })}
        {simNodes.map((n: any) => (
          <g key={n.id} onClick={() => setSelectedNode(n)} style={{ cursor: 'pointer' }}>
            <circle cx={n.x} cy={n.y} r={Math.max(3, n.strength * 7)} fill={n.color} opacity={0.2} filter="url(#mg-glow-strong)">
              <animate attributeName="r" values={`${Math.max(3,n.strength*7)};${Math.max(4,n.strength*8)};${Math.max(3,n.strength*7)}`} dur={`${2 + Math.random() * 3}s`} repeatCount="indefinite" />
            </circle>
            <circle cx={n.x} cy={n.y} r={Math.max(2, n.strength * 4)} fill={n.color} opacity={0.7} filter="url(#mg-glow)" />
            <text x={n.x} y={n.y - Math.max(6, n.strength * 8)} textAnchor="middle" fill="rgba(255,255,255,0.4)" fontSize="7" fontFamily="monospace">{n.label.length > 18 ? n.label.slice(0, 16) + '..' : n.label}</text>
          </g>
        ))}
      </svg>
      {selectedNode && (
        <div style={{ position: 'fixed', bottom: 24, right: 24, zIndex: 100, background: 'rgba(8,8,20,0.97)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 16, minWidth: 260, maxWidth: 340, backdropFilter: 'blur(12px)' }}>
          <button onClick={() => setSelectedNode(null)} style={{ position: 'absolute', top: 6, right: 10, background: 'none', border: 'none', color: 'var(--muted)', fontSize: 16, cursor: 'pointer' }}>×</button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}><span style={{ width: 12, height: 12, borderRadius: '50%', background: selectedNode.color, boxShadow: `0 0 12px ${selectedNode.color}` }} /><span style={{ fontFamily: 'Orbitron', fontSize: 14, fontWeight: 600, color: '#fff' }}>{selectedNode.label}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', fontSize: 11 }}><span style={{ color: 'var(--muted)' }}>Group</span><span style={{ color: '#ccc' }}>{selectedNode.group}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', fontSize: 11 }}><span style={{ color: 'var(--muted)' }}>Type</span><span style={{ color: '#ccc' }}>{selectedNode.type}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', fontSize: 11 }}><span style={{ color: 'var(--muted)' }}>Daemon</span><span style={{ color: '#ccc' }}>{selectedNode.source_daemon}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', fontSize: 11 }}><span style={{ color: 'var(--muted)' }}>Strength</span><span style={{ color: '#ccc' }}>{(selectedNode.strength * 100).toFixed(0)}%</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', fontSize: 11 }}><span style={{ color: 'var(--muted)' }}>Conviction</span><span style={{ color: '#ccc' }}>{(selectedNode.conviction * 100).toFixed(0)}%</span></div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8, justifyContent: 'center' }}>
        {Object.entries(galaxies).map(([name, g]: [string, any]) => (
          <div key={name} style={{ fontSize: 9, color: 'var(--muted)', cursor: 'pointer' }} onClick={() => setSelectedGalaxy(name)}>
            <span style={{ fontWeight: 600, color: name.includes('Trading') ? '#f59e0b' : name.includes('Security') ? '#ef4444' : name.includes('Code') ? '#22c55e' : name.includes('Agent') ? '#a855f7' : name.includes('Memory') ? '#f97316' : '#06b6d4' }}>{name.replace(' Nebula','').replace(' Cluster','').replace(' Constellation','').replace(' Cloud','')}</span>
            <span> {g.node_count} nodes</span>
          </div>
        ))}
      </div>
    </div>
  )
}
