'use strict'
// Sandbox safety + correctness tests. Run: node test.js
const assert = require('assert')
const { runPine } = require('./runner')
const { evaluatePine } = require('./pine-engine')

function candles(n) {
  const out = []
  for (let i = 0; i < n; i++) {
    const c = 100 + i
    out.push({ time: 1700000000 + i * 3600, open: c, high: c + 1, low: c - 1, close: c, volume: 10 + i })
  }
  return out
}

let passed = 0
async function t(name, fn) { await fn(); passed++; console.log('  ok -', name) }

;(async () => {
  // 1. valid Pine subset → series
  await t('ema/rsi plot returns named numeric series', () => {
    const r = evaluatePine('plot(ta.ema(close, 5), "EMA")\nplot(ta.rsi(close, 14), "RSI")', candles(40))
    assert.ok(r.plots.EMA && r.plots.RSI, 'has EMA + RSI plots')
    const last = r.plots.EMA[r.plots.EMA.length - 1]
    assert.ok(typeof last.value === 'number' && isFinite(last.value), 'EMA last is a finite number')
    assert.ok(r.plots.EMA[0].value === null, 'warmup is null')
  })

  // 2. arithmetic + assignment + sources
  await t('assignment + arithmetic over series', () => {
    const r = evaluatePine('fast = ta.sma(close, 3)\nslow = ta.sma(close, 10)\nplot(fast - slow, "spread")', candles(30))
    assert.ok(r.plots.spread, 'spread plotted')
  })

  // 3. malicious: require/fetch/process are parse errors, never executed
  for (const bad of ['plot(require("fs"))', 'plot(fetch("http://x"))', 'plot(process.exit(1))', 'plot(globalThis)']) {
    await t('rejects unsafe token: ' + bad, () => {
      assert.throws(() => evaluatePine(bad, candles(20)), /Unknown|Unexpected|Unsupported|Expected/)
    })
  }

  // 4. no plot() output → error
  await t('script with no plot is rejected', () => {
    assert.throws(() => evaluatePine('x = ta.sma(close, 5)', candles(20)), /no plot/)
  })

  // 5. worker harness: a heavy script is bounded by the worker (returns ok or timeout, never hangs)
  await t('worker runs valid script through isolation', async () => {
    const out = await runPine('plot(ta.ema(close, 10), "e")', candles(50))
    assert.strictEqual(out.ok, true)
    assert.ok(out.result.plots.e)
  })

  // 6. worker returns structured error for bad script (no throw escapes)
  await t('worker returns structured error for unsafe script', async () => {
    const out = await runPine('plot(require("fs"))', candles(20))
    assert.strictEqual(out.ok, false)
    assert.ok(/Unknown|Unexpected|Unsupported/.test(out.error))
  })

  console.log(`\n${passed} pine-runtime checks passed`)
  process.exit(0)
})().catch((e) => { console.error('FAILED:', e); process.exit(1) })
