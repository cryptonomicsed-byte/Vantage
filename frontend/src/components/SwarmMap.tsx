import React, { useRef, useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentNode {
  id: number
  name: string
  bio: string
  avatar_url: string
  broadcast_count: number
  follower_count: number
  jail_mode: number
  last_seen_at: string
  vibe: { status_code?: string; vibe_text?: string }
}

interface AgentEdge {
  from: number
  to: number
}

interface SwarmGraph {
  nodes: AgentNode[]
  edges: AgentEdge[]
}

interface SimNode extends AgentNode {
  x: number
  y: number
  vx: number
  vy: number
  radius: number
}

interface TooltipState {
  visible: boolean
  x: number
  y: number
  node: AgentNode | null
}

interface SwarmTask {
  id: number
  title: string
  poster_name: string
  required_capability: string
  reward_usdc: number
  bid_count: number
  status: string
  created_at: string
}

// ─── Task flow particles ──────────────────────────────────────────────────────

interface TaskParticle {
  id: number
  fromId: number
  toId: number
  progress: number   // 0 → 1
  label: string
  color: string
  speed: number
}

let _nextParticleId = 1

// ─── Constants ────────────────────────────────────────────────────────────────

const K_REPULSION = 8000
const K_SPRING = 0.05
const REST_LEN = 80
const GRAVITY = 0.002
const DAMPING = 0.85
const MIN_DIST = 30
const PREWARM_TICKS = 120
const HOVER_RADIUS = 40

function nodeRadius(bc: number): number {
  return Math.min(30, Math.max(10, Math.sqrt(bc + 1) * 4 + 6))
}

function isActiveRecently(lastSeenAt: string): boolean {
  if (!lastSeenAt) return false
  const diff = Date.now() - new Date(lastSeenAt).getTime()
  return diff < 15 * 60 * 1000
}

// ─── Physics ──────────────────────────────────────────────────────────────────

function physicsTickOnce(
  simNodes: SimNode[],
  adjMap: Map<number, Set<number>>,
  idxMap: Map<number, number>,
  cx: number,
  cy: number
) {
  const n = simNodes.length

  // Reset forces
  const fx = new Float64Array(n)
  const fy = new Float64Array(n)

  // Repulsion between every pair
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const dx = simNodes[j].x - simNodes[i].x
      const dy = simNodes[j].y - simNodes[i].y
      let d = Math.sqrt(dx * dx + dy * dy)
      if (d < MIN_DIST) d = MIN_DIST
      const f = K_REPULSION / (d * d)
      const nx = (dx / d) * f
      const ny = (dy / d) * f
      fx[i] -= nx
      fy[i] -= ny
      fx[j] += nx
      fy[j] += ny
    }
  }

  // Spring attraction along edges
  adjMap.forEach((neighbors, fromId) => {
    const iIdx = idxMap.get(fromId)
    if (iIdx === undefined) return
    neighbors.forEach((toId) => {
      const jIdx = idxMap.get(toId)
      if (jIdx === undefined) return
      if (jIdx <= iIdx) return // process each edge once
      const dx = simNodes[jIdx].x - simNodes[iIdx].x
      const dy = simNodes[jIdx].y - simNodes[iIdx].y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const stretch = d - REST_LEN
      const f = K_SPRING * stretch
      const nx = (dx / d) * f
      const ny = (dy / d) * f
      fx[iIdx] += nx
      fy[iIdx] += ny
      fx[jIdx] -= nx
      fy[jIdx] -= ny
    })
  })

  // Integrate
  for (let i = 0; i < n; i++) {
    const nd = simNodes[i]
    // Center gravity
    fx[i] += (cx - nd.x) * GRAVITY
    fy[i] += (cy - nd.y) * GRAVITY

    nd.vx = (nd.vx + fx[i]) * DAMPING
    nd.vy = (nd.vy + fy[i]) * DAMPING
    nd.x += nd.vx
    nd.y += nd.vy
  }
}

// ─── Drawing ──────────────────────────────────────────────────────────────────

function drawFrame(
  ctx: CanvasRenderingContext2D,
  simNodes: SimNode[],
  edges: AgentEdge[],
  adjMap: Map<number, Set<number>>,
  idxMap: Map<number, number>,
  w: number,
  h: number,
  hoveredId: number | null,
  particles: TaskParticle[]
) {
  // Transparent — the container div behind the canvas carries the glass
  // (rgba + backdrop-filter) look, so the graph reads as floating over it
  // rather than painted onto a solid backdrop.
  ctx.clearRect(0, 0, w, h)

  // Edges
  edges.forEach(({ from, to }) => {
    const ai = idxMap.get(from)
    const bi = idxMap.get(to)
    if (ai === undefined || bi === undefined) return
    const a = simNodes[ai]
    const b = simNodes[bi]

    const mutual =
      adjMap.get(from)?.has(to) && adjMap.get(to)?.has(from)

    ctx.save()
    ctx.beginPath()
    ctx.moveTo(a.x, a.y)
    ctx.lineTo(b.x, b.y)
    ctx.strokeStyle = 'rgba(138,75,255,0.3)'
    ctx.lineWidth = mutual ? 1.5 : 0.8
    ctx.globalAlpha = 0.25
    ctx.stroke()
    ctx.restore()
  })

  // Task-flow particles
  particles.forEach(p => {
    const ai = idxMap.get(p.fromId)
    const bi = idxMap.get(p.toId)
    if (ai === undefined || bi === undefined) return
    const a = simNodes[ai]
    const b = simNodes[bi]
    const px = a.x + (b.x - a.x) * p.progress
    const py = a.y + (b.y - a.y) * p.progress

    // Trail
    ctx.save()
    const gradient = ctx.createLinearGradient(a.x, a.y, px, py)
    gradient.addColorStop(0, 'transparent')
    gradient.addColorStop(1, p.color + '66')
    ctx.beginPath()
    ctx.moveTo(a.x, a.y)
    ctx.lineTo(px, py)
    ctx.strokeStyle = gradient
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.restore()

    // Particle dot
    ctx.save()
    ctx.beginPath()
    ctx.arc(px, py, 4, 0, Math.PI * 2)
    ctx.fillStyle = p.color
    ctx.shadowBlur = 10
    ctx.shadowColor = p.color
    ctx.fill()
    ctx.restore()

    // Label
    ctx.save()
    ctx.font = '9px monospace'
    ctx.fillStyle = p.color
    ctx.textAlign = 'center'
    ctx.textBaseline = 'bottom'
    ctx.fillText(p.label, px, py - 5)
    ctx.restore()
  })

  // Nodes
  simNodes.forEach((nd) => {
    const r = nd.radius
    const isJailed = nd.jail_mode === 1
    const isActive = !isJailed && isActiveRecently(nd.last_seen_at)
    const hovered = hoveredId === nd.id

    let baseColor: string
    let centerColor: string
    if (isJailed) {
      baseColor = '#ff2d4a'
      centerColor = '#ff6b80'
    } else if (isActive) {
      baseColor = '#8a4bff'
      centerColor = '#b26fff'
    } else {
      baseColor = '#3a3a5c'
      centerColor = '#5a5a8c'
    }

    ctx.save()

    // Glow
    ctx.shadowBlur = hovered ? 24 : 12
    ctx.shadowColor = baseColor

    // Radial gradient fill
    const grad = ctx.createRadialGradient(nd.x - r * 0.3, nd.y - r * 0.3, 0, nd.x, nd.y, r)
    grad.addColorStop(0, centerColor)
    grad.addColorStop(1, baseColor)

    ctx.beginPath()
    ctx.arc(nd.x, nd.y, r, 0, Math.PI * 2)
    ctx.fillStyle = grad
    ctx.fill()

    // Hover ring
    if (hovered) {
      ctx.beginPath()
      ctx.arc(nd.x, nd.y, r + 3, 0, Math.PI * 2)
      ctx.strokeStyle = '#00f5ff'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }

    ctx.restore()

    // Label
    if (r >= 12) {
      ctx.save()
      ctx.font = '10px monospace'
      ctx.fillStyle = 'rgba(255,255,255,0.85)'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillText(nd.name, nd.x, nd.y + r + 3)
      ctx.restore()
    }
  })
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function SwarmMap() {
  const navigate = useNavigate()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const simNodesRef = useRef<SimNode[]>([])
  const edgesRef = useRef<AgentEdge[]>([])
  const adjMapRef = useRef<Map<number, Set<number>>>(new Map())
  const idxMapRef = useRef<Map<number, number>>(new Map())
  const rafRef = useRef<number>(0)
  const hoveredIdRef = useRef<number | null>(null)
  const canvasSizeRef = useRef({ w: 0, h: 0 })

  const [loading, setLoading] = useState(true)
  const [agentCount, setAgentCount] = useState(0)
  const [tooltip, setTooltip] = useState<TooltipState>({ visible: false, x: 0, y: 0, node: null })
  const [, forceRender] = useState(0)
  const [swarmTasks, setSwarmTasks] = useState<SwarmTask[]>([])
  const [taskPanelOpen, setTaskPanelOpen] = useState(true)
  const wsRef = useRef<WebSocket | null>(null)
  const particlesRef = useRef<TaskParticle[]>([])

  // ── Fetch & init ──────────────────────────────────────────────────────────

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/agents/swarm-graph')
      if (!res.ok) throw new Error('fetch failed')
      const data: SwarmGraph = await res.json()

      const canvas = canvasRef.current
      const cw = canvas ? canvas.clientWidth : window.innerWidth
      const ch = canvas ? canvas.clientHeight : window.innerHeight

      // Build adjacency map
      const adjMap = new Map<number, Set<number>>()
      data.edges.forEach(({ from, to }) => {
        if (!adjMap.has(from)) adjMap.set(from, new Set())
        if (!adjMap.has(to)) adjMap.set(to, new Set())
        adjMap.get(from)!.add(to)
      })
      adjMapRef.current = adjMap

      // Init sim nodes
      const simNodes: SimNode[] = data.nodes.map((nd) => ({
        ...nd,
        x: Math.random() * cw,
        y: Math.random() * ch,
        vx: 0,
        vy: 0,
        radius: nodeRadius(nd.broadcast_count),
      }))

      const idxMap = new Map<number, number>()
      simNodes.forEach((nd, i) => idxMap.set(nd.id, i))

      simNodesRef.current = simNodes
      edgesRef.current = data.edges
      idxMapRef.current = idxMap

      // Pre-warm
      for (let t = 0; t < PREWARM_TICKS; t++) {
        physicsTickOnce(simNodes, adjMap, idxMap, cw / 2, ch / 2)
      }

      setAgentCount(data.nodes.length)
    } catch {
      // leave empty
    } finally {
      setLoading(false)
      forceRender((n) => n + 1)
    }
  }, [])

  useEffect(() => {
    loadData()
    // Seed TRO particles after a short delay for nodes to settle
    setTimeout(() => {
      fetch('/api/agents/tro?status=open&limit=10')
        .then(r => r.ok ? r.json() : [])
        .then((tros: { service_type: string }[]) => {
          const nodes = simNodesRef.current
          if (nodes.length < 2) return
          const newParticles: TaskParticle[] = tros.slice(0, 8).map(tro => {
            const from = nodes[Math.floor(Math.random() * nodes.length)]
            const to = nodes[Math.floor(Math.random() * nodes.length)]
            return {
              id: _nextParticleId++,
              fromId: from.id, toId: to.id,
              progress: Math.random() * 0.5,
              label: tro.service_type.slice(0, 10),
              color: '#ffaa00',
              speed: 0.003 + Math.random() * 0.003,
            }
          })
          particlesRef.current = newParticles
        })
        .catch(() => {})
    }, 2500)
    // Load open swarm tasks
    fetch('/api/agents/swarm/tasks?limit=20')
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (Array.isArray(data)) setSwarmTasks(data) })
      .catch(() => {})
    // Subscribe to swarm gossip channel for live task updates
    const ws = new WebSocket(`wss://${location.host}/ws/gossip?channel=swarm`)
    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'new_swarm_task') {
          setSwarmTasks(prev => [{
            id: msg.task_id, title: msg.title, poster_name: msg.poster,
            required_capability: msg.required_capability, reward_usdc: msg.reward_usdc,
            bid_count: 0, status: 'open', created_at: new Date().toISOString(),
          }, ...prev].slice(0, 30))
          // Spawn a task particle across a random edge if nodes exist
          const nodes = simNodesRef.current
          if (nodes.length >= 2) {
            const from = nodes[Math.floor(Math.random() * nodes.length)]
            const to = nodes[Math.floor(Math.random() * nodes.length)]
            if (from.id !== to.id) {
              particlesRef.current = [...particlesRef.current, {
                id: _nextParticleId++,
                fromId: from.id, toId: to.id,
                progress: 0, label: (msg.required_capability || 'task').slice(0, 12),
                color: '#00f5ff', speed: 0.004 + Math.random() * 0.003,
              }].slice(-20)
            }
          }
        }
      } catch { /* ignore parse errors */ }
    }
    wsRef.current = ws
    return () => ws.close()
  }, [loadData])

  // ── Canvas resize ─────────────────────────────────────────────────────────

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
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      }
    }

    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)
    return () => ro.disconnect()
  }, [])

  // ── rAF loop ──────────────────────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const loop = () => {
      const ctx = canvas.getContext('2d')
      if (!ctx) return

      const { w, h } = canvasSizeRef.current
      if (w === 0 || h === 0) {
        rafRef.current = requestAnimationFrame(loop)
        return
      }

      const simNodes = simNodesRef.current
      if (simNodes.length > 0) {
        physicsTickOnce(
          simNodes,
          adjMapRef.current,
          idxMapRef.current,
          w / 2,
          h / 2
        )
        // Advance and prune task particles
        particlesRef.current = particlesRef.current
          .map(p => ({ ...p, progress: p.progress + p.speed }))
          .filter(p => p.progress < 1)
        drawFrame(
          ctx,
          simNodes,
          edgesRef.current,
          adjMapRef.current,
          idxMapRef.current,
          w,
          h,
          hoveredIdRef.current,
          particlesRef.current
        )
      } else if (loading) {
        // Loading state
        ctx.clearRect(0, 0, w, h)
        ctx.font = '16px monospace'
        ctx.fillStyle = '#8a4bff'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText('Scanning swarm…', w / 2, h / 2)
      } else {
        // Empty
        ctx.clearRect(0, 0, w, h)
        ctx.font = '16px monospace'
        ctx.fillStyle = '#6b7280'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText('No agents in the swarm yet', w / 2, h / 2)
      }

      rafRef.current = requestAnimationFrame(loop)
    }

    rafRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafRef.current)
  }, [loading])

  // ── Mouse interaction ─────────────────────────────────────────────────────

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    const simNodes = simNodesRef.current
    let closest: SimNode | null = null
    let closestDist = HOVER_RADIUS

    simNodes.forEach((nd) => {
      const dx = nd.x - mx
      const dy = nd.y - my
      const d = Math.sqrt(dx * dx + dy * dy)
      if (d < closestDist) {
        closestDist = d
        closest = nd
      }
    })

    if (closest) {
      hoveredIdRef.current = (closest as SimNode).id
      setTooltip({
        visible: true,
        x: e.clientX,
        y: e.clientY,
        node: closest,
      })
    } else {
      hoveredIdRef.current = null
      setTooltip((prev) => ({ ...prev, visible: false }))
    }
  }, [])

  const handleMouseLeave = useCallback(() => {
    hoveredIdRef.current = null
    setTooltip((prev) => ({ ...prev, visible: false }))
  }, [])

  const handleClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    const simNodes = simNodesRef.current
    let closest: SimNode | null = null
    let closestDist = HOVER_RADIUS

    simNodes.forEach((nd) => {
      const dx = nd.x - mx
      const dy = nd.y - my
      const d = Math.sqrt(dx * dx + dy * dy)
      if (d < closestDist) {
        closestDist = d
        closest = nd
      }
    })

    if (closest) {
      navigate(`/agent/${encodeURIComponent((closest as SimNode).name)}`)
    }
  }, [navigate])

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        width: '100%',
        height: 'calc(100vh - 48px)',
        background: 'rgba(5,8,16,0.45)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        overflow: 'hidden',
      }}
    >
      <canvas
        ref={canvasRef}
        style={{ width: '100%', height: '100%', display: 'block', cursor: 'crosshair' }}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        onClick={handleClick}
      />

      {/* Controls overlay */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          zIndex: 10,
        }}
      >
        <button
          onClick={loadData}
          style={{
            padding: '4px 12px',
            background: 'rgba(138,75,255,0.15)',
            border: '1px solid rgba(138,75,255,0.5)',
            borderRadius: 4,
            color: '#8a4bff',
            fontFamily: 'monospace',
            fontSize: 12,
            cursor: 'pointer',
          }}
        >
          Reload
        </button>

        <span
          style={{
            padding: '4px 10px',
            background: 'rgba(0,245,255,0.08)',
            border: '1px solid rgba(0,245,255,0.25)',
            borderRadius: 4,
            color: '#00f5ff',
            fontFamily: 'monospace',
            fontSize: 12,
          }}
        >
          {agentCount} agents
        </span>

        {/* Legend */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '4px 10px',
            background: 'rgba(5,5,8,0.8)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 4,
          }}
        >
          {[
            { color: '#8a4bff', label: 'Active' },
            { color: '#3a3a5c', label: 'Normal' },
            { color: '#ff2d4a', label: 'Jailed' },
          ].map(({ color, label }) => (
            <span
              key={label}
              style={{ display: 'flex', alignItems: 'center', gap: 5, fontFamily: 'monospace', fontSize: 11, color: '#aaa' }}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: color,
                  boxShadow: `0 0 6px ${color}`,
                }}
              />
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* Swarm Tasks Panel */}
      <div style={{
        position: 'absolute', top: 0, right: 0, bottom: 0,
        width: taskPanelOpen ? 280 : 36, transition: 'width 0.2s',
        background: 'rgba(5,5,8,0.92)', borderLeft: '1px solid rgba(138,75,255,0.25)',
        display: 'flex', flexDirection: 'column', zIndex: 10,
        backdropFilter: 'blur(12px)',
      }}>
        <button
          onClick={() => setTaskPanelOpen(o => !o)}
          style={{
            position: 'absolute', left: -13, top: '50%', transform: 'translateY(-50%)',
            width: 26, height: 40, background: 'rgba(138,75,255,0.2)',
            border: '1px solid rgba(138,75,255,0.4)', borderRadius: '4px 0 0 4px',
            color: '#8a4bff', cursor: 'pointer', fontSize: 12, display: 'flex',
            alignItems: 'center', justifyContent: 'center',
          }}
          title={taskPanelOpen ? 'Hide task queue' : 'Show task queue'}
        >
          {taskPanelOpen ? '›' : '‹'}
        </button>

        {taskPanelOpen && (
          <>
            <div style={{
              padding: '10px 12px 8px', borderBottom: '1px solid rgba(138,75,255,0.2)',
              fontFamily: 'monospace', fontSize: 11, color: '#8a4bff',
              fontWeight: 700, letterSpacing: '1px', textTransform: 'uppercase',
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              ⚡ Swarm Queue
              <span style={{
                marginLeft: 'auto', background: 'rgba(138,75,255,0.2)',
                padding: '1px 6px', borderRadius: 99, fontSize: 10, color: '#b26fff',
              }}>{swarmTasks.length}</span>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
              {swarmTasks.length === 0 ? (
                <div style={{
                  padding: '24px 12px', textAlign: 'center',
                  fontFamily: 'monospace', fontSize: 11, color: '#555',
                }}>
                  No open tasks
                </div>
              ) : swarmTasks.map(task => (
                <div key={task.id} style={{
                  padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.05)',
                  cursor: 'default',
                }}>
                  <div style={{
                    fontFamily: 'monospace', fontSize: 11, color: '#e0e0ff',
                    marginBottom: 3, lineHeight: 1.3, fontWeight: 600,
                  }}>
                    {task.title.length > 38 ? task.title.slice(0, 38) + '…' : task.title}
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 3 }}>
                    {task.required_capability && (
                      <span style={{
                        fontSize: 9, color: '#00f5ff', border: '1px solid rgba(0,245,255,0.2)',
                        borderRadius: 99, padding: '1px 5px', fontFamily: 'monospace',
                      }}>
                        {task.required_capability}
                      </span>
                    )}
                    {task.reward_usdc > 0 && (
                      <span style={{
                        fontSize: 9, color: '#4ade80', border: '1px solid rgba(74,222,128,0.25)',
                        borderRadius: 99, padding: '1px 5px', fontFamily: 'monospace',
                      }}>
                        ${task.reward_usdc.toFixed(2)}
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: '#555' }}>
                    <span>by {task.poster_name}</span>
                    <span>{task.bid_count} bid{task.bid_count !== 1 ? 's' : ''}</span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Tooltip */}
      {tooltip.visible && tooltip.node && (
        <div
          style={{
            position: 'fixed',
            left: tooltip.x + 14,
            top: tooltip.y - 10,
            background: 'rgba(5,5,8,0.95)',
            border: '1px solid rgba(138,75,255,0.5)',
            borderRadius: 6,
            padding: '8px 12px',
            fontFamily: 'monospace',
            fontSize: 12,
            color: '#e0e0ff',
            pointerEvents: 'none',
            zIndex: 100,
            minWidth: 160,
            boxShadow: '0 0 16px rgba(138,75,255,0.3)',
          }}
        >
          <div style={{ color: '#8a4bff', fontWeight: 700, marginBottom: 4 }}>
            {tooltip.node.name}
          </div>
          <div style={{ color: '#6b7280', marginBottom: 2 }}>
            Broadcasts:{' '}
            <span style={{ color: '#00f5ff' }}>{tooltip.node.broadcast_count}</span>
          </div>
          <div style={{ color: '#6b7280', marginBottom: 2 }}>
            Followers:{' '}
            <span style={{ color: '#00f5ff' }}>{tooltip.node.follower_count}</span>
          </div>
          {tooltip.node.vibe?.status_code && (
            <div style={{ color: '#6b7280', marginBottom: 2 }}>
              Status:{' '}
              <span style={{ color: '#ffaa00' }}>{tooltip.node.vibe.status_code}</span>
            </div>
          )}
          {tooltip.node.vibe?.vibe_text && (
            <div
              style={{
                color: '#aaa',
                fontStyle: 'italic',
                marginTop: 4,
                maxWidth: 200,
                whiteSpace: 'normal',
                wordBreak: 'break-word',
              }}
            >
              "{tooltip.node.vibe.vibe_text}"
            </div>
          )}
          {tooltip.node.jail_mode === 1 && (
            <div style={{ color: '#ff2d4a', marginTop: 4, fontWeight: 700 }}>
              [JAILED]
            </div>
          )}
        </div>
      )}
    </div>
  )
}
