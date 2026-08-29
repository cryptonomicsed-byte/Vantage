'use strict'
// Safe Pine-subset evaluator. PARSES a restricted grammar and computes indicator
// series over candles — it never executes arbitrary code. Anything outside the
// whitelist (require, fetch, process, while, assignment to globals, …) is a parse
// error, so this is a stronger guarantee than sandboxing arbitrary JS.
//
// Extended 2026-08-29 to cover what backend/routers/pine.py's own LLM system
// prompt + template fallback actually generate (previously every one of those
// templates failed to parse — indicator()/strategy() declarations, comparisons,
// ternaries, history subscripts [n], hline/bgcolor/plotshape, and
// ta.macd/atr/vwap/crossover/crossunder were all unsupported).
//
// Supported:
//   sources:    close open high low volume hl2 hlc3 ohlc4
//   literals:   numbers, "strings", true false na
//   functions:  ta.sma ta.ema ta.wma ta.rsi ta.stdev ta.highest ta.lowest
//               ta.macd (tuple) ta.atr ta.vwap ta.crossover ta.crossunder
//               input.int input.float (return the default-value argument)
//
//   Extended 2026-08-29 (real-indicator research pass — see commit message
//   for the full evidence trail: TA-Lib/Jesse/pandas-ta were checked on
//   hostinger-vps as CORRECTNESS ORACLES only, never as a runtime dependency
//   — this sandbox is pure-Node with no egress, so every function below is
//   hand-implemented JS math, cross-checked against those libraries' real
//   output on shared test data, not a call-out):
//     REAL Pine v5 builtins (name/signature match TradingView's own):
//       ta.bb(src,len,mult) -> [basis,upper,lower]      (Bollinger Bands)
//       ta.kc(src,len,mult) -> [basis,upper,lower]      (Keltner Channels, true-range based)
//       ta.dmi(diLen,adxLen) -> [plusDI,minusDI,adx]    (Wilder's DMI/ADX)
//       ta.supertrend(factor,atrLen) -> [supertrend,direction]
//       ta.sar(start,increment,maximum) -> series        (Parabolic SAR)
//       ta.cci(src,len) -> series
//       ta.mfi(src,len) -> series                         (src * SOURCES.volume)
//       ta.obv() -> series                                 (real Pine ta.obv takes no args;
//                                                            called as ta.obv() here to fit
//                                                            this grammar's call-only syntax)
//       ta.wpr(len) -> series                              (Williams %R)
//       ta.stoch(src,high,low,len) -> series                (%K only, matches real signature)
//       ta.linreg(src,len,offset) -> series
//     Community-standard composites (NOT literal Pine builtins — documented
//     honestly, same convention as this file's existing vwap/atr caveats):
//       ta.stochrsi(src,rsiLen,stochLen,smoothK,smoothD) -> [k,d]
//       ta.squeeze(len,bbMult,kcMult) -> [sqzOn,momentum]  (LazyBear's Squeeze
//         Momentum — an open, widely-reimplemented concept, not TradingView IP)
//       ta.ichimoku(conv,base,spanB,displacement) -> [conversionLine,baseLine,
//         leadingSpanA,leadingSpanB] — displacement accepted but NOT applied:
//         this sandbox is candle-indexed with no "future" bars to project
//         into, so spans are returned undisplaced (documented limitation,
//         not silently wrong).
//       ta.fib(level,lookback) -> series (rolling retracement level: highest
//         high minus level*range over the trailing window)
//       ta.fvgbull(minGapPct) / ta.fvgbear(minGapPct) -> boolean series
//         (Fair Value Gap / 3-candle imbalance — a well-defined, non-proprietary
//         "smart money concepts" pattern; real Order Blocks were deliberately
//         NOT added — no single agreed-on rule across implementations, unlike
//         FVG's clean 3-candle definition)
//     Deliberately NOT added: Volume Profile (a price-binned histogram is a
//     different output shape than this sandbox's per-candle series model —
//     would need a new output type, real scope decision not an oversight).
//   operators:  + - * /   > < >= <= == !=   and or not   cond ? a : b
//               series[n] (history reference, n a literal integer)
//   statements: `name = <expr>`, `[a, b, c] = <tuple-returning call>`,
//               indicator(...)/strategy(...) (parsed, title captured, no-op),
//               plot(<expr>[, "title"][, named=...]) -> numeric series output
//               hline(<expr>[, "title"][, named=...]) -> constant-level series
//               plotshape(<expr>[, "title"][, named=...]) -> boolean marker series
//               bgcolor(<expr>) -> parse-validated only (pure visual cue, no
//                 numeric equivalent to extract)
//   cosmetic namespaces (color/shape/location/plot/size/line/label/scale/font/
//   text/xloc/yloc/extend/display/format): any bare property or function call
//   under these (color.red, color.new(color.red, 90), plot.style_columns, ...)
//   resolves to an inert placeholder rather than a parse error — these carry
//   no numeric meaning in a "return numeric series" sandbox, and named args
//   built from them (color=..., style=...) are evaluated for validation only,
//   never used.
//
// Output: { plots: { title: [{time, value}] }, markers: { title: [{time,
//   value: bool}] }, alerts: [], title: string|null }

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
    if (prev == null) {
      // Real latent bug, found + fixed 2026-08-29: this seed sum had no
      // null-check (unlike sma()'s already-correct `ok` guard) -- `s +=
      // null` silently coerces to `s += 0` in JS, corrupting the seed if
      // any value in the first window is null. Never triggered while every
      // caller only ever passed close/open/etc (never null at index 0);
      // now exposed by trueRange()'s real index-0 fix below (keltner's
      // rangeEma = ema(trueRange(...), len) now genuinely has a leading
      // null). Mirrors sma()'s pattern: wait for a full real window.
      let s = 0, ok = true
      for (let j = i - len + 1; j <= i; j++) { if (src[j] == null) { ok = false; break }; s += src[j] }
      if (!ok) continue
      prev = s / len
    }
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
function subSeries(a, b) {
  const out = new Array(a.length).fill(null)
  for (let i = 0; i < a.length; i++) { if (a[i] == null || b[i] == null) continue; out[i] = a[i] - b[i] }
  return out
}
function crossover(a, b) {
  const out = new Array(a.length).fill(null)
  for (let i = 1; i < a.length; i++) {
    if (a[i] == null || b[i] == null || a[i - 1] == null || b[i - 1] == null) continue
    out[i] = (a[i] > b[i]) && (a[i - 1] <= b[i - 1])
  }
  return out
}
function crossunder(a, b) {
  const out = new Array(a.length).fill(null)
  for (let i = 1; i < a.length; i++) {
    if (a[i] == null || b[i] == null || a[i - 1] == null || b[i - 1] == null) continue
    out[i] = (a[i] < b[i]) && (a[i - 1] >= b[i - 1])
  }
  return out
}
function trueRange(high, low, close) {
  // Real bug found + fixed 2026-08-29 (correctness cross-check against
  // TA-Lib's ATR/ADX on hostinger-vps): index 0 was fabricated as
  // high[0]-low[0] (no prior close exists, so "true range" is undefined
  // there) -- real TA-Lib leaves index 0 undefined too. That fabricated
  // value shifted rma()'s seed window by one bar for every downstream
  // consumer (atr, dmi's trRma), producing small but real (~0.5-4%)
  // systematic drift from TA-Lib's ATR/+DI/-DI/ADX, confirmed on identical
  // synthetic OHLCV data. null here (not a guess) lets rma()'s existing
  // null-skip logic find the correct first full window on its own.
  const N = high.length
  const out = new Array(N).fill(null)
  for (let i = 1; i < N; i++) {
    out[i] = Math.max(high[i] - low[i], Math.abs(high[i] - close[i - 1]), Math.abs(low[i] - close[i - 1]))
  }
  return out
}
// Wilder's smoothing (alpha = 1/len) — the correct recurrence real Pine's
// ta.rma/ta.atr/ta.dmi all use internally. Distinct from ema() above
// (alpha = 2/(len+1)).
function rma(src, len) {
  const out = new Array(src.length).fill(null)
  let prev = null
  for (let i = 0; i < src.length; i++) {
    if (src[i] == null) continue
    if (prev == null) {
      if (i + 1 < len) continue
      let s = 0, ok = true
      for (let j = i - len + 1; j <= i; j++) { if (src[j] == null) { ok = false; break }; s += src[j] }
      if (!ok) continue
      prev = s / len
    } else {
      prev = (prev * (len - 1) + src[i]) / len
    }
    out[i] = prev
  }
  return out
}
// ATR — real Wilder's RMA of true range. Fixed 2026-08-29 (was an EMA
// approximation, documented as such); real now that rma() exists for
// ta.dmi() to share, and correctness-checked against TA-Lib's ATR on
// hostinger-vps (see commit message).
function atr(len, high, low, close) {
  return rma(trueRange(high, low, close), len)
}
function rollingSum(src, len) {
  const out = new Array(src.length).fill(null)
  for (let i = len - 1; i < src.length; i++) {
    let s = 0, ok = true
    for (let j = i - len + 1; j <= i; j++) { if (src[j] == null) { ok = false; break }; s += src[j] }
    out[i] = ok ? s : null
  }
  return out
}
function bbands(src, len, mult) {
  const basis = sma(src, len), dev = stdev(src, len)
  const N = src.length
  const upper = new Array(N).fill(null), lower = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (basis[i] == null || dev[i] == null) continue
    upper[i] = basis[i] + mult * dev[i]
    lower[i] = basis[i] - mult * dev[i]
  }
  return { __tuple: [basis, upper, lower] }
}
function keltner(src, high, low, close, len, mult) {
  const basis = ema(src, len)
  const rangeEma = ema(trueRange(high, low, close), len)
  const N = src.length
  const upper = new Array(N).fill(null), lower = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (basis[i] == null || rangeEma[i] == null) continue
    upper[i] = basis[i] + mult * rangeEma[i]
    lower[i] = basis[i] - mult * rangeEma[i]
  }
  return { __tuple: [basis, upper, lower] }
}
function dmi(high, low, close, diLen, adxLen) {
  const N = high.length
  const plusDM = new Array(N).fill(null), minusDM = new Array(N).fill(null)
  for (let i = 1; i < N; i++) {
    const up = high[i] - high[i - 1], down = low[i - 1] - low[i]
    plusDM[i] = (up > down && up > 0) ? up : 0
    minusDM[i] = (down > up && down > 0) ? down : 0
  }
  const trRma = rma(trueRange(high, low, close), diLen)
  const plusDMRma = rma(plusDM, diLen), minusDMRma = rma(minusDM, diLen)
  const plusDI = new Array(N).fill(null), minusDI = new Array(N).fill(null), dx = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (trRma[i] == null || !trRma[i] || plusDMRma[i] == null || minusDMRma[i] == null) continue
    plusDI[i] = 100 * plusDMRma[i] / trRma[i]
    minusDI[i] = 100 * minusDMRma[i] / trRma[i]
    const sum = plusDI[i] + minusDI[i]
    dx[i] = sum ? 100 * Math.abs(plusDI[i] - minusDI[i]) / sum : 0
  }
  return { __tuple: [plusDI, minusDI, rma(dx, adxLen)] }
}
// Standard SuperTrend recurrence (matches TradingView's real ta.supertrend,
// pandas-ta's supertrend, and Jesse's supertrend identically in shape —
// cross-checked live on hostinger-vps, see commit message).
function supertrend(high, low, close, factor, atrPeriod) {
  const N = close.length
  const atrSeries = atr(atrPeriod, high, low, close)
  const st = new Array(N).fill(null), dir = new Array(N).fill(null)
  let finalUpper = null, finalLower = null, prevDir = 1
  for (let i = 0; i < N; i++) {
    if (atrSeries[i] == null) continue
    const hl2 = (high[i] + low[i]) / 2
    const basicUpper = hl2 + factor * atrSeries[i]
    const basicLower = hl2 - factor * atrSeries[i]
    if (finalUpper == null) { finalUpper = basicUpper; finalLower = basicLower }
    else {
      finalUpper = (basicUpper < finalUpper || close[i - 1] > finalUpper) ? basicUpper : finalUpper
      finalLower = (basicLower > finalLower || close[i - 1] < finalLower) ? basicLower : finalLower
    }
    const direction = prevDir === -1
      ? (close[i] <= finalUpper ? -1 : 1)
      : (close[i] >= finalLower ? 1 : -1)
    st[i] = direction === -1 ? finalUpper : finalLower
    dir[i] = direction
    prevDir = direction
  }
  return { __tuple: [st, dir] }
}
// Standard iterative Parabolic SAR (Wilder 1978). Initialization convention
// (first-bar trend guess) varies slightly across real implementations
// (TA-Lib/Jesse/TradingView) — values track closely but an exact bar-0 match
// across all of them isn't guaranteed, same honesty convention as this
// file's existing vwap/ichimoku caveats.
function sar(high, low, close, start, increment, maximum) {
  const N = high.length
  const out = new Array(N).fill(null)
  if (N < 2) return out
  let isUp = close[1] >= close[0]
  let af = start
  let ep = isUp ? high[0] : low[0]
  let sarVal = isUp ? low[0] : high[0]
  out[1] = sarVal
  for (let i = 2; i < N; i++) {
    const prevSar = sarVal
    sarVal = prevSar + af * (ep - prevSar)
    if (isUp) {
      sarVal = Math.min(sarVal, low[i - 1], low[i - 2])
      if (high[i] > ep) { ep = high[i]; af = Math.min(af + increment, maximum) }
      if (low[i] < sarVal) { isUp = false; sarVal = ep; ep = low[i]; af = start }
    } else {
      sarVal = Math.max(sarVal, high[i - 1], high[i - 2])
      if (low[i] < ep) { ep = low[i]; af = Math.min(af + increment, maximum) }
      if (high[i] > sarVal) { isUp = true; sarVal = ep; ep = high[i]; af = start }
    }
    out[i] = sarVal
  }
  return out
}
function cci(src, len) {
  const basis = sma(src, len)
  const N = src.length
  const out = new Array(N).fill(null)
  for (let i = len - 1; i < N; i++) {
    if (basis[i] == null) continue
    let devSum = 0
    for (let j = i - len + 1; j <= i; j++) devSum += Math.abs(src[j] - basis[i])
    const meanDev = devSum / len
    out[i] = meanDev ? (src[i] - basis[i]) / (0.015 * meanDev) : null
  }
  return out
}
function mfi(src, volume, len) {
  const N = src.length
  const posFlow = new Array(N).fill(0), negFlow = new Array(N).fill(0)
  for (let i = 1; i < N; i++) {
    const raw = src[i] * volume[i]
    if (src[i] > src[i - 1]) posFlow[i] = raw
    else if (src[i] < src[i - 1]) negFlow[i] = raw
  }
  const posSum = rollingSum(posFlow, len), negSum = rollingSum(negFlow, len)
  const out = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (posSum[i] == null || negSum[i] == null) continue
    out[i] = negSum[i] === 0 ? 100 : 100 - 100 / (1 + posSum[i] / negSum[i])
  }
  return out
}
function obv(close, volume) {
  // Real bug found + fixed 2026-08-29 (correctness cross-check against
  // TA-Lib's OBV): seeded out[0]=0; TA-Lib (and every other real
  // implementation) seeds out[0]=volume[0] -- OBV is a running total that
  // starts AT the first bar's volume, not at zero before it. Confirmed:
  // every subsequent value was off by exactly volume[0] before this fix.
  const N = close.length
  const out = new Array(N).fill(null)
  let cum = volume[0]
  out[0] = cum
  for (let i = 1; i < N; i++) {
    if (close[i] > close[i - 1]) cum += volume[i]
    else if (close[i] < close[i - 1]) cum -= volume[i]
    out[i] = cum
  }
  return out
}
function wpr(high, low, close, len) {
  const hh = rolling(high, len, (w) => Math.max(...w))
  const ll = rolling(low, len, (w) => Math.min(...w))
  const N = close.length
  const out = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (hh[i] == null || ll[i] == null) continue
    const range = hh[i] - ll[i]
    out[i] = range ? -100 * (hh[i] - close[i]) / range : null
  }
  return out
}
function stochK(src, high, low, len) {
  const hh = rolling(high, len, (w) => Math.max(...w))
  const ll = rolling(low, len, (w) => Math.min(...w))
  const N = src.length
  const out = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (hh[i] == null || ll[i] == null) continue
    const range = hh[i] - ll[i]
    out[i] = range ? 100 * (src[i] - ll[i]) / range : null
  }
  return out
}
function linreg(src, len, offset) {
  const N = src.length
  const out = new Array(N).fill(null)
  for (let i = len - 1; i < N; i++) {
    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, ok = true
    for (let j = 0; j < len; j++) {
      const y = src[i - len + 1 + j]
      if (y == null) { ok = false; break }
      sumX += j; sumY += y; sumXY += j * y; sumX2 += j * j
    }
    if (!ok) continue
    const denom = len * sumX2 - sumX * sumX
    const slope = denom ? (len * sumXY - sumX * sumY) / denom : 0
    const intercept = (sumY - slope * sumX) / len
    out[i] = intercept + slope * ((len - 1) - offset)
  }
  return out
}
function stochRsi(src, rsiLen, stochLen, smoothK, smoothD) {
  const rsiSeries = rsi(src, rsiLen)
  const k = sma(stochK(rsiSeries, rsiSeries, rsiSeries, stochLen), smoothK)
  return { __tuple: [k, sma(k, smoothD)] }
}
// LazyBear's Squeeze Momentum — open, widely-reimplemented concept (real
// original: TradingView user LazyBear's free/open Pine publication).
function squeezeMomentum(high, low, close, len, bbMult, kcMult) {
  const N = close.length
  const basis = sma(close, len), dev = stdev(close, len)
  const rangeMa = sma(trueRange(high, low, close), len)
  const sqzOn = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (basis[i] == null || dev[i] == null || rangeMa[i] == null) continue
    const upperBB = basis[i] + bbMult * dev[i], lowerBB = basis[i] - bbMult * dev[i]
    const upperKC = basis[i] + kcMult * rangeMa[i], lowerKC = basis[i] - kcMult * rangeMa[i]
    sqzOn[i] = (lowerBB > lowerKC) && (upperBB < upperKC)
  }
  const hh = rolling(high, len, (w) => Math.max(...w))
  const ll = rolling(low, len, (w) => Math.min(...w))
  const smaClose = sma(close, len)
  const deltaSrc = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (hh[i] == null || ll[i] == null || smaClose[i] == null) continue
    const avgAll = ((hh[i] + ll[i]) / 2 + smaClose[i]) / 2
    deltaSrc[i] = close[i] - avgAll
  }
  return { __tuple: [sqzOn, linreg(deltaSrc, len, 0)] }
}
// Ichimoku Cloud — conversion/base/span-A lines are real per the standard
// formula (Donchian midpoints at different lookbacks). `displacement` is
// accepted but NOT applied: this sandbox is candle-indexed with no "future"
// bars to project spans into, unlike real Pine's forward-shifted plot —
// documented limitation, not silently wrong.
function ichimoku(high, low, conv, base, spanB) {
  const N = high.length
  const donchianMid = (len) => {
    const hh = rolling(high, len, (w) => Math.max(...w))
    const ll = rolling(low, len, (w) => Math.min(...w))
    const out = new Array(N).fill(null)
    for (let i = 0; i < N; i++) { if (hh[i] == null || ll[i] == null) continue; out[i] = (hh[i] + ll[i]) / 2 }
    return out
  }
  const conversionLine = donchianMid(conv), baseLine = donchianMid(base)
  const spanA = new Array(N).fill(null)
  for (let i = 0; i < N; i++) { if (conversionLine[i] == null || baseLine[i] == null) continue; spanA[i] = (conversionLine[i] + baseLine[i]) / 2 }
  return { __tuple: [conversionLine, baseLine, spanA, donchianMid(spanB)] }
}
function fibLevel(high, low, level, lookback) {
  const hh = rolling(high, lookback, (w) => Math.max(...w))
  const ll = rolling(low, lookback, (w) => Math.min(...w))
  const N = high.length
  const out = new Array(N).fill(null)
  for (let i = 0; i < N; i++) {
    if (hh[i] == null || ll[i] == null) continue
    out[i] = hh[i] - (hh[i] - ll[i]) * level
  }
  return out
}
// Fair Value Gap — well-defined, non-proprietary 3-candle imbalance (i vs
// i-2, "smart money concepts" terminology). Bullish: candle i's low is above
// candle i-2's high (a gap price never traded back into). minGapPct filters
// noise-sized gaps.
function fvgBullish(high, low, minGapPct) {
  const N = high.length
  const out = new Array(N).fill(null)
  for (let i = 2; i < N; i++) {
    const gap = low[i] - high[i - 2]
    out[i] = gap > 0 && (gap / high[i - 2]) * 100 >= minGapPct
  }
  return out
}
function fvgBearish(high, low, minGapPct) {
  const N = high.length
  const out = new Array(N).fill(null)
  for (let i = 2; i < N; i++) {
    const gap = low[i - 2] - high[i]
    out[i] = gap > 0 && (gap / low[i - 2]) * 100 >= minGapPct
  }
  return out
}
// Cumulative VWAP over the WHOLE candle window (no session/day anchor —
// ms.ohlc's candles carry no session-boundary metadata). Real ta.vwap()
// resets at each session start; documented simplification, not a bug.
function vwapCum(src, volume) {
  const N = src.length
  const out = new Array(N).fill(null)
  let cumPV = 0, cumV = 0
  for (let i = 0; i < N; i++) {
    if (src[i] == null || volume[i] == null) { out[i] = null; continue }
    cumPV += src[i] * volume[i]
    cumV += volume[i]
    out[i] = cumV ? cumPV / cumV : null
  }
  return out
}
function int(series) {
  // length args arrive as constant series or raw numbers; take the first finite value.
  if (Array.isArray(series)) { const v = series.find((x) => x != null); return Math.max(1, Math.round(v)) }
  return Math.max(1, Math.round(series))
}
// Same "arrives as constant series or raw number" unwrap as int(), but for
// float args (multipliers/factors) where rounding would be wrong.
function num(series) {
  if (Array.isArray(series)) { const v = series.find((x) => x != null); return v == null ? 0 : v }
  return series == null ? 0 : series
}
// Real bug found + fixed 2026-08-29 during correctness cross-check against
// TA-Lib's LINEARREG on hostinger-vps: ta.linreg's offset=0 was being
// coerced through int() (Math.max(1, round(x)), correct for LENGTH args
// where 0 is meaningless) which silently turned a real, valid offset=0
// into 1 -- shifted every output by exactly one bar (~1.6% systematic
// error, confirmed against TA-Lib and numpy.polyfit on identical input).
// Integer args that may legitimately be 0 (offsets, not lengths) must use
// this instead of int().
function intOffset(series) {
  if (Array.isArray(series)) { const v = series.find((x) => x != null); return Math.round(v) }
  return Math.round(series)
}

const COSMETIC_NAMESPACES = new Set([
  'color', 'shape', 'location', 'plot', 'size', 'line', 'label',
  'scale', 'font', 'text', 'xloc', 'yloc', 'extend', 'display', 'format',
])

// ── tokenizer ──
function tokenize(src) {
  const toks = []
  // Multi-char operators must be tried before the single-char class so
  // ">=" doesn't get split into ">" + "=".
  const re = /\s+|\/\/[^\n]*|("(?:[^"\\]|\\.)*")|([A-Za-z_][A-Za-z0-9_.]*)|(\d+\.?\d*)|(>=|<=|==|!=|[()[\]+\-*/,=?:<>])/g
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

function splitTopLevelArgs(toks) {
  const args = []
  let depth = 0, cur = []
  for (const t of toks) {
    if (t.v === '(' || t.v === '[') depth++
    if (t.v === ')' || t.v === ']') depth--
    if (t.v === ',' && depth === 0) { args.push(cur); cur = []; continue }
    cur.push(t)
  }
  if (cur.length) args.push(cur)
  return args
}

function stripNamed(argToks) {
  if (argToks.length >= 2 && argToks[0].t === 'id' && argToks[1].t === 'op' && argToks[1].v === '=') {
    return { name: argToks[0].v, toks: argToks.slice(2) }
  }
  return { name: null, toks: argToks }
}

const CALL_STATEMENTS = new Set(['plot', 'hline', 'bgcolor', 'plotshape', 'indicator', 'strategy'])

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
  const markers = {}
  let plotN = 0
  let scriptTitle = null

  const FUNCS = {
    'ta.sma': (a) => sma(a[0], int(a[1])),
    'ta.ema': (a) => ema(a[0], int(a[1])),
    'ta.wma': (a) => wma(a[0], int(a[1])),
    'ta.rsi': (a) => rsi(a[0], int(a[1])),
    'ta.stdev': (a) => stdev(a[0], int(a[1])),
    'ta.highest': (a) => rolling(a[0], int(a[1]), (w) => Math.max(...w)),
    'ta.lowest': (a) => rolling(a[0], int(a[1]), (w) => Math.min(...w)),
    'ta.crossover': (a) => crossover(asSeries(a[0]), asSeries(a[1])),
    'ta.crossunder': (a) => crossunder(asSeries(a[0]), asSeries(a[1])),
    'ta.atr': (a) => atr(int(a[0]), SOURCES.high, SOURCES.low, SOURCES.close),
    'ta.vwap': (a) => vwapCum(asSeries(a[0] != null ? a[0] : SOURCES.close), SOURCES.volume),
    'ta.macd': (a) => {
      const src = asSeries(a[0]), fast = int(a[1]), slow = int(a[2]), sig = int(a[3])
      const macdLine = subSeries(ema(src, fast), ema(src, slow))
      const signalLine = ema(macdLine, sig)
      const hist = subSeries(macdLine, signalLine)
      return { __tuple: [macdLine, signalLine, hist] }
    },
    'input.int': (a) => a[0],
    'input.float': (a) => a[0],
    'input.bool': (a) => a[0],
    'input.string': (a) => a[0],

    // ── real-indicator research pass, 2026-08-29 (see file header comment
    // and commit message for the full evidence trail) ──
    'ta.bb': (a) => bbands(asSeries(a[0]), int(a[1]), num(a[2])),
    'ta.kc': (a) => keltner(asSeries(a[0]), SOURCES.high, SOURCES.low, SOURCES.close, int(a[1]), num(a[2])),
    'ta.dmi': (a) => dmi(SOURCES.high, SOURCES.low, SOURCES.close, int(a[0]), int(a[1])),
    'ta.supertrend': (a) => supertrend(SOURCES.high, SOURCES.low, SOURCES.close, num(a[0]), int(a[1])),
    'ta.sar': (a) => sar(SOURCES.high, SOURCES.low, SOURCES.close, num(a[0]), num(a[1]), num(a[2])),
    'ta.cci': (a) => cci(asSeries(a[0]), int(a[1])),
    'ta.mfi': (a) => mfi(asSeries(a[0]), SOURCES.volume, int(a[1])),
    'ta.obv': () => obv(SOURCES.close, SOURCES.volume),
    'ta.wpr': (a) => wpr(SOURCES.high, SOURCES.low, SOURCES.close, int(a[0])),
    'ta.stoch': (a) => stochK(asSeries(a[0]), asSeries(a[1]), asSeries(a[2]), int(a[3])),
    'ta.linreg': (a) => linreg(asSeries(a[0]), int(a[1]), a[2] != null ? intOffset(a[2]) : 0),
    'ta.stochrsi': (a) => stochRsi(asSeries(a[0]), int(a[1]), int(a[2]), int(a[3]), int(a[4])),
    'ta.squeeze': (a) => squeezeMomentum(SOURCES.high, SOURCES.low, SOURCES.close, int(a[0]), num(a[1]), num(a[2])),
    'ta.ichimoku': (a) => ichimoku(SOURCES.high, SOURCES.low, int(a[0]), int(a[1]), int(a[2])),
    'ta.fib': (a) => fibLevel(SOURCES.high, SOURCES.low, num(a[0]), int(a[1])),
    'ta.fvgbull': (a) => fvgBullish(SOURCES.high, SOURCES.low, a[0] != null ? num(a[0]) : 0),
    'ta.fvgbear': (a) => fvgBearish(SOURCES.high, SOURCES.low, a[0] != null ? num(a[0]) : 0),
  }

  function asSeries(x) {
    if (Array.isArray(x)) return x
    return new Array(N).fill(x)
  }
  function binop(a, b, op) {
    const A = asSeries(a), B = asSeries(b)
    const out = new Array(N).fill(null)
    for (let i = 0; i < N; i++) {
      if (A[i] == null || B[i] == null || typeof A[i] !== 'number' || typeof B[i] !== 'number') continue
      out[i] = op === '+' ? A[i] + B[i] : op === '-' ? A[i] - B[i] : op === '*' ? A[i] * B[i] : (B[i] ? A[i] / B[i] : null)
    }
    return out
  }
  function compareOp(a, b, op) {
    const A = asSeries(a), B = asSeries(b)
    const out = new Array(N).fill(null)
    for (let i = 0; i < N; i++) {
      const x = A[i], y = B[i]
      if (x == null || y == null) continue
      out[i] = op === '>' ? x > y : op === '<' ? x < y : op === '>=' ? x >= y : op === '<=' ? x <= y : op === '==' ? x === y : x !== y
    }
    return out
  }
  function logicalOp(a, b, kind) {
    const A = asSeries(a), B = asSeries(b)
    const out = new Array(N).fill(null)
    for (let i = 0; i < N; i++) {
      const av = A[i], bv = B[i]
      if (kind === 'and') {
        if (av === false || bv === false) { out[i] = false; continue }
        if (av == null || bv == null) continue
        out[i] = !!(av && bv)
      } else {
        if (av === true || bv === true) { out[i] = true; continue }
        if (av == null || bv == null) continue
        out[i] = !!(av || bv)
      }
    }
    return out
  }
  function logicalNot(a) {
    return asSeries(a).map((v) => (v == null ? null : !v))
  }
  function ternary(cond, a, b) {
    const C = asSeries(cond), A = asSeries(a), B = asSeries(b)
    const out = new Array(N).fill(null)
    for (let i = 0; i < N; i++) out[i] = C[i] == null ? null : (C[i] ? A[i] : B[i])
    return out
  }
  function shiftSeries(s, n) {
    const S = asSeries(s)
    const out = new Array(N).fill(null)
    for (let i = 0; i < N; i++) { const j = i - n; out[i] = j >= 0 ? S[j] : null }
    return out
  }

  // expression parser (recursive descent, lowest to highest precedence):
  // ternary -> or -> and -> not -> comparison -> additive -> multiplicative
  // -> unary -> postfix([n]) -> primary
  function parseExpr(toks) {
    let pos = 0
    const peek = () => toks[pos]
    const next = () => toks[pos++]
    const isWordOp = (word) => { const p = peek(); return p && p.t === 'id' && p.v === word }

    function primary() {
      const tk = next()
      if (!tk) throw new Error('Unexpected end of expression')
      if (tk.t === 'num') return tk.v
      if (tk.t === 'str') return tk.v
      if (tk.t === 'op' && tk.v === '(') { const e = ternaryExpr(); const c = next(); if (!c || c.v !== ')') throw new Error('Expected )'); return e }
      if (tk.t === 'op' && tk.v === '-') return binop(0, unary(), '-')
      if (tk.t === 'id') {
        if (tk.v === 'true') return true
        if (tk.v === 'false') return false
        if (tk.v === 'na') return null
        if (peek() && peek().v === '(') {
          next() // (
          const args = []
          if (peek() && peek().v !== ')') { args.push(ternaryExpr()); while (peek() && peek().v === ',') { next(); args.push(ternaryExpr()) } }
          const c = next(); if (!c || c.v !== ')') throw new Error('Expected ) after args')
          const fn = FUNCS[tk.v]
          if (fn) return fn(args)
          if (COSMETIC_NAMESPACES.has(tk.v.split('.')[0])) return tk.v // inert placeholder
          throw new Error('Unknown function: ' + tk.v)
        }
        if (tk.v in SOURCES) return SOURCES[tk.v]
        if (tk.v in vars) return vars[tk.v]
        if (COSMETIC_NAMESPACES.has(tk.v.split('.')[0])) return tk.v // inert placeholder
        throw new Error('Unknown identifier: ' + tk.v)
      }
      throw new Error('Unexpected token: ' + JSON.stringify(tk))
    }
    function postfix() {
      let a = primary()
      while (peek() && peek().v === '[') {
        next() // [
        const n = next()
        if (!n || n.t !== 'num') throw new Error('History reference [n] requires a literal integer')
        const c = next(); if (!c || c.v !== ']') throw new Error('Expected ]')
        a = shiftSeries(a, Math.round(n.v))
      }
      return a
    }
    function unary() { return postfix() }
    function multiplicative() { let a = unary(); while (peek() && (peek().v === '*' || peek().v === '/')) { const op = next().v; a = binop(a, unary(), op) } return a }
    function additive() { let a = multiplicative(); while (peek() && (peek().v === '+' || peek().v === '-')) { const op = next().v; a = binop(a, multiplicative(), op) } return a }
    function comparison() {
      let a = additive()
      if (peek() && peek().t === 'op' && ['>', '<', '>=', '<=', '==', '!='].includes(peek().v)) {
        const op = next().v
        a = compareOp(a, additive(), op)
      }
      return a
    }
    function notExpr() { if (isWordOp('not')) { next(); return logicalNot(notExpr()) } return comparison() }
    function andExpr() { let a = notExpr(); while (isWordOp('and')) { next(); a = logicalOp(a, notExpr(), 'and') } return a }
    function orExpr() { let a = andExpr(); while (isWordOp('or')) { next(); a = logicalOp(a, andExpr(), 'or') } return a }
    function ternaryExpr() {
      const cond = orExpr()
      if (peek() && peek().v === '?') {
        next()
        const a = ternaryExpr()
        const c = next(); if (!c || c.v !== ':') throw new Error('Expected : in ternary')
        const b = ternaryExpr()
        return ternary(cond, a, b)
      }
      return cond
    }

    const result = ternaryExpr()
    if (pos !== toks.length) throw new Error('Trailing tokens in expression')
    return result
  }

  // statements: split by newline
  for (const rawLine of script.split('\n')) {
    const line = rawLine.replace(/\/\/.*$/, '').trim()
    if (!line) continue
    const toks = tokenize(line)
    if (toks.length === 0) continue

    // destructuring assignment: [a, b, c] = <tuple-returning call>
    if (toks[0].t === 'op' && toks[0].v === '[') {
      let depth = 0, end = -1
      for (let i = 0; i < toks.length; i++) {
        if (toks[i].v === '[') depth++
        else if (toks[i].v === ']') { depth--; if (depth === 0) { end = i; break } }
      }
      if (end < 0) throw new Error('Unbalanced [ in destructuring assignment')
      const names = []
      for (let i = 1; i < end; i++) {
        if (toks[i].t === 'id') names.push(toks[i].v)
        else if (toks[i].v === ',') continue
        else throw new Error('Invalid destructuring target')
      }
      if (!(toks[end + 1] && toks[end + 1].v === '=')) throw new Error('Expected = after destructuring target')
      const rhs = parseExpr(toks.slice(end + 2))
      if (!rhs || typeof rhs !== 'object' || !rhs.__tuple) throw new Error('Right side of destructuring is not a tuple-returning call')
      if (rhs.__tuple.length !== names.length) throw new Error(`Destructure count mismatch: ${names.length} names, ${rhs.__tuple.length} values`)
      names.forEach((n, i) => { vars[n] = rhs.__tuple[i] })
      continue
    }

    // plain assignment: id = expr
    if (toks.length >= 3 && toks[0].t === 'id' && toks[1].t === 'op' && toks[1].v === '=') {
      vars[toks[0].v] = parseExpr(toks.slice(2))
      continue
    }

    // call statements: plot/hline/bgcolor/plotshape/indicator/strategy
    if (toks[0].t === 'id' && CALL_STATEMENTS.has(toks[0].v) && toks[1] && toks[1].v === '(') {
      const fname = toks[0].v
      let depth = 0, end = -1
      for (let i = 1; i < toks.length; i++) {
        if (toks[i].v === '(') depth++
        else if (toks[i].v === ')') { depth--; if (depth === 0) { end = i; break } }
      }
      if (end < 0) throw new Error('Unbalanced ' + fname + '()')
      const rawArgs = splitTopLevelArgs(toks.slice(2, end))

      if (fname === 'indicator' || fname === 'strategy') {
        if (rawArgs[0] && rawArgs[0].length === 1 && rawArgs[0][0].t === 'str') scriptTitle = rawArgs[0][0].v
        continue
      }

      // Evaluate every arg (named-prefix stripped) — real errors inside any
      // arg (including cosmetic-only ones, now that namespace lookups are
      // inert placeholders rather than failures) still surface as real
      // parse errors; nothing is silently swallowed.
      const evaluated = rawArgs.map((raw) => {
        const { name, toks: valToks } = stripNamed(raw)
        if (valToks.length === 1 && valToks[0].t === 'str') return { name, value: valToks[0].v, isStr: true }
        return { name, value: parseExpr(valToks), isStr: false }
      })

      if (fname === 'plot') {
        const seriesArg = evaluated.find((e) => e.name === null && !e.isStr)
        if (!seriesArg) throw new Error('plot() requires a series argument')
        const titleArg = evaluated.find((e) => e.isStr)
        const series = asSeries(seriesArg.value)
        const name = (titleArg && titleArg.value) || `plot_${++plotN}`
        plots[name] = candles.map((c, i) => ({
          time: Number(c.time),
          value: (series[i] == null || typeof series[i] !== 'number' || !isFinite(series[i])) ? null : Number(series[i].toFixed(8)),
        }))
      } else if (fname === 'hline') {
        const levelArg = evaluated[0]
        const titleArg = evaluated.find((e) => e.isStr)
        let level = null
        if (levelArg) {
          const v = levelArg.value
          level = typeof v === 'number' ? v : Array.isArray(v) ? (v.find((x) => typeof x === 'number') ?? null) : null
        }
        const name = (titleArg && titleArg.value) || `hline_${++plotN}`
        plots[name] = candles.map((c) => ({ time: Number(c.time), value: level == null ? null : Number(level) }))
      } else if (fname === 'plotshape') {
        const condArg = evaluated.find((e) => e.name === null && !e.isStr)
        if (!condArg) throw new Error('plotshape() requires a condition argument')
        const titleArg = evaluated.find((e) => e.isStr)
        const series = asSeries(condArg.value)
        const name = (titleArg && titleArg.value) || `marker_${++plotN}`
        markers[name] = candles.map((c, i) => ({ time: Number(c.time), value: series[i] === true }))
      } else if (fname === 'bgcolor') {
        // Pure visual cue (a color, not a number) — parse-validated above,
        // no numeric series to extract. Deliberately not an error.
      }
      continue
    }

    throw new Error('Unsupported statement: ' + line)
  }

  if (Object.keys(plots).length === 0 && Object.keys(markers).length === 0) {
    throw new Error('Script produced no plot()/plotshape() output')
  }
  return { plots, markers, alerts: [], title: scriptTitle }
}

module.exports = { evaluatePine }
