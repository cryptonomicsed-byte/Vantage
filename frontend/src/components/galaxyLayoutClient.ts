/// <reference types="vite/client" />
/* Main-thread side of the wasm galaxy layout (default OFF).
   Enable with localStorage['galaxy.wasm'] = '1' or VITE_GALAXY_WASM=1 at
   build time. GalaxyViewer always renders the JS layout first; this swaps in
   the worker's layout only if it answers and matches JS (≤ 1e-3). Any
   failure keeps the JS layout and warns once. */

import type { Layout } from './galaxyLayout'
import { encodeLayoutInput, maxPositionError } from './galaxyLayoutWasm'
import type { WasmLayoutNode, WasmLayoutResult } from './galaxyLayoutWasm'

export const GALAXY_WASM_FLAG = 'galaxy.wasm'
const TIMEOUT_MS = 20000
const PARITY_TOL = 1e-3

export function galaxyWasmEnabled(): boolean {
  try {
    if (import.meta.env?.VITE_GALAXY_WASM === '1') return true
  } catch { /* */ }
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(GALAXY_WASM_FLAG) === '1'
  } catch { return false }
}

let warned = false
export function warnGalaxyWasmOnce(err: unknown) {
  if (warned) return
  warned = true
  console.warn('[galaxy] wasm layout unavailable, using JS layout:', err)
}

export interface WasmLayoutSwap {
  layout: Layout
  merkleRoot: string
  nodeHashes: Uint8Array
  ms: number
}

type Pending = { resolve: (r: { result: WasmLayoutResult; ms: number }) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> }
let worker: Worker | null = null
let broken: Error | null = null
let seq = 0
const pending = new Map<number, Pending>()

function failAll(e: Error) {
  pending.forEach(p => { clearTimeout(p.timer); p.reject(e) })
  pending.clear()
}

function getWorker(): Worker {
  if (broken) throw broken
  if (!worker) {
    worker = new Worker(new URL('../workers/galaxyLayout.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (ev: MessageEvent) => {
      const m = ev.data as { id: number; ok: boolean; result?: WasmLayoutResult; ms?: number; error?: string }
      const p = pending.get(m.id)
      if (!p) return
      pending.delete(m.id)
      clearTimeout(p.timer)
      if (m.ok && m.result) p.resolve({ result: m.result, ms: m.ms ?? 0 })
      else p.reject(new Error(m.error || 'worker error'))
    }
    worker.onerror = (ev: ErrorEvent) => {
      broken = new Error(`galaxy worker failed: ${ev.message || 'load error'}`)
      ev.preventDefault?.()
      failAll(broken)
      worker?.terminate(); worker = null
    }
  }
  return worker
}

/** Compute the layout in the worker and return a Layout that reuses the JS
 *  layout's id/coord maps (same node indices) with wasm positions + cells. */
export async function computeWasmLayout(nodes: WasmLayoutNode[], jsLayout: Layout): Promise<WasmLayoutSwap> {
  const w = getWorker()
  const input = encodeLayoutInput(nodes)
  const id = ++seq
  const wasmUrl = `${import.meta.env?.BASE_URL ?? '/'}wasm/galaxy_layout.wasm`
  const { result, ms } = await new Promise<{ result: WasmLayoutResult; ms: number }>((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('galaxy worker timeout')) }, TIMEOUT_MS)
    pending.set(id, { resolve, reject, timer })
    w.postMessage({ id, wasmUrl, input },
      [input.x.buffer, input.y.buffer, input.z.buffer, input.group.buffer, input.strBytes.buffer, input.offs.buffer])
  })
  const err = maxPositionError(result, jsLayout)
  if (!(err <= PARITY_TOL)) throw new Error(`wasm/js layout mismatch: max error ${err}`)
  if (result.cells.length !== jsLayout.cells.length) throw new Error('wasm/js constellation count mismatch')
  return {
    layout: {
      wx: result.wx, wy: result.wy,
      idx: jsLayout.idx, byCoord: jsLayout.byCoord,
      cells: result.cells.map(c => ({ name: c.name, x: c.x, y: c.y, w: c.w, h: c.h, count: c.count })),
      minX: result.minX, minY: result.minY, maxX: result.maxX, maxY: result.maxY,
    },
    merkleRoot: result.merkleRoot,
    nodeHashes: result.nodeHashes,
    ms,
  }
}

export function nodeGixId(hashes: Uint8Array, i: number, bytes = 6): string {
  let s = ''
  for (let k = 0; k < bytes; k++) s += hashes[i * 32 + k].toString(16).padStart(2, '0')
  return s
}
