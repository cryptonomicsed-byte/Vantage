'use strict'
// Executes one Pine evaluation in an isolated worker thread. The parent enforces
// a wall-clock timeout by terminating this worker. No network/fs is used here.
const { parentPort, workerData } = require('worker_threads')
const { evaluatePine } = require('./pine-engine')

try {
  const { script, candles } = workerData
  if (!Array.isArray(candles) || candles.length === 0) throw new Error('no candles')
  const result = evaluatePine(script, candles)
  parentPort.postMessage({ ok: true, result })
} catch (e) {
  parentPort.postMessage({ ok: false, error: String(e && e.message || e) })
}
