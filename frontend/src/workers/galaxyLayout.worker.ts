/* Galaxy layout worker: loads services/galaxy_layout (wasm32) once and runs
   the layout + GIX hashing + vault merkle root off the main thread.
   Request:  { id, wasmUrl, input: WasmLayoutInput }   (typed arrays transferred)
   Response: { id, ok: true, result: WasmLayoutResult, ms } | { id, ok: false, error } */

import { instantiateGalaxyWasm, runWasmLayout } from '../components/galaxyLayoutWasm'
import type { GalaxyWasmExports, WasmLayoutInput } from '../components/galaxyLayoutWasm'

interface WorkerScope {
  onmessage: ((ev: MessageEvent) => void) | null
  postMessage(message: unknown, transfer: Transferable[]): void
}
const ctx = self as unknown as WorkerScope

let wasm: Promise<GalaxyWasmExports> | null = null
const load = (url: string) => {
  if (!wasm) {
    wasm = fetch(url)
      .then(r => { if (!r.ok) throw new Error(`fetch ${url}: HTTP ${r.status}`); return r.arrayBuffer() })
      .then(instantiateGalaxyWasm)
    wasm.catch(() => { wasm = null })   // allow a retry on the next request
  }
  return wasm
}

ctx.onmessage = async (ev: MessageEvent) => {
  const { id, wasmUrl, input } = ev.data as { id: number; wasmUrl: string; input: WasmLayoutInput }
  try {
    const ex = await load(wasmUrl)
    const t0 = performance.now()
    const result = runWasmLayout(ex, input)
    const ms = performance.now() - t0
    ctx.postMessage({ id, ok: true, result, ms },
      [result.wx.buffer, result.wy.buffer, result.nodeHashes.buffer] as Transferable[])
  } catch (err) {
    ctx.postMessage({ id, ok: false, error: String((err as Error)?.message ?? err) }, [])
  }
}
