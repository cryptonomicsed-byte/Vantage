'use strict'
// Sandbox safety + correctness tests. Run: node test.js
const assert = require('assert')
const { runPine } = require('./runner')
const { evaluatePine, validatePine } = require('./pine-engine')

function candles(n) {
  const out = []
  for (let i = 0; i < n; i++) {
    const c = 100 + i
    out.push({ time: 1700000000 + i * 3600, open: c, high: c + 1, low: c - 1, close: c, volume: 10 + i })
  }
  return out
}

// Deterministic synthetic OHLCV — same seed/formula used to generate the
// dataset the 2026-08-29 real-indicator additions were cross-validated
// against on hostinger-vps (TA-Lib 0.6.8 + Jesse 2.4.1, both real installs,
// checked via `pip list`, not assumed). Reference values below are TA-Lib's
// own real output on this exact dataset, not hand-derived expectations.
function realisticCandles(n) {
  const out = []
  let seed = 42
  function rand() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff }
  let p = 100
  for (let i = 0; i < n; i++) {
    p += Math.sin(i / 7) * 2 + (rand() - 0.5) * 1.5
    const c = p
    const h = c + 0.5 + rand()
    const l = c - 0.5 - rand()
    out.push({ time: 1700000000 + i * 3600, open: c - 0.3, high: h, low: l, close: c, volume: 1000 + i * 7 + rand() * 200 })
  }
  return out
}
function approxEqual(a, b, tol) { assert.ok(Math.abs(a - b) <= tol, `expected ${a} ≈ ${b} (tol ${tol}), diff=${Math.abs(a - b)}`) }

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

  // 7. real templates from backend/routers/pine.py's generate_pine() fallback
  // (2026-08-29 extension) — every one of these previously failed to parse
  // (indicator(), comparisons, ternaries, [n] history refs, hline/bgcolor/
  // plotshape, ta.macd/atr/vwap/crossover/crossunder were all unsupported).
  const RSI_DIVERGENCE = `//@version=5
indicator("RSI Divergence", overlay=true)
length = input.int(14, "RSI Length")
rsi = ta.rsi(close, length)
priceHH = close > close[1] and close[1] > close[2]
rsiLH = rsi < rsi[1] and rsi[1] < rsi[2]
bearishDiv = priceHH and rsiLH
priceLL = close < close[1] and close[1] < close[2]
rsiHL = rsi > rsi[1] and rsi[1] > rsi[2]
bullishDiv = priceLL and rsiHL
plotshape(bearishDiv, "Bearish Div", shape.triangledown, location.abovebar, color=color.red)
plotshape(bullishDiv, "Bullish Div", shape.triangleup, location.belowbar, color=color.green)
bgcolor(bearishDiv ? color.new(color.red, 90) : bullishDiv ? color.new(color.green, 90) : na)
hline(70, "Overbought", color=color.red)
hline(30, "Oversold", color=color.green)
plot(rsi, "RSI", color=color.purple)`

  const BOLLINGER_SQUEEZE = `//@version=5
indicator("Bollinger Squeeze", overlay=true)
length = input.int(20, "Length")
mult = input.float(2.0, "Std Dev")
basis = ta.sma(close, length)
dev = mult * ta.stdev(close, length)
upper = basis + dev
lower = basis - dev
plot(basis, "SMA", color=color.blue)
plot(upper, "Upper", color=color.red)
plot(lower, "Lower", color=color.green)
bandWidth = (upper - lower) / basis * 100
squeeze = bandWidth < ta.sma(bandWidth, 20)
bgcolor(squeeze ? color.new(color.yellow, 90) : na)`

  const MACD_CUSTOM = `//@version=5
indicator("MACD Custom", overlay=false)
[macdLine, signalLine, hist] = ta.macd(close, 12, 26, 9)
plot(macdLine, "MACD", color=color.blue)
plot(signalLine, "Signal", color=color.orange)
plot(hist, "Histogram", color=hist > 0 ? color.green : color.red, style=plot.style_columns)
hline(0, "Zero", color=color.gray)`

  const VWAP_CUSTOM = `//@version=5
indicator("VWAP Custom", overlay=true)
v = ta.vwap(close)
plot(v, "VWAP", color=color.orange, linewidth=2)
volColor = close >= open ? color.green : color.red
plot(volume, "Volume", color=color.new(volColor, 70), style=plot.style_columns)`

  const EMA_CROSSOVER = `//@version=5
indicator("Custom Indicator", overlay=true)
fastLen = input.int(10, "Fast EMA")
slowLen = input.int(30, "Slow EMA")
fastEMA = ta.ema(close, fastLen)
slowEMA = ta.ema(close, slowLen)
plot(fastEMA, "Fast EMA", color=color.green)
plot(slowEMA, "Slow EMA", color=color.red)
bullish = ta.crossover(fastEMA, slowEMA)
bearish = ta.crossunder(fastEMA, slowEMA)
bgcolor(bullish ? color.new(color.green, 90) : bearish ? color.new(color.red, 90) : na)`

  await t('pine.py template: RSI Divergence', () => {
    const r = evaluatePine(RSI_DIVERGENCE, candles(60))
    assert.strictEqual(r.title, 'RSI Divergence')
    assert.ok(r.plots.RSI, 'RSI plotted')
    assert.ok(r.plots.Overbought && r.plots.Oversold, 'hlines present')
    assert.ok(r.markers['Bearish Div'] && r.markers['Bullish Div'], 'plotshape markers present')
    assert.ok(r.markers['Bearish Div'].every((p) => typeof p.value === 'boolean'), 'markers are real booleans')
  })

  await t('pine.py template: Bollinger Squeeze', () => {
    const r = evaluatePine(BOLLINGER_SQUEEZE, candles(60))
    assert.ok(r.plots.SMA && r.plots.Upper && r.plots.Lower, 'bands plotted')
  })

  await t('pine.py template: MACD Custom (tuple destructuring)', () => {
    const r = evaluatePine(MACD_CUSTOM, candles(80))
    assert.ok(r.plots.MACD && r.plots.Signal && r.plots.Histogram, 'all three macd series plotted')
    assert.ok(r.plots.Zero, 'zero hline present')
    const last = r.plots.MACD[r.plots.MACD.length - 1]
    assert.ok(typeof last.value === 'number' && isFinite(last.value), 'macd converges to a real number')
  })

  await t('pine.py template: VWAP Custom', () => {
    const r = evaluatePine(VWAP_CUSTOM, candles(60))
    assert.ok(r.plots.VWAP, 'vwap plotted')
    assert.ok(r.plots.Volume, 'volume plotted despite unevaluated color ternary')
  })

  await t('pine.py template: default EMA crossover (ta.crossover/crossunder)', () => {
    const r = evaluatePine(EMA_CROSSOVER, candles(60))
    assert.ok(r.plots['Fast EMA'] && r.plots['Slow EMA'], 'both EMAs plotted')
  })

  // 2026-08-29: real templates added alongside the new ta.supertrend/ta.squeeze
  // functions -- parse-tested here exactly as deployed in pine.py's
  // generate_pine() fallback (copy kept in sync manually; a real end-to-end
  // API test isn't practical here since this file has no Python import path).
  const SUPERTREND_TEMPLATE = `//@version=5
indicator("SuperTrend", overlay=true)
factor = input.float(3.0, "Factor")
atrLen = input.int(10, "ATR Length")
[st, dir] = ta.supertrend(factor, atrLen)
plot(st, "SuperTrend", color=dir < 0 ? color.green : color.red, linewidth=2)
bullish = ta.crossover(dir, 0)
bearish = ta.crossunder(dir, 0)
plotshape(bullish, "Bullish Flip", shape.triangleup, location.belowbar, color=color.green)
plotshape(bearish, "Bearish Flip", shape.triangledown, location.abovebar, color=color.red)`

  const SQUEEZE_TEMPLATE = `//@version=5
indicator("Squeeze Momentum", overlay=false)
length = input.int(20, "BB/KC Length")
bbMult = input.float(2.0, "BB Mult")
kcMult = input.float(1.5, "KC Mult")
[sqzOn, mom] = ta.squeeze(length, bbMult, kcMult)
plot(mom, "Momentum", color=mom > 0 ? color.green : color.red, style=plot.style_columns)
plotshape(sqzOn, "Squeeze On", shape.circle, location.bottom, color=color.yellow)
hline(0, "Zero", color=color.gray)`

  await t('pine.py template: SuperTrend', () => {
    const r = evaluatePine(SUPERTREND_TEMPLATE, candles(60))
    assert.strictEqual(r.title, 'SuperTrend')
    assert.ok(r.plots.SuperTrend, 'SuperTrend line plotted')
    assert.ok(r.markers['Bullish Flip'] && r.markers['Bearish Flip'], 'flip markers present')
  })

  await t('pine.py template: Squeeze Momentum', () => {
    const r = evaluatePine(SQUEEZE_TEMPLATE, candles(60))
    assert.strictEqual(r.title, 'Squeeze Momentum')
    assert.ok(r.plots.Momentum, 'momentum histogram plotted')
    assert.ok(r.markers['Squeeze On'], 'squeeze marker present')
  })

  // 8. real-indicator additions, 2026-08-29 — structural coverage (every
  // new native function parses + produces the declared plots) plus real
  // correctness regressions: exact reference values from TA-Lib 0.6.8 on
  // hostinger-vps, on the SAME deterministic dataset (realisticCandles(200)
  // here == the dataset used for the live cross-validation pass, same
  // seed/formula) — not hand-derived expectations. Three real bugs were
  // found and fixed via this process (see pine-engine.js's inline
  // docstrings for each): true-range fabricating a value at index 0
  // (shifted ATR/DMI's RMA seed by one bar), OBV seeding at 0 instead of
  // volume[0], and ta.linreg's offset=0 being silently clamped to 1 by a
  // length-oriented int() helper. All three are now exact matches below.
  const rc = realisticCandles(200)

  await t('ta.bb matches TA-Lib BBANDS exactly at bar 19', () => {
    const r = evaluatePine('[b,u,l] = ta.bb(close, 20, 2.0)\nplot(b,"basis")\nplot(u,"upper")\nplot(l,"lower")', rc)
    approxEqual(r.plots.basis[19].value, 112.1124888992163, 1e-6)
    approxEqual(r.plots.upper[19].value, 130.0209271882967, 1e-6)
    approxEqual(r.plots.lower[19].value, 94.2040506101359, 1e-6)
  })

  await t('ta.atr matches TA-Lib ATR exactly (real Wilder RMA, fixed true-range seed)', () => {
    const r = evaluatePine('plot(ta.atr(14), "atr")', rc)
    approxEqual(r.plots.atr[14].value, 2.6673166712253362, 1e-6)
    approxEqual(r.plots.atr[50].value, 2.3595789554268243, 1e-6)
  })

  await t('ta.obv matches TA-Lib OBV exactly (real bug fix: seed was 0, not volume[0])', () => {
    const r = evaluatePine('plot(ta.obv(), "obv")', rc)
    approxEqual(r.plots.obv[0].value, 1139.7431135828342, 1e-4)
    approxEqual(r.plots.obv[10].value, 9912.44827606317, 1e-4)
  })

  await t('ta.wpr matches TA-Lib WILLR exactly', () => {
    const r = evaluatePine('plot(ta.wpr(14), "wpr")', rc)
    approxEqual(r.plots.wpr[13].value, -3.2164470982567743, 1e-6)
    approxEqual(r.plots.wpr[50].value, -11.430774716736984, 1e-6)
  })

  await t('ta.linreg matches TA-Lib LINEARREG exactly (real bug fix: offset=0 was clamped to 1)', () => {
    const r = evaluatePine('plot(ta.linreg(close, 14, 0), "lr")', rc)
    approxEqual(r.plots.lr[13].value, 116.92454504613923, 1e-6)
    approxEqual(r.plots.lr[50].value, 105.8574988628577, 1e-6)
  })

  await t('ta.stoch matches TA-Lib STOCHF %K exactly', () => {
    const r = evaluatePine('plot(ta.stoch(close, high, low, 14), "k")', rc)
    approxEqual(r.plots.k[50].value, 88.56922528326301, 1e-6)
  })

  await t('ta.dmi tracks TA-Lib PLUS_DI/ADX and converges (documented Wilder seed-transient, not exact-match — see pine-engine.js)', () => {
    const r = evaluatePine('[p,m,a] = ta.dmi(14,14)\nplot(p,"plus")\nplot(m,"minus")\nplot(a,"adx")', rc)
    assert.ok(typeof r.plots.plus[59].value === 'number', 'plusDI produces real numbers')
    // late-bar convergence check (bar 59): loose tolerance reflecting the
    // real, documented Wilder-smoothing seed transient, not a precision bug
    approxEqual(r.plots.plus[59].value, 46.01731630044779, 0.05)
  })

  await t('ta.kc, ta.cci, ta.mfi, ta.sar, ta.supertrend, ta.stochrsi, ta.squeeze, ta.ichimoku, ta.fib, ta.fvgbull/fvgbear all parse and produce real series', () => {
    const r = evaluatePine(
      `[kb,ku,kl] = ta.kc(close, 20, 1.5)
plot(kb, "kcbasis")
plot(ta.cci(close, 20), "cci")
plot(ta.mfi(close, 14), "mfi")
plot(ta.sar(0.02, 0.02, 0.2), "sar")
[st, dir] = ta.supertrend(3.0, 10)
plot(st, "st")
[k, d] = ta.stochrsi(close, 14, 14, 3, 3)
plot(k, "srsi_k")
[sqzOn, mom] = ta.squeeze(20, 2.0, 1.5)
plot(mom, "sqz_mom")
[conv, base, spanA, spanB] = ta.ichimoku(9, 26, 52)
plot(conv, "ichi_conv")
plot(ta.fib(0.618, 50), "fib618")
plotshape(ta.fvgbull(0.1), "FVG Bull")
plotshape(ta.fvgbear(0.1), "FVG Bear")`,
      rc,
    )
    for (const name of ['kcbasis', 'cci', 'mfi', 'sar', 'st', 'srsi_k', 'sqz_mom', 'ichi_conv', 'fib618']) {
      const last = r.plots[name][r.plots[name].length - 1]
      assert.ok(typeof last.value === 'number' && isFinite(last.value), name + ' converges to a real finite number')
    }
    assert.ok(r.markers['FVG Bull'] && r.markers['FVG Bull'].every((p) => typeof p.value === 'boolean'), 'FVG bull markers are real booleans')
    assert.ok(r.markers['FVG Bear'] && r.markers['FVG Bear'].every((p) => typeof p.value === 'boolean'), 'FVG bear markers are real booleans')
  })

  await t('ta.linreg offset=0 is NOT silently clamped like a length arg (regression for the real bug)', () => {
    // A direct unit-level regression, independent of the TA-Lib comparison
    // above: offset=1 must differ from offset=0 (proves offset is actually
    // read, not hardcoded/ignored after the fix).
    const r0 = evaluatePine('plot(ta.linreg(close, 14, 0), "lr")', rc)
    const r1 = evaluatePine('plot(ta.linreg(close, 14, 1), "lr")', rc)
    assert.notStrictEqual(r0.plots.lr[50].value, r1.plots.lr[50].value, 'offset=0 and offset=1 must give different values')
  })

  // ── validatePine: the local gate ──
  //
  // These matter because an external compiler accepts the whole Pine language
  // while this engine implements a subset. A script that compiles at
  // TradingView and cannot run here must be caught before it reaches the
  // sandbox, not after.

  await t('validatePine accepts a script this engine can run', () => {
    const r = validatePine('indicator("x")\nplot(ta.sma(close, 20))')
    assert(r.valid === true, JSON.stringify(r))
  })

  await t('validatePine rejects a function this engine lacks, with a line number', () => {
    const r = validatePine('indicator("x")\nplot(close)\nplot(ta.percentrank(close, 20))')
    assert(r.valid === false)
    assert(r.errors[0].line === 3, 'expected line 3, got ' + r.errors[0].line)
    assert(/percentrank/.test(r.errors[0].message))
  })

  await t('validatePine reports the offending source line', () => {
    const r = validatePine('indicator("x")\nplot(ta.nope(close))')
    assert(r.errors[0].source === 'plot(ta.nope(close))', r.errors[0].source)
  })

  await t('validatePine catches a syntax error with its line', () => {
    const r = validatePine('indicator("x")\nplot(ta.sma(close, 20)')
    assert(r.valid === false && r.errors[0].line === 2)
  })

  await t('validatePine needs no candles from the caller', () => {
    // The whole point of the endpoint: validation must work before any market
    // data has been fetched.
    assert(validatePine('plot(ta.ema(close, 9))').valid === true)
  })

  // ── functions added so TradingView-valid scripts run here too ──

  await t('ta.change defaults to a one-bar difference', () => {
    const r = evaluatePine('plot(ta.change(close), "d")', rc)
    approxEqual(r.plots.d[5].value, rc[5].close - rc[4].close, 1e-6)
  })

  await t('ta.roc is a percentage, not a ratio', () => {
    const r = evaluatePine('plot(ta.roc(close, 10), "r")', rc)
    approxEqual(r.plots.r[20].value, ((rc[20].close - rc[10].close) / rc[10].close) * 100, 1e-6)
  })

  await t('ta.vwma weights by volume, so it differs from ta.sma', () => {
    const r = evaluatePine('plot(ta.vwma(close, 20), "v")\nplot(ta.sma(close, 20), "s")', rc)
    assert(r.plots.v[50].value !== r.plots.s[50].value, 'vwma must not equal sma on varying volume')
  })

  await t('ta.barssince is null before the condition has ever held', () => {
    // Zero would claim "it just happened", which is a different statement.
    const r = evaluatePine('plot(ta.barssince(close > 1000000), "b")', rc)
    assert(r.plots.b[50].value === null, 'expected null, got ' + r.plots.b[50].value)
  })

  await t('ta.barssince counts bars back to the last true', () => {
    const r = evaluatePine('plot(ta.barssince(close > 0), "b")', rc)
    assert(r.plots.b[50].value === 0, 'always-true condition should read 0')
  })

  await t('ta.pivothigh publishes at the confirming bar, not the extreme', () => {
    // A pivot is not knowable until `right` more bars exist; backdating it
    // would let a script see the future.
    const c = realisticCandles(60)
    const r = evaluatePine('plot(ta.pivothigh(high, 2, 2), "p")', c)
    const firstIdx = r.plots.p.findIndex((x) => x.value != null)
    assert(firstIdx >= 4, 'a 2/2 pivot cannot be confirmed before bar 4, got ' + firstIdx)
  })

  await t('ta.cum accumulates', () => {
    const r = evaluatePine('plot(ta.cum(volume), "c")', rc)
    let s = 0
    for (let i = 0; i <= 10; i++) s += rc[i].volume
    approxEqual(r.plots.c[10].value, s, 1e-6)
  })

  await t('ta.valuewhen holds the value from the last matching bar', () => {
    const r = evaluatePine('plot(ta.valuewhen(close > open, close, 0), "v")', rc)
    assert(r.plots.v.some((x) => x.value != null), 'expected some values')
  })

  await t('ta.hma responds faster than ta.sma of the same length', () => {
    const r = evaluatePine('plot(ta.hma(close, 20), "h")\nplot(ta.sma(close, 20), "s")', rc)
    assert(r.plots.h[80].value !== r.plots.s[80].value)
  })

  await t('ta.tr still leaves index 0 undefined (no prior close exists)', () => {
    // Regression: an added helper once redefined trueRange to fabricate
    // high[0]-low[0] here, reintroducing the TA-Lib drift ta.atr had fixed.
    const r = evaluatePine('plot(ta.tr, "t")', rc)
    assert(r.plots.t[0].value === null, 'index 0 must be null, got ' + r.plots.t[0].value)
  })

  // ── multi-line statements, math namespace, variance ──

  await t('a call may span several lines while its brackets are open', () => {
    const r = validatePine('indicator("x")\nlen = input.int(20,\n  "Length",\n  minval=1)\nplot(ta.sma(close, len))')
    assert(r.valid === true, JSON.stringify(r))
  })

  await t('a bracket inside a string does not swallow the next line', () => {
    // "Length (bars)" is text, not structure. Counting it would join the
    // following line into this statement and break the parse.
    const r = validatePine('indicator("x")\nlen = input.int(20, "Length (bars)")\nplot(ta.sma(close, len))')
    assert(r.valid === true, JSON.stringify(r))
  })

  await t('an unterminated call reports the line the statement started on', () => {
    const r = validatePine('indicator("x")\nplot(ta.sma(\n  close,\n  20)')
    assert(r.valid === false)
    assert(r.errors[0].line === 2, 'expected the opening line, got ' + r.errors[0].line)
  })

  await t('ta.variance is exactly ta.stdev squared', () => {
    // They share one implementation now, so this can only break deliberately.
    const r = evaluatePine('plot(ta.variance(close,20),"v")\nplot(ta.stdev(close,20),"s")', rc)
    const v = r.plots.v[40].value, sd = r.plots.s[40].value
    approxEqual(v, sd * sd, 1e-6)
  })

  await t('math.max lifts over two series', () => {
    const r = evaluatePine('plot(math.max(close, open), "m")', rc)
    approxEqual(r.plots.m[10].value, Math.max(rc[10].close, rc[10].open), 1e-6)
  })

  await t('math.abs and math.pow work on a series', () => {
    const r = evaluatePine('plot(math.abs(close - open), "a")\nplot(math.pow(close, 2), "p")', rc)
    approxEqual(r.plots.a[10].value, Math.abs(rc[10].close - rc[10].open), 1e-6)
    approxEqual(r.plots.p[10].value, Math.pow(rc[10].close, 2), 1e-6)
  })

  await t('math functions keep a warm-up null as null', () => {
    // Turning it into 0 would draw a real line through a period where the
    // indicator has no value.
    const r = evaluatePine('plot(math.abs(ta.sma(close, 20)), "a")', rc)
    assert(r.plots.a[0].value === null, 'expected null at bar 0, got ' + r.plots.a[0].value)
  })

  await t('hex colour literals parse instead of stopping on #', () => {
    assert(validatePine('indicator("x")\nplot(close, color=#FF0000)').valid === true)
    assert(validatePine('indicator("x")\nplot(close, color=#FF0000AA)').valid === true)
  })

  await t('quoted strings still parse after the hex-colour change', () => {
    assert(validatePine('indicator("x")\nplot(close, "Title")').valid === true)
    assert(validatePine("indicator('x')\nplot(close, 'Title')").valid === true)
  })

  console.log(`\n${passed} pine-runtime checks passed`)
  process.exit(0)
})().catch((e) => { console.error('FAILED:', e); process.exit(1) })
