import React, { useRef, useEffect, useState, useCallback } from 'react'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface NeuralNode {
  id: string
  title: string
  x: number
  y: number
  z: number
  size: number
  color: string
  constellation: string
  tags: string[]
  content_type: string
  path: string
  created?: string
}

interface NeuralEdge {
  id: string
  subject: string
  predicate: string
  object: string
  source: [number, number, number]
  target: [number, number, number]
  weight: number
  path: string
}

interface NeuralNebula {
  id: string
  trace_type: string
  x: number
  y: number
  z: number
  opacity: number
  size: number
  path: string
}

export interface GalaxyData {
  agent_name: string
  agent_id: number
  stars: NeuralNode[]
  edges: NeuralEdge[]
  nebulae: NeuralNebula[]
  clusters: Record<string, Array<unknown>>
  bounds: { min: number[]; max: number[] }
  predictions?: { predictions?: number[]; lower?: number[]; upper?: number[] }
  patterns?: Array<{ pattern: string; confidence: number }>
}

interface SynapsePulse {
  edgeIdx: number
  t: number       // 0..1 progress along edge
  speed: number
  opacity: number
}

interface Props {
  data: GalaxyData
  agentName: string
  onStarSelect?: (star: NeuralNode) => void
  crossAgentLinks?: Array<{source_note_path: string; target_note_path: string; link_type: string}>
}

// ─── Color map by content type ────────────────────────────────────────────────
const TYPE_COLOR: Record<string, string> = {
  broadcast:  '#00f5ff',
  knowledge:  '#a855f7',
  trace:      '#f59e0b',
  file:       '#22c55e',
  note:       '#ec4899',
  graph:      '#6366f1',
  audio:      '#f97316',
  image:      '#14b8a6',
  text:       '#00f5ff',
  video:      '#e879f9',
}

function getNodeColor(node: NeuralNode): string {
  if (node.color && node.color !== '#6b7280') return node.color
  return TYPE_COLOR[node.content_type] || TYPE_COLOR[node.constellation] || '#00f5ff'
}

// ─── Projection (isometric-ish) ────────────────────────────────────────────────
function project(
  x: number, y: number, z: number,
  scale: number, ox: number, oy: number
): [number, number] {
  const sx = (x - z * 0.4) * scale + ox
  const sy = (y + z * 0.2) * scale + oy
  return [sx, sy]
}

// ─── Main component ────────────────────────────────────────────────────────────
export default function GalaxyViewer({ data, agentName: _agentName }: Props) {
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const rafRef       = useRef<number>(0)
  const sizeRef      = useRef({ w: 0, h: 0 })

  // View transform
  const scaleRef   = useRef(0.07)
  const oxRef      = useRef(0)
  const oyRef      = useRef(0)
  const dragRef    = useRef(false)
  const lastPosRef = useRef({ x: 0, y: 0 })
  const downPosRef = useRef({ x: 0, y: 0 })

  // Animation state
  const timeRef    = useRef(0)
  const pulsesRef  = useRef<SynapsePulse[]>([])

  // Hover / selection
  const hoveredRef  = useRef<string | null>(null)
  const [hoveredNode, setHoveredNode] = useState<NeuralNode | null>(null)
  const [tooltipPos, setTooltipPos]   = useState({ x: 0, y: 0 })
  const [selectedNode, setSelectedNode] = useState<NeuralNode | null>(null)
  const [activeFilter, setActiveFilter] = useState('all')
  const filterRef  = useRef('all')

  const nodes: NeuralNode[]   = data.stars   || []
  const edges   = data.edges   || []
  const nebulae = data.nebulae || []

  const contentTypes = Array.from(new Set(nodes.map(n => n.content_type).filter(Boolean)))

  // ── Center view on mount ──────────────────────────────────────────────────
  useEffect(() => {
    if (nodes.length === 0) return
    const b = data.bounds
    if (!b?.min || !b?.max) return
    const midX = (b.min[0] + b.max[0]) / 2
    const midY = (b.min[1] + b.max[1]) / 2
    const midZ = (b.min[2] + b.max[2]) / 2
    const cw = canvasRef.current?.clientWidth  || 800
    const ch = canvasRef.current?.clientHeight || 500
    const s  = scaleRef.current
    oxRef.current = cw / 2 - (midX - midZ * 0.4) * s
    oyRef.current = ch / 2 - (midY + midZ * 0.2) * s
  }, [data, nodes.length])

  // ── Seed initial synapse pulses ──────────────────────────────────────────
  useEffect(() => {
    if (edges.length === 0) return
    const initial: SynapsePulse[] = []
    edges.forEach((_, i) => {
      // Stagger pulses so they don't all start at 0
      if (Math.random() < 0.6) {
        initial.push({
          edgeIdx: i,
          t: Math.random(),
          speed: 0.003 + Math.random() * 0.005,
          opacity: 0.5 + Math.random() * 0.5,
        })
      }
    })
    pulsesRef.current = initial
  }, [edges])

  // ── Canvas resize ─────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    const dpr = window.devicePixelRatio || 1
    const resize = () => {
      const w = container.clientWidth
      const h = container.clientHeight
      canvas.width  = w * dpr
      canvas.height = h * dpr
      sizeRef.current = { w, h }
      const ctx = canvas.getContext('2d')
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)
    return () => ro.disconnect()
  }, [])

  // ── rAF render loop ───────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const loop = () => {
      const ctx = canvas.getContext('2d')
      if (!ctx) { rafRef.current = requestAnimationFrame(loop); return }
      const { w, h } = sizeRef.current
      if (w === 0 || h === 0) { rafRef.current = requestAnimationFrame(loop); return }

      timeRef.current += 0.016

      // Advance synapse pulses
      const pulses = pulsesRef.current
      for (let i = pulses.length - 1; i >= 0; i--) {
        pulses[i].t += pulses[i].speed
        if (pulses[i].t > 1) {
          // Respawn on a random edge
          const edgeIdx = Math.floor(Math.random() * edges.length)
          pulses[i] = {
            edgeIdx,
            t: 0,
            speed: 0.003 + Math.random() * 0.006,
            opacity: 0.6 + Math.random() * 0.4,
          }
        }
      }

      drawNeuralNet(ctx, w, h)
      rafRef.current = requestAnimationFrame(loop)
    }

    rafRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafRef.current)
  }, [nodes, edges, nebulae])

  // ── Draw everything ───────────────────────────────────────────────────────
  function drawNeuralNet(ctx: CanvasRenderingContext2D, w: number, h: number) {
    const t    = timeRef.current
    const sc   = scaleRef.current
    const ox   = oxRef.current
    const oy   = oyRef.current
    const hov  = hoveredRef.current
    const filt = filterRef.current
    const sel  = selectedNode

    // ── Background ─────────────────────────────────────────────────────────
    ctx.clearRect(0, 0, w, h)

    // Deep black with subtle blue tint
    const bg = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7)
    bg.addColorStop(0, '#03060f')
    bg.addColorStop(1, '#010208')
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, w, h)

    // Subtle grid
    ctx.save()
    ctx.strokeStyle = 'rgba(0,245,255,0.025)'
    ctx.lineWidth = 0.5
    const gridSpacing = 80
    for (let gx = ox % gridSpacing; gx < w; gx += gridSpacing) {
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke()
    }
    for (let gy = oy % gridSpacing; gy < h; gy += gridSpacing) {
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke()
    }
    ctx.restore()

    const visNodes = filt === 'all' ? nodes : nodes.filter(n => n.content_type === filt)
    const visIds   = new Set(visNodes.map(n => n.id))

    // ── Nebulae / cluster halos ─────────────────────────────────────────────
    nebulae.forEach(neb => {
      const [sx, sy] = project(neb.x, neb.y, neb.z, sc, ox, oy)
      const r = neb.size * sc * 0.2
      if (r < 2) return
      const pulse = 0.7 + 0.3 * Math.sin(t * 0.5 + neb.x * 0.01)
      ctx.save()
      const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, r)
      g.addColorStop(0,   `rgba(138,75,255,${neb.opacity * 0.4 * pulse})`)
      g.addColorStop(0.4, `rgba(100,60,200,${neb.opacity * 0.15 * pulse})`)
      g.addColorStop(1,   'rgba(138,75,255,0)')
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2)
      ctx.fillStyle = g; ctx.fill()
      ctx.restore()
    })

    // ── Edges (synaptic connections) ────────────────────────────────────────
    edges.forEach(edge => {
      const srcVisible = visIds.has(edge.subject) || filt === 'all'
      const tgtVisible = visIds.has(edge.object)  || filt === 'all'
      if (!srcVisible && !tgtVisible) return

      const [sx1, sy1] = project(edge.source[0], edge.source[1], edge.source[2], sc, ox, oy)
      const [sx2, sy2] = project(edge.target[0], edge.target[1], edge.target[2], sc, ox, oy)

      const isHighlighted = hov && (edge.subject === hov || edge.object === hov)

      ctx.save()
      if (isHighlighted) {
        // Bright glowing edge for hovered connections
        ctx.shadowBlur  = 8
        ctx.shadowColor = '#00f5ff'
        ctx.strokeStyle = `rgba(0,245,255,0.75)`
        ctx.lineWidth   = Math.max(1, edge.weight * 1.5)
      } else {
        ctx.strokeStyle = `rgba(0,200,255,0.12)`
        ctx.lineWidth   = Math.max(0.4, edge.weight * 0.5)
      }
      ctx.beginPath()
      ctx.moveTo(sx1, sy1)
      ctx.lineTo(sx2, sy2)
      ctx.stroke()
      ctx.restore()
    })

    // ── Synapse pulses (animated signal dots) ──────────────────────────────
    const pulses = pulsesRef.current
    pulses.forEach(pulse => {
      const edge = edges[pulse.edgeIdx]
      if (!edge) return
      const srcVisible = visIds.has(edge.subject) || filt === 'all'
      const tgtVisible = visIds.has(edge.object)  || filt === 'all'
      if (!srcVisible && !tgtVisible) return

      const [sx1, sy1] = project(edge.source[0], edge.source[1], edge.source[2], sc, ox, oy)
      const [sx2, sy2] = project(edge.target[0], edge.target[1], edge.target[2], sc, ox, oy)

      const px = sx1 + (sx2 - sx1) * pulse.t
      const py = sy1 + (sy2 - sy1) * pulse.t

      const fade = Math.sin(pulse.t * Math.PI) // fade in/out along edge
      ctx.save()
      ctx.shadowBlur  = 12
      ctx.shadowColor = '#00f5ff'
      ctx.beginPath()
      ctx.arc(px, py, 2.5, 0, Math.PI * 2)
      ctx.fillStyle = `rgba(0,245,255,${pulse.opacity * fade})`
      ctx.fill()
      ctx.restore()
    })

    // ── Nodes (neurons) ────────────────────────────────────────────────────
    visNodes.forEach(node => {
      const [sx, sy] = project(node.x, node.y, node.z, sc, ox, oy)
      const baseR = Math.max(3, (node.size / 50) * 14 * sc)
      const isHov = hov === node.id
      const isSel = sel?.id === node.id
      const color = getNodeColor(node)

      // Breathing pulse
      const breath = 1 + 0.12 * Math.sin(t * 1.8 + node.x * 0.05 + node.y * 0.05)
      const r = baseR * (isHov || isSel ? 1.4 : 1) * breath

      ctx.save()

      // Outer bloom / glow halo
      const glowR = r * 3.5
      const glow  = ctx.createRadialGradient(sx, sy, r * 0.5, sx, sy, glowR)
      const alpha = isHov || isSel ? 0.5 : 0.2
      glow.addColorStop(0,   hexToRgba(color, alpha))
      glow.addColorStop(0.5, hexToRgba(color, alpha * 0.3))
      glow.addColorStop(1,   hexToRgba(color, 0))
      ctx.beginPath(); ctx.arc(sx, sy, glowR, 0, Math.PI * 2)
      ctx.fillStyle = glow; ctx.fill()

      // Core node
      ctx.shadowBlur  = isHov || isSel ? 30 : 15
      ctx.shadowColor = color
      const grad = ctx.createRadialGradient(sx - r * 0.3, sy - r * 0.3, 0, sx, sy, r)
      grad.addColorStop(0,   '#ffffff')
      grad.addColorStop(0.2, color)
      grad.addColorStop(1,   hexToRgba(color, 0.6))
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2)
      ctx.fillStyle = grad; ctx.fill()

      // Selection ring
      if (isSel) {
        ctx.beginPath(); ctx.arc(sx, sy, r + 5, 0, Math.PI * 2)
        ctx.strokeStyle = '#ffffff'
        ctx.lineWidth = 2
        ctx.shadowBlur = 20; ctx.shadowColor = color
        ctx.stroke()
        // Second rotating ring
        ctx.beginPath(); ctx.arc(sx, sy, r + 9, 0, Math.PI * 2)
        ctx.strokeStyle = hexToRgba(color, 0.5)
        ctx.lineWidth = 1; ctx.stroke()
      } else if (isHov) {
        ctx.beginPath(); ctx.arc(sx, sy, r + 4, 0, Math.PI * 2)
        ctx.strokeStyle = '#00f5ff'
        ctx.lineWidth = 1.5
        ctx.shadowBlur = 12; ctx.shadowColor = '#00f5ff'
        ctx.stroke()
      }

      // Label
      if (r >= 4 || isHov || isSel) {
        ctx.shadowBlur  = 0
        ctx.font        = `${Math.max(9, Math.min(13, r * 1.4))}px 'Inter', sans-serif`
        ctx.fillStyle   = isHov || isSel ? '#ffffff' : 'rgba(220,230,255,0.85)'
        ctx.textAlign   = 'center'
        ctx.textBaseline = 'top'
        const label = node.title.length > 24 ? node.title.slice(0, 24) + '…' : node.title
        ctx.fillText(label, sx, sy + r + 4)
      }

      ctx.restore()
    })

    // Dim non-visible nodes as ghost dots
    if (filt !== 'all') {
      nodes.filter(n => !visIds.has(n.id)).forEach(node => {
        const [sx, sy] = project(node.x, node.y, node.z, sc, ox, oy)
        const r = Math.max(1.5, (node.size / 50) * 6 * sc)
        ctx.save()
        ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(100,100,120,0.1)'
        ctx.fill(); ctx.restore()
      })
    }

    // ── Minimap ────────────────────────────────────────────────────────────
    if (nodes.length > 0) {
      drawMinimap(ctx, w, h, nodes, edges, sc, ox, oy, filt, visIds)
    }
  }

  // ── Minimap ───────────────────────────────────────────────────────────────
  function drawMinimap(
    ctx: CanvasRenderingContext2D,
    w: number, h: number,
    nodes: NeuralNode[],
    edges: NeuralEdge[],
    sc: number, ox: number, oy: number,
    filt: string, visIds: Set<string>
  ) {
    const mmW = 160, mmH = 100
    const mmX = w - mmW - 14, mmY = h - mmH - 14
    const pad = 10

    // Compute bounds of all projected node positions
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    nodes.forEach(n => {
      const [px, py] = project(n.x, n.y, n.z, sc, ox, oy)
      if (px < minX) minX = px; if (px > maxX) maxX = px
      if (py < minY) minY = py; if (py > maxY) maxY = py
    })
    const rangeX = maxX - minX || 1
    const rangeY = maxY - minY || 1
    const mmScX  = (mmW - pad * 2) / rangeX
    const mmScY  = (mmH - pad * 2) / rangeY
    const mmSc   = Math.min(mmScX, mmScY)

    const toMM = (px: number, py: number): [number, number] => [
      mmX + pad + (px - minX) * mmSc,
      mmY + pad + (py - minY) * mmSc,
    ]

    ctx.save()
    // Background
    ctx.fillStyle = 'rgba(3,6,20,0.85)'
    ctx.strokeStyle = 'rgba(0,245,255,0.25)'
    ctx.lineWidth = 1
    roundRect(ctx, mmX, mmY, mmW, mmH, 6)
    ctx.fill(); ctx.stroke()

    // Edges in minimap
    edges.forEach(edge => {
      const [sx1, sy1] = project(edge.source[0], edge.source[1], edge.source[2], sc, ox, oy)
      const [sx2, sy2] = project(edge.target[0], edge.target[1], edge.target[2], sc, ox, oy)
      const [mx1, my1] = toMM(sx1, sy1)
      const [mx2, my2] = toMM(sx2, sy2)
      ctx.beginPath(); ctx.moveTo(mx1, my1); ctx.lineTo(mx2, my2)
      ctx.strokeStyle = 'rgba(0,200,255,0.12)'; ctx.lineWidth = 0.5; ctx.stroke()
    })

    // Nodes in minimap
    nodes.forEach(node => {
      const [px, py] = project(node.x, node.y, node.z, sc, ox, oy)
      const [mx, my] = toMM(px, py)
      const color = getNodeColor(node)
      const isVis = visIds.has(node.id) || filt === 'all'
      ctx.beginPath(); ctx.arc(mx, my, isVis ? 2.5 : 1, 0, Math.PI * 2)
      ctx.fillStyle = isVis ? color : 'rgba(80,80,100,0.4)'
      ctx.fill()
    })

    // Viewport indicator
    const [vx1, vy1] = toMM(0, 0)
    const [vx2, vy2] = toMM(w, h)
    const vpW = vx2 - vx1, vpH = vy2 - vy1
    ctx.strokeStyle = 'rgba(255,255,255,0.4)'
    ctx.lineWidth = 1
    ctx.strokeRect(Math.max(mmX, vx1), Math.max(mmY, vy1), Math.min(vpW, mmW), Math.min(vpH, mmH))

    // Label
    ctx.font = '9px Inter, sans-serif'
    ctx.fillStyle = 'rgba(0,245,255,0.5)'
    ctx.textAlign = 'left'
    ctx.fillText(`${nodes.length} nodes`, mmX + 6, mmY + 5)

    ctx.restore()
  }

  // ── Mouse events ──────────────────────────────────────────────────────────
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    dragRef.current  = true
    lastPosRef.current = { x: e.clientX, y: e.clientY }
    downPosRef.current = { x: e.clientX, y: e.clientY }
  }, [])

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (dragRef.current) {
      oxRef.current += e.clientX - lastPosRef.current.x
      oyRef.current += e.clientY - lastPosRef.current.y
      lastPosRef.current = { x: e.clientX, y: e.clientY }
      return
    }

    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mx   = e.clientX - rect.left
    const my   = e.clientY - rect.top
    const filt = filterRef.current
    const vis: NeuralNode[] = filt === 'all' ? nodes : nodes.filter(n => n.content_type === filt)

    let closest: NeuralNode | null = null
    let closestD = 18
    vis.forEach(node => {
      const [sx, sy] = project(node.x, node.y, node.z, scaleRef.current, oxRef.current, oyRef.current)
      const d = Math.hypot(sx - mx, sy - my)
      if (d < closestD) { closestD = d; closest = node }
    })
    hoveredRef.current = closest ? (closest as NeuralNode).id : null
    setHoveredNode(closest)
    if (closest) setTooltipPos({ x: e.clientX, y: e.clientY })
  }, [nodes])

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const wasDrag = Math.hypot(
      e.clientX - downPosRef.current.x,
      e.clientY - downPosRef.current.y
    ) > 4
    dragRef.current = false
    if (!wasDrag && hoveredRef.current) {
      const node = nodes.find(n => n.id === hoveredRef.current)
      setSelectedNode(prev => prev?.id === node?.id ? null : (node || null))
    }
  }, [nodes])

  const handleMouseLeave = useCallback(() => {
    dragRef.current    = false
    hoveredRef.current = null
    setHoveredNode(null)
  }, [])

  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mx   = e.clientX - rect.left
    const my   = e.clientY - rect.top
    const zf   = e.deltaY < 0 ? 1.12 : 0.9
    const ns   = Math.max(0.01, Math.min(5, scaleRef.current * zf))
    oxRef.current = mx + (oxRef.current - mx) * (ns / scaleRef.current)
    oyRef.current = my + (oyRef.current - my) * (ns / scaleRef.current)
    scaleRef.current = ns
  }, [])

  // Double-click: zoom into node
  const handleDoubleClick = useCallback(() => {
    const node = nodes.find(n => n.id === hoveredRef.current)
    if (!node) return
    const canvas = canvasRef.current
    if (!canvas) return
    const cw = canvas.clientWidth, ch = canvas.clientHeight
    const [px, py] = project(node.x, node.y, node.z, 1, 0, 0)
    const newScale  = 0.8
    scaleRef.current = newScale
    oxRef.current = cw / 2 - px * newScale
    oyRef.current = ch / 2 - py * newScale
  }, [nodes])

  const filteredCount = activeFilter === 'all'
    ? nodes.length
    : nodes.filter(n => n.content_type === activeFilter).length

  const handleFilterChange = useCallback((f: string) => {
    filterRef.current = f
    setActiveFilter(f)
  }, [])

  const resetZoom = useCallback(() => {
    if (nodes.length === 0) return
    const b = data.bounds
    if (!b?.min || !b?.max) return
    scaleRef.current = 0.07
    const cw = canvasRef.current?.clientWidth  || 800
    const ch = canvasRef.current?.clientHeight || 500
    const s  = scaleRef.current
    const midX = (b.min[0] + b.max[0]) / 2
    const midY = (b.min[1] + b.max[1]) / 2
    const midZ = (b.min[2] + b.max[2]) / 2
    oxRef.current = cw / 2 - (midX - midZ * 0.4) * s
    oyRef.current = ch / 2 - (midY + midZ * 0.2) * s
  }, [nodes, data])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Canvas */}
      <div
        ref={containerRef}
        className="galaxy-container"
        style={{ flex: 1, position: 'relative', minHeight: 480 }}
      >
        <canvas
          ref={canvasRef}
          className="galaxy-canvas"
          style={{ cursor: dragRef.current ? 'grabbing' : (hoveredNode ? 'pointer' : 'crosshair') }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseLeave}
          onWheel={handleWheel}
          onDoubleClick={handleDoubleClick}
        />

        {/* Empty state overlay */}
        {nodes.length === 0 && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            gap: 12, pointerEvents: 'none',
          }}>
            <div style={{ fontSize: 48, opacity: 0.15 }}>🧠</div>
            <div style={{ fontSize: 14, color: 'rgba(0,245,255,0.4)', fontFamily: 'Inter, sans-serif', letterSpacing: '0.1em' }}>
              NO NEURAL PATTERNS YET
            </div>
            <div style={{ fontSize: 12, color: 'rgba(107,114,128,0.7)', fontFamily: 'Inter, sans-serif' }}>
              Sync your vault to generate the network
            </div>
          </div>
        )}

        {/* Stats overlay */}
        <div className="galaxy-stats" style={{ fontFamily: 'Inter, sans-serif' }}>
          ⬡ {filteredCount} nodes · {edges.length} synapses · {nebulae.length} clusters
        </div>

        {/* Zoom reset button */}
        <button
          onClick={resetZoom}
          style={{
            position: 'absolute', top: 10, right: 10,
            background: 'rgba(0,245,255,0.08)',
            border: '1px solid rgba(0,245,255,0.2)',
            borderRadius: 6, color: '#00f5ff',
            padding: '5px 10px', fontSize: 11,
            fontFamily: 'Inter, sans-serif',
            cursor: 'pointer', letterSpacing: '0.05em',
          }}
        >
          ⊙ RESET
        </button>

        {/* Hover tooltip */}
        {hoveredNode && (
          <div
            className="galaxy-tooltip"
            style={{ position: 'fixed', left: tooltipPos.x + 16, top: tooltipPos.y - 14, pointerEvents: 'none' }}
          >
            <div style={{ color: getNodeColor(hoveredNode), fontWeight: 700, marginBottom: 4, fontSize: 13 }}>
              {hoveredNode.title}
            </div>
            <div style={{ fontSize: 10, color: '#6b7280', marginBottom: 2 }}>
              {hoveredNode.content_type} · {hoveredNode.constellation}
            </div>
            {hoveredNode.tags.length > 0 && (
              <div style={{ fontSize: 10, color: 'rgba(0,245,255,0.5)' }}>
                {hoveredNode.tags.slice(0, 4).map(t => `#${t}`).join(' ')}
              </div>
            )}
            <div style={{ fontSize: 9, color: 'rgba(107,114,128,0.5)', marginTop: 4 }}>
              double-click to focus
            </div>
          </div>
        )}
      </div>

      {/* Node detail panel */}
      {selectedNode && (
        <div style={{
          background: 'linear-gradient(135deg, rgba(0,245,255,0.04), rgba(138,75,255,0.04))',
          border: '1px solid rgba(0,245,255,0.15)',
          borderRadius: 10, margin: '0 0 8px 0', padding: '12px 16px',
          display: 'flex', gap: 16, alignItems: 'flex-start',
          fontFamily: 'Inter, sans-serif',
        }}>
          <div style={{
            width: 36, height: 36, borderRadius: '50%', flexShrink: 0,
            background: `radial-gradient(circle, white 15%, ${getNodeColor(selectedNode)} 60%)`,
            boxShadow: `0 0 16px ${getNodeColor(selectedNode)}88`,
            marginTop: 2,
          }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: getNodeColor(selectedNode), marginBottom: 4 }}>
              {selectedNode.title}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
              <span style={{ fontSize: 10, background: 'rgba(0,245,255,0.1)', color: '#00f5ff', borderRadius: 4, padding: '2px 6px' }}>
                {selectedNode.content_type}
              </span>
              <span style={{ fontSize: 10, background: 'rgba(138,75,255,0.1)', color: '#a855f7', borderRadius: 4, padding: '2px 6px' }}>
                {selectedNode.constellation}
              </span>
              <span style={{ fontSize: 10, color: 'rgba(107,114,128,0.7)' }}>
                strength: {selectedNode.size}
              </span>
            </div>
            {selectedNode.tags.length > 0 && (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {selectedNode.tags.map(tag => (
                  <span key={tag} style={{ fontSize: 10, color: 'rgba(0,245,255,0.6)' }}>#{tag}</span>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={() => setSelectedNode(null)}
            style={{ background: 'none', border: 'none', color: 'rgba(107,114,128,0.7)', cursor: 'pointer', fontSize: 16, padding: 0 }}
          >
            ×
          </button>
        </div>
      )}

      {/* Filter bar */}
      <div className="galaxy-filter-bar">
        <button
          className={`galaxy-filter-btn${activeFilter === 'all' ? ' active' : ''}`}
          onClick={() => handleFilterChange('all')}
        >
          All ({nodes.length})
        </button>
        {contentTypes.map(ct => (
          <button
            key={ct}
            className={`galaxy-filter-btn${activeFilter === ct ? ' active' : ''}`}
            onClick={() => handleFilterChange(ct)}
          >
            {ct} ({nodes.filter(n => n.content_type === ct).length})
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function hexToRgba(hex: string, alpha: number): string {
  if (hex.startsWith('rgba') || hex.startsWith('rgb')) {
    return hex.replace(/[\d.]+\)$/, `${alpha})`)
  }
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + r)
  ctx.lineTo(x + w, y + h - r)
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}
