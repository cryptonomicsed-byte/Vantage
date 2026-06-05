import React from 'react'
import { Share2 } from 'lucide-react'

interface Broadcast {
  id: number
  title: string
  description: string
  post_content: string
  view_count: number
  created_at: string
  agent_name: string
  model_name: string
  model_provider: string
}

interface Props {
  broadcast: Broadcast
  onClick: () => void
}

export default function KnowledgeGraphCard({ broadcast: b, onClick }: Props) {
  let graph = { nodes: [] as any[], edges: [] as any[] }
  try { graph = JSON.parse(b.post_content || '{}') } catch {}

  const date = new Date(b.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  return (
    <div className="graph-card" onClick={onClick}>
      <div className="graph-card-preview">
        <GraphMiniPreview nodes={graph.nodes} edges={graph.edges} />
      </div>
      <div className="card-body">
        <div className="card-title">{b.title}</div>
        <div className="card-meta">
          <span className="content-type-pill"><Share2 size={9} /> {graph.nodes.length} nodes · {(graph.edges || []).length} edges</span>
          {b.model_name && (
            <span className={`model-pill model-pill-${b.model_provider || 'default'}`}>{b.model_name}</span>
          )}
          <span>{date}</span>
        </div>
      </div>
    </div>
  )
}

function GraphMiniPreview({ nodes, edges }: { nodes: any[]; edges: any[] }) {
  if (!nodes.length) return (
    <div className="graph-card-empty"><Share2 size={32} /></div>
  )

  const W = 220, H = 120
  const n = nodes.length
  const positions = nodes.map((_, i) => ({
    x: W / 2 + (W * 0.4) * Math.cos((2 * Math.PI * i) / n - Math.PI / 2),
    y: H / 2 + (H * 0.4) * Math.sin((2 * Math.PI * i) / n - Math.PI / 2),
  }))

  const nodeById: Record<string, number> = {}
  nodes.forEach((node, i) => { nodeById[node.id] = i })

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
      {(edges || []).map((e, i) => {
        const fi = nodeById[e.from ?? e.source]
        const ti = nodeById[e.to ?? e.target]
        if (fi === undefined || ti === undefined) return null
        const f = positions[fi], t = positions[ti]
        return <line key={i} x1={f.x} y1={f.y} x2={t.x} y2={t.y} stroke="rgba(138,75,255,0.4)" strokeWidth={1} />
      })}
      {nodes.map((node, i) => (
        <g key={i}>
          <circle cx={positions[i].x} cy={positions[i].y} r={5} fill="rgba(0,245,255,0.7)" />
          <text
            x={positions[i].x} y={positions[i].y - 8}
            fontSize={7} fill="rgba(255,255,255,0.6)"
            textAnchor="middle" dominantBaseline="auto"
          >
            {(node.label || node.id || '').slice(0, 10)}
          </text>
        </g>
      ))}
    </svg>
  )
}
