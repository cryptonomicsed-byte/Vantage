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

  console.log(`\n${passed} pine-runtime checks passed`)
  process.exit(0)
})().catch((e) => { console.error('FAILED:', e); process.exit(1) })
