/* Bridge between JS and services/galaxy_layout (wasm32, no wasm-bindgen).
   Pure module — used by the Web Worker and by the node parity script, so the
   parity test exercises the exact code path the browser runs. ABI is
   documented in services/galaxy_layout/src/ffi.rs. */

import type { LayoutNode } from './galaxyLayout.ts'

export const GALAXY_WASM_ABI = 1

/** Typed-array request: everything is transferable, no JSON of 15k nodes. */
export interface WasmLayoutInput {
  n: number
  x: Float64Array
  y: Float64Array
  z: Float64Array
  group: Uint32Array
  groupNames: string[]
  /** UTF-8 of node keys (path || id) followed by group names */
  strBytes: Uint8Array
  /** n + groupNames.length + 1 byte offsets into strBytes */
  offs: Uint32Array
}

export interface WasmLayoutCell {
  name: string; x: number; y: number; w: number; h: number; count: number
  oduBase: number; oduComposed: number
}

export interface WasmLayoutResult {
  wx: Float32Array
  wy: Float32Array
  cells: WasmLayoutCell[]
  minX: number; minY: number; maxX: number; maxY: number
  /** n * 32 bytes: GIX content_hash(path || id) per node — stable node ids */
  nodeHashes: Uint8Array
  /** gix1_merkle_root (sort-then-hash) over the node hashes, hex */
  merkleRoot: string
}

export interface GalaxyWasmExports {
  memory: WebAssembly.Memory
  ga_version(): number
  ga_alloc(len: number): number
  ga_free(ptr: number, len: number): void
  ga_layout(n: number, x: number, y: number, z: number, g: number, nGroups: number,
            s: number, sLen: number, offs: number): number
  ga_result_free(header: number): void
}

export type WasmLayoutNode = LayoutNode & { path?: string }

export function encodeLayoutInput(nodes: WasmLayoutNode[]): WasmLayoutInput {
  const n = nodes.length
  const x = new Float64Array(n), y = new Float64Array(n), z = new Float64Array(n)
  const group = new Uint32Array(n)
  const gIndex = new Map<string, number>()
  const groupNames: string[] = []
  const keys: string[] = new Array(n)
  let bound = 0
  for (let i = 0; i < n; i++) {
    const nd = nodes[i]
    x[i] = nd.x; y[i] = nd.y; z[i] = nd.z
    const g = nd.constellation || 'other'           // same rule as buildLayout
    let gi = gIndex.get(g)
    if (gi === undefined) { gi = groupNames.length; gIndex.set(g, gi); groupNames.push(g) }
    group[i] = gi
    keys[i] = String(nd.path || nd.id || '')
    bound += keys[i].length * 3
  }
  for (const g of groupNames) bound += g.length * 3
  const enc = new TextEncoder()
  const buf = new Uint8Array(bound)
  const offs = new Uint32Array(n + groupNames.length + 1)
  let pos = 0, k = 0
  const put = (s: string) => { offs[k++] = pos; pos += enc.encodeInto(s, buf.subarray(pos)).written ?? 0 }
  keys.forEach(put)
  groupNames.forEach(put)
  offs[k] = pos
  return { n, x, y, z, group, groupNames, strBytes: buf.slice(0, pos), offs }
}

const hex = (b: Uint8Array) => Array.from(b, v => v.toString(16).padStart(2, '0')).join('')

export function runWasmLayout(ex: GalaxyWasmExports, inp: WasmLayoutInput): WasmLayoutResult {
  if (ex.ga_version() !== GALAXY_WASM_ABI) throw new Error(`galaxy_layout ABI ${ex.ga_version()} != ${GALAXY_WASM_ABI}`)
  const { n } = inp
  const parts: Array<[ArrayBufferView, number]> = []
  const put = (a: Float64Array | Uint32Array | Uint8Array) => {
    const p = ex.ga_alloc(a.byteLength)
    if (!p) throw new Error('galaxy_layout: alloc failed')
    parts.push([a, p])
    return p
  }
  const px = put(inp.x), py = put(inp.y), pz = put(inp.z), pg = put(inp.group)
  const ps = put(inp.strBytes), po = put(inp.offs)
  // copy after all allocs — memory.grow detaches earlier views
  const mem = new Uint8Array(ex.memory.buffer)
  for (const [a, p] of parts) if (a.byteLength) mem.set(new Uint8Array(a.buffer, a.byteOffset, a.byteLength), p)
  let hdr = 0
  try {
    hdr = ex.ga_layout(n, px, py, pz, pg, inp.groupNames.length, ps, inp.strBytes.byteLength, po)
    const buf = ex.memory.buffer
    const h = new Uint32Array(buf, hdr, 12)
    if (h[8] !== 0) throw new Error(`galaxy_layout: bad input (status ${h[8]})`)
    const nCells = h[4]
    const cf = new Float64Array(buf, h[3], nCells * 8)
    const bounds = new Float64Array(buf, h[5], 4)
    const cells: WasmLayoutCell[] = []
    for (let c = 0; c < nCells; c++) {
      const o = c * 8
      cells.push({ name: inp.groupNames[cf[o]], x: cf[o + 1], y: cf[o + 2], w: cf[o + 3], h: cf[o + 4],
        count: cf[o + 5], oduBase: cf[o + 6], oduComposed: cf[o + 7] })
    }
    return {
      wx: new Float32Array(buf, h[0], n).slice(),
      wy: new Float32Array(buf, h[1], n).slice(),
      cells,
      minX: bounds[0], minY: bounds[1], maxX: bounds[2], maxY: bounds[3],
      nodeHashes: new Uint8Array(buf, h[6], n * 32).slice(),
      merkleRoot: hex(new Uint8Array(buf, h[7], 32)),
    }
  } finally {
    if (hdr) ex.ga_result_free(hdr)
    for (const [a, p] of parts) ex.ga_free(p, a.byteLength)
  }
}

export async function instantiateGalaxyWasm(bytes: BufferSource): Promise<GalaxyWasmExports> {
  const { instance } = await WebAssembly.instantiate(bytes, {})
  return instance.exports as unknown as GalaxyWasmExports
}

/** Largest |wasm − js| over positions; Infinity on length mismatch. */
export function maxPositionError(a: { wx: Float32Array; wy: Float32Array }, b: { wx: Float32Array; wy: Float32Array }): number {
  if (a.wx.length !== b.wx.length || a.wy.length !== b.wy.length) return Infinity
  // equal (incl. ±Infinity) or both NaN → 0; exactly one NaN → Infinity
  const df = (p: number, q: number) => p === q || (p !== p && q !== q) ? 0 : (p !== p || q !== q ? Infinity : Math.abs(p - q))
  let m = 0
  for (let i = 0; i < a.wx.length; i++) {
    const d = Math.max(df(a.wx[i], b.wx[i]), df(a.wy[i], b.wy[i]))
    if (d > m) m = d
  }
  return m
}
