import React, { useEffect, useRef, useState, useCallback } from 'react'
import { RefreshCw, Share2 } from 'lucide-react'

/**
 * Money-Flow Graph — a real wallet-to-wallet network, built entirely from
 * genuinely observed data: every /api/intel/trace lookup and watchlist
 * refresh accumulates counterparty edges (backend/routers/intel.py's
 * wallet_edges table). There is no separate clustering step — densely
 * connected wallets naturally pull into visible "hot zones" under the same
 * force-directed physics NeuralVault.tsx uses for the memory galaxy, whose
 * mount/resize/teardown shell this component reuses.
 *
 * Nodes are wallet addresses (brighter/larger = more total value observed
 * moving through them); links are real sender/recipient edges (thicker =
 * more transactions observed between that pair).
 */

interface WalletLink {
  source: string; target: string; role: string
  tx_count: number; total_value: number
  first_seen: string; last_seen: string
}
interface WalletNetworkData {
  chain: string; nodes: { id: string }[]; links: WalletLink[]
  node_count: number; link_count: number
}

interface GNode { id: string; name: string; val: number; color: string }
interface GLink { source: string; target: string; color: string; width: number; role: string; tx_count: number; total_value: number }

function mixColor(hexA: string, hexB: string, t: number): string {
  const parse = (h: string) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(h)
    const n = m ? parseInt(m[1], 16) : 0
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
  }
  const [ar, ag, ab] = parse(hexA)
  const [br, bg, bb] = parse(hexB)
  const mix = (a: number, b: number) => Math.round(a + (b - a) * t)
  return `#${[mix(ar, br), mix(ag, bg), mix(ab, bb)].map(v => v.toString(16).padStart(2, '0')).join('')}`
}

function buildGraph(data: WalletNetworkData): { nodes: GNode[]; links: GLink[] } {
  const totalByNode: Record<string, number> = {}
  const edgesByNode: Record<string, number> = {}
  for (const l of data.links) {
    totalByNode[l.source] = (totalByNode[l.source] || 0) + l.total_value
    totalByNode[l.target] = (totalByNode[l.target] || 0) + l.total_value
    edgesByNode[l.source] = (edgesByNode[l.source] || 0) + 1
    edgesByNode[l.target] = (edgesByNode[l.target] || 0) + 1
  }
  const maxTotal = Math.max(1, ...Object.values(totalByNode))
  const nodes: GNode[] = data.nodes.map(n => {
    const total = totalByNode[n.id] || 0
    const t = Math.min(1, total / maxTotal)
    return {
      id: n.id,
      name: `${n.id.slice(0, 6)}…${n.id.slice(-4)}`,
      val: 3 + t * 14,
      color: mixColor('#3b82f6', '#ff2d4a', t), // quiet blue → hot-zone red
    }
  })
  const maxTx = Math.max(1, ...data.links.map(l => l.tx_count))
  const links: GLink[] = data.links.map(l => ({
    source: l.source, target: l.target,
    color: l.role === 'recipient' ? 'rgba(57,255,20,0.55)' : 'rgba(255,170,0,0.55)',
    width: 0.6 + (l.tx_count / maxTx) * 3,
    role: l.role, tx_count: l.tx_count, total_value: l.total_value,
  }))
  return { nodes, links }
}

export default function MoneyFlowGraph() {
  const mountRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)
  const [chain, setChain] = useState('solana')
  const [minTxCount, setMinTxCount] = useState(1)
  const [network, setNetwork] = useState<WalletNetworkData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<{ address: string; totalValue: number; edgeCount: number } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/intel/wallet-network?chain=${encodeURIComponent(chain)}&min_tx_count=${minTxCount}&limit=500`)
      if (r.ok) setNetwork(await r.json())
    } catch { /* offline — keep last-known graph */ }
    setLoading(false)
  }, [chain, minTxCount])

  useEffect(() => { load() }, [load])

  /* Mount / update the 3D graph */
  useEffect(() => {
    if (!network || !mountRef.current) return
    let disposed = false
    ;(async () => {
      const [{ default: ForceGraph3D }, THREE] = await Promise.all([
        import('3d-force-graph'), import('three'),
      ])
      if (disposed || !mountRef.current) return

      const { nodes, links } = buildGraph(network)

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
          .nodeLabel((n: any) => `<div style="font-family:monospace;font-size:11px;color:#dfe6ff;background:rgba(5,5,16,.9);padding:4px 8px;border-radius:6px;border:1px solid rgba(255,255,255,.12)">${n.id}</div>`)
          .nodeThreeObject((n: any) => {
            const mat = new THREE.SpriteMaterial({
              map: nodeTex, color: n.color, transparent: true, opacity: 0.9,
              depthWrite: false, blending: THREE.AdditiveBlending,
            })
            const sprite = new THREE.Sprite(mat)
            const s = 4 + n.val * 1.4
            sprite.scale.set(s, s, 1)
            return sprite
          })
          .linkColor((l: any) => l.color)
          .linkOpacity(0.35)
          .linkWidth((l: any) => l.width)
          .linkDirectionalParticles((l: any) => Math.min(4, Math.round(l.tx_count / 2)))
          .linkDirectionalParticleWidth(1.4)
          .onNodeClick((n: any) => {
            const g = graphRef.current
            const dist = 60
            const ratio = 1 + dist / Math.hypot(n.x || 1, n.y || 1, n.z || 1)
            g.cameraPosition({ x: (n.x || 1) * ratio, y: (n.y || 1) * ratio, z: (n.z || 1) * ratio }, n, 1200)
            const touching = links.filter(l => (typeof l.source === 'object' ? (l.source as any).id : l.source) === n.id
              || (typeof l.target === 'object' ? (l.target as any).id : l.target) === n.id)
            setSelected({
              address: n.id,
              totalValue: touching.reduce((s, l) => s + l.total_value, 0),
              edgeCount: touching.length,
            })
          })
        const controls = graphRef.current.controls()
        controls.autoRotate = true
        controls.autoRotateSpeed = 0.4
        const stopSpin = () => { controls.autoRotate = false }
        mountRef.current!.addEventListener('pointerdown', stopSpin, { once: true })
        mountRef.current!.addEventListener('wheel', stopSpin, { once: true })
      }
      graphRef.current
        .width(mountRef.current!.clientWidth)
        .height(mountRef.current!.clientHeight)
        .graphData({ nodes, links })
    })()
    return () => { disposed = true }
  }, [network])

  /* Resize + teardown */
  useEffect(() => {
    const onResize = () => {
      if (graphRef.current && mountRef.current)
        graphRef.current.width(mountRef.current.clientWidth).height(mountRef.current.clientHeight)
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      graphRef.current?._destructor?.()
      graphRef.current = null
    }
  }, [])

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select className="ares-input" value={chain} onChange={e => setChain(e.target.value)} style={{ maxWidth: 120 }}>
          <option value="solana">Solana</option>
          <option value="bitcoin">Bitcoin</option>
        </select>
        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Min tx count
          <input
            className="ares-input" type="number" min={1} value={minTxCount}
            onChange={e => setMinTxCount(Math.max(1, Number(e.target.value) || 1))}
            style={{ width: 60 }}
          />
        </label>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} className={loading ? 'spin' : ''} /> Refresh</button>
      </div>

      <div style={{ position: 'relative', height: 560, borderRadius: 12, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.06)', background: 'rgba(5,8,16,0.45)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)' }}>
        <div style={{ position: 'absolute', top: 12, left: 14, zIndex: 10, pointerEvents: 'none' }}>
          <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#b9a8ff', letterSpacing: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Share2 size={13} /> MONEY-FLOW GRAPH
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 11, color: 'rgba(255,255,255,.55)', marginTop: 2 }}>
            {network?.node_count ?? 0} wallets · {network?.link_count ?? 0} observed edges
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 10, color: 'rgba(255,255,255,.32)', marginTop: 2 }}>
            built from real /trace + watchlist activity · drag to orbit · click a wallet to inspect
          </div>
        </div>

        <div ref={mountRef} style={{ position: 'absolute', inset: 0 }} />

        {!network && loading && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,.4)', fontFamily: 'monospace', fontSize: 12 }}>
            charting the network…
          </div>
        )}
        {network && network.node_count === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,.45)', pointerEvents: 'none', textAlign: 'center', padding: 20 }}>
            <Share2 size={32} style={{ opacity: 0.5, marginBottom: 10 }} />
            <p style={{ fontSize: 13, maxWidth: 360 }}>No wallet activity observed yet for this chain — run a few lookups in the Trace tab, or track wallets in the Watchlist tab, and this graph fills in from real usage.</p>
          </div>
        )}

        {selected && (
          <div style={{ position: 'absolute', bottom: 12, right: 14, zIndex: 11, width: 'min(320px, 90%)', background: 'rgba(6,6,16,.96)', border: '1px solid rgba(185,168,255,.25)', borderRadius: 12, padding: '14px 16px', backdropFilter: 'blur(14px)' }}>
            <div style={{ fontFamily: 'monospace', fontSize: 10, color: '#b9a8ff', letterSpacing: 1, marginBottom: 6 }}>WALLET</div>
            <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#fff', wordBreak: 'break-all', marginBottom: 8 }}>{selected.address}</div>
            <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Observed edges: <b>{selected.edgeCount}</b></div>
            <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Total value moved: <b>{selected.totalValue.toLocaleString(undefined, { maximumFractionDigits: 4 })}</b></div>
          </div>
        )}
      </div>
    </div>
  )
}
