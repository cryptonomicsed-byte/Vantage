import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { Network, RefreshCw, Search, ZoomIn, ZoomOut } from 'lucide-react'

interface Snippet {
  id: number; subject: string; predicate: string; object: string;
  confidence: number; tags: string; created_at: string; agent_name: string;
}
interface GNode {
  id: string; label: string; type: 'subject' | 'object' | 'both';
  count: number; avgConfidence: number;
  x: number; y: number; vx: number; vy: number;
}
interface GEdge {
  source: string; target: string; predicate: string; confidence: number;
}
interface Tooltip {
  x: number; y: number;
  node: GNode | null;
  edge: (GEdge & { sourceLabel: string; targetLabel: string }) | null;
}

const NODE_COLORS = {
  subject: '#8a4bff',
  object: '#00f5ff',
  both: '#ff2d78',
}

const KG_STYLE = `
@keyframes kg-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
`

function buildGraph(snippets: Snippet[]): { nodes: GNode[]; edges: GEdge[] } {
  const nodeMap = new Map<string, { type: 'subject' | 'object' | 'both'; count: number; sumConf: number }>()
  const edgeMap = new Map<string, GEdge>()

  const upsert = (key: string, role: 'subject' | 'object', conf: number) => {
    const existing = nodeMap.get(key)
    if (!existing) {
      nodeMap.set(key, { type: role, count: 1, sumConf: conf })
    } else {
      const newType = existing.type === role ? role : 'both'
      nodeMap.set(key, { type: newType, count: existing.count + 1, sumConf: existing.sumConf + conf })
    }
  }

  for (const s of snippets) {
    upsert(s.subject, 'subject', s.confidence)
    upsert(s.object, 'object', s.confidence)
    const eKey = `${s.subject}→${s.predicate}→${s.object}`
    if (!edgeMap.has(eKey)) {
      edgeMap.set(eKey, { source: s.subject, target: s.object, predicate: s.predicate, confidence: s.confidence })
    }
  }

  const cx = 600, cy = 400
  const nodes: GNode[] = []
  let i = 0
  for (const [label, data] of nodeMap) {
    const angle = (i / nodeMap.size) * Math.PI * 2
    const r = 200 + Math.random() * 100
    nodes.push({
      id: label, label,
      type: data.type,
      count: data.count,
      avgConfidence: data.sumConf / data.count,
      x: cx + Math.cos(angle) * r + (Math.random() - 0.5) * 60,
      y: cy + Math.sin(angle) * r + (Math.random() - 0.5) * 60,
      vx: 0, vy: 0,
    })
    i++
  }

  return { nodes, edges: Array.from(edgeMap.values()) }
}

export default function KnowledgeExplorer() {
  const [snippets, setSnippets] = useState<Snippet[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [agentFilter, setAgentFilter] = useState('all')
  const [minConf, setMinConf] = useState(0)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [tooltip, setTooltip] = useState<Tooltip | null>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })
  const rafRef = useRef<number | null>(null)
  const nodesRef = useRef<GNode[]>([])
  const edgesRef = useRef<GEdge[]>([])
  const [renderTick, setRenderTick] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/agents/knowledge?limit=200')
      if (r.ok) setSnippets(await r.json())
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const filteredSnippets = useMemo(() => {
    return snippets.filter(s => {
      if (agentFilter !== 'all' && s.agent_name !== agentFilter) return false
      if (s.confidence < minConf) return false
      if (search) {
        const q = search.toLowerCase()
        return s.subject.toLowerCase().includes(q) || s.predicate.toLowerCase().includes(q) || s.object.toLowerCase().includes(q)
      }
      return true
    })
  }, [snippets, agentFilter, minConf, search])

  const agentNames = useMemo(() => ['all', ...Array.from(new Set(snippets.map(s => s.agent_name)))], [snippets])

  useEffect(() => {
    if (!filteredSnippets.length) { nodesRef.current = []; edgesRef.current = []; return }
    const { nodes, edges } = buildGraph(filteredSnippets)
    nodesRef.current = nodes
    edgesRef.current = edges
    setRenderTick(t => t + 1)
  }, [filteredSnippets])

  // Force simulation
  useEffect(() => {
    if (!nodesRef.current.length) return
    let tick = 0
    const simulate = () => {
      const nodes = nodesRef.current
      const edges = edgesRef.current
      const cx = 600, cy = 400
      const k_rep = 4000, k_spring = 0.04, rest = 120, k_center = 0.001, damp = 0.88

      for (let i = 0; i < nodes.length; i++) {
        let fx = 0, fy = 0
        for (let j = 0; j < nodes.length; j++) {
          if (i === j) continue
          const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y
          const d2 = Math.max(dx * dx + dy * dy, 100)
          const d = Math.sqrt(d2)
          fx += (dx / d) * k_rep / d2
          fy += (dy / d) * k_rep / d2
        }
        for (const e of edges) {
          const other = e.source === nodes[i].id ? nodes.find(n => n.id === e.target) : e.target === nodes[i].id ? nodes.find(n => n.id === e.source) : null
          if (!other) continue
          const dx = nodes[i].x - other.x, dy = nodes[i].y - other.y
          const d = Math.sqrt(dx * dx + dy * dy) || 1
          const force = k_spring * (d - rest)
          fx -= (dx / d) * force
          fy -= (dy / d) * force
        }
        fx += (cx - nodes[i].x) * k_center
        fy += (cy - nodes[i].y) * k_center
        nodes[i].vx = (nodes[i].vx + fx) * damp
        nodes[i].vy = (nodes[i].vy + fy) * damp
        nodes[i].x += nodes[i].vx
        nodes[i].y += nodes[i].vy
      }
      tick++
      if (tick % 6 === 0) setRenderTick(t => t + 1)
      rafRef.current = requestAnimationFrame(simulate)
    }
    rafRef.current = requestAnimationFrame(simulate)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [filteredSnippets.length])

  const nodeRadius = (n: GNode) => Math.max(8, Math.min(22, 6 + n.count * 2.5))

  const visNodes = nodesRef.current.filter(n => !selectedNode || n.id === selectedNode || edgesRef.current.some(e => (e.source === selectedNode && e.target === n.id) || (e.target === selectedNode && e.source === n.id)))
  const visNodeIds = new Set(visNodes.map(n => n.id))
  const visEdges = edgesRef.current.filter(e => visNodeIds.has(e.source) && visNodeIds.has(e.target))

  const nodeById = (id: string) => nodesRef.current.find(n => n.id === id)

  const handleSvgMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (dragging) {
      const dx = e.clientX - dragStart.x, dy = e.clientY - dragStart.y
      setPan(p => ({ x: p.x + dx, y: p.y + dy }))
      setDragStart({ x: e.clientX, y: e.clientY })
      return
    }
    const rect = (e.target as SVGElement).closest('svg')!.getBoundingClientRect()
    const svgX = (e.clientX - rect.left - pan.x) / zoom
    const svgY = (e.clientY - rect.top - pan.y) / zoom
    let closest: GNode | null = null, minD = 30
    for (const n of nodesRef.current) {
      const d = Math.hypot(n.x - svgX, n.y - svgY)
      if (d < minD && d < nodeRadius(n) + 12) { minD = d; closest = n }
    }
    setTooltip(closest ? { x: e.clientX + 14, y: e.clientY - 10, node: closest, edge: null } : null)
  }

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    setZoom(z => Math.max(0.3, Math.min(3, z * (e.deltaY > 0 ? 0.9 : 1.1))))
  }

  if (loading) return (
    <div className="kg-empty">
      <span style={{ animation: 'kg-spin 1s linear infinite', display: 'inline-block' }}><Network size={32} /></span>
      <span>Indexing knowledge graph…</span>
    </div>
  )

  return (
    <div className="kg-root">
      <style>{KG_STYLE}</style>

      {/* Controls */}
      <div className="kg-controls">
        <Search size={13} style={{ color: 'var(--muted)' }} />
        <input className="kg-search" placeholder="Search nodes…" value={search} onChange={e => setSearch(e.target.value)} />
        <select className="kg-select" value={agentFilter} onChange={e => setAgentFilter(e.target.value)}>
          {agentNames.map(n => <option key={n} value={n}>{n === 'all' ? 'All agents' : n}</option>)}
        </select>
        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 5 }}>
          Confidence ≥
          <input type="range" min={0} max={2} step={0.1} value={minConf} onChange={e => setMinConf(parseFloat(e.target.value))} style={{ width: 80 }} />
          <span style={{ width: 24 }}>{minConf.toFixed(1)}</span>
        </label>
        {selectedNode && <button className="btn btn-ghost btn-sm" onClick={() => setSelectedNode(null)}>Clear filter</button>}
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} /></button>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.min(3, z * 1.2))}><ZoomIn size={12} /></button>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.max(0.3, z * 0.8))}><ZoomOut size={12} /></button>
        <span className="kg-badge">{filteredSnippets.length} triples / {nodesRef.current.length} nodes</span>
      </div>

      {/* SVG */}
      <div className="kg-svg-wrap">
        {!filteredSnippets.length ? (
          <div className="kg-empty">
            <Network size={40} style={{ opacity: 0.3 }} />
            <span>No knowledge snippets match your filters.</span>
            <span style={{ fontSize: 12 }}>Agents add snippets via POST /api/agents/knowledge</span>
          </div>
        ) : (
          <svg
            className="kg-svg"
            onMouseMove={handleSvgMouseMove}
            onMouseLeave={() => { setTooltip(null); if (!dragging) {} }}
            onMouseDown={e => { setDragging(true); setDragStart({ x: e.clientX, y: e.clientY }) }}
            onMouseUp={() => setDragging(false)}
            onWheel={handleWheel}
            onClick={e => {
              const rect = (e.target as SVGElement).closest('svg')!.getBoundingClientRect()
              const svgX = (e.clientX - rect.left - pan.x) / zoom
              const svgY = (e.clientY - rect.top - pan.y) / zoom
              let clicked: GNode | null = null
              for (const n of nodesRef.current) {
                if (Math.hypot(n.x - svgX, n.y - svgY) < nodeRadius(n) + 4) { clicked = n; break }
              }
              setSelectedNode(clicked ? (selectedNode === clicked.id ? null : clicked.id) : null)
            }}
          >
            <defs>
              <marker id="arrowhead" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">
                <polygon points="0 0, 6 2, 0 4" fill="rgba(138,75,255,0.5)" />
              </marker>
            </defs>
            <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
              {/* Edges */}
              {visEdges.map((e, i) => {
                const src = nodeById(e.source), tgt = nodeById(e.target)
                if (!src || !tgt) return null
                const mx = (src.x + tgt.x) / 2, my = (src.y + tgt.y) / 2
                const dx = tgt.x - src.x, dy = tgt.y - src.y
                const d = Math.hypot(dx, dy) || 1
                const r = nodeRadius(tgt)
                const tx = tgt.x - (dx / d) * (r + 6), ty = tgt.y - (dy / d) * (r + 6)
                return (
                  <g key={i}>
                    <line
                      x1={src.x} y1={src.y} x2={tx} y2={ty}
                      stroke={`rgba(138,75,255,${0.15 + e.confidence * 0.15})`}
                      strokeWidth={0.5 + e.confidence * 0.8}
                      markerEnd="url(#arrowhead)"
                    />
                    {zoom > 0.7 && (
                      <text x={mx} y={my} textAnchor="middle" fontSize={8} fill="rgba(107,114,128,0.8)"
                        transform={`rotate(${Math.atan2(dy, dx) * 180 / Math.PI},${mx},${my})`}>
                        {e.predicate.slice(0, 20)}
                      </text>
                    )}
                  </g>
                )
              })}
              {/* Nodes */}
              {visNodes.map(n => {
                const r = nodeRadius(n)
                const col = NODE_COLORS[n.type]
                const isSelected = selectedNode === n.id
                return (
                  <g key={n.id} style={{ cursor: 'pointer' }}>
                    <circle
                      cx={n.x} cy={n.y} r={r}
                      fill={col} fillOpacity={0.25}
                      stroke={col} strokeWidth={isSelected ? 2.5 : 1.5}
                      filter={isSelected ? `drop-shadow(0 0 6px ${col})` : undefined}
                    />
                    {zoom > 0.5 && (
                      <text x={n.x} y={n.y + r + 10} textAnchor="middle" fontSize={9} fill="rgba(232,232,248,0.8)">
                        {n.label.slice(0, 18)}{n.label.length > 18 ? '…' : ''}
                      </text>
                    )}
                  </g>
                )
              })}
            </g>
          </svg>
        )}

        {/* Legend */}
        <div className="kg-legend">
          {Object.entries(NODE_COLORS).map(([type, color]) => (
            <div key={type} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
              <span>{type}</span>
            </div>
          ))}
          <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>Scroll to zoom · Drag to pan · Click node to filter</div>
        </div>
      </div>

      {/* Tooltip */}
      {tooltip?.node && (
        <div className="kg-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
          <div className="kg-tooltip-title">{tooltip.node.label}</div>
          <div className="kg-tooltip-row">Type: <span style={{ color: NODE_COLORS[tooltip.node.type] }}>{tooltip.node.type}</span></div>
          <div className="kg-tooltip-row">Appears in: {tooltip.node.count} triple{tooltip.node.count !== 1 ? 's' : ''}</div>
          <div className="kg-tooltip-row">Avg confidence: {tooltip.node.avgConfidence.toFixed(2)}</div>
        </div>
      )}
    </div>
  )
}
