// Deterministic synthetic vault for the galaxy layout parity checks.
// Mimics the server's shape: constellations along x, y/z from a title hash,
// so many nodes share exact coordinates (coincident fan-out), plus empty /
// literal 'other' constellations (both map to 'other'), negative coordinates
// and exact .5 ties for Math.round.

export function mulberry32(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const CONSTELLATIONS = ['broadcast', 'knowledge', 'trace', 'file', 'note', 'graph', 'audio', 'image', 'video', 'text', '', 'other']

export function genNodes(n, seed = 1, nConst = CONSTELLATIONS.length) {
  const rnd = mulberry32(seed)
  const titles = Math.max(1, Math.floor(n / 6))   // ~6 nodes per title → duplicates
  const nodes = []
  for (let i = 0; i < n; i++) {
    // skewed group sizes so the size-desc sort matters, with ties too
    const ci = Math.min(nConst - 1, Math.floor(Math.pow(rnd(), 1.7) * nConst))
    const c = CONSTELLATIONS[ci]
    const t = Math.floor(rnd() * titles)
    const tr = mulberry32(t * 7919 + 13)          // same title → same y/z
    let x = ci * 1400 + Math.round(tr() * 1000) / 4 - 300
    let y = Math.round((tr() * 1000 - 500) * 8) / 8
    let z = Math.round((tr() * 600 - 300) * 4) / 4
    if (i % 97 === 0) { x = 2.5; y = -2.5; z = 0 }  // exact Math.round ties (2.5 → 3, -2.5 → -2)
    if (i % 101 === 0) { x = -0.4; y = 0.3; z = 0 } // rounds to -0 vs 0
    nodes.push({
      id: `n${i}`,
      title: `t${t}`,
      path: i % 13 === 0 ? '' : `vault/${c || 'none'}/note-${i}.md`,  // '' → falls back to id
      x, y, z,
      constellation: c,
    })
  }
  return nodes
}
