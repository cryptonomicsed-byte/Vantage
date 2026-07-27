import React, { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * Swarm graph -- rebuilt on the same 3d-force-graph/three.js shell as
 * MoneyFlowGraph.tsx and NeuralVault.tsx (the "memory vault"/"money flow"
 * galaxies). The old version was a hand-rolled 2D canvas + custom spring
 * physics clamped to a fixed viewport with a manual [0.35x, 4x] zoom range --
 * as agent count grew, nodes drifted past the visible canvas edge with no
 * way to see them all. A real orbit camera (three.js OrbitControls, wired in
 * automatically by 3d-force-graph) has no such ceiling: scroll to zoom out
 * arbitrarily far, drag to orbit, pinch on touch -- same interaction model
 * as every other galaxy view in the app, so all nodes are always reachable.
 */

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

interface GNode { id: number; name: string; val: number; color: string; raw: AgentNode }
interface GLink { source: number; target: number; color: string; live: boolean }

function isActiveRecently(lastSeenAt: string): boolean {
  if (!lastSeenAt) return false
  const diff = Date.now() - new Date(lastSeenAt).getTime()
  return diff < 15 * 60 * 1000
}

function nodeColor(nd: AgentNode): string {
  if (nd.jail_mode === 1) return '#ff2d4a'
  if (isActiveRecently(nd.last_seen_at)) return '#8a4bff'
  return '#5a5a8c'
}

function buildGraph(data: SwarmGraph, taskLinks: GLink[]): { nodes: GNode[]; links: GLink[] } {
  const nodes: GNode[] = data.nodes.map(nd => ({
    id: nd.id,
    name: nd.name,
    val: 2 + Math.sqrt(nd.broadcast_count + 1) * 1.6,
    color: nodeColor(nd),
    raw: nd,
  }))
  const nodeIds = new Set(nodes.map(n => n.id))
  const links: GLink[] = data.edges
    .filter(e => nodeIds.has(e.from) && nodeIds.has(e.to))
    .map(e => ({ source: e.from, target: e.to, color: 'rgba(138,75,255,0.35)', live: false }))
  return { nodes, links: [...links, ...taskLinks.filter(l => nodeIds.has(l.source) && nodeIds.has(l.target))] }
}

let _nextTaskLinkId = 1

export default function SwarmMap() {
  const navigate = useNavigate()
  const mountRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)
  const [network, setNetwork] = useState<SwarmGraph | null>(null)
  const [loading, setLoading] = useState(true)
  const [agentCount, setAgentCount] = useState(0)
  const [swarmTasks, setSwarmTasks] = useState<SwarmTask[]>([])
  const [taskPanelOpen, setTaskPanelOpen] = useState(true)
  const [taskLinks, setTaskLinks] = useState<(GLink & { id: number })[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/agents/swarm-graph')
      if (!res.ok) throw new Error('fetch failed')
      const data: SwarmGraph = await res.json()
      setNetwork(data)
      setAgentCount(data.nodes.length)
    } catch {
      // leave last-known graph in place
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
    fetch('/api/agents/swarm/tasks?limit=20')
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (Array.isArray(data)) setSwarmTasks(data) })
      .catch(() => {})

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

          // Spawn a transient "live" link between two random agents so the
          // new task visibly flows through the swarm -- pruned a few
          // seconds later via the timeout below.
          const nodes = network?.nodes || []
          if (nodes.length >= 2) {
            const from = nodes[Math.floor(Math.random() * nodes.length)]
            const to = nodes[Math.floor(Math.random() * nodes.length)]
            if (from.id !== to.id) {
              const id = _nextTaskLinkId++
              setTaskLinks(prev => [...prev, { id, source: from.id, target: to.id, color: 'rgba(0,245,255,0.8)', live: true }])
              setTimeout(() => setTaskLinks(prev => prev.filter(l => l.id !== id)), 6000)
            }
          }
        }
      } catch { /* ignore parse errors */ }
    }
    wsRef.current = ws
    return () => ws.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadData])

  /* Mount / update the 3D graph */
  useEffect(() => {
    if (!network || !mountRef.current) return
    let disposed = false
    ;(async () => {
      const [{ default: ForceGraph3D }, THREE] = await Promise.all([
        import('3d-force-graph'), import('three'),
      ])
      if (disposed || !mountRef.current) return

      const { nodes, links } = buildGraph(network, taskLinks)

      const cv = document.createElement('canvas'); cv.width = cv.height = 64
      const cx = cv.getContext('2d')!
      const grad = cx.createRadialGradient(32, 32, 0, 32, 32, 32)
      grad.addColorStop(0, 'rgba(255,255,255,1)')
      grad.addColorStop(0.25, 'rgba(255,255,255,0.65)')
      grad.addColorStop(0.6, 'rgba(255,255,255,0.15)')
      grad.addColorStop(1, 'rgba(255,255,255,0)')
      cx.fillStyle = grad; cx.fillRect(0, 0, 64, 64)
      const nodeTex = new THREE.CanvasTexture(cv)

      if (!graphRef.current) {
        graphRef.current = new ForceGraph3D(mountRef.current!)
          .backgroundColor('rgba(4,3,13,0)')
          .showNavInfo(false)
          .nodeLabel((n: any) => {
            const nd: AgentNode = n.raw
            const jailed = nd.jail_mode === 1
            return `<div style="font-family:monospace;font-size:11px;color:#dfe6ff;background:rgba(5,5,16,.92);padding:6px 10px;border-radius:6px;border:1px solid rgba(255,255,255,.12);max-width:220px">
              <div style="color:#8a4bff;font-weight:700;margin-bottom:3px">${nd.name}</div>
              <div style="color:#6b7280">Broadcasts: <span style="color:#00f5ff">${nd.broadcast_count}</span></div>
              <div style="color:#6b7280">Followers: <span style="color:#00f5ff">${nd.follower_count}</span></div>
              ${nd.vibe?.status_code ? `<div style="color:#6b7280">Status: <span style="color:#ffaa00">${nd.vibe.status_code}</span></div>` : ''}
              ${nd.vibe?.vibe_text ? `<div style="color:#aaa;font-style:italic;margin-top:3px">"${nd.vibe.vibe_text}"</div>` : ''}
              ${jailed ? '<div style="color:#ff2d4a;font-weight:700;margin-top:3px">[JAILED]</div>' : ''}
            </div>`
          })
          .nodeThreeObject((n: any) => {
            const jailed = (n.raw as AgentNode).jail_mode === 1
            const active = !jailed && isActiveRecently((n.raw as AgentNode).last_seen_at)
            const brightness = jailed ? 0.9 : active ? 0.75 : 0.35
            const mat = new THREE.SpriteMaterial({
              map: nodeTex, color: n.color, transparent: true, opacity: 0.35 + brightness * 0.6,
              depthWrite: false, blending: THREE.AdditiveBlending,
            })
            const sprite = new THREE.Sprite(mat)
            const s = 4 + n.val * 1.4
            sprite.scale.set(s, s, 1)
            return sprite
          })
          .linkColor((l: any) => l.color)
          .linkOpacity(0.45)
          .linkWidth(0.6)
          .linkDirectionalParticles((l: any) => l.live ? 3 : 0)
          .linkDirectionalParticleWidth(1.6)
          .linkDirectionalParticleSpeed(0.008)
          .onNodeClick((n: any) => {
            navigate(`/agent/${encodeURIComponent((n.raw as AgentNode).name)}`)
          })
        const controls = graphRef.current.controls()
        controls.autoRotate = true
        controls.autoRotateSpeed = 0.4
        const stopSpin = () => { controls.autoRotate = false }
        mountRef.current!.addEventListener('pointerdown', stopSpin, { once: true })
        mountRef.current!.addEventListener('wheel', stopSpin, { once: true })
        mountRef.current!.addEventListener('touchstart', stopSpin, { once: true })
      }
      graphRef.current
        .width(mountRef.current!.clientWidth)
        .height(mountRef.current!.clientHeight)
        .graphData({ nodes, links })
    })()
    return () => { disposed = true }
  }, [network, taskLinks, navigate])

  /* Resize + teardown */
  useEffect(() => {
    const onResize = () => {
      if (graphRef.current && mountRef.current)
        graphRef.current.width(mountRef.current.clientWidth).height(mountRef.current.clientHeight)
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      if (graphRef.current) {
        try {
          const renderer = graphRef.current.renderer?.()
          if (renderer) {
            renderer.dispose()
            renderer.forceContextLoss()
            renderer.domElement?.remove()
          }
          graphRef.current._destructor?.()
        } catch (e) {
          console.error('Error disposing swarm graph:', e)
        }
      }
      graphRef.current = null
    }
  }, [])

  function resetView() {
    if (!graphRef.current) return
    graphRef.current.cameraPosition({ x: 0, y: 0, z: 400 }, { x: 0, y: 0, z: 0 }, 800)
  }
  function zoomBy(factor: number) {
    const g = graphRef.current
    if (!g) return
    const cam = g.camera()
    const pos = cam.position
    const ratio = 1 / factor
    g.cameraPosition({ x: pos.x * ratio, y: pos.y * ratio, z: pos.z * ratio }, undefined, 400)
  }

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: 'calc(100vh - 48px)',
        background: 'radial-gradient(circle at 25% 15%, rgba(138,75,255,0.12), rgba(5,8,16,0.18) 55%, rgba(5,8,16,0) 100%)',
        overflow: 'hidden',
      }}
    >
      <div ref={mountRef} style={{ position: 'absolute', inset: 0 }} />

      {loading && !network && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8a4bff', fontFamily: 'monospace', fontSize: 14, pointerEvents: 'none' }}>
          Scanning swarm…
        </div>
      )}
      {network && network.nodes.length === 0 && !loading && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#6b7280', fontFamily: 'monospace', fontSize: 14, pointerEvents: 'none' }}>
          No agents in the swarm yet
        </div>
      )}

      {/* Zoom controls -- scroll/pinch to zoom (unlimited range via the real
          orbit camera), drag/one-finger to orbit, reset button to recenter. */}
      <div style={{ position: 'absolute', bottom: 16, right: 16, display: 'flex', flexDirection: 'column', gap: 6, zIndex: 10 }}>
        <button
          onClick={() => zoomBy(1.35)}
          style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(10,10,20,0.75)', border: '1px solid rgba(138,75,255,0.3)', color: '#e0e0ff', cursor: 'pointer', fontSize: 16, fontWeight: 700 }}
          title="Zoom in"
        >+</button>
        <button
          onClick={() => zoomBy(1 / 1.35)}
          style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(10,10,20,0.75)', border: '1px solid rgba(138,75,255,0.3)', color: '#e0e0ff', cursor: 'pointer', fontSize: 16, fontWeight: 700 }}
          title="Zoom out"
        >−</button>
        <button
          onClick={resetView}
          style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(10,10,20,0.75)', border: '1px solid rgba(138,75,255,0.3)', color: '#e0e0ff', cursor: 'pointer', fontSize: 11 }}
          title="Reset view"
        >⟲</button>
      </div>

      {/* Controls overlay */}
      <div style={{ position: 'absolute', top: 12, left: 12, display: 'flex', alignItems: 'center', gap: 12, zIndex: 10 }}>
        <button
          onClick={loadData}
          style={{
            padding: '4px 12px', background: 'rgba(138,75,255,0.15)', border: '1px solid rgba(138,75,255,0.5)',
            borderRadius: 4, color: '#8a4bff', fontFamily: 'monospace', fontSize: 12, cursor: 'pointer',
          }}
        >
          Reload
        </button>

        <span style={{
          padding: '4px 10px', background: 'rgba(0,245,255,0.08)', border: '1px solid rgba(0,245,255,0.25)',
          borderRadius: 4, color: '#00f5ff', fontFamily: 'monospace', fontSize: 12,
        }}>
          {agentCount} agents
        </span>

        {/* Legend */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '4px 10px',
          background: 'rgba(5,5,8,0.8)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4,
        }}>
          {[
            { color: '#8a4bff', label: 'Active' },
            { color: '#3a3a5c', label: 'Normal' },
            { color: '#ff2d4a', label: 'Jailed' },
          ].map(({ color, label }) => (
            <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 5, fontFamily: 'monospace', fontSize: 11, color: '#aaa' }}>
              <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}` }} />
              {label}
            </span>
          ))}
        </div>
        <span style={{ fontFamily: 'monospace', fontSize: 10, color: 'rgba(255,255,255,.35)' }}>
          scroll to zoom · drag to orbit · click a node to open the agent
        </span>
      </div>

      {/* Swarm Tasks Panel */}
      <div style={{
        position: 'absolute', top: 0, right: 0, bottom: 0,
        width: taskPanelOpen ? 280 : 36, transition: 'width 0.2s',
        background: 'rgba(5,5,8,0.45)', borderLeft: '1px solid rgba(138,75,255,0.25)',
        display: 'flex', flexDirection: 'column', zIndex: 10,
        backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
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
                <div style={{ padding: '24px 12px', textAlign: 'center', fontFamily: 'monospace', fontSize: 11, color: '#555' }}>
                  No open tasks
                </div>
              ) : swarmTasks.map(task => (
                <div key={task.id} style={{ padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.05)', cursor: 'default' }}>
                  <div style={{ fontFamily: 'monospace', fontSize: 11, color: '#e0e0ff', marginBottom: 3, lineHeight: 1.3, fontWeight: 600 }}>
                    {task.title.length > 38 ? task.title.slice(0, 38) + '…' : task.title}
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 3 }}>
                    {task.required_capability && (
                      <span style={{ fontSize: 9, color: '#00f5ff', border: '1px solid rgba(0,245,255,0.2)', borderRadius: 99, padding: '1px 5px', fontFamily: 'monospace' }}>
                        {task.required_capability}
                      </span>
                    )}
                    {task.reward_usdc > 0 && (
                      <span style={{ fontSize: 9, color: '#4ade80', border: '1px solid rgba(74,222,128,0.25)', borderRadius: 99, padding: '1px 5px', fontFamily: 'monospace' }}>
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
    </div>
  )
}
