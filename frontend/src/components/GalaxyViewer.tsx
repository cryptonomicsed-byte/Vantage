import React, { useRef, useEffect, useState, useCallback } from 'react'
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  SimulationNodeDatum,
  SimulationLinkDatum,
} from 'd3-force'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface GalaxyStar {
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

interface GalaxyEdge {
  id: string
  subject: string
  predicate: string
  object: string
  source: [number, number, number]
  target: [number, number, number]
  weight: number
  path: string
  trust?: number
}

interface CrossAgentLink {
  source_note_path: string
  target_note_path: string
  link_type: string
}

interface GalaxyNebula {
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
  stars: GalaxyStar[]
  edges: GalaxyEdge[]
  nebulae: GalaxyNebula[]
  clusters: Record<string, Array<unknown>>
  bounds: { min: number[]; max: number[] }
}

interface SimNode extends SimulationNodeDatum {
  id: string
  star: GalaxyStar
  degree: number
  size: number
  color: string
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  weight: number
  trust: number
}

interface Props {
  data: GalaxyData
  agentName: string
  onStarSelect?: (star: GalaxyStar) => void
  crossAgentLinks?: CrossAgentLink[]
}

// ─── Color by content type ────────────────────────────────────────────────────

function nodeColor(content_type: string): string {
  switch (content_type) {
    case 'text':
    case 'broadcast':
      return '#ffe66d'
    case 'knowledge':
      return '#00f5ff'
    case 'trace':
    case 'traces':
      return '#8a4bff'
    case 'note':
    case 'draft':
    case 'drafts':
      return '#a8ff78'
    case 'template':
    case 'templates':
      return '#ff9ff3'
    default:
      return '#c7ceea'
  }
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function GalaxyViewer({ data, agentName: _agentName, onStarSelect, crossAgentLinks = [] }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasSizeRef = useRef({ w: 0, h: 0 })
  const rafRef = useRef<number>(0)

  // Simulation
  const simRef = useRef<ReturnType<typeof forceSimulation<SimNode>> | null>(null)
  const nodesRef = useRef<SimNode[]>([])
  const linksRef = useRef<SimLink[]>([])

  // Visual FX refs
  const bgStarsRef = useRef<Array<{ x: number; y: number; r: number; phase: number }>>([])
  const nebulasRef = useRef<Array<{ x: number; y: number; r: number; color: string }>>([])
  const edgeParticlesRef = useRef<Array<{ progress: number; linkIdx: number }>>([])

  // View state
  const scaleRef = useRef(1.0)
  const offsetXRef = useRef(0)
  const offsetYRef = useRef(0)
  const isDraggingRef = useRef(false)
  const dragNodeRef = useRef<SimNode | null>(null)
  const lastMouseRef = useRef({ x: 0, y: 0 })
  const mouseDownPosRef = useRef({ x: 0, y: 0 })
  const hoveredStarIdRef = useRef<string | null>(null)
  const activeFilterRef = useRef<string>('all')
  const selectedConstellationRef = useRef<string | null>(null)
  const lastTouchDistRef = useRef<number>(0)
  const dateRangeRef = useRef<[number, number]>([0, Infinity])
  const crossAgentLinksRef = useRef<CrossAgentLink[]>(crossAgentLinks)
  crossAgentLinksRef.current = crossAgentLinks

  // React UI state
  const [activeFilter, setActiveFilter] = useState<string>('all')
  const [hoveredStar, setHoveredStar] = useState<GalaxyStar | null>(null)
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 })
  const [selectedConstellation, setSelectedConstellation] = useState<string | null>(null)
  const [dateLabel, setDateLabel] = useState<string>('All time')

  const stars = data.stars || []
  const edges = data.edges || []
  const contentTypes = Array.from(new Set(stars.map(s => s.content_type).filter(Boolean)))

  const starDates = stars
    .map(s => s.created ? new Date(s.created).getTime() : NaN)
    .filter(t => !isNaN(t))
  const minDate = starDates.length ? Math.min(...starDates) : 0
  const maxDate = starDates.length ? Math.max(...starDates) : Date.now()

  // ── Canvas resize ──────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    const dpr = window.devicePixelRatio || 1
    const resize = () => {
      const w = container.clientWidth
      const h = container.clientHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      canvasSizeRef.current = { w, h }
      const ctx = canvas.getContext('2d')
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)

    // Initialize background starfield and nebulas once
    bgStarsRef.current = Array.from({ length: 220 }, () => ({
      x: Math.random(),
      y: Math.random(),
      r: Math.random() * 1.3 + 0.3,
      phase: Math.random() * Math.PI * 2,
    }))
    nebulasRef.current = [
      { x: 0.18, y: 0.28, r: 190, color: '138,75,255' },
      { x: 0.76, y: 0.62, r: 155, color: '0,245,255' },
      { x: 0.50, y: 0.12, r: 130, color: '120,60,220' },
      { x: 0.88, y: 0.22, r: 105, color: '0,180,255' },
      { x: 0.30, y: 0.82, r: 115, color: '80,40,180' },
    ]

    return () => ro.disconnect()
  }, [])

  // ── Build / restart simulation when data changes ───────────────────────────
  useEffect(() => {
    if (stars.length === 0) { nodesRef.current = []; linksRef.current = []; return }

    const { w, h } = canvasSizeRef.current
    const cw = w || 800
    const ch = h || 500

    const nodes: SimNode[] = stars.map(star => ({
      id: star.id,
      star,
      degree: 0,
      size: 6,
      color: nodeColor(star.content_type),
      x: cw / 2 + (Math.random() - 0.5) * cw * 0.6,
      y: ch / 2 + (Math.random() - 0.5) * ch * 0.6,
    }))

    const nodeById = new Map(nodes.map(n => [n.id, n]))
    const nodeTitleMap = new Map(nodes.map(n => [n.star.title.toLowerCase(), n]))

    const links: SimLink[] = []
    edges.forEach(edge => {
      const src = nodeTitleMap.get((edge.subject || '').toLowerCase())
      const tgt = nodeTitleMap.get((edge.object || '').toLowerCase())
      if (src && tgt && src !== tgt) {
        links.push({
          source: src.id,
          target: tgt.id,
          weight: edge.weight || 1,
          trust: edge.trust ?? 0.5,
        } as SimLink)
        src.degree = (src.degree || 0) + 1
        tgt.degree = (tgt.degree || 0) + 1
      }
    })

    // Size by degree
    nodes.forEach(n => {
      n.size = Math.min(24, 4 + (n.degree || 0) * 1.5)
    })

    nodesRef.current = nodes
    linksRef.current = links
    void nodeById  // suppress unused warning

    simRef.current?.stop()

    const sim = forceSimulation<SimNode>(nodes)
      .force(
        'link',
        forceLink<SimNode, SimLink>(links)
          .id(d => d.id)
          .distance(80)
          .strength(0.4)
      )
      .force('charge', forceManyBody<SimNode>().strength(-120))
      .force('center', forceCenter<SimNode>(cw / 2, ch / 2))
      .force('collide', forceCollide<SimNode>(d => d.size * 3))
      .alphaDecay(0.02)

    simRef.current = sim
    return () => { sim.stop() }
  }, [stars, edges])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── rAF render loop ────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const draw = () => {
      const ctx = canvas.getContext('2d')
      if (!ctx) { rafRef.current = requestAnimationFrame(draw); return }
      const { w, h } = canvasSizeRef.current
      if (w === 0 || h === 0) { rafRef.current = requestAnimationFrame(draw); return }

      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = '#040408'
      ctx.fillRect(0, 0, w, h)

      const now = Date.now()
      const t = now * 0.001

      // ── Nebula depth blobs ──────────────────────────────────────────────────
      nebulasRef.current.forEach(neb => {
        const nx = neb.x * w, ny = neb.y * h
        const grad = ctx.createRadialGradient(nx, ny, 0, nx, ny, neb.r)
        grad.addColorStop(0, `rgba(${neb.color},0.07)`)
        grad.addColorStop(0.5, `rgba(${neb.color},0.03)`)
        grad.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.save()
        ctx.beginPath()
        ctx.arc(nx, ny, neb.r, 0, Math.PI * 2)
        ctx.fillStyle = grad
        ctx.fill()
        ctx.restore()
      })

      // ── Twinkling background starfield ──────────────────────────────────────
      bgStarsRef.current.forEach(star => {
        const alpha = 0.18 + Math.sin(t * 0.5 + star.phase) * 0.22 + 0.18
        const sx = star.x * w, sy = star.y * h
        ctx.save()
        if (Math.sin(t * 0.3 + star.phase) > 0.65) {
          ctx.shadowBlur = 5
          ctx.shadowColor = 'rgba(255,255,255,0.85)'
        }
        ctx.beginPath()
        ctx.arc(sx, sy, star.r, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(255,255,255,${alpha})`
        ctx.fill()
        ctx.restore()
      })

      const nodes = nodesRef.current
      const links = linksRef.current

      if (nodes.length === 0) {
        ctx.font = '14px Orbitron, sans-serif'
        ctx.fillStyle = '#6b7280'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText('No memory stars in this vault yet', w / 2, h / 2)
        rafRef.current = requestAnimationFrame(draw)
        return
      }

      const sc = scaleRef.current
      const ox = offsetXRef.current
      const oy = offsetYRef.current
      const filterActive = activeFilterRef.current
      const selConst = selectedConstellationRef.current
      const dateRange = dateRangeRef.current
      const hoveredId = hoveredStarIdRef.current
      const crossLinks = crossAgentLinksRef.current

      const toScreen = (x: number, y: number): [number, number] => [x * sc + ox, y * sc + oy]

      // ── Draw edges ──────────────────────────────────────────────────────────
      links.forEach(link => {
        const s = link.source as SimNode
        const tgt = link.target as SimNode
        if (typeof s === 'string' || typeof tgt === 'string') return
        if (s.x == null || s.y == null || tgt.x == null || tgt.y == null) return

        const [x1, y1] = toScreen(s.x, s.y)
        const [x2, y2] = toScreen(tgt.x, tgt.y)

        const trust = link.trust ?? 0.5
        let strokeStyle = 'rgba(0,245,255,0.22)'
        if (hoveredId === s.id || hoveredId === tgt.id) {
          const rgb = trust >= 0.8 ? '0,245,255' : trust >= 0.5 ? '255,170,0' : trust >= 0.3 ? '255,51,51' : '150,150,180'
          strokeStyle = `rgba(${rgb},0.85)`
        } else if (selConst && (s.star.constellation === selConst || tgt.star.constellation === selConst)) {
          strokeStyle = 'rgba(0,245,255,0.4)'
        }

        ctx.save()
        ctx.shadowBlur = 4
        ctx.shadowColor = 'rgba(0,200,255,0.35)'
        ctx.beginPath()
        ctx.moveTo(x1, y1)
        ctx.lineTo(x2, y2)
        ctx.strokeStyle = strokeStyle
        ctx.lineWidth = Math.max(1.0, link.weight * 0.6)
        ctx.stroke()
        ctx.restore()
      })

      // ── Edge pulse particles (firing-off effect) ────────────────────────────
      if (links.length > 0 && Math.random() < 0.10) {
        edgeParticlesRef.current.push({
          progress: 0,
          linkIdx: Math.floor(Math.random() * links.length),
        })
      }
      edgeParticlesRef.current = edgeParticlesRef.current.filter(p => {
        p.progress += 0.014
        const link = links[p.linkIdx]
        if (!link) return false
        const s = link.source as SimNode
        const tgt = link.target as SimNode
        if (typeof s === 'string' || s.x == null || s.y == null || tgt.x == null || tgt.y == null) return false
        const [x1, y1] = toScreen(s.x as number, s.y as number)
        const [x2, y2] = toScreen(tgt.x as number, tgt.y as number)
        const px = x1 + (x2 - x1) * p.progress
        const py = y1 + (y2 - y1) * p.progress
        ctx.save()
        ctx.beginPath()
        ctx.arc(px, py, 2.5, 0, Math.PI * 2)
        ctx.fillStyle = '#00f5ff'
        ctx.shadowBlur = 14
        ctx.shadowColor = '#00f5ff'
        ctx.globalAlpha = 1 - p.progress * 0.4
        ctx.fill()
        ctx.restore()
        return p.progress < 1
      })

      // ── Cross-agent dashed beziers ──────────────────────────────────────────
      if (crossLinks.length > 0) {
        const starByPath: Record<string, SimNode> = {}
        nodes.forEach(n => { starByPath[n.star.path] = n })

        ctx.save()
        ctx.setLineDash([4, 4])
        crossLinks.forEach(link => {
          const sn = starByPath[link.source_note_path]
          const tn = starByPath[link.target_note_path]
          if (!sn || !tn || sn.x == null || sn.y == null || tn.x == null || tn.y == null) return

          const [x1, y1] = toScreen(sn.x, sn.y)
          const [x2, y2] = toScreen(tn.x, tn.y)
          const ctrlX = (x1 + x2) / 2
          const ctrlY = (y1 + y2) / 2 - 30

          const rgb = link.link_type === 'reference' ? '147,112,219'
            : link.link_type === 'fork' ? '255,20,147'
            : link.link_type === 'cites' ? '0,206,209'
            : '68,68,68'

          ctx.beginPath()
          ctx.moveTo(x1, y1)
          ctx.quadraticCurveTo(ctrlX, ctrlY, x2, y2)
          ctx.strokeStyle = `rgba(${rgb},0.5)`
          ctx.lineWidth = 1
          ctx.stroke()
        })
        ctx.restore()
      }

      // ── Draw nodes ──────────────────────────────────────────────────────────
      nodes.forEach((node, nodeIndex) => {
        if (node.x == null || node.y == null) return

        const star = node.star
        const isFiltered = filterActive !== 'all' && star.content_type !== filterActive
        const inConst = !selConst || star.constellation === selConst
        const starTime = star.created ? new Date(star.created).getTime() : 0
        const inDateRange = starTime === 0 || (starTime >= dateRange[0] && starTime <= dateRange[1])
        const hovered = hoveredId === node.id

        const [screenX, screenY] = toScreen(node.x, node.y)
        // Breathing radius — each node pulses at its own phase
        const breathe = 1 + 0.1 * Math.sin(now * 0.0025 + nodeIndex * 0.87)
        const r = Math.max(3, node.size * sc) * breathe

        ctx.save()

        if (isFiltered || !inConst) {
          ctx.globalAlpha = 0.1
        } else if (!inDateRange) {
          ctx.globalAlpha = 0.06
          ctx.beginPath()
          ctx.arc(screenX, screenY, r, 0, Math.PI * 2)
          ctx.fillStyle = node.color
          ctx.fill()
          ctx.restore()
          return
        }

        // ── Rotating orbit ring ─────────────────────────────────────────────
        const rotDir = nodeIndex % 2 === 0 ? 1 : -1
        ctx.save()
        ctx.translate(screenX, screenY)
        ctx.rotate(now * 0.0012 * rotDir + nodeIndex * 1.05)
        ctx.beginPath()
        ctx.setLineDash([4, 8])
        ctx.arc(0, 0, r * 2.1, 0, Math.PI * 2)
        ctx.strokeStyle = node.color + 'cc'
        ctx.lineWidth = 0.9
        ctx.shadowBlur = 7
        ctx.shadowColor = node.color
        ctx.stroke()
        ctx.setLineDash([])
        ctx.restore()

        // ── Counter-rotating outer ring (for connected nodes) ───────────────
        if (node.degree > 1) {
          ctx.save()
          ctx.translate(screenX, screenY)
          ctx.rotate(-now * 0.0007 * rotDir + nodeIndex * 0.65)
          ctx.beginPath()
          ctx.setLineDash([2, 12])
          ctx.arc(0, 0, r * 3.5, 0, Math.PI * 2)
          ctx.strokeStyle = node.color + '55'
          ctx.lineWidth = 0.6
          ctx.shadowBlur = 4
          ctx.shadowColor = node.color
          ctx.stroke()
          ctx.setLineDash([])
          ctx.restore()
        }

        // ── Orbiting sparkle dots ───────────────────────────────────────────
        const sparkleCount = Math.min(4, 1 + Math.floor(node.degree / 2))
        for (let i = 0; i < sparkleCount; i++) {
          const angle = now * 0.002 * rotDir + nodeIndex * 1.3 + (i * Math.PI * 2) / sparkleCount
          const orbitR = r * 2.1
          const spx = screenX + Math.cos(angle) * orbitR
          const spy = screenY + Math.sin(angle) * orbitR
          const sparkAlpha = 0.5 + 0.4 * Math.sin(now * 0.005 + i * 2.1 + nodeIndex)
          ctx.save()
          ctx.globalAlpha = sparkAlpha
          ctx.beginPath()
          ctx.arc(spx, spy, 1.5, 0, Math.PI * 2)
          ctx.fillStyle = '#ffffff'
          ctx.shadowBlur = 10
          ctx.shadowColor = node.color
          ctx.fill()
          ctx.restore()
        }

        // ── Outer halo — soft pulsing bloom ────────────────────────────────
        const haloAlpha = 0.05 + Math.sin(now * 0.002 + nodeIndex * 0.7) * 0.03
        ctx.save()
        ctx.globalAlpha = haloAlpha
        ctx.shadowBlur = r * 5
        ctx.shadowColor = node.color
        ctx.beginPath()
        ctx.arc(screenX, screenY, r * 2.6, 0, Math.PI * 2)
        ctx.fillStyle = node.color
        ctx.fill()
        ctx.restore()

        // ── Glow ────────────────────────────────────────────────────────────
        ctx.shadowBlur = hovered ? r * 8 : r * 4
        ctx.shadowColor = node.color

        // ── Radial gradient fill ─────────────────────────────────────────────
        const grad = ctx.createRadialGradient(
          screenX - r * 0.28, screenY - r * 0.28, 0,
          screenX, screenY, r
        )
        grad.addColorStop(0, '#ffffff')
        grad.addColorStop(0.25, node.color)
        grad.addColorStop(0.7, node.color + 'bb')
        grad.addColorStop(1, node.color + '44')

        ctx.beginPath()
        ctx.arc(screenX, screenY, r, 0, Math.PI * 2)
        ctx.fillStyle = grad
        ctx.fill()

        // ── Hover ring ───────────────────────────────────────────────────────
        if (hovered) {
          ctx.shadowBlur = 0
          // spinning accent dashes on hover
          ctx.save()
          ctx.translate(screenX, screenY)
          ctx.rotate(now * 0.003)
          ctx.beginPath()
          ctx.setLineDash([6, 6])
          ctx.arc(0, 0, r + 5, 0, Math.PI * 2)
          ctx.strokeStyle = '#00f5ff'
          ctx.lineWidth = 1.5
          ctx.shadowBlur = 16
          ctx.shadowColor = '#00f5ff'
          ctx.stroke()
          ctx.setLineDash([])
          ctx.restore()
        }

        // ── Label ─────────────────────────────────────────────────────────
        if ((node.degree > 2 || hovered) && !isFiltered && inConst) {
          ctx.shadowBlur = 0
          ctx.font = hovered ? 'bold 11px Orbitron, sans-serif' : '10px Orbitron, sans-serif'
          ctx.fillStyle = hovered ? '#00f5ff' : 'rgba(232,232,248,0.78)'
          ctx.textAlign = 'center'
          ctx.textBaseline = 'top'
          const label = star.title.length > 22 ? star.title.slice(0, 22) + '…' : star.title
          ctx.fillText(label, screenX, screenY + r + 5)
        }

        ctx.restore()
      })

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  // ── Hit-test: find node under screen coords ─────────────────────────────────
  const getNodeAt = useCallback((mx: number, my: number): SimNode | null => {
    const sc = scaleRef.current
    const ox = offsetXRef.current
    const oy = offsetYRef.current
    const filterActive = activeFilterRef.current
    let closest: SimNode | null = null
    let closestDist = 24

    nodesRef.current.forEach(node => {
      if (node.x == null || node.y == null) return
      if (filterActive !== 'all' && node.star.content_type !== filterActive) return
      const screenX = node.x * sc + ox
      const screenY = node.y * sc + oy
      const r = Math.max(3, node.size * sc)
      const d = Math.sqrt((screenX - mx) ** 2 + (screenY - my) ** 2)
      if (d < r + 8 && d < closestDist) {
        closestDist = d
        closest = node
      }
    })

    return closest
  }, [])

  // ── Mouse handlers ─────────────────────────────────────────────────────────
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    mouseDownPosRef.current = { x: e.clientX, y: e.clientY }
    lastMouseRef.current = { x: e.clientX, y: e.clientY }

    const node = getNodeAt(mx, my)
    if (node) {
      dragNodeRef.current = node
      node.fx = node.x
      node.fy = node.y
      simRef.current?.alphaTarget(0.3).restart()
    } else {
      isDraggingRef.current = true
    }
  }, [getNodeAt])

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const wasDragged =
      Math.abs(e.clientX - mouseDownPosRef.current.x) > 5 ||
      Math.abs(e.clientY - mouseDownPosRef.current.y) > 5

    if (dragNodeRef.current) {
      if (!wasDragged) {
        const clicked = dragNodeRef.current
        onStarSelect?.(clicked.star)
        const clickedConst = clicked.star.constellation
        if (selectedConstellationRef.current === clickedConst) {
          selectedConstellationRef.current = null
          setSelectedConstellation(null)
        } else {
          selectedConstellationRef.current = clickedConst
          setSelectedConstellation(clickedConst)
        }
      }
      dragNodeRef.current.fx = null
      dragNodeRef.current.fy = null
      simRef.current?.alphaTarget(0)
      dragNodeRef.current = null
    }

    isDraggingRef.current = false
  }, [onStarSelect])

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    if (dragNodeRef.current) {
      const node = dragNodeRef.current
      node.fx = (mx - offsetXRef.current) / scaleRef.current
      node.fy = (my - offsetYRef.current) / scaleRef.current
      lastMouseRef.current = { x: e.clientX, y: e.clientY }
      return
    }

    if (isDraggingRef.current) {
      const dx = e.clientX - lastMouseRef.current.x
      const dy = e.clientY - lastMouseRef.current.y
      offsetXRef.current += dx
      offsetYRef.current += dy
      lastMouseRef.current = { x: e.clientX, y: e.clientY }
      return
    }

    const node = getNodeAt(mx, my)
    hoveredStarIdRef.current = node ? node.id : null
    setHoveredStar(node ? node.star : null)
    if (node) setTooltipPos({ x: e.clientX, y: e.clientY })
  }, [getNodeAt])

  const handleMouseLeave = useCallback(() => {
    isDraggingRef.current = false
    if (dragNodeRef.current) {
      dragNodeRef.current.fx = null
      dragNodeRef.current.fy = null
      simRef.current?.alphaTarget(0)
      dragNodeRef.current = null
    }
    hoveredStarIdRef.current = null
    setHoveredStar(null)
  }, [])

  const handleClearConstellation = useCallback(() => {
    selectedConstellationRef.current = null
    setSelectedConstellation(null)
  }, [])

  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9
    const newScale = Math.max(0.1, Math.min(8, scaleRef.current * zoomFactor))
    offsetXRef.current = mx + (offsetXRef.current - mx) * (newScale / scaleRef.current)
    offsetYRef.current = my + (offsetYRef.current - my) * (newScale / scaleRef.current)
    scaleRef.current = newScale
  }, [])

  // ── Touch handlers ─────────────────────────────────────────────────────────
  const handleTouchStart = useCallback((e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    if (e.touches.length === 1) {
      const t = e.touches[0]
      isDraggingRef.current = true
      lastMouseRef.current = { x: t.clientX, y: t.clientY }
      mouseDownPosRef.current = { x: t.clientX, y: t.clientY }
      lastTouchDistRef.current = 0
    } else if (e.touches.length === 2) {
      const dx = e.touches[1].clientX - e.touches[0].clientX
      const dy = e.touches[1].clientY - e.touches[0].clientY
      lastTouchDistRef.current = Math.sqrt(dx * dx + dy * dy)
      isDraggingRef.current = false
    }
  }, [])

  const handleTouchMove = useCallback((e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    if (e.touches.length === 1 && isDraggingRef.current) {
      const t = e.touches[0]
      const dx = t.clientX - lastMouseRef.current.x
      const dy = t.clientY - lastMouseRef.current.y
      offsetXRef.current += dx
      offsetYRef.current += dy
      lastMouseRef.current = { x: t.clientX, y: t.clientY }
    } else if (e.touches.length === 2) {
      const dx = e.touches[1].clientX - e.touches[0].clientX
      const dy = e.touches[1].clientY - e.touches[0].clientY
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (lastTouchDistRef.current > 0) {
        const delta = dist - lastTouchDistRef.current
        const oldScale = scaleRef.current
        const newScale = Math.min(Math.max(oldScale * (1 + delta * 0.008), 0.1), 8)
        const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2
        const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2
        const canvas = canvasRef.current
        const rect = canvas?.getBoundingClientRect()
        if (rect) {
          const px = cx - rect.left
          const py = cy - rect.top
          offsetXRef.current = px - (px - offsetXRef.current) * (newScale / oldScale)
          offsetYRef.current = py - (py - offsetYRef.current) * (newScale / oldScale)
        }
        scaleRef.current = newScale
      }
      lastTouchDistRef.current = dist
    }
  }, [])

  const handleTouchEnd = useCallback((e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    if (e.touches.length === 0) {
      isDraggingRef.current = false
      lastTouchDistRef.current = 0
    }
  }, [])

  const handleFilterChange = useCallback((filter: string) => {
    activeFilterRef.current = filter
    setActiveFilter(filter)
  }, [])

  const filteredCount = activeFilter === 'all' ? stars.length : stars.filter(s => s.content_type === activeFilter).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <div ref={containerRef} className="galaxy-container">
        <canvas
          ref={canvasRef}
          className="galaxy-canvas"
          style={{ touchAction: 'none', cursor: hoveredStar ? 'pointer' : 'default' }}
          onMouseDown={handleMouseDown}
          onMouseUp={handleMouseUp}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          onWheel={handleWheel}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
        />

        {selectedConstellation && (
          <div className="galaxy-constellation-label">
            <span>{selectedConstellation.toUpperCase()}</span>
            <button onClick={handleClearConstellation}>× Clear</button>
          </div>
        )}

        <div className="galaxy-stats">
          ✦ {filteredCount} stars · {edges.length} edges
        </div>

        {hoveredStar && (
          <div
            className="galaxy-tooltip"
            style={{ position: 'fixed', left: tooltipPos.x + 14, top: tooltipPos.y - 10 }}
          >
            <div style={{ color: nodeColor(hoveredStar.content_type), fontWeight: 700, marginBottom: 3 }}>
              {hoveredStar.title}
            </div>
            <div style={{ fontSize: 10, color: '#6b7280' }}>
              {hoveredStar.content_type} · {hoveredStar.constellation}
            </div>
            {hoveredStar.tags.length > 0 && (
              <div style={{ fontSize: 10, color: '#6b7280', marginTop: 2 }}>
                {hoveredStar.tags.slice(0, 4).join(', ')}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Filter bar */}
      <div className="galaxy-filter-bar">
        <button
          className={`galaxy-filter-btn${activeFilter === 'all' ? ' active' : ''}`}
          onClick={() => handleFilterChange('all')}
        >
          All ({stars.length})
        </button>
        {contentTypes.map(ct => (
          <button
            key={ct}
            className={`galaxy-filter-btn${activeFilter === ct ? ' active' : ''}`}
            onClick={() => handleFilterChange(ct)}
          >
            {ct} ({stars.filter(s => s.content_type === ct).length})
          </button>
        ))}
      </div>

      {/* Date range slider */}
      {stars.length > 0 && (
        <div className="galaxy-time-bar">
          <span style={{ whiteSpace: 'nowrap', fontSize: '0.7rem', color: 'var(--muted)' }}>⏱</span>
          <input
            type="range"
            min={minDate}
            max={maxDate}
            defaultValue={minDate}
            style={{ flex: 1, accentColor: 'var(--cyan)' }}
            onChange={e => {
              const from = Number(e.target.value)
              dateRangeRef.current = [from, dateRangeRef.current[1]]
              const fromStr = from <= minDate ? 'Start' : new Date(from).toLocaleDateString()
              const toVal = dateRangeRef.current[1]
              const toStr = toVal >= maxDate ? 'Now' : new Date(toVal).toLocaleDateString()
              setDateLabel(fromStr === 'Start' && toStr === 'Now' ? 'All time' : `${fromStr} → ${toStr}`)
            }}
          />
          <span className="galaxy-time-label">{dateLabel}</span>
          <input
            type="range"
            min={minDate}
            max={maxDate}
            defaultValue={maxDate}
            style={{ flex: 1, accentColor: 'var(--cyan)' }}
            onChange={e => {
              const to = Number(e.target.value)
              dateRangeRef.current = [dateRangeRef.current[0], to]
              const fromVal = dateRangeRef.current[0]
              const fromStr = fromVal <= minDate ? 'Start' : new Date(fromVal).toLocaleDateString()
              const toStr = to >= maxDate ? 'Now' : new Date(to).toLocaleDateString()
              setDateLabel(fromStr === 'Start' && toStr === 'Now' ? 'All time' : `${fromStr} → ${toStr}`)
            }}
          />
          <button
            style={{ fontSize: '0.7rem', background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', padding: '0 4px' }}
            onClick={() => { dateRangeRef.current = [0, Infinity]; setDateLabel('All time') }}
          >Reset</button>
        </div>
      )}
    </div>
  )
}
