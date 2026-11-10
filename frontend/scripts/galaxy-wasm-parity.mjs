#!/usr/bin/env node
// Parity + timing: JS buildLayout (src/components/galaxyLayout.ts) vs the
// wasm port (public/wasm/galaxy_layout.wasm) on a generated 15k-node vault
// with duplicates and multiple constellations, through the same bridge the
// worker uses (src/components/galaxyLayoutWasm.ts).
//   frontend/scripts/build-galaxy-wasm.sh && node frontend/scripts/galaxy-wasm-parity.mjs [n]
// Exits non-zero on any mismatch > 1e-3. Needs node >= 22.18 (type stripping).
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { performance } from 'node:perf_hooks'
import { buildLayout } from '../src/components/galaxyLayout.ts'
import { encodeLayoutInput, instantiateGalaxyWasm, maxPositionError, runWasmLayout } from '../src/components/galaxyLayoutWasm.ts'
import { genNodes } from './galaxy-fixture-gen.mjs'

const TOL = 1e-3
const here = dirname(fileURLToPath(import.meta.url))
const wasmPath = join(here, '..', 'public', 'wasm', 'galaxy_layout.wasm')
const N = Number(process.argv[2] || 15406)
const RUNS = 7

const median = a => [...a].sort((x, y) => x - y)[a.length >> 1]
const fail = msg => { console.error(`FAIL: ${msg}`); process.exitCode = 1 }

const nodes = genNodes(N, 15406)
const bytes = readFileSync(wasmPath)
const ex = await instantiateGalaxyWasm(bytes)

let js, wasm
const tJs = [], tWasm = [], tEnc = []
for (let r = 0; r < RUNS; r++) {
  let t = performance.now(); js = buildLayout(nodes); tJs.push(performance.now() - t)
  t = performance.now(); const input = encodeLayoutInput(nodes); tEnc.push(performance.now() - t)
  t = performance.now(); wasm = runWasmLayout(ex, input); tWasm.push(performance.now() - t)
}

const coords = new Set(nodes.map(n => `${n.x}|${n.y}|${n.z}`)).size
console.log(`fixture: ${N} nodes, ${js.cells.length} constellations, ${N - coords} coincident (share coordinates)`)
console.log(`wasm: ${bytes.length} bytes`)

const err = maxPositionError(wasm, js)
console.log(`max position error: ${err.toExponential(3)} (tol ${TOL})`)
if (!(err <= TOL)) fail(`position error ${err}`)
let bitDiff = 0
for (let i = 0; i < N; i++) if (wasm.wx[i] !== js.wx[i] || wasm.wy[i] !== js.wy[i]) bitDiff++
console.log(`positions not bit-identical: ${bitDiff}/${N}`)

if (wasm.cells.length !== js.cells.length) fail('cell count')
js.cells.forEach((c, k) => {
  const w = wasm.cells[k]
  if (!w || w.name !== c.name || w.count !== c.count) return fail(`cell ${k} ${c.name} order/count`)
  for (const f of ['x', 'y', 'w', 'h']) if (Math.abs(w[f] - c[f]) > TOL) fail(`cell ${k}.${f}`)
})
for (const f of ['minX', 'minY', 'maxX', 'maxY']) if (Math.abs(wasm[f] - js[f]) > TOL) fail(`bounds ${f}`)

// determinism + order independence of the merkle root
const again = runWasmLayout(ex, encodeLayoutInput(nodes))
if (again.merkleRoot !== wasm.merkleRoot) fail('merkle root not deterministic')
const shuffled = [...nodes].reverse()
const rev = runWasmLayout(ex, encodeLayoutInput(shuffled))
if (rev.merkleRoot !== wasm.merkleRoot) fail('merkle root depends on node order')
console.log(`vault merkle root: ${wasm.merkleRoot}`)
console.log(`cell odu (first 3): ${wasm.cells.slice(0, 3).map(c => `${c.name}=${c.oduBase}/${c.oduComposed}`).join(', ')}`)

console.log(`timing (median of ${RUNS}): js buildLayout ${median(tJs).toFixed(2)} ms | ` +
  `wasm encode ${median(tEnc).toFixed(2)} ms + layout+hash+merkle ${median(tWasm).toFixed(2)} ms`)
console.log(process.exitCode ? 'PARITY: FAIL' : 'PARITY: OK')
