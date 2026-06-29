'use strict'
// Safe Pine-subset evaluator. PARSES a restricted grammar and computes indicator
// series over candles — it never executes arbitrary code. Anything outside the
// whitelist (require, fetch, process, while, assignment to globals, …) is a parse
// error, so this is a stronger guarantee than sandboxing arbitrary JS.
//
// Supported:
//   sources:   close open high low volume hl2 hlc3 ohlc4
//   functions: ta.sma ta.ema ta.wma ta.rsi ta.stdev ta.highest ta.lowest
//   operators: + - * /  and parentheses; series⊕series and series⊕scalar
//   statements: `name = <expr>` and `plot(<expr>[, "title"])`
//
// Output: { plots: { title: [{time, value}] }, alerts: [] }

// ── indicator math (mirrors backend/indicators.py) ──
function sma(src, len) {
  const out = new Array(src.length).fill(null)
  for (let i = len - 1; i < src.length; i++) {
    let s = 0, ok = true
    for (let j = i - len + 1; j <= i; j++) { if (src[j] == null) { ok = false; break } s += src[j] }
    out[i] = ok ? s / len : null
  }
  return out
}
function ema(src, len) {
  const out = new Array(src.length).fill(null)
  const k = 2 / (len + 1)
  let prev = null
  for (let i = 0; i < src.length; i++) {
    if (i + 1 < len) continue
    if (prev == null) { let s = 0; for (let j = i - len + 1; j <= i; j++) s += src[j]; prev = s / len }
    else prev = src[i] * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}
function wma(src, len) {
  const out = new Array(src.length).fill(null)
  const denom = (len * (len + 1)) / 2
  for (let i = len - 1; i < src.length; i++) {
    let s = 0
    for (let j = 0; j < len; j++) s += src[i - len + 1 + j] * (j + 1)
    out[i] = s / denom
  }
  return out
}
function rsi(src, len) {
  const out = new Array(src.length).fill(null)
  let avgG = null, avgL = null, g = 0, l = 0
  for (let i = 1; i < src.length; i++) {
    const ch = src[i] - src[i - 1]
    const gain = Math.max(ch, 0), loss = Math.max(-ch, 0)
    if (i <= len) { g += gain; l += loss; if (i === len) { avgG = g / len; avgL = l / len; out[i] = 100 - 100 / (1 + (avgL ? avgG / avgL : Infinity)) } }
    else { avgG = (avgG * (len - 1) + gain) / len; avgL = (avgL * (len - 1) + loss) / len; out[i] = 100 - 100 / (1 + (avgL ? avgG / avgL : Infinity)) }
  }
  return out
}
function stdev(src, len) {
  const out = new Array(src.length).fill(null)
  for (let i = len - 1; i < src.length; i++) {
    let m = 0; for (let j = i - len + 1; j <= i; j++) m += src[j]; m /= len
    let v = 0; for (let j = i - len + 1; j <= i; j++) v += (src[j] - m) ** 2
    out[i] = Math.sqrt(v / len)
  }
  return out
}
function rolling(src, len, fn) {
  const out = new Array(src.length).fill(null)
  for (let i = len - 1; i < src.length; i++) out[i] = fn(src.slice(i - len + 1, i + 1))
  return out
}

const FUNCS = {
  'ta.sma': (a) => sma(a[0], int(a[1])),
  'ta.ema': (a) => ema(a[0], int(a[1])),
  'ta.wma': (a) => wma(a[0], int(a[1])),
  'ta.rsi': (a) => rsi(a[0], int(a[1])),
  'ta.stdev': (a) => stdev(a[0], int(a[1])),
  'ta.highest': (a) => rolling(a[0], int(a[1]), (w) => Math.max(...w)),
  'ta.lowest': (a) => rolling(a[0], int(a[1]), (w) => Math.min(...w)),
}
function int(series) {
  // length args arrive as constant series; take the first finite value.
  if (Array.isArray(series)) { const v = series.find((x) => x != null); return Math.max(1, Math.round(v)) }
  return Math.max(1, Math.round(series))
}

// ── tokenizer ──
function tokenize(src) {
  const toks = []
  const re = /\s+|\/\/[^\n]*|("(?:[^"\\]|\\.)*")|([A-Za-z_][A-Za-z0-9_.]*)|(\d+\.?\d*)|([()+\-*/,=])/g
  let m, last = 0
  while ((m = re.exec(src)) !== null) {
    if (m.index !== last) throw new Error('Unexpected character: ' + src.slice(last, m.index))
    last = re.lastIndex
    if (m[0].trim() === '' || m[0].startsWith('//')) continue
    if (m[1]) toks.push({ t: 'str', v: m[1].slice(1, -1) })
    else if (m[2]) toks.push({ t: 'id', v: m[2] })
    else if (m[3]) toks.push({ t: 'num', v: parseFloat(m[3]) })
    else toks.push({ t: 'op', v: m[4] })
  }
  if (last !== src.length) throw new Error('Unexpected character near: ' + src.slice(last))
  return toks
}

// ── parser/evaluator over series ──
function evaluatePine(script, candles) {
  if (typeof script !== 'string') throw new Error('script must be a string')
  if (script.length > 8000) throw new Error('script too long')
  const N = candles.length
  const col = (f) => candles.map((c) => Number(c[f]))
  const SOURCES = {
    close: col('close'), open: col('open'), high: col('high'), low: col('low'), volume: col('volume'),
    hl2: candles.map((c) => (Number(c.high) + Number(c.low)) / 2),
    hlc3: candles.map((c) => (Number(c.high) + Number(c.low) + Number(c.close)) / 3),
    ohlc4: candles.map((c) => (Number(c.open) + Number(c.high) + Number(c.low) + Number(c.close)) / 4),
  }
  const vars = {}
  const plots = {}
  let plotN = 0

  function asSeries(x) { return Array.isArray(x) ? x : new Array(N).fill(x) }
  function binop(a, b, op) {
    const A = asSeries(a), B = asSeries(b)
    const out = new Array(N).fill(null)
    for (let i = 0; i < N; i++) {
      if (A[i] == null || B[i] == null) continue
      out[i] = op === '+' ? A[i] + B[i] : op === '-' ? A[i] - B[i] : op === '*' ? A[i] * B[i] : (B[i] ? A[i] / B[i] : null)
    }
    return out
  }

  // expression parser (recursive descent: term ± term, factor */ factor, primary)
  function parseExpr(toks) {
    let pos = 0
    const peek = () => toks[pos]
    const next = () => toks[pos++]
    function primary() {
      const tk = next()
      if (!tk) throw new Error('Unexpected end of expression')
      if (tk.t === 'num') return tk.v
      if (tk.t === 'op' && tk.v === '(') { const e = expr(); const c = next(); if (!c || c.v !== ')') throw new Error('Expected )'); return e }
      if (tk.t === 'op' && tk.v === '-') return binop(0, primary(), '-')
      if (tk.t === 'id') {
        if (peek() && peek().v === '(') { // function call
          next() // (
          const args = []
          if (peek() && peek().v !== ')') { args.push(expr()); while (peek() && peek().v === ',') { next(); args.push(expr()) } }
          const c = next(); if (!c || c.v !== ')') throw new Error('Expected ) after args')
          const fn = FUNCS[tk.v]
          if (!fn) throw new Error('Unknown function: ' + tk.v)
          return fn(args)
        }
        if (tk.v in SOURCES) return SOURCES[tk.v]
        if (tk.v in vars) return vars[tk.v]
        throw new Error('Unknown identifier: ' + tk.v)
      }
      throw new Error('Unexpected token: ' + JSON.stringify(tk))
    }
    function factor() { let a = primary(); while (peek() && (peek().v === '*' || peek().v === '/')) { const op = next().v; a = binop(a, primary(), op) } return a }
    function expr() { let a = factor(); while (peek() && (peek().v === '+' || peek().v === '-')) { const op = next().v; a = binop(a, factor(), op) } return a }
    const result = expr()
    if (pos !== toks.length) throw new Error('Trailing tokens in expression')
    return result
  }

  // statements: split by newline; each is `id = expr` or `plot(...)`
  for (const rawLine of script.split('\n')) {
    const line = rawLine.replace(/\/\/.*$/, '').trim()
    if (!line) continue
    const toks = tokenize(line)
    if (toks.length === 0) continue
    // assignment: id = ...
    if (toks.length >= 3 && toks[0].t === 'id' && toks[1].t === 'op' && toks[1].v === '=') {
      vars[toks[0].v] = parseExpr(toks.slice(2))
      continue
    }
    // plot(expr, "title")
    if (toks[0].t === 'id' && toks[0].v === 'plot' && toks[1] && toks[1].v === '(') {
      // find matching close paren (top-level)
      let depth = 0, end = -1
      for (let i = 1; i < toks.length; i++) { if (toks[i].v === '(') depth++; else if (toks[i].v === ')') { depth--; if (depth === 0) { end = i; break } } }
      if (end < 0) throw new Error('Unbalanced plot()')
      const inner = toks.slice(2, end)
      // split off a trailing , "title"
      let title = null, exprToks = inner
      if (inner.length >= 2 && inner[inner.length - 1].t === 'str' && inner[inner.length - 2].v === ',') {
        title = inner[inner.length - 1].v
        exprToks = inner.slice(0, inner.length - 2)
      }
      const series = asSeries(parseExpr(exprToks))
      const name = title || `plot_${++plotN}`
      plots[name] = candles.map((c, i) => ({ time: Number(c.time), value: series[i] == null || !isFinite(series[i]) ? null : Number(series[i].toFixed(8)) }))
      continue
    }
    throw new Error('Unsupported statement: ' + line)
  }

  if (Object.keys(plots).length === 0) throw new Error('Script produced no plot() output')
  return { plots, alerts: [] }
}

module.exports = { evaluatePine }
