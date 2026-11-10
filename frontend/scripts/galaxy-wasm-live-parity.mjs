// Parity of JS buildLayout vs the wasm layout on a REAL vault dump
// (GET /api/agents/{name}/vault/galaxy?compact=1 saved to a file).
// usage: node --no-warnings scripts/galaxy-wasm-live-parity.mjs <vault.json>
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { performance } from 'node:perf_hooks'
import { buildLayout } from '../src/components/galaxyLayout.ts'
import { encodeLayoutInput, instantiateGalaxyWasm, maxPositionError, runWasmLayout } from '../src/components/galaxyLayoutWasm.ts'

const here = dirname(fileURLToPath(import.meta.url))
const vault = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const nodes = vault.stars || []
const t0 = performance.now()
const js = buildLayout(nodes)
const t1 = performance.now()
const inst = await instantiateGalaxyWasm(readFileSync(join(here, '../public/wasm/galaxy_layout.wasm')))
const t2 = performance.now()
const out = runWasmLayout(inst, encodeLayoutInput(nodes))
const t3 = performance.now()
const err = maxPositionError(js, out)
console.log(`live vault: ${nodes.length} nodes, ${js.cells.length} constellations`)
console.log(`js ${(t1 - t0).toFixed(0)} ms | wasm instantiate ${(t2 - t1).toFixed(0)} ms | wasm layout+hash ${(t3 - t2).toFixed(0)} ms`)
console.log(`max position error ${err.toExponential(3)} | merkle root ${String(out.merkleRoot || out.root || '').slice(0, 16)}`)
console.log(err <= 1e-3 ? 'LIVE PARITY: OK' : 'LIVE PARITY: FAIL')
process.exit(err <= 1e-3 ? 0 : 1)
