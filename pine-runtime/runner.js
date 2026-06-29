'use strict'
// Run a Pine script in an isolated worker with a hard wall-clock timeout and a
// memory cap. Terminates the worker if it exceeds the limit (kills infinite
// loops / runaway work). Returns {ok, result} or {ok:false, error}.
const path = require('path')
const { Worker } = require('worker_threads')

const TIMEOUT_MS = 2000
const MAX_MEM_MB = 64

function runPine(script, candles) {
  return new Promise((resolve) => {
    let settled = false
    const done = (v) => { if (!settled) { settled = true; resolve(v) } }
    let worker
    try {
      worker = new Worker(path.join(__dirname, 'worker.js'), {
        workerData: { script, candles },
        resourceLimits: { maxOldGenerationSizeMb: MAX_MEM_MB, maxYoungGenerationSizeMb: 16 },
      })
    } catch (e) {
      return done({ ok: false, error: 'sandbox spawn failed: ' + e.message })
    }
    const timer = setTimeout(() => { worker.terminate(); done({ ok: false, error: 'timeout: script exceeded ' + TIMEOUT_MS + 'ms' }) }, TIMEOUT_MS)
    worker.on('message', (msg) => { clearTimeout(timer); worker.terminate(); done(msg.ok ? { ok: true, result: msg.result } : { ok: false, error: msg.error }) })
    worker.on('error', (e) => { clearTimeout(timer); done({ ok: false, error: 'sandbox error: ' + e.message }) })
    worker.on('exit', (code) => { clearTimeout(timer); if (!settled) done({ ok: false, error: 'sandbox exited (' + code + ')' }) })
  })
}

module.exports = { runPine, TIMEOUT_MS }
