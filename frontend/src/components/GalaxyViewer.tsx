import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { buildLayout } from './galaxyLayout'
import { computeWasmLayout, galaxyWasmEnabled, nodeGixId, warnGalaxyWasmOnce } from './galaxyLayoutClient'
import type { WasmLayoutSwap } from './galaxyLayoutClient'

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
  clusters?: Record<string, Array<unknown>>
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

/* Layout lives in ./galaxyLayout (pure, shared with the wasm parity script).
   Optional wasm path (services/galaxy_layout in a Web Worker) is behind the
   default-off flag localStorage['galaxy.wasm']==='1' / VITE_GALAXY_WASM=1. */

const DETAIL_MAX = 600       // full glow + labels only when this few nodes are on screen
const LIST_MAX = 300         // rows rendered in the node list (search narrows it)
const MM_W = 180, MM_H = 120 // minimap

// ─── Main component ────────────────────────────────────────────────────────────
export default function GalaxyViewer({ data, agentName: _agentName, onStarSelect }: Props) {
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const rafRef       = useRef<number>(0)
  const sizeRef      = useRef({ w: 0, h: 0 })

  // View transform
  const scaleRef = useRef(0.07)
  const oxRef    = useRef(0)
  const oyRef    = useRef(0)
  const fitScaleRef = useRef(0.07)
  const fittedRef   = useRef(false)

  // Pointers (mouse / touch / pen)
  const pointersRef = useRef(new Map<number, { x: number; y: number }>())
  const downRef     = useRef<{ x: number; y: number; moved: boolean } | null>(null)
  const pinchRef    = useRef<{ d: number; s: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  // Animation
  const timeRef   = useRef(0)
  const pulsesRef = useRef<SynapsePulse[]>([])

  // Last frame's on-screen nodes (for hit-testing without scanning every node)
  const visIdxRef = useRef<Int32Array>(new Int32Array(0))
  const visSxRef  = useRef<Float32Array>(new Float32Array(0))
  const visSyRef  = useRef<Float32Array>(new Float32Array(0))
  const visCountRef = useRef(0)

  // Hover / selection
  const hoveredRef = useRef<string | null>(null)
  const [hoveredNode, setHoveredNode] = useState<NeuralNode | null>(null)
  const [tooltipPos, setTooltipPos]   = useState({ x: 0, y: 0 })
  const [selectedNode, setSelectedNode] = useState<NeuralNode | null>(null)
  const selectedRef = useRef<NeuralNode | null>(null)
  selectedRef.current = selectedNode
  const [activeFilter, setActiveFilter] = useState('all')
  const filterRef = useRef('all')

  // Node list
  const [query, setQuery] = useState('')
  const [listConst, setListConst] = useState('all')
  const [listOpen, setListOpen] = useState(true)

  const nodes: NeuralNode[] = useMemo(() => data.stars || [], [data])
  const edges   = useMemo(() => data.edges || [], [data])
  const nebulae = data.nebulae || []

  // JS layout is always computed and rendered first; with the wasm flag on the
  // worker's (parity-checked) layout is swapped in when it answers.
  const jsLayout = useMemo(() => buildLayout(nodes), [nodes])
  const wasmFlag = useMemo(() => galaxyWasmEnabled(), [])
  const [wasmSwap, setWasmSwap] = useState<{ nodes: NeuralNode[]; swap: WasmLayoutSwap } | null>(null)
  useEffect(() => {
    if (!wasmFlag || nodes.length === 0) return
    let live = true
    try {
      computeWasmLayout(nodes, jsLayout)
        .then(swap => { if (live) setWasmSwap({ nodes, swap }) })
        .catch(err => warnGalaxyWasmOnce(err))
    } catch (err) { warnGalaxyWasmOnce(err) }
    return () => { live = false }
  }, [wasmFlag, nodes, jsLayout])
  const wasmActive = !!wasmSwap && wasmSwap.nodes === nodes
  const layout = wasmActive ? wasmSwap!.swap.layout : jsLayout
  const layoutRef = useRef(layout)
  layoutRef.current = layout

  const contentTypes = useMemo(() => Array.from(new Set(nodes.map(n => n.content_type).filter(Boolean))), [nodes])
  const typeCounts = useMemo(() => {
    const m: Record<string, number> = {}
    nodes.forEach(n => { m[n.content_type] = (m[n.content_type] || 0) + 1 })
    return m
  }, [nodes])

  // edges resolved to node indices (by id, else by the server's coordinates)
  const edgeIdx = useMemo(() => edges.map(e => {
    const a = layout.idx.get(e.subject) ?? layout.byCoord.get(`${e.source[0]}|${e.source[1]}|${e.source[2]}`)
    const b = layout.idx.get(e.object) ?? layout.byCoord.get(`${e.target[0]}|${e.target[1]}|${e.target[2]}`)
    return a == null || b == null ? null : [a, b] as [number, number]
    // idx/byCoord are shared by the JS and wasm layouts, so a swap keeps the pulses
  }), [edges, layout.idx, layout.byCoord])

  // minimap: pre-rendered once per layout
  const minimap = useMemo(() => {
    if (typeof document === 'undefined' || nodes.length === 0) return null
    const c = document.createElement('canvas')
    c.width = MM_W; c.height = MM_H
    const ctx = c.getContext('2d')
    if (!ctx) return null
    const pad = 8
    const rw = layout.maxX - layout.minX || 1, rh = layout.maxY - layout.minY || 1
    const s = Math.min((MM_W - pad * 2) / rw, (MM_H - pad * 2) / rh)
    ctx.fillStyle = 'rgba(0,0,0,0.9)'; ctx.fillRect(0, 0, MM_W, MM_H)
    for (let i = 0; i < nodes.length; i++) {
      ctx.fillStyle = getNodeColor(nodes[i])
      ctx.fillRect(pad + (layout.wx[i] - layout.minX) * s, pad + (layout.wy[i] - layout.minY) * s, 1.5, 1.5)
    }
    return { canvas: c, s, pad }
  }, [layout, nodes])

  // ── View helpers ──────────────────────────────────────────────────────────
  const clampScale = (s: number) => Math.max(fitScaleRef.current / 4, Math.min(6, s))

  const fitTo = useCallback((x0: number, y0: number, x1: number, y1: number, pad = 40) => {
    const { w, h } = sizeRef.current
    if (!w || !h) return
    const s = Math.min((w - pad * 2) / Math.max(1, x1 - x0), (h - pad * 2) / Math.max(1, y1 - y0))
    scaleRef.current = s
    oxRef.current = (w - (x1 - x0) * s) / 2 - x0 * s
    oyRef.current = (h - (y1 - y0) * s) / 2 - y0 * s
  }, [])

  const fitAll = useCallback(() => {
    const L = layoutRef.current
    fitTo(L.minX, L.minY, L.maxX, L.maxY)
    fitScaleRef.current = scaleRef.current
  }, [fitTo])

  const zoomAt = useCallback((mx: number, my: number, factor: number) => {
    const ns = clampScale(scaleRef.current * factor)
    oxRef.current = mx + (oxRef.current - mx) * (ns / scaleRef.current)
    oyRef.current = my + (oyRef.current - my) * (ns / scaleRef.current)
    scaleRef.current = ns
  }, [])

  const focusNode = useCallback((node: NeuralNode, open = true) => {
    const i = layoutRef.current.idx.get(node.id)
    const { w, h } = sizeRef.current
    if (i == null || !w) return
    const s = Math.max(scaleRef.current, Math.min(1.2, fitScaleRef.current * 25))
    scaleRef.current = s
    oxRef.current = w / 2 - layoutRef.current.wx[i] * s
    oyRef.current = h / 2 - layoutRef.current.wy[i] * s
    setSelectedNode(node)
    if (open) onStarSelect?.(node)
  }, [onStarSelect])

  // ── Seed initial synapse pulses ──────────────────────────────────────────
  useEffect(() => {
    const initial: SynapsePulse[] = []
    edgeIdx.forEach((e, i) => {
      if (e && Math.random() < 0.6) {
        initial.push({ edgeIdx: i, t: Math.random(), speed: 0.003 + Math.random() * 0.005, opacity: 0.5 + Math.random() * 0.5 })
      }
    })
    pulsesRef.current = initial
  }, [edgeIdx])

  // ── Canvas resize (fits the view the first time it has a size) ───────────
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
      if (!fittedRef.current && w && h && layoutRef.current.wx.length) { fitAll(); fittedRef.current = true }
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)
    return () => ro.disconnect()
  }, [fitAll])

  // new data → fit again (keyed on the JS layout: the wasm swap has the same
  // positions, so it must not reset the user's pan/zoom)
  useEffect(() => {
    fittedRef.current = false
    if (sizeRef.current.w && nodes.length) { fitAll(); fittedRef.current = true }
  }, [jsLayout, fitAll, nodes.length])

  // ── Wheel: a real non-passive listener so it zooms instead of scrolling ──
  // (React's onWheel is passive; its preventDefault was ignored, so the page
  // scrolled away under the cursor.)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = canvas.getBoundingClientRect()
      const k = e.deltaMode === 1 ? 16 : 1           // lines → px
      const f = Math.exp(-e.deltaY * k * (e.ctrlKey ? 0.01 : 0.0015))
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, f)
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  // ── rAF render loop ───────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const loop = () => {
      const ctx = canvas.getContext('2d')
      const { w, h } = sizeRef.current
      if (ctx && w && h) {
        timeRef.current += 0.016
        const pulses = pulsesRef.current
        for (let i = pulses.length - 1; i >= 0; i--) {
          pulses[i].t += pulses[i].speed
          if (pulses[i].t > 1) {
            pulses[i] = { edgeIdx: Math.floor(Math.random() * edgeIdx.length), t: 0,
              speed: 0.003 + Math.random() * 0.006, opacity: 0.6 + Math.random() * 0.4 }
          }
        }
        draw(ctx, w, h)
      }
      rafRef.current = requestAnimationFrame(loop)
    }
    rafRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edgeIdx, nebulae, layout, minimap])

  // ── Draw ──────────────────────────────────────────────────────────────────
  function draw(ctx: CanvasRenderingContext2D, w: number, h: number) {
    const t = timeRef.current
    const sc = scaleRef.current, ox = oxRef.current, oy = oyRef.current
    const hov = hoveredRef.current, sel = selectedRef.current
    const filt = filterRef.current
    const L = layout

    ctx.fillStyle = '#000'
    ctx.fillRect(0, 0, w, h)

    // constellation frames + labels (navigation aid)
    ctx.save()
    ctx.font = `600 ${Math.max(10, Math.min(16, 220 * sc))}px Inter, sans-serif`
    ctx.textBaseline = 'bottom'
    L.cells.forEach(c => {
      const x = c.x * sc + ox, y = c.y * sc + oy, cw = c.w * sc, ch = c.h * sc
      if (x > w || y > h || x + cw < 0 || y + ch < -40) return
      ctx.strokeStyle = 'rgba(0,245,255,0.06)'
      ctx.strokeRect(x - 8, y - 8, cw + 16, ch + 16)
      ctx.fillStyle = 'rgba(0,245,255,0.55)'
      ctx.fillText(`${c.name} · ${c.count.toLocaleString()}`, x - 6, y - 12)
    })
    ctx.restore()

    // project + cull
    const n = nodes.length
    if (visIdxRef.current.length < n) {
      visIdxRef.current = new Int32Array(n); visSxRef.current = new Float32Array(n); visSyRef.current = new Float32Array(n)
    }
    const vi = visIdxRef.current, vx = visSxRef.current, vy = visSyRef.current
    let vc = 0
    for (let i = 0; i < n; i++) {
      if (filt !== 'all' && nodes[i].content_type !== filt) continue
      const sx = L.wx[i] * sc + ox, sy = L.wy[i] * sc + oy
      if (sx < -20 || sy < -20 || sx > w + 20 || sy > h + 20) continue
      vi[vc] = i; vx[vc] = sx; vy[vc] = sy; vc++
    }
    visCountRef.current = vc
    const detail = vc <= DETAIL_MAX

    // edges
    ctx.save()
    edgeIdx.forEach((e, k) => {
      if (!e) return
      const [a, b] = e
      const x1 = L.wx[a] * sc + ox, y1 = L.wy[a] * sc + oy, x2 = L.wx[b] * sc + ox, y2 = L.wy[b] * sc + oy
      if (Math.max(x1, x2) < 0 || Math.min(x1, x2) > w || Math.max(y1, y2) < 0 || Math.min(y1, y2) > h) return
      const lit = hov && (nodes[a].id === hov || nodes[b].id === hov)
      ctx.strokeStyle = lit ? 'rgba(0,245,255,0.75)' : 'rgba(0,200,255,0.14)'
      ctx.lineWidth = lit ? Math.max(1, edges[k].weight * 1.5) : Math.max(0.4, edges[k].weight * 0.5)
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
    })
    if (detail) {
      pulsesRef.current.forEach(p => {
        const e = edgeIdx[p.edgeIdx]
        if (!e) return
        const [a, b] = e
        const px = (L.wx[a] + (L.wx[b] - L.wx[a]) * p.t) * sc + ox
        const py = (L.wy[a] + (L.wy[b] - L.wy[a]) * p.t) * sc + oy
        if (px < 0 || py < 0 || px > w || py > h) return
        ctx.beginPath(); ctx.arc(px, py, 2.5, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(0,245,255,${p.opacity * Math.sin(p.t * Math.PI)})`
        ctx.fill()
      })
    }
    ctx.restore()

    // nodes
    if (!detail) {
      // many on screen: plain dots, no gradients/shadows (15k glows froze the tab)
      const r = Math.max(1.2, Math.min(3, 6 * sc))
      for (let k = 0; k < vc; k++) {
        ctx.fillStyle = getNodeColor(nodes[vi[k]])
        ctx.fillRect(vx[k] - r / 2, vy[k] - r / 2, r, r)
      }
    } else {
      for (let k = 0; k < vc; k++) {
        const nd = nodes[vi[k]], sx = vx[k], sy = vy[k]
        const isHov = hov === nd.id, isSel = sel?.id === nd.id
        const color = getNodeColor(nd)
        const baseR = Math.max(2.5, (nd.size / 50) * 14 * sc)
        const r = baseR * (isHov || isSel ? 1.4 : 1) * (1 + 0.12 * Math.sin(t * 1.8 + L.wx[vi[k]] * 0.05))
        ctx.save()
        const glowR = r * 3.5
        const glow = ctx.createRadialGradient(sx, sy, r * 0.5, sx, sy, glowR)
        const alpha = isHov || isSel ? 0.5 : 0.2
        glow.addColorStop(0, hexToRgba(color, alpha)); glow.addColorStop(0.5, hexToRgba(color, alpha * 0.3)); glow.addColorStop(1, hexToRgba(color, 0))
        ctx.beginPath(); ctx.arc(sx, sy, glowR, 0, Math.PI * 2); ctx.fillStyle = glow; ctx.fill()
        const grad = ctx.createRadialGradient(sx - r * 0.3, sy - r * 0.3, 0, sx, sy, r)
        grad.addColorStop(0, '#ffffff'); grad.addColorStop(0.2, color); grad.addColorStop(1, hexToRgba(color, 0.6))
        ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2); ctx.fillStyle = grad; ctx.fill()
        if (isSel || isHov) {
          ctx.beginPath(); ctx.arc(sx, sy, r + (isSel ? 5 : 4), 0, Math.PI * 2)
          ctx.strokeStyle = isSel ? '#ffffff' : '#00f5ff'; ctx.lineWidth = isSel ? 2 : 1.5; ctx.stroke()
        }
        if (r >= 4 || isHov || isSel) {
          ctx.font = `${Math.max(9, Math.min(13, r * 1.4))}px 'Inter', sans-serif`
          ctx.fillStyle = isHov || isSel ? '#ffffff' : 'rgba(220,230,255,0.85)'
          ctx.textAlign = 'center'; ctx.textBaseline = 'top'
          ctx.fillText(nd.title.length > 24 ? nd.title.slice(0, 24) + '…' : nd.title, sx, sy + r + 4)
        }
        ctx.restore()
      }
    }

    // minimap + viewport
    if (minimap) {
      const mx = w - MM_W - 12, my = h - MM_H - 12
      ctx.save()
      ctx.drawImage(minimap.canvas, mx, my)
      ctx.strokeStyle = 'rgba(0,245,255,0.35)'; ctx.lineWidth = 1
      ctx.strokeRect(mx + 0.5, my + 0.5, MM_W - 1, MM_H - 1)
      const toMM = (wxv: number, wyv: number): [number, number] =>
        [mx + minimap.pad + (wxv - L.minX) * minimap.s, my + minimap.pad + (wyv - L.minY) * minimap.s]
      const [a1, b1] = toMM(-ox / sc, -oy / sc)
      const [a2, b2] = toMM((w - ox) / sc, (h - oy) / sc)
      ctx.beginPath(); ctx.rect(mx, my, MM_W, MM_H); ctx.clip()
      ctx.strokeStyle = 'rgba(255,255,255,0.7)'
      ctx.strokeRect(a1, b1, Math.max(2, a2 - a1), Math.max(2, b2 - b1))
      ctx.restore()
    }
  }

  // ── Hit testing over last frame's on-screen nodes ────────────────────────
  const hitTest = useCallback((mx: number, my: number): NeuralNode | null => {
    const vi = visIdxRef.current, vx = visSxRef.current, vy = visSyRef.current, vc = visCountRef.current
    let best = -1, bestD = vc > DETAIL_MAX ? 8 : 16
    for (let k = 0; k < vc; k++) {
      const d = Math.hypot(vx[k] - mx, vy[k] - my)
      if (d < bestD) { bestD = d; best = vi[k] }
    }
    return best >= 0 ? nodes[best] : null
  }, [nodes])

  const inMinimap = (mx: number, my: number) => {
    const { w, h } = sizeRef.current
    return !!minimap && mx >= w - MM_W - 12 && mx <= w - 12 && my >= h - MM_H - 12 && my <= h - 12
  }
  const jumpFromMinimap = (mx: number, my: number) => {
    if (!minimap) return
    const { w, h } = sizeRef.current, L = layoutRef.current
    const wxv = L.minX + (mx - (w - MM_W - 12) - minimap.pad) / minimap.s
    const wyv = L.minY + (my - (h - MM_H - 12) - minimap.pad) / minimap.s
    oxRef.current = w / 2 - wxv * scaleRef.current
    oyRef.current = h / 2 - wyv * scaleRef.current
  }

  // ── Pointer events: pan (1 pointer), pinch-zoom (2), tap to select ───────
  const local = (e: React.PointerEvent | React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = local(e)
    canvasRef.current?.focus({ preventScroll: true })
    if (inMinimap(p.x, p.y)) { jumpFromMinimap(p.x, p.y); downRef.current = null; return }
    try { canvasRef.current?.setPointerCapture(e.pointerId) } catch { /* */ }
    pointersRef.current.set(e.pointerId, p)
    if (pointersRef.current.size === 1) downRef.current = { x: p.x, y: p.y, moved: false }
    if (pointersRef.current.size === 2) {
      const [a, b] = [...pointersRef.current.values()]
      pinchRef.current = { d: Math.hypot(a.x - b.x, a.y - b.y), s: scaleRef.current }
    }
    setDragging(true)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = local(e)
    const ptrs = pointersRef.current
    if (ptrs.has(e.pointerId)) {
      const prev = ptrs.get(e.pointerId)!
      ptrs.set(e.pointerId, p)
      if (ptrs.size >= 2 && pinchRef.current) {
        const [a, b] = [...ptrs.values()]
        const d = Math.hypot(a.x - b.x, a.y - b.y)
        const target = clampScale(pinchRef.current.s * (d / Math.max(1, pinchRef.current.d)))
        zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, target / scaleRef.current)
        if (downRef.current) downRef.current.moved = true
        return
      }
      oxRef.current += p.x - prev.x
      oyRef.current += p.y - prev.y
      if (downRef.current && Math.hypot(p.x - downRef.current.x, p.y - downRef.current.y) > 4) downRef.current.moved = true
      return
    }
    if (e.pointerType !== 'mouse') return
    const node = hitTest(p.x, p.y)
    hoveredRef.current = node ? node.id : null
    setHoveredNode(node)
    if (node) setTooltipPos({ x: e.clientX, y: e.clientY })
  }

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = local(e)
    pointersRef.current.delete(e.pointerId)
    if (pointersRef.current.size < 2) pinchRef.current = null
    if (pointersRef.current.size === 0) setDragging(false)
    const d = downRef.current
    if (d && !d.moved && pointersRef.current.size === 0) {
      const node = hitTest(p.x, p.y)
      if (node) {
        setSelectedNode(prev => prev?.id === node.id ? null : node)
        onStarSelect?.(node)
      }
    }
    if (pointersRef.current.size === 0) downRef.current = null
  }

  const onPointerLeave = () => {
    hoveredRef.current = null
    setHoveredNode(null)
  }

  const onDoubleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const p = local(e)
    const node = hitTest(p.x, p.y)
    if (node) focusNode(node, false)
    else zoomAt(p.x, p.y, 2)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    const { w, h } = sizeRef.current
    const step = 80
    switch (e.key) {
      case 'ArrowLeft':  oxRef.current += step; break
      case 'ArrowRight': oxRef.current -= step; break
      case 'ArrowUp':    oyRef.current += step; break
      case 'ArrowDown':  oyRef.current -= step; break
      case '+': case '=': zoomAt(w / 2, h / 2, 1.3); break
      case '-': case '_': zoomAt(w / 2, h / 2, 1 / 1.3); break
      case '0': case 'f': fitAll(); break
      default: return
    }
    e.preventDefault()
  }

  const handleFilterChange = useCallback((f: string) => {
    filterRef.current = f
    setActiveFilter(f)
  }, [])

  const filteredCount = activeFilter === 'all' ? nodes.length : (typeCounts[activeFilter] || 0)

  // node list (search + constellation + type filter)
  const listMatches = useMemo(() => {
    const q = query.trim().toLowerCase()
    const out: NeuralNode[] = []
    let total = 0
    for (const n of nodes) {
      if (listConst !== 'all' && n.constellation !== listConst) continue
      if (activeFilter !== 'all' && n.content_type !== activeFilter) continue
      if (q && !(n.title || '').toLowerCase().includes(q) && !(n.tags || []).some(t => t.toLowerCase().includes(q))
          && !(n.path || '').toLowerCase().includes(q)) continue
      total++
      if (out.length < LIST_MAX) out.push(n)
    }
    return { rows: out, total }
  }, [nodes, query, listConst, activeFilter])

  const zoomBtn: React.CSSProperties = {
    background: 'rgba(0,0,0,0.75)', border: '1px solid rgba(0,245,255,0.25)', borderRadius: 6,
    color: '#00f5ff', width: 32, height: 32, fontSize: 16, cursor: 'pointer', lineHeight: '30px', padding: 0,
  }
  const inputStyle: React.CSSProperties = {
    background: '#0a0a0a', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 6, color: '#e5e7eb', fontSize: 12,
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Canvas */}
      <div
        ref={containerRef}
        className="galaxy-container"
        style={{ flex: 1, position: 'relative', minHeight: 480, background: '#000', touchAction: 'none' }}
      >
        <canvas
          ref={canvasRef}
          className="galaxy-canvas"
          tabIndex={0}
          aria-label="Memory galaxy. Drag to pan, wheel or pinch to zoom, arrow keys to pan, plus and minus to zoom, 0 to fit."
          style={{ cursor: dragging ? 'grabbing' : (hoveredNode ? 'pointer' : 'grab'), outline: 'none', touchAction: 'none' }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={onPointerLeave}
          onDoubleClick={onDoubleClick}
          onKeyDown={onKeyDown}
        />

        {nodes.length === 0 && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 12, pointerEvents: 'none',
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

        <div className="galaxy-stats" style={{ fontFamily: 'Inter, sans-serif' }}>
          ⬡ {filteredCount.toLocaleString()} nodes · {edges.length} synapses · {layout.cells.length} constellations
          {wasmFlag && <> · layout: {wasmActive ? 'wasm' : 'js'}{wasmActive && <span title={`GIX vault merkle root ${wasmSwap!.swap.merkleRoot}`}> · root {wasmSwap!.swap.merkleRoot.slice(0, 12)}</span>}</>}
        </div>

        {/* Zoom controls */}
        <div style={{ position: 'absolute', top: 10, right: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button aria-label="Zoom in" style={zoomBtn} onClick={() => zoomAt(sizeRef.current.w / 2, sizeRef.current.h / 2, 1.4)}>+</button>
          <button aria-label="Zoom out" style={zoomBtn} onClick={() => zoomAt(sizeRef.current.w / 2, sizeRef.current.h / 2, 1 / 1.4)}>−</button>
          <button aria-label="Fit all nodes" style={{ ...zoomBtn, fontSize: 10 }} onClick={fitAll}>FIT</button>
        </div>

        <div style={{ position: 'absolute', bottom: 12, left: 12, fontSize: 10, color: 'rgba(160,170,190,0.6)', fontFamily: 'Inter, sans-serif', pointerEvents: 'none' }}>
          drag to pan · wheel / pinch to zoom · double-click to focus · click the minimap to jump
        </div>

        {hoveredNode && (
          <div className="galaxy-tooltip"
            style={{ position: 'fixed', left: tooltipPos.x + 16, top: tooltipPos.y - 14, pointerEvents: 'none' }}>
            <div style={{ color: getNodeColor(hoveredNode), fontWeight: 700, marginBottom: 4, fontSize: 13 }}>{hoveredNode.title}</div>
            <div style={{ fontSize: 10, color: '#6b7280', marginBottom: 2 }}>
              {hoveredNode.content_type} · {hoveredNode.constellation}{hoveredNode.created ? ` · ${String(hoveredNode.created).slice(0, 10)}` : ''}
            </div>
            {hoveredNode.tags.length > 0 && (
              <div style={{ fontSize: 10, color: 'rgba(0,245,255,0.5)' }}>{hoveredNode.tags.slice(0, 4).map(t => `#${t}`).join(' ')}</div>
            )}
          </div>
        )}
      </div>

      {/* Node detail panel */}
      {selectedNode && (
        <div style={{
          background: '#000', border: '1px solid rgba(0,245,255,0.15)', borderRadius: 10,
          margin: '8px 0', padding: '12px 16px', display: 'flex', gap: 16, alignItems: 'flex-start', fontFamily: 'Inter, sans-serif',
        }}>
          <div style={{
            width: 36, height: 36, borderRadius: '50%', flexShrink: 0,
            background: `radial-gradient(circle, white 15%, ${getNodeColor(selectedNode)} 60%)`,
            boxShadow: `0 0 16px ${getNodeColor(selectedNode)}88`, marginTop: 2,
          }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: getNodeColor(selectedNode), marginBottom: 4 }}>{selectedNode.title}</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
              <span style={{ fontSize: 10, background: 'rgba(0,245,255,0.1)', color: '#00f5ff', borderRadius: 4, padding: '2px 6px' }}>{selectedNode.content_type}</span>
              <span style={{ fontSize: 10, background: 'rgba(138,75,255,0.1)', color: '#a855f7', borderRadius: 4, padding: '2px 6px' }}>{selectedNode.constellation}</span>
              <span style={{ fontSize: 10, color: 'rgba(107,114,128,0.7)' }}>strength: {selectedNode.size}</span>
              {selectedNode.created && <span style={{ fontSize: 10, color: 'rgba(107,114,128,0.7)' }}>{String(selectedNode.created).slice(0, 16)}</span>}
              {wasmActive && layout.idx.get(selectedNode.id) != null && (
                <span style={{ fontSize: 10, color: 'rgba(107,114,128,0.7)', fontFamily: 'monospace' }}
                  title="GIX content_hash(path || id)">gix {nodeGixId(wasmSwap!.swap.nodeHashes, layout.idx.get(selectedNode.id)!)}</span>
              )}
            </div>
            {selectedNode.tags.length > 0 && (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {selectedNode.tags.map(tag => <span key={tag} style={{ fontSize: 10, color: 'rgba(0,245,255,0.6)' }}>#{tag}</span>)}
              </div>
            )}
          </div>
          <button onClick={() => setSelectedNode(null)}
            style={{ background: 'none', border: 'none', color: 'rgba(107,114,128,0.7)', cursor: 'pointer', fontSize: 16, padding: 0 }}>×</button>
        </div>
      )}

      {/* Type filter bar */}
      <div className="galaxy-filter-bar">
        <button className={`galaxy-filter-btn${activeFilter === 'all' ? ' active' : ''}`} onClick={() => handleFilterChange('all')}>
          All ({nodes.length.toLocaleString()})
        </button>
        {contentTypes.map(ct => (
          <button key={ct} className={`galaxy-filter-btn${activeFilter === ct ? ' active' : ''}`} onClick={() => handleFilterChange(ct)}>
            {ct} ({(typeCounts[ct] || 0).toLocaleString()})
          </button>
        ))}
      </div>

      {/* Constellations: jump to any one */}
      {layout.cells.length > 1 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '8px 0', fontFamily: 'Inter, sans-serif' }}>
          {layout.cells.map(c => (
            <button key={c.name} className="galaxy-filter-btn"
              onClick={() => { fitTo(c.x - 20, c.y - 60, c.x + c.w + 20, c.y + c.h + 20); setListConst(c.name) }}>
              {c.name} · {c.count.toLocaleString()}
            </button>
          ))}
        </div>
      )}

      {/* Every node, searchable and scrollable */}
      <div style={{ border: '1px solid rgba(0,245,255,0.15)', borderRadius: 10, background: '#000', fontFamily: 'Inter, sans-serif' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', padding: '8px 10px', borderBottom: listOpen ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
          <button onClick={() => setListOpen(o => !o)} className="galaxy-filter-btn" aria-expanded={listOpen}>
            {listOpen ? '▾' : '▸'} All nodes
          </button>
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search titles, tags, paths…"
            style={{ ...inputStyle, flex: 1, minWidth: 120, padding: '5px 8px' }} />
          <select value={listConst} onChange={e => setListConst(e.target.value)} style={{ ...inputStyle, padding: '5px 6px' }}>
            <option value="all">all constellations</option>
            {layout.cells.map(c => <option key={c.name} value={c.name}>{c.name} ({c.count})</option>)}
          </select>
          <span style={{ fontSize: 11, color: 'rgba(160,170,190,0.7)', whiteSpace: 'nowrap' }}>
            {listMatches.total.toLocaleString()} match{listMatches.total === 1 ? '' : 'es'}
          </span>
        </div>
        {listOpen && (
          <div style={{ maxHeight: 280, overflowY: 'auto', overscrollBehavior: 'contain' }}>
            {listMatches.rows.map(n => (
              <button key={n.id} onClick={() => focusNode(n)}
                style={{
                  display: 'flex', width: '100%', gap: 8, alignItems: 'center', textAlign: 'left',
                  background: selectedNode?.id === n.id ? 'rgba(0,245,255,0.08)' : 'transparent',
                  border: 'none', borderBottom: '1px solid rgba(255,255,255,0.04)', color: '#e5e7eb',
                  padding: '6px 10px', fontSize: 12, cursor: 'pointer',
                }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: getNodeColor(n), flexShrink: 0 }} />
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.title || n.id}</span>
                <span style={{ fontSize: 10, color: 'rgba(160,170,190,0.6)', whiteSpace: 'nowrap' }}>{n.constellation}</span>
                {n.created && <span style={{ fontSize: 10, color: 'rgba(160,170,190,0.45)', whiteSpace: 'nowrap' }}>{String(n.created).slice(0, 10)}</span>}
              </button>
            ))}
            {listMatches.total > listMatches.rows.length && (
              <div style={{ padding: '8px 10px', fontSize: 11, color: 'rgba(160,170,190,0.6)' }}>
                Showing {listMatches.rows.length} of {listMatches.total.toLocaleString()} — search or pick a constellation to narrow.
              </div>
            )}
            {listMatches.total === 0 && <div style={{ padding: '8px 10px', fontSize: 11, color: 'rgba(160,170,190,0.6)' }}>No nodes match.</div>}
          </div>
        )}
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
