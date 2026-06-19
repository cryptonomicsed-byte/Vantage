import React, { useRef, useEffect, useState, useCallback } from 'react'

// ─── Types ────────────────────────────────────────────────────────────────────

interface GalaxyStar {
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
  predictions?: { predictions?: number[]; lower?: number[]; upper?: number[] }
  patterns?: Array<{ pattern: string; confidence: number }>
}

interface Props {
  data: GalaxyData
  agentName: string
}

// ─── Projection ───────────────────────────────────────────────────────────────

function project(
  x: number,
  y: number,
  z: number,
  scale: number,
  offsetX: number,
  offsetY: number
): [number, number] {
  const sx = (x - z * 0.5) * scale + offsetX
  const sy = (y + z * 0.25) * scale + offsetY
  return [sx, sy]
}

// ─── Drawing ──────────────────────────────────────────────────────────────────

function drawScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  stars: GalaxyStar[],
  edges: GalaxyEdge[],
  nebulae: GalaxyNebula[],
  scale: number,
  offsetX: number,
  offsetY: number,
  hoveredStarId: string | null,
  activeFilter: string,
  selectedConstellation: string | null
) {
  ctx.clearRect(0, 0, w, h)

  // Background
  ctx.fillStyle = '#040408'
  ctx.fillRect(0, 0, w, h)

  const filteredStars = activeFilter === 'all'
    ? stars
    : stars.filter(s => s.content_type === activeFilter)

  const filteredStarIds = new Set(filteredStars.map(s => s.id))

  // ── Nebulae ────────────────────────────────────────────────────────────────
  nebulae.forEach(neb => {
    const [sx, sy] = project(neb.x, neb.y, neb.z, scale, offsetX, offsetY)
    const r = neb.size * scale * 0.15
    if (r < 1) return

    ctx.save()
    const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, r)
    grad.addColorStop(0, `rgba(138,75,255,${neb.opacity * 0.35})`)
    grad.addColorStop(0.5, `rgba(138,75,255,${neb.opacity * 0.1})`)
    grad.addColorStop(1, 'rgba(138,75,255,0)')
    ctx.beginPath()
    ctx.arc(sx, sy, r, 0, Math.PI * 2)
    ctx.fillStyle = grad
    ctx.fill()
    ctx.restore()
  })

  // ── Edges ──────────────────────────────────────────────────────────────────
  edges.forEach(edge => {
    const [sx1, sy1] = project(edge.source[0], edge.source[1], edge.source[2], scale, offsetX, offsetY)
    const [sx2, sy2] = project(edge.target[0], edge.target[1], edge.target[2], scale, offsetX, offsetY)

    ctx.save()
    ctx.beginPath()
    ctx.moveTo(sx1, sy1)
    ctx.lineTo(sx2, sy2)
    ctx.strokeStyle = 'rgba(0,245,255,0.3)'
    ctx.lineWidth = Math.max(0.5, edge.weight * 0.5)
    ctx.globalAlpha = 0.3
    ctx.stroke()
    ctx.restore()
  })

  // ── Constellation labels ───────────────────────────────────────────────────
  const constellationCenters: Record<string, { x: number; y: number; count: number }> = {}
  filteredStars.forEach(star => {
    const [sx, sy] = project(star.x, star.y, star.z, scale, offsetX, offsetY)
    if (!constellationCenters[star.constellation]) {
      constellationCenters[star.constellation] = { x: 0, y: 0, count: 0 }
    }
    constellationCenters[star.constellation].x += sx
    constellationCenters[star.constellation].y += sy
    constellationCenters[star.constellation].count++
  })

  Object.entries(constellationCenters).forEach(([name, center]) => {
    if (center.count < 1) return
    const cx = center.x / center.count
    const cy = center.y / center.count
    ctx.save()
    ctx.font = '9px Orbitron, sans-serif'
    ctx.fillStyle = 'rgba(107,114,128,0.6)'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(name.toUpperCase(), cx, cy - 24)
    ctx.restore()
  })

  // ── Stars ──────────────────────────────────────────────────────────────────
  filteredStars.forEach(star => {
    const [sx, sy] = project(star.x, star.y, star.z, scale, offsetX, offsetY)
    const r = Math.max(2, (star.size / 50) * 10 * scale)
    const hovered = hoveredStarId === star.id
    const inSelectedConstellation = !selectedConstellation || star.constellation === selectedConstellation

    ctx.save()

    if (!inSelectedConstellation) {
      ctx.globalAlpha = 0.3
    }

    // Outer glow
    ctx.shadowBlur = hovered ? 30 : 14
    ctx.shadowColor = star.color

    // Radial gradient
    const grad = ctx.createRadialGradient(sx - r * 0.25, sy - r * 0.25, 0, sx, sy, r)
    grad.addColorStop(0, '#ffffff')
    grad.addColorStop(0.3, star.color)
    grad.addColorStop(1, star.color + '88')

    ctx.beginPath()
    ctx.arc(sx, sy, r, 0, Math.PI * 2)
    ctx.fillStyle = grad
    ctx.fill()

    // Hover ring
    if (hovered) {
      ctx.beginPath()
      ctx.arc(sx, sy, r + 3, 0, Math.PI * 2)
      ctx.strokeStyle = '#00f5ff'
      ctx.lineWidth = 1.5
      ctx.shadowBlur = 10
      ctx.shadowColor = '#00f5ff'
      ctx.stroke()
    }

    // Label for larger stars
    if (r >= 4 || hovered) {
      ctx.shadowBlur = 0
      ctx.font = '10px Orbitron, sans-serif'
      ctx.fillStyle = hovered ? '#00f5ff' : 'rgba(232,232,248,0.75)'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      const label = star.title.length > 22 ? star.title.slice(0, 22) + '…' : star.title
      ctx.fillText(label, sx, sy + r + 3)
    }

    ctx.restore()
  })

  // Dimmed overlay for filtered-out stars (show as very faint)
  if (activeFilter !== 'all') {
    stars.filter(s => !filteredStarIds.has(s.id)).forEach(star => {
      const [sx, sy] = project(star.x, star.y, star.z, scale, offsetX, offsetY)
      const r = Math.max(1, (star.size / 50) * 6 * scale)
      ctx.save()
      ctx.beginPath()
      ctx.arc(sx, sy, r, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(107,114,128,0.15)'
      ctx.fill()
      ctx.restore()
    })
  }
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function GalaxyViewer({ data, agentName: _agentName }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasSizeRef = useRef({ w: 0, h: 0 })
  const rafRef = useRef<number>(0)

  // View state in refs for rAF loop
  const scaleRef = useRef(0.06)
  const offsetXRef = useRef(0)
  const offsetYRef = useRef(0)
  const hoveredStarIdRef = useRef<string | null>(null)
  const activeFilterRef = useRef<string>('all')
  const selectedConstellationRef = useRef<string | null>(null)
  const isDraggingRef = useRef(false)
  const lastMouseRef = useRef({ x: 0, y: 0 })
  const mouseDownPosRef = useRef({ x: 0, y: 0 })

  // React state for UI
  const [activeFilter, setActiveFilter] = useState<string>('all')
  const [hoveredStar, setHoveredStar] = useState<GalaxyStar | null>(null)
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 })
  const [selectedConstellation, setSelectedConstellation] = useState<string | null>(null)

  const stars = data.stars || []
  const edges = data.edges || []
  const nebulae = data.nebulae || []

  // Derive unique content types for filter bar
  const contentTypes = Array.from(new Set(stars.map(s => s.content_type).filter(Boolean)))

  // ── Center the view on mount ───────────────────────────────────────────────
  useEffect(() => {
    if (stars.length === 0) return
    const bounds = data.bounds
    if (bounds && bounds.min && bounds.max) {
      const midX = (bounds.min[0] + bounds.max[0]) / 2
      const midY = (bounds.min[1] + bounds.max[1]) / 2
      const midZ = (bounds.min[2] + bounds.max[2]) / 2
      // After projection, center this point at canvas center
      const canvas = canvasRef.current
      const cw = canvas ? canvas.clientWidth : 800
      const ch = canvas ? canvas.clientHeight : 500
      const scale = scaleRef.current
      const [px, py] = [
        (midX - midZ * 0.5) * scale,
        (midY + midZ * 0.25) * scale,
      ]
      offsetXRef.current = cw / 2 - px
      offsetYRef.current = ch / 2 - py
    }
  }, [data, stars.length])

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
    return () => ro.disconnect()
  }, [])

  // ── rAF render loop ────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const loop = () => {
      const ctx = canvas.getContext('2d')
      if (!ctx) { rafRef.current = requestAnimationFrame(loop); return }
      const { w, h } = canvasSizeRef.current
      if (w === 0 || h === 0) { rafRef.current = requestAnimationFrame(loop); return }

      if (stars.length === 0) {
        ctx.clearRect(0, 0, w, h)
        ctx.fillStyle = '#040408'
        ctx.fillRect(0, 0, w, h)
        ctx.font = '14px Orbitron, sans-serif'
        ctx.fillStyle = '#6b7280'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText('No memory stars in this vault yet', w / 2, h / 2)
      } else {
        drawScene(
          ctx, w, h,
          stars, edges, nebulae,
          scaleRef.current, offsetXRef.current, offsetYRef.current,
          hoveredStarIdRef.current,
          activeFilterRef.current,
          selectedConstellationRef.current
        )
      }

      rafRef.current = requestAnimationFrame(loop)
    }

    rafRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafRef.current)
  }, [stars, edges, nebulae])

  // ── Mouse: pan + click ───────────────────────────────────────────────────
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    isDraggingRef.current = true
    lastMouseRef.current = { x: e.clientX, y: e.clientY }
    mouseDownPosRef.current = { x: e.clientX, y: e.clientY }
  }, [])

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const wasDragged =
      Math.abs(e.clientX - mouseDownPosRef.current.x) > 4 ||
      Math.abs(e.clientY - mouseDownPosRef.current.y) > 4
    isDraggingRef.current = false

    if (!wasDragged && hoveredStarIdRef.current) {
      const canvas = canvasRef.current
      const container = containerRef.current
      if (!canvas || !container) return

      const clickedStar = stars.find(s => s.id === hoveredStarIdRef.current)
      if (!clickedStar) return

      const clickedConstellation = clickedStar.constellation
      const currentSelected = selectedConstellationRef.current

      if (currentSelected === clickedConstellation) {
        // Zoom in to center on constellation bounding box
        const constellationStars = stars.filter(s => s.constellation === clickedConstellation)
        if (constellationStars.length === 0) return

        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
        constellationStars.forEach(s => {
          const [px, py] = project(s.x, s.y, s.z, 1, 0, 0)
          if (px < minX) minX = px
          if (px > maxX) maxX = px
          if (py < minY) minY = py
          if (py > maxY) maxY = py
        })

        const cw = container.clientWidth
        const ch = container.clientHeight
        const bboxW = maxX - minX || 100
        const bboxH = maxY - minY || 100
        const padding = 0.8
        const newScale = Math.min(
          (cw * padding) / bboxW,
          (ch * padding) / bboxH,
          2
        )
        const centerPX = (minX + maxX) / 2
        const centerPY = (minY + maxY) / 2
        scaleRef.current = newScale
        offsetXRef.current = cw / 2 - centerPX * newScale
        offsetYRef.current = ch / 2 - centerPY * newScale
      } else {
        // Select this constellation
        selectedConstellationRef.current = clickedConstellation
        setSelectedConstellation(clickedConstellation)
      }
    }
  }, [stars])

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return

    if (isDraggingRef.current) {
      const dx = e.clientX - lastMouseRef.current.x
      const dy = e.clientY - lastMouseRef.current.y
      offsetXRef.current += dx
      offsetYRef.current += dy
      lastMouseRef.current = { x: e.clientX, y: e.clientY }
      return
    }

    // Hover detection
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    const filterActive = activeFilterRef.current
    const visibleStars = filterActive === 'all' ? stars : stars.filter(s => s.content_type === filterActive)

    let closest: GalaxyStar | null = null
    let closestDist = 15

    visibleStars.forEach(star => {
      const [sx, sy] = project(star.x, star.y, star.z, scaleRef.current, offsetXRef.current, offsetYRef.current)
      const d = Math.sqrt((sx - mx) ** 2 + (sy - my) ** 2)
      if (d < closestDist) {
        closestDist = d
        closest = star
      }
    })

    const closestStar = closest as GalaxyStar | null
    hoveredStarIdRef.current = closestStar ? closestStar.id : null
    setHoveredStar(closestStar)
    if (closestStar) setTooltipPos({ x: e.clientX, y: e.clientY })
  }, [stars])

  const handleMouseLeave = useCallback(() => {
    isDraggingRef.current = false
    hoveredStarIdRef.current = null
    setHoveredStar(null)
  }, [])

  // ── Clear constellation selection ────────────────────────────────────────
  const handleClearConstellation = useCallback(() => {
    selectedConstellationRef.current = null
    setSelectedConstellation(null)
  }, [])

  // ── Scroll: zoom ──────────────────────────────────────────────────────────
  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const canvas = canvasRef.current
    if (!canvas) return

    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9
    const newScale = Math.max(0.01, Math.min(2, scaleRef.current * zoomFactor))

    // Zoom towards mouse position
    offsetXRef.current = mx + (offsetXRef.current - mx) * (newScale / scaleRef.current)
    offsetYRef.current = my + (offsetYRef.current - my) * (newScale / scaleRef.current)
    scaleRef.current = newScale
  }, [])

  // ── Filter change ─────────────────────────────────────────────────────────
  const handleFilterChange = useCallback((filter: string) => {
    activeFilterRef.current = filter
    setActiveFilter(filter)
  }, [])

  const filteredCount = activeFilter === 'all' ? stars.length : stars.filter(s => s.content_type === activeFilter).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* Canvas container */}
      <div ref={containerRef} className="galaxy-container">
        <canvas
          ref={canvasRef}
          className="galaxy-canvas"
          style={{ cursor: isDraggingRef.current ? 'grabbing' : 'crosshair' }}
          onMouseDown={handleMouseDown}
          onMouseUp={handleMouseUp}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          onWheel={handleWheel}
        />

        {/* Constellation label overlay */}
        {selectedConstellation && (
          <div className="galaxy-constellation-label">
            <span>{selectedConstellation.toUpperCase()}</span>
            <button onClick={handleClearConstellation}>× Clear</button>
          </div>
        )}

        {/* Stats overlay */}
        <div className="galaxy-stats">
          ✦ {filteredCount} stars · {edges.length} edges · {nebulae.length} nebulae
        </div>

        {/* Hover tooltip */}
        {hoveredStar && (
          <div
            className="galaxy-tooltip"
            style={{
              position: 'fixed',
              left: tooltipPos.x + 14,
              top: tooltipPos.y - 10,
            }}
          >
            <div style={{ color: hoveredStar.color, fontWeight: 700, marginBottom: 3 }}>
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
    </div>
  )
}
