'use strict'
// Pine-runtime sidecar — HTTP surface for the Vantage backend to run agent
// Pine Script in isolation. Binds localhost only; in deployment the container
// has egress denied (see docker-compose) so even a sandbox escape has no network.
const http = require('http')
const { runPine } = require('./runner')

const HOST = process.env.PINE_HOST || '127.0.0.1'
const PORT = parseInt(process.env.PINE_PORT || '9871', 10)
const MAX_BODY = 2 * 1024 * 1024 // 2MB

function send(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) })
  res.end(body)
}

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/healthz') return send(res, 200, { status: 'ok', service: 'pine-runtime' })
  if (req.method !== 'POST' || req.url !== '/run') return send(res, 404, { error: 'not found' })

  let body = ''
  let aborted = false
  req.on('data', (chunk) => {
    body += chunk
    if (body.length > MAX_BODY) { aborted = true; send(res, 413, { error: 'payload too large' }); req.destroy() }
  })
  req.on('end', async () => {
    if (aborted) return
    let payload
    try { payload = JSON.parse(body) } catch { return send(res, 400, { error: 'invalid JSON' }) }
    const { script, candles } = payload || {}
    if (typeof script !== 'string' || !Array.isArray(candles)) return send(res, 400, { error: 'script (string) and candles (array) required' })
    const out = await runPine(script, candles)
    if (out.ok) return send(res, 200, out.result)
    return send(res, 422, { error: out.error })
  })
})

if (require.main === module) {
  server.listen(PORT, HOST, () => console.log(`pine-runtime listening on http://${HOST}:${PORT}`))
}

module.exports = { server }
