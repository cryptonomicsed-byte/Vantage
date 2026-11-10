#!/usr/bin/env node
// Regenerates services/galaxy_layout/tests/fixtures/*.json from the JS
// buildLayout (frontend/src/components/galaxyLayout.ts) — the Rust parity
// test (cargo test --test parity) asserts the port reproduces them.
// Needs node >= 22.18 / 23.6 (built-in TypeScript type stripping).
//   node frontend/scripts/galaxy-layout-fixtures.mjs
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildLayout } from '../src/components/galaxyLayout.ts'
import { genNodes } from './galaxy-fixture-gen.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const out = join(here, '..', '..', 'services', 'galaxy_layout', 'tests', 'fixtures')
mkdirSync(out, { recursive: true })

const cases = {
  empty: [],
  single: [{ id: 'a', path: 'a.md', x: 10, y: 20, z: 30, constellation: 'note' }],
  small: [
    { id: 'a', path: 'a.md', x: 0, y: 0, z: 0, constellation: 'note' },
    { id: 'b', path: 'b.md', x: 0, y: 0, z: 0, constellation: 'note' },
    { id: 'c', path: 'c.md', x: 0.2, y: -0.3, z: 0, constellation: 'note' },
    { id: 'd', path: '', x: 5, y: 5, z: 5, constellation: '' },
    { id: 'e', path: 'e.md', x: 5, y: 5, z: 5, constellation: 'other' },
    { id: 'f', path: 'f.md', x: 1000, y: 40, z: -12, constellation: 'trace' },
    { id: 'g', path: 'g.md', x: 2.5, y: -2.5, z: 0, constellation: 'trace' },
    { id: 'h', path: 'h.md', x: 3.4, y: -2.6, z: 0, constellation: 'trace' },
  ],
  medium: genNodes(600, 42),
  many_groups: genNodes(400, 7).map((n, i) => ({ ...n, constellation: `c${i % 37}` })),
}

for (const [name, nodes] of Object.entries(cases)) {
  const L = buildLayout(nodes)
  const fixture = {
    nodes: nodes.map(({ id, path, x, y, z, constellation }) => ({ id, path, x, y, z, constellation })),
    expected: {
      wx: Array.from(L.wx), wy: Array.from(L.wy),
      cells: L.cells,
      bounds: [L.minX, L.minY, L.maxX, L.maxY],
    },
  }
  writeFileSync(join(out, `${name}.json`), JSON.stringify(fixture) + '\n')
  console.log(`${name}: ${nodes.length} nodes, ${L.cells.length} cells`)
}
