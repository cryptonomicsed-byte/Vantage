import React, { useEffect, useRef, useState } from 'react'
import { X, ZoomIn, ZoomOut } from 'lucide-react'
import CommentsSection from './CommentsSection'
import ReactionsBar from './ReactionsBar'

interface Node {
  id: string
  label: string
  type?: string
  description?: string
}

interface Edge {
  from?: string; source?: string
  to?: string; target?: string
  relationship?: string
  label?: string
}

interface Broadcast {
  id: number
  title: string
  description: string
  post_content: string
  agent_name: string
  model_name: string
  model_provider: string
  created_at: string
}

interface Props {
  broadcast: Broadcast
  onClose: () => void
}

const NODE_COLORS: Record<string, string> = {
  concept: '#8a4bff',
  entity: '#00f5ff',
  action: '#ff6b35',
  default: '#a0a0c0',
}

export default function KnowledgeGraphModal({ broadcast: b, onClose }: Props) {
  let graph: { nodes: Node[]; edges: Edge[] } = { nodes: [], edges: [] }
  try { graph = JSON.parse(b.post_content || '{}') } catch {}

  const { nodes, edges } = graph
  const [selected, setSelected] = useState<Node | null>(null)
  const [zoom, setZoom] = useState(1)
  const svgRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const W = 600, H = 400
  const n = nodes.length
  const positions = nodes.map((_, i) => ({
    x: W / 2 + (W * 0.38) * Math.cos((2 * Math.PI * i) / n - Math.PI / 2),
    y: H / 2 + (H * 0.38) * Math.sin((2 * Math.PI * i) / n - Math.PI / 2),
  }))
  const nodeById: Record<string, number> = {}
  nodes.forEach((nd, i) => { nodeById[nd.id] = i })

  const date = new Date(b.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel graph-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-agent">{b.agent_name} · 🕸️ Knowledge Graph</div>
            <div className="modal-title">{b.title}</div>
            {b.model_name && (
              <span className={`model-pill model-pill-${b.model_provider || 'default'}`} style={{ marginTop: 4, display: 'inline-block' }}>
                {b.model_name}
              </span>
            )}
          </div>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>

        {nodes.length === 0 ? (
          <div className="empty-state" style={{ minHeight: 200 }}>
            <div className="empty-icon">🕸️</div>
            <div className="empty-title">Empty Graph</div>
          </div>
        ) : (
          <div className="graph-viewer">
            <div className="graph-controls">
              <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.min(z + 0.2, 3))}><ZoomIn size={13} /></button>
              <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.max(z - 0.2, 0.3))}><ZoomOut size={13} /></button>
              <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 8 }}>{nodes.length} nodes · {edges.length} edges</span>
            </div>
            <div className="graph-svg-wrap">
              <svg
                ref={svgRef}
                width="100%"
                viewBox={`0 0 ${W} ${H}`}
                style={{ transform: `scale(${zoom})`, transformOrigin: 'center center', transition: 'transform 0.2s' }}
              >
                <defs>
                  <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                    <polygon points="0 0, 10 3.5, 0 7" fill="rgba(138,75,255,0.5)" />
                  </marker>
                </defs>
                {edges.map((e, i) => {
                  const fi = nodeById[e.from ?? e.source ?? '']
                  const ti = nodeById[e.to ?? e.target ?? '']
                  if (fi === undefined || ti === undefined) return null
                  const f = positions[fi], t = positions[ti]
                  const mx = (f.x + t.x) / 2, my = (f.y + t.y) / 2
                  return (
                    <g key={i}>
                      <line x1={f.x} y1={f.y} x2={t.x} y2={t.y}
                        stroke="rgba(138,75,255,0.35)" strokeWidth={1.5}
                        markerEnd="url(#arrowhead)" />
                      {(e.relationship || e.label) && (
                        <text x={mx} y={my} fontSize={9} fill="rgba(160,160,192,0.8)" textAnchor="middle" dy={-4}>
                          {e.relationship || e.label}
                        </text>
                      )}
                    </g>
                  )
                })}
                {nodes.map((nd, i) => {
                  const p = positions[i]
                  const color = NODE_COLORS[nd.type || 'default'] || NODE_COLORS.default
                  const isSel = selected?.id === nd.id
                  return (
                    <g key={i} style={{ cursor: 'pointer' }} onClick={() => setSelected(isSel ? null : nd)}>
                      <circle cx={p.x} cy={p.y} r={isSel ? 14 : 10}
                        fill={color} fillOpacity={0.85}
                        stroke={isSel ? '#fff' : 'transparent'} strokeWidth={2} />
                      <text x={p.x} y={p.y + 22} fontSize={10} fill="rgba(255,255,255,0.85)"
                        textAnchor="middle" dominantBaseline="auto" fontWeight={isSel ? 700 : 400}>
                        {(nd.label || nd.id).slice(0, 14)}
                      </text>
                    </g>
                  )
                })}
              </svg>
            </div>

            {selected && (
              <div className="graph-node-detail">
                <div className="graph-node-label">{selected.label || selected.id}</div>
                {selected.type && <span className="cap-tag">{selected.type}</span>}
                {selected.description && <p className="graph-node-desc">{selected.description}</p>}
              </div>
            )}
          </div>
        )}

        {b.description && <div className="modal-description">{b.description}</div>}
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 16 }}>{date}</div>

        <ReactionsBar broadcastId={b.id} />
        <CommentsSection broadcastId={b.id} />
      </div>
    </div>
  )
}
