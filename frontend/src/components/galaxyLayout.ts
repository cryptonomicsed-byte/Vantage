/* ─── Layout ─────────────────────────────────────────────────────────────────
   The server places constellations side by side along x (a 16000 × 1000
   strip for a large vault) and derives y/z from a hash of the title, so every
   memory with the same title ("Intel Scan Complete" × hundreds) lands on the
   same point. Here the isometric projection is applied once, constellations
   are arranged on a grid, and coincident nodes fan out on a golden-angle
   spiral so each one is visible and clickable. World units are the server's;
   the view transform is screen = world * scale + offset.

   Pure module (no React / DOM) so the wasm parity script can run it under
   node. services/galaxy_layout (Rust → wasm) is a port of buildLayout and
   must stay in lock-step with it: change both, then rerun
   frontend/scripts/galaxy-wasm-parity.mjs and galaxy-layout-fixtures.mjs. */

export interface LayoutNode {
  id: string
  x: number
  y: number
  z: number
  constellation: string
}

export interface LayoutCell { name: string; x: number; y: number; w: number; h: number; count: number }

export interface Layout {
  wx: Float32Array
  wy: Float32Array
  idx: Map<string, number>
  cells: LayoutCell[]
  minX: number; minY: number; maxX: number; maxY: number
  byCoord: Map<string, number>
}

export const iso = (x: number, y: number, z: number): [number, number] => [x - z * 0.4, y + z * 0.2]

export function buildLayout(nodes: LayoutNode[]): Layout {
  const n = nodes.length
  const wx = new Float32Array(n), wy = new Float32Array(n)
  const idx = new Map<string, number>()
  const byCoord = new Map<string, number>()
  const groups = new Map<string, number[]>()
  nodes.forEach((nd, i) => {
    const g = nd.constellation || 'other'
    if (!groups.has(g)) groups.set(g, [])
    groups.get(g)!.push(i)
  })
  const names = [...groups.keys()].sort((a, b) => groups.get(b)!.length - groups.get(a)!.length)
  const local = names.map(name => {
    const ids = groups.get(name)!
    let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity
    ids.forEach(i => {
      const [px, py] = iso(nodes[i].x, nodes[i].y, nodes[i].z)
      if (px < mnx) mnx = px; if (py < mny) mny = py
      if (px > mxx) mxx = px; if (py > mxy) mxy = py
    })
    return { name, ids, mnx, mny, w: Math.max(200, mxx - mnx), h: Math.max(200, mxy - mny) }
  })
  const cellW = Math.max(200, ...local.map(l => l.w)) + 400
  const cellH = Math.max(200, ...local.map(l => l.h)) + 500
  const cols = Math.max(1, Math.ceil(Math.sqrt(local.length * (cellH / cellW) * 1.6)))
  const cells: Layout['cells'] = []
  local.forEach((l, k) => {
    const cx = (k % cols) * cellW, cy = Math.floor(k / cols) * cellH
    const seen = new Map<string, number>()
    l.ids.forEach(i => {
      const nd = nodes[i]
      const [px, py] = iso(nd.x, nd.y, nd.z)
      let x = cx + (px - l.mnx), y = cy + (py - l.mny)
      const key = `${Math.round(px)}|${Math.round(py)}`
      const dup = seen.get(key) || 0
      seen.set(key, dup + 1)
      if (dup > 0) {                       // fan coincident memories out
        const a = dup * 2.39996, r = 14 * Math.sqrt(dup)
        x += Math.cos(a) * r; y += Math.sin(a) * r
      }
      wx[i] = x; wy[i] = y
      idx.set(nd.id, i)
      const ck = `${nd.x}|${nd.y}|${nd.z}`
      if (!byCoord.has(ck)) byCoord.set(ck, i)
    })
    cells.push({ name: l.name, x: cx, y: cy, w: l.w, h: l.h, count: l.ids.length })
  })
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (let i = 0; i < n; i++) {
    if (wx[i] < minX) minX = wx[i]; if (wy[i] < minY) minY = wy[i]
    if (wx[i] > maxX) maxX = wx[i]; if (wy[i] > maxY) maxY = wy[i]
  }
  if (n === 0) { minX = minY = 0; maxX = maxY = 1 }
  return { wx, wy, idx, cells, minX, minY: minY - 160, maxX, maxY, byCoord }
}
