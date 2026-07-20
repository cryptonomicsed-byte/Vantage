import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { RefreshCw, Share2, ExternalLink } from 'lucide-react'

/**
 * Money-Flow Galaxy — wallets, tokens, and the social accounts that mentioned
 * them, built entirely from genuinely observed data:
 *   - wallet_trades (pipeline/wallet_tracker.py — Helius Enhanced Transactions)
 *   - tracked_wallets (degen_score/trade_count from pumpfun_wallet_intel.py +
 *     degen_alpha_fusion.py enrichment)
 *   - social_signals (social_tracker.py — Twitter/Telegram mentions linked by
 *     ticker/contract address)
 * via backend/routers/alpha.py's GET /api/moneyflow. No client-side fabrication
 * or clustering step — force-directed physics naturally pulls densely-connected
 * entities into visible "hot zones", same shell NeuralVault.tsx uses for the
 * memory galaxy.
 *
 * Node brightness is recent-activity share (the Mandelbrot fade: dormant
 * nodes sink toward the background nebula, active ones glow); size is capital
 * weight (wallets/tokens) or mention count (social accounts). Token nodes
 * carry a lifecycle tier (just_launch → billion_club) for the top nodes by
 * volume, fetched live from DexScreener server-side.
 */

interface FlowNode {
  id: string; type: 'wallet' | 'token' | 'social' | 'exchange'; label: string
  trades: number; recent: number; volume_sol: number
  brightness: number; size: number; first_seen: number; last_seen?: number
  // wallet-only
  address?: string; chain?: string; degen_score?: number
  trade_count?: number; unique_tokens?: number; address_type?: string
  is_migration_anchor?: boolean; balance_usd?: number; dormant_dim?: boolean
  // token-only
  ca?: string; market_cap?: number | null; tier?: string
  migration_distance?: number; dormant_void?: boolean
  // social-only
  platform?: string; mentions?: number
}
interface FlowEdge {
  source: string; target: string
  type: 'traded' | 'counterparty' | 'mentioned' | 'claimed_wallet' | 'migration_gravity' | `role:${string}`
  trades: number; volume_sol: number; net_sol: number; sentiment?: string; last_ts: number
  // role:* edges (deployer/top_holder/top_trader/first_buyer, from
  // pumpfun_wallet_intel.py's persisted token_wallet_roles)
  role?: string; rank?: number; metric?: number; metric_label?: string; color?: string
  // claimed_wallet edges (social account → wallet extracted from their own posts)
  post_url?: string; post_excerpt?: string
  // migration_gravity edges (token → nearest major exchange anchor):
  // 0 = migrated/connected, 1 = just launched/far. Drives custom link
  // distance below so tokens visibly drift toward the exchange node as
  // they approach migration.
  migration_distance?: number
}
interface MoneyFlowData {
  nodes: FlowNode[]; edges: FlowEdge[]
  wallets: number; tokens: number; social: number; window_hours: number; generated_at: number
}

interface GNode { id: string; name: string; val: number; color: string; ntype: string; raw: FlowNode }
interface GLink { source: string; target: string; color: string; width: number; type: string; live: boolean; migrationDistance?: number }

// A synapse only fires if its underlying activity happened within this many
// seconds of the server's snapshot time — i.e. within roughly one refresh
// cycle. Everything older is a real, drawn connection (still visible as a
// static line) but does not pulse, so the galaxy isn't animating 24/7 on
// data that's actually hours or days old.
const LIVE_WINDOW_SECONDS = 180

const TIER_LABEL: Record<string, string> = {
  just_launch: 'Just Launched', pumpfun_10k_20k: 'Pump 10-20k', pre_migration: 'Pre-Migration',
  just_migrated: 'Just Migrated', migrated_1m: '$1M+', migrated_10m: '$10M+', migrated_20m: '$20M+',
  migrated_100m: '$100M+', migrated_500m: '$500M+', migrated_1b: '$1B+', billion_club: 'Billion Club',
}
const TIER_ORDER = ['just_launch', 'pumpfun_10k_20k', 'pre_migration', 'just_migrated',
  'migrated_1m', 'migrated_10m', 'migrated_20m', 'migrated_100m', 'migrated_500m', 'migrated_1b', 'billion_club']

const TYPE_COLOR: Record<string, string> = { wallet: '#3b82f6', token: '#ff2d4a', social: '#39ff14', exchange: '#f5a623' }

// 9 wallet balance tiers, dimmest (smallest) to brightest (whale). Under
// $100k is a dim slate blue through to >$250M, a hot white-gold — meant to
// read at a glance which wallets in the galaxy are actually worth watching.
const WALLET_TIERS: { max: number; color: string; label: string }[] = [
  { max: 100_000, color: '#475569', label: '<$100K' },
  { max: 250_000, color: '#3b82f6', label: '<$250K' },
  { max: 500_000, color: '#22d3ee', label: '<$500K' },
  { max: 1_000_000, color: '#22c55e', label: '<$1M' },
  { max: 10_000_000, color: '#eab308', label: '<$10M' },
  { max: 50_000_000, color: '#f97316', label: '<$50M' },
  { max: 100_000_000, color: '#ef4444', label: '<$100M' },
  { max: 250_000_000, color: '#ec4899', label: '<$250M' },
  { max: Infinity, color: '#f8fafc', label: '$250M+' },
]

function walletTierColor(balanceUsd: number | undefined): string {
  const b = balanceUsd || 0
  for (const t of WALLET_TIERS) if (b < t.max) return t.color
  return WALLET_TIERS[WALLET_TIERS.length - 1].color
}
function walletTierLabel(balanceUsd: number | undefined): string {
  const b = balanceUsd || 0
  for (const t of WALLET_TIERS) if (b < t.max) return t.label
  return WALLET_TIERS[WALLET_TIERS.length - 1].label
}

function externalLinks(n: FlowNode): { label: string; url: string }[] {
  if (n.type === 'wallet' && n.address) {
    return [
      { label: 'Solscan', url: `https://solscan.io/account/${n.address}` },
      { label: 'Birdeye', url: `https://birdeye.so/profile/${n.address}?chain=solana` },
    ]
  }
  if (n.type === 'token' && n.ca) {
    return [
      { label: 'DexScreener', url: `https://dexscreener.com/solana/${n.ca}` },
      { label: 'Birdeye', url: `https://birdeye.so/token/${n.ca}?chain=solana` },
      { label: 'Solscan', url: `https://solscan.io/token/${n.ca}` },
      { label: 'Pump.fun', url: `https://pump.fun/${n.ca}` },
    ]
  }
  if (n.type === 'social' && n.platform && n.label) {
    const handle = n.label.replace(/^@/, '')
    const url = n.platform === 'telegram' ? `https://t.me/${handle}` : `https://twitter.com/${handle}`
    return [{ label: n.platform === 'telegram' ? 'Telegram' : 'Twitter/X', url }]
  }
  if (n.type === 'exchange' && n.address) {
    return [{ label: 'Solscan (program)', url: `https://solscan.io/account/${n.address}` }]
  }
  return []
}

function buildGraph(data: MoneyFlowData, activeTiers: Set<string> | null, trackedAddresses: Set<string> | null): { nodes: GNode[]; links: GLink[] } {
  let filteredNodes = data.nodes
  if (trackedAddresses && trackedAddresses.size > 0) {
    // "My Tracked Wallets" — narrow to only wallet nodes whose address is
    // in tracked_wallets (same set the Portfolio/watch-only forms write
    // to), plus whatever they're directly connected to (tokens, social
    // accounts) so it's not just isolated dots.
    const keep = new Set<string>()
    for (const n of data.nodes) {
      if (n.type === 'wallet' && n.address && trackedAddresses.has(n.address.toLowerCase())) keep.add(n.id)
    }
    for (const e of data.edges) {
      if (keep.has(e.source) || keep.has(e.target)) { keep.add(e.source); keep.add(e.target) }
    }
    filteredNodes = data.nodes.filter(n => keep.has(n.id))
  } else if (activeTiers && activeTiers.size > 0) {
    const keep = new Set<string>()
    for (const n of data.nodes) {
      if (n.type === 'token' && n.tier && activeTiers.has(n.tier)) keep.add(n.id)
    }
    // pull in directly-connected wallets/social so tokens don't float alone
    for (const e of data.edges) {
      if (keep.has(e.source) || keep.has(e.target)) { keep.add(e.source); keep.add(e.target) }
    }
    filteredNodes = data.nodes.filter(n => n.type !== 'token' ? keep.has(n.id) : activeTiers.has(n.tier || ''))
  }
  const nodeIds = new Set(filteredNodes.map(n => n.id))

  const nodes: GNode[] = filteredNodes.map(n => {
    // Wallets are colored by balance tier (9 buckets, dim slate → hot
    // white for $250M+) rather than a flat "all wallets are blue" —
    // that's the actual point of tracking balance at all: at a glance,
    // which wallets in the galaxy are worth paying attention to.
    const base = n.type === 'wallet' ? walletTierColor(n.balance_usd)
      : (n.type === 'token' && n.dormant_void) ? '#4b5563'  // rugged/collapsed — faded slate, not the live token red
      : TYPE_COLOR[n.type]
    return {
      id: n.id,
      name: n.label,
      ntype: n.type,
      val: 2 + n.size * 16,
      color: base,
      raw: n,
    }
  })

  const links: GLink[] = data.edges
    .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
    .map(e => ({
      source: e.source, target: e.target, type: e.type,
      color: e.type === 'migration_gravity'
        ? `rgba(245,166,35,${0.08 + (1 - (e.migration_distance ?? 0.5)) * 0.3})` // fainter when far, brighter as it nears/joins the exchange
        : e.type.startsWith('role:')
        ? (e.color || '#a855f7') + 'aa'
        : e.type === 'claimed_wallet'
        ? 'rgba(236,72,153,0.6)'
        : e.type === 'mentioned'
        ? (e.sentiment === 'BEARISH' ? 'rgba(255,45,74,0.5)' : 'rgba(57,255,20,0.45)')
        : (e.net_sol >= 0 ? 'rgba(57,255,20,0.4)' : 'rgba(255,170,0,0.4)'),
      width: e.type === 'migration_gravity' ? 0.5 : e.type.startsWith('role:') || e.type === 'claimed_wallet' ? 1.5 : 0.5 + Math.min(3, e.trades / 3),
      live: e.type === 'migration_gravity' ? false : (data.generated_at - (e.last_ts || 0)) <= LIVE_WINDOW_SECONDS,
      migrationDistance: e.migration_distance,
    }))
  return { nodes, links }
}

export default function MoneyFlowGraph() {
  const mountRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)
  const [hours, setHours] = useState(24)
  const [network, setNetwork] = useState<MoneyFlowData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<FlowNode | null>(null)
  const [activeTiers, setActiveTiers] = useState<Set<string>>(new Set())
  const [showTrackedOnly, setShowTrackedOnly] = useState(false)
  const [trackedAddresses, setTrackedAddresses] = useState<Set<string>>(new Set())

  useEffect(() => {
    (async () => {
      try {
        const key = localStorage.getItem('vantage_api_key') || ''
        const r = await fetch('/api/intel/watchlist', { headers: { 'X-Agent-Key': key } })
        if (r.ok) {
          const d = await r.json()
          setTrackedAddresses(new Set((d.wallets || []).map((w: any) => (w.address || '').toLowerCase())))
        }
      } catch { /* best-effort */ }
    })()
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/moneyflow?hours=${hours}&tier_lookups=15`)
      if (r.ok) setNetwork(await r.json())
    } catch { /* offline — keep last-known graph */ }
    setLoading(false)
  }, [hours])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const id = setInterval(load, 60_000) // self-updating, same cadence as other intel tabs
    return () => clearInterval(id)
  }, [load])

  const tiersPresent = useMemo(() => {
    if (!network) return []
    const present = new Set(network.nodes.filter(n => n.type === 'token' && n.tier).map(n => n.tier!))
    return TIER_ORDER.filter(t => present.has(t))
  }, [network])

  const toggleTier = (tier: string) => {
    setActiveTiers(prev => {
      const next = new Set(prev)
      next.has(tier) ? next.delete(tier) : next.add(tier)
      return next
    })
  }

  /* Mount / update the 3D graph */
  useEffect(() => {
    if (!network || !mountRef.current) return
    let disposed = false
    ;(async () => {
      const [{ default: ForceGraph3D }, THREE] = await Promise.all([
        import('3d-force-graph'), import('three'),
      ])
      if (disposed || !mountRef.current) return

      const { nodes, links } = buildGraph(network, activeTiers, showTrackedOnly ? trackedAddresses : null)

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
          .nodeLabel((n: any) => `<div style="font-family:monospace;font-size:11px;color:#dfe6ff;background:rgba(5,5,16,.9);padding:4px 8px;border-radius:6px;border:1px solid rgba(255,255,255,.12)">${n.ntype.toUpperCase()} · ${n.name}</div>`)
          .nodeThreeObject((n: any) => {
            const brightness = Math.max(0.15, n.raw.brightness)
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
          .linkOpacity(0.35)
          .linkWidth((l: any) => l.width)
          .linkDirectionalParticles((l: any) => l.live ? Math.min(4, Math.max(1, Math.round(l.width))) : 0)
          .linkDirectionalParticleWidth(1.4)
          .linkDirectionalParticleSpeed((l: any) => l.live ? 0.006 : 0)
          .onNodeClick((n: any) => {
            const g = graphRef.current
            const dist = 60
            const ratio = 1 + dist / Math.hypot(n.x || 1, n.y || 1, n.z || 1)
            g.cameraPosition({ x: (n.x || 1) * ratio, y: (n.y || 1) * ratio, z: (n.z || 1) * ratio }, n, 1200)
            setSelected(n.raw)
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

      const linkForce = graphRef.current.d3Force('link')
      if (linkForce) {
        linkForce.distance((l: any) =>
          l.type === 'migration_gravity' ? 40 + (l.migrationDistance ?? 0.5) * 350 : 60
        )
      }
    })()
    return () => { disposed = true }
  }, [network, activeTiers, showTrackedOnly, trackedAddresses])

  /* Resize + teardown */
  useEffect(() => {
    const onResize = () => {
      if (graphRef.current && mountRef.current)
        graphRef.current.width(mountRef.current.clientWidth).height(mountRef.current.clientHeight)
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      // Properly dispose Three.js renderer and WebGL context
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
          console.error('Error disposing graph:', e)
        }
      }
      graphRef.current = null
    }
  }, [])

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Window
          <select className="ares-input" value={hours} onChange={e => setHours(Number(e.target.value))} style={{ maxWidth: 110 }}>
            <option value={6}>6h</option>
            <option value={24}>24h</option>
            <option value={72}>3d</option>
            <option value={168}>7d</option>
            <option value={720}>30d</option>
          </select>
        </label>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={12} className={loading ? 'spin' : ''} /> Refresh</button>
        <button
          className="btn btn-sm"
          onClick={() => setShowTrackedOnly(v => !v)}
          title={trackedAddresses.size === 0 ? 'No tracked wallets yet — add some from Portfolio → Wallets or Add External Wallet' : `${trackedAddresses.size} tracked wallet(s)`}
          style={showTrackedOnly ? { background: 'rgba(59,130,246,0.25)', border: '1px solid rgba(59,130,246,0.5)', color: '#3b82f6' } : undefined}
        >
          My Tracked Wallets{trackedAddresses.size > 0 ? ` (${trackedAddresses.size})` : ''}
        </button>
        {activeTiers.size > 0 && (
          <button className="btn btn-ghost btn-sm" onClick={() => setActiveTiers(new Set())}>Clear tier filter</button>
        )}
      </div>

      {tiersPresent.length > 0 && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
          {tiersPresent.map(t => (
            <button
              key={t}
              onClick={() => toggleTier(t)}
              className="btn btn-ghost btn-sm"
              style={{
                fontSize: 10, padding: '3px 8px',
                background: activeTiers.has(t) ? 'rgba(255,45,74,0.25)' : undefined,
                border: activeTiers.has(t) ? '1px solid rgba(255,45,74,0.5)' : undefined,
              }}
            >
              {TIER_LABEL[t] || t}
            </button>
          ))}
        </div>
      )}

      <div style={{ position: 'relative', height: 560, borderRadius: 12, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)', background: 'radial-gradient(circle at 25% 15%, rgba(138,75,255,0.12), rgba(5,8,16,0.18) 55%, rgba(5,8,16,0) 100%)' }}>
        <div style={{ position: 'absolute', top: 12, left: 14, zIndex: 10, pointerEvents: 'none' }}>
          <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#b9a8ff', letterSpacing: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Share2 size={13} /> MONEY-FLOW GALAXY
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 11, color: 'rgba(255,255,255,.55)', marginTop: 2 }}>
            {network?.wallets ?? 0} wallets · {network?.tokens ?? 0} tokens · {network?.social ?? 0} social · {network?.edges.length ?? 0} flows
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 10, color: 'rgba(255,255,255,.32)', marginTop: 2 }}>
            blue=wallet · red=token · green=social · drag to orbit · click to inspect
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 9, color: 'rgba(255,255,255,.28)', marginTop: 2 }}>
            edges: gold=deployer · purple=top holder · cyan=first buyer · pink=claimed wallet (from a PnL post)
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 9, color: 'rgba(245,166,35,.4)', marginTop: 2 }}>
            faint orange = migration gravity — tokens drift toward the exchange node as they near/complete migration
          </div>
        </div>

        <div ref={mountRef} style={{ position: 'absolute', inset: 0 }} />

        {!network && loading && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,.4)', fontFamily: 'monospace', fontSize: 12 }}>
            charting the galaxy…
          </div>
        )}
        {network && network.nodes.length === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,.45)', pointerEvents: 'none', textAlign: 'center', padding: 20 }}>
            <Share2 size={32} style={{ opacity: 0.5, marginBottom: 10 }} />
            <p style={{ fontSize: 13, maxWidth: 360 }}>No trade activity observed yet in this window — the wallet tracker fills this in as tracked wallets trade, and social mentions link in from social_tracker.py automatically.</p>
          </div>
        )}

        {selected && (
          <div style={{ position: 'absolute', bottom: 12, right: 14, zIndex: 11, width: 'min(340px, 90%)', background: 'rgba(6,6,16,.96)', border: '1px solid rgba(185,168,255,.25)', borderRadius: 12, padding: '14px 16px', backdropFilter: 'blur(14px)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontFamily: 'monospace', fontSize: 10, color: TYPE_COLOR[selected.type], letterSpacing: 1, marginBottom: 6 }}>
                  {selected.type.toUpperCase()}{selected.tier ? ` · ${TIER_LABEL[selected.tier] || selected.tier}` : ''}
                </div>
                <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#fff', wordBreak: 'break-all', marginBottom: 8 }}>
                  {selected.address || selected.ca || selected.label}
                </div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setSelected(null)} style={{ padding: '2px 6px' }}>✕</button>
            </div>

            {selected.type === 'wallet' && (
              <>
                <div style={{ fontSize: 12, color: walletTierColor(selected.balance_usd), fontWeight: 700 }}>
                  Balance: {selected.balance_usd ? `$${selected.balance_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : 'unknown'} · {walletTierLabel(selected.balance_usd)}
                </div>
                {selected.dormant_dim && (
                  <div style={{ fontSize: 11, color: '#f59e0b', marginBottom: 4 }}>Dormant (no activity 3+ days) — kept visible only because balance ≥ $10K</div>
                )}
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Degen score: <b>{selected.degen_score ?? 0}</b></div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Trade count: <b>{selected.trade_count ?? selected.trades}</b></div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Unique tokens: <b>{selected.unique_tokens ?? 0}</b></div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Volume observed: <b>{selected.volume_sol.toLocaleString(undefined, { maximumFractionDigits: 4 })} SOL</b></div>
              </>
            )}
            {selected.type === 'token' && (
              <>
                {selected.dormant_void && (
                  <div style={{ fontSize: 11, color: '#ef4444', fontWeight: 700, marginBottom: 4 }}>⚠ Graduated then collapsed back under $7K — faded into the dormant void</div>
                )}
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Market cap: <b>{selected.market_cap ? `$${selected.market_cap.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : 'unlisted'}</b></div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Trades observed: <b>{selected.trades}</b></div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Volume: <b>{selected.volume_sol.toLocaleString(undefined, { maximumFractionDigits: 4 })} SOL</b></div>
              </>
            )}
            {selected.type === 'exchange' && (
              <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>The real on-chain destination pump.fun-origin tokens migrate their liquidity to.</div>
            )}
            {selected.type === 'social' && (
              <>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Platform: <b>{selected.platform}</b></div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,.7)' }}>Mentions: <b>{selected.mentions ?? 0}</b></div>
              </>
            )}
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,.4)', marginTop: 4 }}>
              Activity: <b style={{ color: selected.brightness > 0.4 ? '#39ff14' : 'rgba(255,255,255,.5)' }}>
                {selected.brightness > 0.6 ? 'hot' : selected.brightness > 0.2 ? 'active' : 'dormant'}
              </b>
            </div>

            {externalLinks(selected).length > 0 && (
              <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {externalLinks(selected).map(l => (
                  <a key={l.url} href={l.url} target="_blank" rel="noreferrer"
                     style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 8px', background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)', borderRadius: 6, color: '#b9a8ff', textDecoration: 'none', fontSize: 10 }}>
                    {l.label} <ExternalLink size={10} />
                  </a>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
